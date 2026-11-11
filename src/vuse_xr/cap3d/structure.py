"""Pipeline 3d: Hierarchie und Geometrie eines Teils als Text-Kontext für caption.py.

    uv run python -m vuse_xr.cap3d.structure --config configs/cap3d_uuid.yaml --only <part_id>   # Block ansehen

Input:
    data/interim/uid_mapping.json aus Pipeline 1 (path, kind, n_meshes, size, rel_size)

Pro Teil ein kurzer Textblock nur mit Strukturfakten aus dem Szenengraph, z. B.
    Level 2 of 3 in the assembly tree.
    Parent sub-assembly contains 13 parts; 4 of them (incl. this one) have identical dimensions.
    Size: 8.0 % of the whole object, rank 6 of 13 among siblings. Shape: flat (2:2:1).

Der Block enthält nur Zahlen und feste Formulierungen, nie node, path oder aliases.
Eltern und Geschwister über group_of(): nächste Baugruppe im Mapping, Hüllen (Aliase) werden übersprungen.
Eingeschaltet in caption.py über hierarchy: true in der Variante (Variante hier).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from ..log import add_log_args, setup_logging
from .render import ROOT, load_cfg

SAME_DIMS_TOL = 0.02  # Maße gelten als gleich bei Abweichung <= 2 % der längsten Kante


def group_of(entry: dict, mapping: dict) -> str | None:
    """Nächste Baugruppe über dem Teil als part_id, sonst None.
    Läuft den path von unten nach oben, weil der direkte Elternknoten oft nur eine Hülle (Alias) ist."""
    stem = Path(entry["asset"]).stem
    for node in reversed([n for n in entry["path"].split("/") if n]):
        if f"{stem}__{node}" in mapping:
            return f"{stem}__{node}"
    return None


def parent_key(entry: dict, mapping: dict) -> str:
    """Baugruppe als part_id, oder 'ROOT:<asset>' für Teile ohne Baugruppe darüber."""
    return group_of(entry, mapping) or "ROOT:" + Path(entry["asset"]).stem


def level(part_id: str, mapping: dict) -> int:
    """1 = oberste Ebene. Zählt nur Baugruppen im Mapping, Hüllen nicht."""
    n, g = 1, group_of(mapping[part_id], mapping)
    while g:
        n, g = n + 1, group_of(mapping[g], mapping)
    return n


def build_tree(mapping: dict) -> dict:
    """Einmal pro Lauf: Kinder je Baugruppe (bzw. 'ROOT:<asset>') und tiefste Ebene je Asset."""
    children: dict[str, list[str]] = defaultdict(list)
    max_level: dict[str, int] = defaultdict(int)
    for pid, e in mapping.items():
        children[parent_key(e, mapping)].append(pid)
        stem = Path(e["asset"]).stem
        max_level[stem] = max(max_level[stem], level(pid, mapping))
    return {"children": dict(children), "max_level": dict(max_level)}


def same_dims(a: list[float], b: list[float], tol: float = SAME_DIMS_TOL) -> bool:
    """Maßgleich, auch wenn um 90° gedreht (sortierte Maße). Schräg gedrehte Kopien erkennt das nicht,
    weil size eine achsenparallele Bounding-Box in Weltkoordinaten ist."""
    sa, sb = sorted(a), sorted(b)
    return all(abs(x - y) <= tol * max(sa[-1], sb[-1]) for x, y in zip(sa, sb))


def shape(size: list[float]) -> str:
    """Grobe Form aus den sortierten Maßen: elongated (Welle, Schraube) | flat (Platte) | compact."""
    l, w, h = sorted(size, reverse=True)
    if l <= 0:
        return "unknown"
    h = max(h, l / 100)  # flache Teile: Division durch ~0 vermeiden
    w = max(w, h)
    label = "elongated" if l >= 3 * w else "flat" if w >= 3 * h else "compact"
    return f"{label} ({l / h:.0f}:{w / h:.0f}:1)"


def describe(part_id: str, mapping: dict, tree: dict) -> str:
    """Textblock mit Struktur- und Geometriefakten. Nur Zahlen, keine Knotennamen."""
    e = mapping[part_id]
    stem = Path(e["asset"]).stem
    sibs = tree["children"][parent_key(e, mapping)]
    same = sum(same_dims(mapping[s]["size"], e["size"]) for s in sibs)
    rank = 1 + sum(max(mapping[s]["size"]) > max(e["size"]) for s in sibs)
    where = "Parent sub-assembly" if group_of(e, mapping) else "The top level of the object"

    lines = [
        f"Level {level(part_id, mapping)} of {tree['max_level'][stem]} in the assembly tree.",
        f"{where} contains {len(sibs)} parts; "
        + (f"{same} of them (incl. this one) have identical dimensions." if same > 1
           else "no other part there has identical dimensions."),
    ]
    size = f"Size: {e['rel_size'] * 100:.1f} % of the whole object, " if e.get("rel_size") is not None else "Size: "
    lines.append(f"{size}rank {rank} of {len(sibs)} among siblings. Shape: {shape(e['size'])}.")
    if e["kind"] == "group":
        lines.append(f"This part is itself a sub-assembly of {e['n_meshes']} meshes.")
    return "Structure facts from the CAD scene graph:\n" + "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "configs" / "cap3d.yaml"))
    ap.add_argument("--only", help="nur part_ids, die diesen Text enthalten")
    ap.add_argument("--limit", type=int, help="höchstens N Teile ausgeben")
    add_log_args(ap)
    args = ap.parse_args()
    setup_logging(args.verbose, args.quiet)

    cfg = load_cfg(Path(args.config))
    mp = ROOT / cfg["split"]["uid_mapping"]
    if not mp.exists():
        sys.exit(f"{mp} fehlt – erst Pipeline 1 (split_nodes.py) laufen lassen.")
    mapping = json.loads(mp.read_text())
    tree = build_tree(mapping)

    ids = [p for p in sorted(mapping) if not args.only or args.only in p][: args.limit]
    for pid in ids:
        print(f"## {pid}\n{describe(pid, mapping, tree)}\n")


if __name__ == "__main__":
    main()
