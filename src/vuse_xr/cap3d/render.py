"""Pipeline 2: Rendern – Modi asset / part / highlight, Backend blender (DiffuRank) oder threejs.

    uv run python -m vuse_xr.cap3d.render --mode asset
    uv run python -m vuse_xr.cap3d.render --mode part  --only motor
    uv run python -m vuse_xr.cap3d.render --mode highlight --only motor --limit 3
    uv run python -m vuse_xr.cap3d.render --mode asset --backend threejs   # Backend aus Config überschreiben

Backend (render.backend in configs/cap3d.yaml, oder --backend):
    blender  Blender 3.4.1 + DiffuRank (Cycles)        -> paths.renders
    threejs  three.js in headless Chromium (Playwright) -> paths.renders_threejs
    Beide schreiben dieselbe Ordnerstruktur, nur in getrennte Wurzeln -> direkt vergleichbar.

Output:
    <renders>/<mode>/Cap3D_imgs/<id>/00020.png … 00027.png
    asset:     <id> = Asset-Dateiname ohne Endung
    part:      <id> = Teil-Dateiname aus Pipeline 1, z. B. motor__mesh_13
    highlight: <id> = part_id aus uid_mapping.json (= gleicher Name wie bei part)

Bereits fertig gerenderte IDs werden übersprungen (--force rendert neu).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pickle
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent          # src/vuse_xr/cap3d
ROOT = HERE.parents[2]                           # Repo-Root
VIEW_FILES = [f"{i:05}.png" for i in range(20, 28)]  # 8 Views, Namensschema von DiffuRank


def load_cfg(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


BACKENDS = ("blender", "threejs")


def render_root(cfg: dict, mode: str, backend: str | None = None) -> Path:
    """Ausgabeordner von Pipeline 2 (auch für Pipeline 3): blender -> paths.renders, threejs -> paths.renders_threejs."""
    backend = backend or cfg["render"].get("backend", "blender")
    if backend not in BACKENDS:
        sys.exit(f"Unbekanntes Backend '{backend}' (erlaubt: {', '.join(BACKENDS)})")
    key = "renders" if backend == "blender" else f"renders_{backend}"
    return ROOT / cfg["paths"][key] / mode


def rid(path: str | Path) -> str:
    """Render-ID: Dateiname bis zum ersten Punkt."""
    return Path(path).name.split(".")[0]


def check_name(path: str | Path) -> None:
    stem = Path(path).stem
    if "." in stem:
        sys.exit(
            f"Dateiname '{Path(path).name}' enthält einen Punkt. DiffuRank schneidet am ersten Punkt ab "
            f"('{rid(path)}') -> Kollisionen. Bitte umbenennen (Pipeline 1 macht das für Teile)."
        )


def is_done(out_root: Path, render_id: str) -> bool:
    d = out_root / "Cap3D_imgs" / render_id
    return all((d / v).exists() for v in VIEW_FILES)


def collect_asset(cfg: dict) -> list[Path]:
    paths = sorted(Path(p).resolve() for p in glob.glob(str(ROOT / cfg["render"]["asset_glob"])))
    if not paths:
        sys.exit(f"Keine Assets gefunden für {cfg['render']['asset_glob']}")
    return paths


def collect_part(cfg: dict) -> list[Path]:
    pkl = ROOT / cfg["split"]["object_paths_pkl"]
    if not pkl.exists():
        sys.exit(f"{pkl} fehlt – erst Pipeline 1 (split_nodes.py) laufen lassen.")
    with open(pkl, "rb") as f:
        paths = [Path(p) for p in pickle.load(f)]
    paths = [p if p.is_absolute() else (ROOT / p) for p in paths]
    missing = [p for p in paths if not p.exists()]
    for p in missing:
        print(f"[warn] Teil-Datei fehlt: {p}")
    return [p for p in paths if p.exists()]


def collect_highlight(cfg: dict) -> dict[str, list[dict]]:
    """uid_mapping.json -> {asset_pfad: [{part_id, node}, …]}"""
    mp = ROOT / cfg["split"]["uid_mapping"]
    if not mp.exists():
        sys.exit(f"{mp} fehlt – erst Pipeline 1 (split_nodes.py) laufen lassen.")
    with open(mp) as f:
        mapping = json.load(f)
    jobs: dict[str, list[dict]] = defaultdict(list)
    for part_id, entry in mapping.items():
        asset = Path(entry["asset"])
        asset = asset if asset.is_absolute() else ROOT / asset
        jobs[str(asset.resolve())].append({"part_id": part_id, "node": entry["node"], "kind": entry.get("kind", "group")})
    return jobs


def blender_cmd(cfg: dict, script: Path, script_args: list[str]) -> list[str]:
    blender = os.environ.get("BLENDER", cfg["render"]["blender"])
    if not Path(blender).exists():
        sys.exit(f"Blender nicht gefunden: {blender} (Pfad in configs/cap3d.yaml oder env BLENDER setzen)")
    if script.name.startswith("render_script"):  # DiffuRank läuft nur unter Blender 3.4
        out = subprocess.run([blender, "--version"], capture_output=True, text=True).stdout
        version = out.split("\n")[0].replace("Blender", "").strip()
        if not version.startswith("3.4"):
            sys.exit(f"{blender} ist Blender {version}. DiffuRank braucht 3.4.x "
                     "(Blender 3.4.1 parallel installieren und Pfad in configs/cap3d.yaml anpassen).")
    return [
        blender, "-b", "--factory-startup", "--python-exit-code", "1",
        "-P", str(HERE / "blender_device.py"),
        "-P", str(script),
        "--", *script_args,
    ]


def run(cmd: list[str], cfg: dict, log_file: Path) -> None:
    env = {**os.environ, "RENDER_DEVICE": str(cfg["render"].get("device", "CPU")),
           "RENDER_AMBIENT": str(cfg["render"].get("ambient", 1.0))}
    print(f"[render] Blender läuft … Log: {log_file.relative_to(ROOT)}")
    t0 = time.time()
    with open(log_file, "w") as log:
        proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=ROOT)
    print(f"[render] fertig nach {time.time() - t0:.0f} s (Exit-Code {proc.returncode})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", required=True, choices=["asset", "part", "highlight"])
    ap.add_argument("--backend", choices=BACKENDS, help="überschreibt render.backend aus der Config")
    ap.add_argument("--config", default=str(ROOT / "configs" / "cap3d.yaml"))
    ap.add_argument("--only", help="nur IDs/Assets, die diesen Text enthalten")
    ap.add_argument("--limit", type=int, help="höchstens N IDs rendern (zum Testen)")
    ap.add_argument("--force", action="store_true", help="auch bereits gerenderte IDs neu rendern")
    args = ap.parse_args()

    cfg = load_cfg(Path(args.config))
    backend = args.backend or cfg["render"].get("backend", "blender")
    out_root = render_root(cfg, args.mode, backend)
    out_root.mkdir(parents=True, exist_ok=True)

    def keep(render_id: str) -> bool:
        if args.only and args.only not in render_id:
            return False
        return args.force or not is_done(out_root, render_id)

    if args.mode in ("asset", "part"):
        paths = collect_asset(cfg) if args.mode == "asset" else collect_part(cfg)
        for p in paths:
            check_name(p)
        ids = [rid(p) for p in paths]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            sys.exit(f"Doppelte Render-IDs: {sorted(dupes)}")
        todo = [p for p in paths if keep(rid(p))][: args.limit]
        expected = [rid(p) for p in todo]
        skipped = len([p for p in paths if (not args.only or args.only in rid(p))]) - len(todo)
        print(f"[render] Modus {args.mode}, Backend {backend}: {len(todo)} zu rendern, {skipped} übersprungen/gefiltert")
        if not todo:
            return
        if backend == "threejs":
            from .threejs_render import render_paths
            render_paths(todo, out_root, cfg, args.mode)
        else:
            run_blender_paths(todo, out_root, cfg)

    else:  # highlight
        jobs = collect_highlight(cfg)
        job_list, expected = [], []
        for asset, parts in jobs.items():
            if args.only and args.only not in Path(asset).stem:  # --only trifft Asset -> alle Teile, sonst Teil-Filter
                parts = [p for p in parts if args.only in p["part_id"]]
            parts = [p for p in parts if args.force or not is_done(out_root, p["part_id"])]
            if args.limit is not None:
                parts = parts[: max(args.limit - len(expected), 0)]
            if parts:
                job_list.append({"asset": asset, "parts": parts})
                expected += [p["part_id"] for p in parts]
        print(f"[render] Modus highlight, Backend {backend}: {len(expected)} Teile in {len(job_list)} Asset(s)")
        if not job_list:
            return
        if backend == "threejs":
            from .threejs_render import render_highlight
            render_highlight(job_list, out_root, cfg)
        else:
            run_blender_highlight(job_list, out_root, cfg)

    missing = [i for i in expected if not is_done(out_root, i)]
    print(f"[render] {len(expected) - len(missing)}/{len(expected)} vollständig (8 Views)")
    if missing:
        print("[render] unvollständig: " + ", ".join(missing[:20]) + (" …" if len(missing) > 20 else ""))
        if backend == "blender":
            print(f"[render] Details im Log: {(out_root / '_blender.log').relative_to(ROOT)}")
        sys.exit(1)


def run_blender_paths(todo: list[Path], out_root: Path, cfg: dict) -> None:
    """Modi asset/part mit DiffuRank in Blender 3.4.1."""
    todo_pkl = out_root / "_todo.pkl"
    with open(todo_pkl, "wb") as f:
        pickle.dump([str(p) for p in todo], f)
    script = ROOT / cfg["render"]["diffurank_script"]
    if not script.exists():
        sys.exit(f"{script} fehlt – erst 'bash scripts/setup_diffurank.sh' ausführen.")
    cmd = blender_cmd(cfg, script, ["--object_path_pkl", str(todo_pkl), "--parent_dir", str(out_root)])
    run(cmd, cfg, out_root / "_blender.log")


def run_blender_highlight(job_list: list[dict], out_root: Path, cfg: dict) -> None:
    """Modus highlight mit eigener Render-Logik (blender_highlight.py)."""
    hl = cfg["render"]["highlight"]
    jobs_json = out_root / "_jobs.json"
    jobs_json.write_text(json.dumps(job_list, indent=2))
    cmd = blender_cmd(cfg, HERE / "blender_highlight.py", [
        "--jobs", str(jobs_json), "--parent_dir", str(out_root),
        "--views", str(cfg["render"].get("n_views", 8)),
        "--resolution", str(cfg["render"]["resolution"]), "--samples", str(hl["samples"]),
        "--context_alpha", str(hl["context_alpha"]),
        "--color", *[str(c) for c in hl["highlight_color"]],
    ])
    run(cmd, cfg, out_root / "_blender.log")


if __name__ == "__main__":
    main()
