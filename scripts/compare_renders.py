"""Vergleichsbild Blender vs. three.js: pro Render-ID oben die 8 Blender-Views, unten die 8 three.js-Views.

    uv run python scripts/compare_renders.py --mode asset
    uv run python scripts/compare_renders.py --mode highlight --only centrifuge --limit 5

Output: data/interim/renders_compare/<mode>/<id>.png (nur IDs, die beide Backends gerendert haben)
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

from vuse_xr.cap3d.render import ROOT, VIEW_FILES, load_cfg, render_root

TILE = 256


def row(d: Path) -> Image.Image:
    img = Image.new("RGB", (TILE * len(VIEW_FILES), TILE), "white")
    for i, v in enumerate(VIEW_FILES):
        im = Image.open(d / v).convert("RGBA").resize((TILE, TILE), Image.LANCZOS)
        img.paste(im, (i * TILE, 0), im)  # transparenter Hintergrund -> weiß wie für das Vision-Modell
    return img


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", default="asset", choices=["asset", "part", "highlight"])
    ap.add_argument("--config", default=str(ROOT / "configs" / "cap3d.yaml"))
    ap.add_argument("--only")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    cfg = load_cfg(Path(args.config))
    roots = {b: render_root(cfg, args.mode, b) / "Cap3D_imgs" for b in ("blender", "threejs")}
    done = {b: {d.name for d in r.glob("*") if all((d / v).exists() for v in VIEW_FILES)} for b, r in roots.items()}
    ids = sorted(done["blender"] & done["threejs"])
    ids = [i for i in ids if not args.only or args.only in i][: args.limit]
    if not ids:
        raise SystemExit("Keine IDs, die von beiden Backends gerendert sind.")

    out_dir = ROOT / "data" / "interim" / "renders_compare" / args.mode
    out_dir.mkdir(parents=True, exist_ok=True)
    for rid in ids:
        a, b = row(roots["blender"] / rid), row(roots["threejs"] / rid)
        img = Image.new("RGB", (a.width, 2 * TILE + 24), "white")
        img.paste(a, (0, 24))
        img.paste(b, (0, 24 + TILE))
        draw = ImageDraw.Draw(img)
        draw.text((6, 6), f"{rid}   oben: blender   unten: threejs", fill="black")
        img.save(out_dir / f"{rid}.png")
    print(f"[compare] {len(ids)} Vergleichsbilder -> {out_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
