"""Gemeinsame Datenbasis der Evaluation (judge.py, evaluate.py, grounding.py): Teile, richtige Namen, Captions.

    uv run python -m vuse_xr.cap3d.eval_data --config configs/cap3d_uuid.yaml                 # Übersicht
    uv run python -m vuse_xr.cap3d.eval_data --config configs/cap3d_uuid.yaml --only torque_wrench -v

Input:
    data/interim/uuid/uid_mapping.json aus Pipeline 1 (part_id -> asset, node, kind, path, aliases, …)
    data/raw/3d_assets_uuid/ground_truth.csv aus scripts/make_uuid_testset.py (uuid -> Originalname, Pfad)
    <paths.output>/captions/<modell>/<variante>/<part_id>.json aus Pipeline 3

Im UUID-Testset stehen in uid_mapping.json nur UUIDs (node, path, aliases), weil split_nodes.py auf den
anonymisierten GLBs läuft. Die Originalnamen kommen über die UUID aus ground_truth.csv (liegt neben den GLBs).
Richtige Namen pro Teil = Mesh-/Gruppenname + Hüllen aus aliases (Variante C). Wurzelknoten sind keine Teile.

Fehlgeschlagene Teile speichert caption.py nicht: missing() liefert sie, evaluate.py zählt sie als 0.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ..log import add_log_args, setup_logging
from .caption import asset_label
from .render import ROOT, load_cfg

log = logging.getLogger("vuse_xr.cap3d.eval_data")  # fester Name: bei python -m wäre __name__ == "__main__"

GT_FILE = "ground_truth.csv"


@dataclass(frozen=True)
class Part:
    part_id: str
    asset: str                 # Stem, z. B. 04_torque_wrench
    asset_label: str           # "torque wrench"
    kind: str                  # mesh | group
    node: str                  # Originalname
    names: tuple[str, ...]     # richtige Namen: node + Hüllen (Originalnamen)
    path: str                  # Elternknoten mit Originalnamen, ohne Wurzel, z. B. Housing_Assembly/Control_Panel
    rel_size: float | None
    n_meshes: int | None       # nur bei Baugruppen


@dataclass(frozen=True)
class Caption:
    model: str                 # Ordnername (safe_name), echte Version steht in meta
    variant: str
    part_id: str
    caption: dict              # name, type, function, description, confidence
    meta: dict


def load_ground_truth(path: Path) -> dict[str, dict]:
    """ground_truth.csv -> {uuid: Zeile}."""
    with open(path, newline="", encoding="utf-8") as f:
        return {r["uuid"]: r for r in csv.DictReader(f)}


def gt_path_for(mapping: dict) -> Path:
    """ground_truth.csv liegt neben den UUID-GLBs (Verzeichnis von mapping[*].asset)."""
    dirs = {(ROOT / v["asset"]).parent for v in mapping.values()}
    if len(dirs) != 1:
        raise ValueError(f"Assets aus mehreren Verzeichnissen im Mapping: {sorted(map(str, dirs))}")
    return dirs.pop() / GT_FILE


def build_parts(mapping: dict, gt: dict[str, dict]) -> dict[str, Part]:
    """Mapping (UUIDs) + Ground Truth -> Teile mit Originalnamen. Fehlende UUID = Fehler (falsches Testset)."""
    def orig(uuid: str, part_id: str) -> dict:
        row = gt.get(uuid)
        if row is None:
            raise KeyError(f"{part_id}: Knoten {uuid!r} fehlt in {GT_FILE} – Mapping und Testset passen nicht")
        return row

    parts = {}
    for part_id, v in mapping.items():
        row = orig(v["node"], part_id)
        stem = Path(v["asset"]).stem
        if row["asset"] != stem:
            raise ValueError(f"{part_id}: Asset {stem!r} im Mapping, {row['asset']!r} in {GT_FILE}")
        if row["kind"] == "root":
            continue
        hulls = [orig(a, part_id)["node"] for a in v.get("aliases", [])]
        parts[part_id] = Part(
            part_id=part_id,
            asset=stem,
            asset_label=asset_label(v["asset"]),
            kind=v["kind"],
            node=row["node"],
            names=tuple(dict.fromkeys([row["node"], *hulls])),  # Reihenfolge behalten, doppelte raus
            path=row["path"],
            rel_size=v.get("rel_size"),
            n_meshes=v.get("n_meshes"),
        )
    return parts


def load_parts(cfg: dict) -> dict[str, Part]:
    mp = ROOT / cfg["split"]["uid_mapping"]
    if not mp.exists():
        raise FileNotFoundError(f"{mp} fehlt – erst Pipeline 1 (split_nodes.py) laufen lassen")
    mapping = json.loads(mp.read_text())
    gp = gt_path_for(mapping)
    if not gp.exists():
        raise FileNotFoundError(f"{gp} fehlt – erst scripts/make_uuid_testset.py laufen lassen")
    return build_parts(mapping, load_ground_truth(gp))


def captions_dir(cfg: dict) -> Path:
    return ROOT / cfg["paths"]["output"] / "captions"


def iter_captions(root: Path, models: Iterable[str] | None = None,
                  variants: Iterable[str] | None = None) -> Iterator[Caption]:
    """Alle Captions unter <root>/<modell>/<variante>/<part_id>.json; unlesbare Dateien -> Warnung."""
    models, variants = set(models or []), set(variants or [])
    for f in sorted(root.glob("*/*/*.json")):
        variant_dir = f.parent
        model, variant = variant_dir.parent.name, variant_dir.name
        if (models and model not in models) or (variants and variant not in variants):
            continue
        try:
            d = json.loads(f.read_text())
            yield Caption(model, variant, d.get("part_id", f.stem), d["caption"], d.get("meta", {}))
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            log.warning("%s unlesbar, übersprungen: %s", f.relative_to(root), e)


def missing(part_ids: Iterable[str], root: Path, model: str, variant: str) -> list[str]:
    """Erwartete Teile ohne Caption-Datei (fehlgeschlagen oder übersprungen) -> status failed."""
    d = root / model / variant
    return [p for p in part_ids if not (d / f"{p}.json").exists()]


_BLENDER_SUFFIX = re.compile(r"\.\d{3}$")                       # Flywheel.001
_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[a-z])(?=\d)")


def norm_name(s: str) -> str:
    """Originalname -> Klartext: 'RatchetHead.001' -> 'ratchet head', 'Rubber_Foot_1' -> 'rubber foot 1'."""
    s = _BLENDER_SUFFIX.sub("", s.strip())
    s = _CAMEL.sub(" ", s)
    s = re.sub(r"[_\-.\s]+", " ", s)
    return s.strip().lower()


def twin_key(s: str) -> str:
    """Name ohne Nummern, für Zwillinge: 'Rubber_Foot_2' -> 'rubber foot' (= Rubber_Foot_1)."""
    return " ".join(t for t in norm_name(s).split() if not t.isdigit())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "configs" / "cap3d_uuid.yaml"))
    ap.add_argument("--only", help="nur part_ids, die diesen Text enthalten (z. B. Assetname)")
    add_log_args(ap)
    args = ap.parse_args()
    setup_logging(args.verbose, args.quiet)

    cfg = load_cfg(Path(args.config))
    parts = {k: v for k, v in load_parts(cfg).items() if not args.only or args.only in k}
    kinds = Counter(p.kind for p in parts.values())
    log.info("%d Teile (%d Meshes, %d Baugruppen) aus %d Assets", len(parts), kinds["mesh"], kinds["group"],
             len({p.asset for p in parts.values()}))
    for p in parts.values():
        log.debug("%s: %s | %s | %s", p.part_id, " = ".join(p.names), p.path or "-", p.kind)

    root = captions_dir(cfg)
    caps = Counter((c.model, c.variant) for c in iter_captions(root) if c.part_id in parts)
    for (model, variant), n in sorted(caps.items()):
        log.info("Captions %s / %s: %d/%d", model, variant, n, len(parts))
    if not caps:
        log.info("keine Captions unter %s", root.relative_to(ROOT))


if __name__ == "__main__":
    main()
