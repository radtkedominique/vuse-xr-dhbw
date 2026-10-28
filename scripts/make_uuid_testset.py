from __future__ import annotations

import argparse
import copy
import csv
import json
import logging
import struct
import sys
import uuid
from pathlib import Path

from vuse_xr.log import add_log_args, setup_logging

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
log = logging.getLogger("vuse_xr.make_uuid_testset")

NS = uuid.uuid5(uuid.NAMESPACE_URL, "vusexr-dhbw-testset")


def read_glb(path: Path) -> tuple[dict, bytes]:
    """returns JSON as dict + keeps binary chunk the same."""
    data = path.read_bytes()
    if data[:4] != b"glTF":
        raise ValueError(f"{path.name} ist keine GLB-Datei")
    jlen = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20:20 + jlen]), data[20 + jlen:]

def write_glb(path: Path, gltf: dict, rest: bytes) -> None:
    """writes anonymized dict to JSON, saves anonymized glb"""
    j = json.dumps(gltf, separators=(",", ":"), ensure_ascii=False).encode()
    j += b" " * (-len(j) % 4)
    total = 12 + 8 + len(j) + len(rest)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(j), b"JSON") + j + rest)

def anonymize(stem: str, gltf: dict):
    """anonymizes the glb file, replacing all names with UUIDs. returns ground-truth rows."""
    nodes = gltf["nodes"]
    roots = set(gltf["scenes"][gltf.get("scene", 0)]["nodes"])
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
    orig = [n.get("name", f"node_{i}") for i, n in enumerate(nodes)]

    def path_of(i: int) -> str:
        names = []
        while i in parent:
            i = parent[i]
            if i not in roots:
                names.append(orig[i])
        return "/".join(reversed(names))

    def mesh_desc(i: int) -> list[int]:
        out = [i] if "mesh" in nodes[i] else []
        for c in nodes[i].get("children", []):
            out += mesh_desc(c)
        return out

    ids = [str(uuid.uuid5(NS, f"{stem}/{path_of(i)}/{orig[i]}")) for i in range(len(nodes))]

    rows = []
    for i, n in enumerate(nodes):
        rows.append({
            "uuid": ids[i],
            "asset": stem,
            "node": orig[i],
            "path": path_of(i),
            "kind": "mesh" if "mesh" in n else "root" if i in roots else "group",
            "n_meshes": len(mesh_desc(i)),
            "parent_uuid": ids[parent[i]] if i in parent else "",
        })
        n["name"] = ids[i]
        n.pop("extras", None)

    for m in gltf.get("meshes", []):
        m.pop("name", None)
        m.pop("extras", None)
    for s in gltf.get("scenes", []):
        s.pop("extras", None)
    gltf.pop("extras", None)
    return rows

def strip_names(g: dict) -> dict:
    g = copy.deepcopy(g)
    for key in ("nodes", "meshes"):
        for x in g.get(key, []):
            x.pop("name", None)
            x.pop("extras", None)
    for s in g.get("scenes", []):
        s.pop("extras", None)
    g.pop("extras", None)
    return g


def check(src_dir: Path, dst_dir: Path) -> bool:
    """checks if the glb file is valid"""
    ok = True
    gt = list(csv.DictReader(open(dst_dir / "ground_truth.csv", encoding="utf-8")))
    total = 0
    for src in sorted(src_dir.glob("*.glb")):
        g0, b0 = read_glb(src)
        g1, b1 = read_glb(dst_dir / src.name)
        total += len(g0["nodes"])
        names = {n.get("name") for n in g0["nodes"]} | {m.get("name") for m in g0.get("meshes", [])}
        blob = json.dumps({k: v for k, v in g1.items() if k != "materials"})
        leaks = sorted(n for n in names - {None} if n in blob)
        same = b0 == b1 and strip_names(g0) == strip_names(g1)
        if leaks or not same:
            ok = False
            log.error("%s: identisch=%s, Lecks=%s", src.stem, same, leaks[:5])
    uuids = [r["uuid"] for r in gt]
    if len(uuids) != total or len(set(uuids)) != total:
        ok = False
        log.error("ground_truth.csv: %d Zeilen, %d eindeutig, erwartet %d", len(uuids), len(set(uuids)), total)
    log.info("Check %s: %d Knoten", "OK" if ok else "FEHLGESCHLAGEN", total)
    return ok

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=str(ROOT / "data/raw/3d_assets"))
    ap.add_argument("--dst", default=str(ROOT / "data/raw/3d_assets_uuid"))
    ap.add_argument("--check", action="store_true", help="nur prüfen, nichts schreiben")
    add_log_args(ap)
    args = ap.parse_args()
    setup_logging(args.verbose, args.quiet)
    src_dir, dst_dir = Path(args.src), Path(args.dst)

    if not args.check:
        dst_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        for src in sorted(src_dir.glob("*.glb")):
            gltf, rest = read_glb(src)
            r = anonymize(src.stem, gltf)
            write_glb(dst_dir / src.name, gltf, rest)
            log.info("%s: %d Knoten", src.stem, len(r))
            rows += r
        with open(dst_dir / "ground_truth.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    sys.exit(0 if check(src_dir, dst_dir) else 1)


if __name__ == "__main__":
    main()