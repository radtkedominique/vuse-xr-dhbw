"""Pipeline 4b: Ergebnisse aus Pipeline 3 mit der Ground Truth (Pipeline 4a) vergleichen.

    uv run python -m vuse_xr.cap3d.evaluate                       # Modell aus caption.model
    uv run python -m vuse_xr.cap3d.evaluate --model qwen2.5vl:7b

Input:
    <split.uid_mapping>                               part_id -> node (UUID), aliases (UUIDs der Hüllen)
    data/raw/3d_assets_uuid/ground_truth.csv          UUID -> echter Name
    <paths.output>/captions/<modell>/<part_id>.json   generierter Name (caption.name)
Output:
    <paths.output>/eval/<modell>.csv                  eine Zeile pro Teil, Trefferquote im Log
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from pathlib import Path

import yaml

from ..log import add_log_args, setup_logging
from .render import render_root

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
log = logging.getLogger("vuse_xr.cap3d.evaluate")


def model_dir_name(model: str) -> str:
    """Ordnername wie in caption.py: qwen2.5vl:7b -> qwen2.5vl_7b."""
    return re.sub(r"[^\w.-]", "_", model)


def load_truth(mapping: dict, gt_path: Path) -> dict[str, list[str]]:
    """part_id -> richtige Namen: Mesh-Name + Namen der Hüllen (Variante C)."""
    gt = {r["uuid"]: r["node"] for r in csv.DictReader(open(gt_path, newline=""))}
    return {pid: [gt[v["node"]]] + [gt[a] for a in v["aliases"]] for pid, v in mapping.items()}


def load_captions(cap_dir: Path) -> dict[str, dict]:
    """part_id -> Ergebnis aus Pipeline 3 (eine JSON-Datei pro Teil)."""
    out = {}
    for f in sorted(cap_dir.glob("*.json")):
        r = json.loads(f.read_text())
        out[r["part_id"]] = r
    return out


def normalize(name: str) -> str:
    """Control_Button_0 -> 'control button', FlyWheel001 -> 'fly wheel'."""
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)  # CamelCase trennen
    name = re.sub(r"[\d_\-.]+", " ", name).lower()    # Ziffern und Trennzeichen weg
    return " ".join(name.split())


def matches(generated: str, truth: list[str]) -> bool:
    """Vergleichsregel (Schritt 4). Erste Version: gleich nach Normalisierung."""
    g = normalize(generated)
    return any(g == normalize(t) for t in truth)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "configs" / "cap3d_uuid.yaml"))
    ap.add_argument("--gt", default=str(ROOT / "data" / "raw" / "3d_assets_uuid" / "ground_truth.csv"))
    ap.add_argument("--model", help="welcher Ordner unter captions/ (Standard: caption.model)")
    add_log_args(ap)
    args = ap.parse_args()
    setup_logging(args.verbose, args.quiet)

    # 1. Config + Pfade
    cfg = yaml.safe_load(open(args.config))
    model = args.model or cfg["caption"].get("model")
    if not model:
        sys.exit("Kein Modell: --model angeben oder caption.model in der Config setzen.")
    out_root = ROOT / cfg["paths"]["output"]
    cap_dir = out_root / "captions" / model_dir_name(model)

    # 2. Ground Truth + 3. Ergebnisse laden
    mapping = json.loads((ROOT / cfg["split"]["uid_mapping"]).read_text())
    truth = load_truth(mapping, Path(args.gt))
    captions = load_captions(cap_dir)
    log.info("%d Teile, %d Ergebnisse in %s", len(truth), len(captions), cap_dir.relative_to(ROOT))

    unknown = set(captions) - set(truth)
    if unknown:  # z. B. Pipeline 3 lief mit cap3d.yaml statt cap3d_uuid.yaml
        log.warning("%d Ergebnisse passen zu keinem Teil, z. B. %s", len(unknown), next(iter(unknown)))

    # 4. Vergleichen, eine Zeile pro Teil
    img_root = render_root(cfg, "part") / "Cap3D_imgs"
    rows = []
    for pid, names in truth.items():
        r = captions.get(pid)
        gen = r["caption"].get("name", "") if r else ""
        rows.append({
            "part_id": pid,
            "node_uuid": mapping[pid]["node"],
            "echter_name": " / ".join(names),
            "generierter_name": gen,
            "richtig": matches(gen, names) if r else None,  # None = noch kein Ergebnis
            "confidence": r["caption"].get("confidence") if r else None,
            "bild": str((img_root / pid / "00020.png").relative_to(ROOT)),
        })

    # 5. Tabelle schreiben + Trefferquote
    out = out_root / "eval" / f"{model_dir_name(model)}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    bewertet = [x for x in rows if x["richtig"] is not None]
    treffer = sum(x["richtig"] for x in bewertet)
    quote = 100 * treffer / len(bewertet) if bewertet else 0.0
    log.info("Trefferquote %s: %d/%d = %.1f %% (%d Teile noch ohne Ergebnis)",
             model, treffer, len(bewertet), quote, len(rows) - len(bewertet))
    log.info("Tabelle -> %s", out.relative_to(ROOT))


if __name__ == "__main__":
    main()