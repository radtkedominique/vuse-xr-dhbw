"""Pipeline 1: Assets in Einzelteile (Scene-Graph-Knoten) zerlegen.

    uv run python -m vuse_xr.cap3d.split_nodes                 # alle Assets
    uv run python -m vuse_xr.cap3d.split_nodes --only centrifuge
    uv run python -m vuse_xr.cap3d.split_nodes --level mesh    # nur Knoten mit eigenem Mesh

Was ist ein "Teil"?
    mesh:  glTF-Knoten mit eigenem Mesh (nur seine eigene Geometrie, ohne Kinder)
    group: glTF-Knoten ohne Mesh mit >= 2 Meshes darunter (Baugruppe, z. B. Lid_Assembly)
    Hüllen mit genau einem Mesh darunter werden nicht doppelt exportiert, sondern als Alias
    beim Mesh vermerkt (Housing_Screw_1 -> Housing_Screw_1_Screw). Der Wurzelknoten = ganzes Asset
    (dafür gibt es render --mode asset).

Output:
    data/interim/nodes/<asset>__<knoten>.glb   Teil in Weltkoordinaten, Materialien erhalten
    data/interim/object_paths.pkl              Liste absoluter Pfade (Input für render --mode part)
    data/interim/uid_mapping.json              part_id -> Infos (Input für render --mode highlight,
                                               Pipeline 3 und Ground Truth)
"""
from __future__ import annotations

import argparse
import glob
import json
import pickle
import re
import struct
import sys
from pathlib import Path

import numpy as np
import trimesh
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def clean(name: str) -> str:
    """Dateinamen-tauglich: alles außer Buchstaben, Ziffern, _ und - wird zu _ (Flywheel.001 -> Flywheel_001)."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def read_gltf_json(path: Path) -> dict:
    """Nur den JSON-Teil der GLB lesen: echte Knotennamen + Hierarchie, wie Blender sie sieht."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"glTF":
        raise ValueError(f"{path.name} ist keine GLB-Datei")
    length = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20:20 + length])


def plan_parts(gltf: dict, level: str) -> tuple[list[dict], dict[str, list[str]]]:
    """Welche glTF-Knoten werden zu Teilen? Gibt Teile + Aliase (Hülle -> Mesh) zurück."""
    nodes = gltf.get("nodes", [])
    roots = set(gltf["scenes"][gltf.get("scene", 0)]["nodes"])
    parent: dict[int, int] = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}

    def path_of(i: int) -> str:
        names = []
        while i in parent:
            i = parent[i]
            if i not in roots:
                names.append(nodes[i].get("name", f"node_{i}"))
        return "/".join(reversed(names))

    def mesh_desc(i: int) -> list[int]:
        out = [i] if "mesh" in nodes[i] else []
        for c in nodes[i].get("children", []):
            out += mesh_desc(c)
        return out

    parts, aliases = [], {}
    for i, n in enumerate(nodes):
        name = n.get("name", f"node_{i}")
        if "mesh" in n:
            if level in ("mesh", "all"):
                parts.append({"idx": i, "node": name, "kind": "mesh", "path": path_of(i)})
        elif i not in roots:
            desc = mesh_desc(i)
            if len(desc) == 1:  # reine Hülle -> Alias statt doppeltem Teil
                aliases.setdefault(nodes[desc[0]].get("name"), []).append(name)
            elif len(desc) >= 2 and level in ("group", "all"):
                parts.append({"idx": i, "node": name, "kind": "group", "path": path_of(i), "n_meshes": len(desc)})
    return parts, aliases


def collect_geometry(scene: trimesh.Scene, node: str, kind: str, gltf_names: set[str]) -> list[str]:
    """trimesh-Knoten mit Geometrie, die zu diesem glTF-Knoten gehören.

    trimesh hängt bei Meshes mit mehreren Materialien Hilfsknoten an (Handle -> Handle_d2dd09, …).
    mesh:  der Knoten selbst + Hilfsknoten, aber nicht die echten glTF-Kinder (Zollstock-Segmente)
    group: alles darunter
    """
    children = scene.graph.transforms.children
    out, stack = [], [node]
    while stack:
        n = stack.pop()
        if n in scene.graph.nodes_geometry:
            out.append(n)
        for c in children.get(n, []):
            if kind == "mesh" and c in gltf_names:
                continue
            stack.append(c)
    return out


def export_part(scene: trimesh.Scene, geom_nodes: list[str], out: Path) -> np.ndarray:
    """Geometrie in Weltkoordinaten in neue Szene kopieren und als GLB speichern. Gibt Bounding-Box zurück."""
    part = trimesh.Scene()
    for n in geom_nodes:
        transform, geom_name = scene.graph[n]
        mesh = scene.geometry[geom_name].copy()
        mesh.apply_transform(transform)
        part.add_geometry(mesh, node_name=n, geom_name=f"{n}_geom")
    out.parent.mkdir(parents=True, exist_ok=True)
    part.export(out, file_type="glb")
    return part.bounds


def split_asset(asset: Path, nodes_dir: Path, level: str, force: bool) -> dict[str, dict]:
    stem = asset.stem
    gltf = read_gltf_json(asset)
    gltf_names = {n.get("name") for n in gltf.get("nodes", [])}
    parts, aliases = plan_parts(gltf, level)
    scene = trimesh.load(asset, force="scene")
    asset_size = float(np.max(scene.extents))

    mapping, used = {}, set()
    for p in parts:
        if p["node"] not in scene.graph.transforms.children and p["node"] not in scene.graph.nodes_geometry:
            print(f"  [warn] Knoten '{p['node']}' fehlt im trimesh-Graph, übersprungen")
            continue
        part_id = f"{stem}__{clean(p['node'])}"
        while part_id in used:  # Kollision nach Bereinigung (z. B. 'a.1' und 'a_1')
            part_id += "_x"
        used.add(part_id)
        out = nodes_dir / f"{part_id}.glb"
        geom = collect_geometry(scene, p["node"], p["kind"], gltf_names)
        if not geom:
            continue
        if force or not out.exists():
            bounds = export_part(scene, geom, out)
        else:
            bounds = trimesh.load(out, force="scene").bounds
        size = (bounds[1] - bounds[0]).tolist()
        mapping[part_id] = {
            "asset": str(asset.relative_to(ROOT)),
            "node": p["node"],                       # originaler glTF-Name (für highlight)
            "kind": p["kind"],                       # mesh | group
            "path": p["path"],                       # Elternknoten, z. B. Housing_Assembly/Control_Panel
            "file": str(out.relative_to(ROOT)),
            "aliases": aliases.get(p["node"], []),
            "size": [round(s, 5) for s in size],     # Bounding-Box in Modell-Einheiten (glTF: Meter)
            "rel_size": round(max(size) / asset_size, 4) if asset_size else None,
            **({"n_meshes": p["n_meshes"]} if p["kind"] == "group" else {}),
        }
    return mapping


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "configs" / "cap3d.yaml"))
    ap.add_argument("--only", help="nur Assets, deren Name diesen Text enthält")
    ap.add_argument("--level", choices=["mesh", "group", "all"], help="überschreibt split.level aus der Config")
    ap.add_argument("--force", action="store_true", help="bereits exportierte Teile neu schreiben")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    sc = cfg["split"]
    level = args.level or sc.get("level", "all")
    nodes_dir = ROOT / sc["nodes_dir"]
    map_path, pkl_path = ROOT / sc["uid_mapping"], ROOT / sc["object_paths_pkl"]

    assets = sorted(Path(p) for p in glob.glob(str(ROOT / cfg["render"]["asset_glob"])))
    if args.only:
        assets = [a for a in assets if args.only in a.stem]
    if not assets:
        sys.exit("Keine Assets gefunden.")

    # bestehendes Mapping behalten, nur die bearbeiteten Assets ersetzen (für --only)
    mapping = json.loads(map_path.read_text()) if map_path.exists() else {}
    done_assets = {str(a.relative_to(ROOT)) for a in assets}
    mapping = {k: v for k, v in mapping.items() if v["asset"] not in done_assets}

    for a in assets:
        m = split_asset(a, nodes_dir, level, args.force)
        n_mesh = sum(v["kind"] == "mesh" for v in m.values())
        print(f"[split] {a.stem}: {len(m)} Teile ({n_mesh} mesh, {len(m) - n_mesh} group)")
        mapping.update(m)

    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(json.dumps(mapping, indent=2, ensure_ascii=False))
    with open(pkl_path, "wb") as f:
        pickle.dump([str((ROOT / v["file"]).resolve()) for v in mapping.values()], f)
    print(f"[split] gesamt {len(mapping)} Teile -> {map_path.relative_to(ROOT)}, {pkl_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
