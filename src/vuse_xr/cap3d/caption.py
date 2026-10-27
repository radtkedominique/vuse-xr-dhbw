"""Pipeline 3: Teile mit einem Vision-Modell beschreiben (Name, Typ, Funktion, Beschreibung, Confidence).

    uv run python -m vuse_xr.cap3d.caption --only torque_wrench        # alle Teile eines Assets
    uv run python -m vuse_xr.cap3d.caption --limit 3                    # erste 3 Teile (zum Testen)
    uv run python -m vuse_xr.cap3d.caption --backend ollama --model qwen2.5vl:7b --only torque_wrench

Input:
    Renders aus Pipeline 2 (render_root(cfg, mode)/Cap3D_imgs/<part_id>/00020.png … 00027.png)
    data/interim/uid_mapping.json aus Pipeline 1 (welche Teile es gibt, zu welchem Asset sie gehören)
    Die 8 Views je Modus werden zu einem Rasterbild (4×2) zusammengesetzt, das spart ~75 % Tokens
    (siehe docs/pipeline3.md). Welche Modi ins Modell gehen, steuert caption.input_views.

Das Modell sieht nur die Bilder und den Asset-Typ (z. B. "torque wrench") – nie die part_id oder den
Knotennamen, sonst könnte es die Lösung ablesen.

Output:
    <paths.output>/captions/<modell>/<part_id>.json   {"part_id", "caption": {name, type, function,
                                              description, confidence}, "meta": {backend, model, input_views, …}}
Ein Ordner pro Modell, damit sich Ergebnisse verschiedener Modelle vergleichen lassen.
Bereits beschriebene Teile werden übersprungen (--force beschreibt neu).

Backends (caption.backend):
    gemini  Google Gemini API, Key über GEMINI_API_KEY (Umgebung oder .env im Repo-Root)
    ollama  lokales Modell über Ollama (caption.ollama_url)
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from ..log import add_log_args, setup_logging
from .render import ROOT, VIEW_FILES, is_done, load_cfg, render_root

log = logging.getLogger("vuse_xr.cap3d.caption")  # fester Name: bei python -m wäre __name__ == "__main__"

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_STATUS = {429, 500, 502, 503, 504}  # Überlastung / Rate-Limit -> später nochmal versuchen

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "short English name of the part, e.g. 'ratchet head'"},
        "type": {"type": "string", "description": "generic component category, e.g. knob, screw, housing, shaft"},
        "function": {"type": "string", "description": "what the part does within the object"},
        "description": {"type": "string", "description": "shape, material and position in the object, 1-2 sentences"},
        "confidence": {"type": "number", "description": "0.0-1.0, lower if the part could be confused with similar parts"},
    },
    "required": ["name", "type", "function", "description", "confidence"],
}

IMAGE_TEXT = {
    "part": "8 views of the isolated part",
    "highlight": "the same 8 views of the whole {asset}, with the part highlighted in orange and the rest transparent",
    "asset": "8 views of the whole {asset}",
}

PROMPT = """You see renders of one part of a 3D model of a {asset}.
{images}
Identify the part as an engineer would name it in the assembly. Use the highlight to judge where the part sits
and what it does. If several identical-looking parts exist, describe this one and lower the confidence.
Answer only with JSON matching the schema."""


def load_env(path: Path) -> None:
    """Einfache .env-Unterstützung (KEY=VALUE), überschreibt keine gesetzten Umgebungsvariablen."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def asset_label(asset_path: str) -> str:
    """data/raw/3d_assets/04_torque_wrench.glb -> 'torque wrench'"""
    return re.sub(r"^\d+_", "", Path(asset_path).stem).replace("_", " ")


def grid_png(view_dir: Path, tile: int) -> bytes:
    """8 Views (transparent) als 4×2-Raster auf Weiß."""
    grid = Image.new("RGB", (4 * tile, 2 * tile), "white")
    for k, name in enumerate(VIEW_FILES):
        im = Image.open(view_dir / name).convert("RGBA")
        bg = Image.new("RGBA", im.size, "white")
        bg.alpha_composite(im)
        grid.paste(bg.convert("RGB").resize((tile, tile), Image.LANCZOS), ((k % 4) * tile, (k // 4) * tile))
    buf = io.BytesIO()
    grid.save(buf, "PNG")
    return buf.getvalue()


def post_json(url: str, body: dict, headers: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def call_gemini(prompt: str, images: list[bytes], cc: dict) -> tuple[dict, dict]:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY fehlt – in .env eintragen (siehe .env.example).")
    parts = [{"text": prompt}] + [
        {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(img).decode()}} for img in images
    ]
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA},
    }
    r = post_json(GEMINI_URL.format(model=cc["model"]), body, {"x-goog-api-key": key}, cc.get("timeout", 120))
    text = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"] if not p.get("thought"))
    u = r.get("usageMetadata", {})
    usage = {"input": u.get("promptTokenCount"), "output": u.get("candidatesTokenCount"),
             "thinking": u.get("thoughtsTokenCount"), "model_version": r.get("modelVersion")}
    return json.loads(text), usage


def call_ollama(prompt: str, images: list[bytes], cc: dict) -> tuple[dict, dict]:
    body = {
        "model": cc["model"],
        "stream": False,
        "format": SCHEMA,
        "options": {"num_ctx": cc.get("ollama_num_ctx", 12288)},
        "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(i).decode() for i in images]}],
    }
    r = post_json(cc.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/chat", body, {},
                  cc.get("timeout", 900))
    usage = {"input": r.get("prompt_eval_count"), "output": r.get("eval_count")}
    return json.loads(r["message"]["content"]), usage


BACKENDS = {"gemini": call_gemini, "ollama": call_ollama}


class QuotaExceeded(RuntimeError):
    """Tageslimit erreicht (z. B. Gemini-Gratis-Tarif) – weitere Versuche heute zwecklos."""


def call_with_retry(backend: str, prompt: str, images: list[bytes], cc: dict) -> tuple[dict, dict]:
    retries = cc.get("retries", 5)
    for attempt in range(retries + 1):
        try:
            return BACKENDS[backend](prompt, images, cc)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if e.code == 429 and "PerDay" in detail:
                raise QuotaExceeded("Tageslimit des Anbieters erreicht, morgen weitermachen") from e
            detail = detail[:300]
            if e.code not in RETRY_STATUS or attempt == retries:
                raise RuntimeError(f"HTTP {e.code}: {detail}") from e
            reason = f"HTTP {e.code}"
        except (TimeoutError, urllib.error.URLError) as e:
            if attempt == retries:
                raise RuntimeError(f"keine Antwort: {e}") from e
            reason = type(e).__name__
        wait = min(10 * 2**attempt, 120)
        log.warning("%s – neuer Versuch in %d s (%d/%d)", reason, wait, attempt + 1, retries)
        time.sleep(wait)
    raise AssertionError("unreachable")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "configs" / "cap3d.yaml"))
    ap.add_argument("--only", help="nur part_ids, die diesen Text enthalten (z. B. Assetname)")
    ap.add_argument("--limit", type=int, help="höchstens N Teile beschreiben (zum Testen)")
    ap.add_argument("--force", action="store_true", help="auch bereits beschriebene Teile neu beschreiben")
    ap.add_argument("--backend", choices=sorted(BACKENDS), help="überschreibt caption.backend")
    ap.add_argument("--model", help="überschreibt caption.model")
    add_log_args(ap)
    args = ap.parse_args()
    setup_logging(args.verbose, args.quiet)
    load_env(ROOT / ".env")

    cfg = load_cfg(Path(args.config))
    cc = dict(cfg["caption"])
    cc["backend"] = args.backend or cc["backend"]
    cc["model"] = args.model or cc["model"]
    if not cc["model"]:
        sys.exit("caption.model ist nicht gesetzt.")
    modes = cc["input_views"].split("+")

    mp = ROOT / cfg["split"]["uid_mapping"]
    if not mp.exists():
        sys.exit(f"{mp} fehlt – erst Pipeline 1 (split_nodes.py) laufen lassen.")
    mapping = json.loads(mp.read_text())

    out_dir = ROOT / cfg["paths"]["output"] / "captions" / re.sub(r"[^\w.-]", "_", cc["model"])  # qwen2.5vl:7b -> qwen2.5vl_7b
    out_dir.mkdir(parents=True, exist_ok=True)
    roots = {m: render_root(cfg, m) for m in modes}

    todo, missing = [], []
    for part_id in sorted(mapping):
        if args.only and args.only not in part_id:
            continue
        if not args.force and (out_dir / f"{part_id}.json").exists():
            continue
        if not all(is_done(roots[m], part_id if m != "asset" else Path(mapping[part_id]["asset"]).stem) for m in modes):
            missing.append(part_id)
            continue
        todo.append(part_id)
    todo = todo[: args.limit]
    if missing:
        log.warning("%d Teile ohne vollständige Renders (%s) übersprungen, z. B. %s",
                    len(missing), cc["input_views"], missing[0])
    log.info("Backend %s, Modell %s, Views %s: %d zu beschreiben", cc["backend"], cc["model"], cc["input_views"], len(todo))

    failed = []
    for i, part_id in enumerate(todo, 1):
        entry = mapping[part_id]
        asset = asset_label(entry["asset"])
        images, lines = [], []
        for n, m in enumerate(modes, 1):
            render_id = part_id if m != "asset" else Path(entry["asset"]).stem
            images.append(grid_png(roots[m] / "Cap3D_imgs" / render_id, cc.get("grid_tile", 256)))
            lines.append(f"Image {n}: " + IMAGE_TEXT[m].format(asset=asset) + ".")
        prompt = PROMPT.format(asset=asset, images="\n".join(lines))

        t = time.time()
        try:
            caption, usage = call_with_retry(cc["backend"], prompt, images, cc)
        except QuotaExceeded as e:
            log.error("%s – Abbruch nach %d/%d Teilen, Rest wird beim nächsten Lauf nachgeholt", e, i - 1, len(todo))
            sys.exit(1)
        except (RuntimeError, json.JSONDecodeError, KeyError) as e:
            log.error("%s: %s", part_id, e)
            log.debug("Details", exc_info=True)
            failed.append(part_id)
            continue
        secs = round(time.time() - t, 1)
        result = {
            "part_id": part_id,
            "caption": caption,
            "meta": {"backend": cc["backend"], "model": cc["model"], "input_views": cc["input_views"],
                     "seconds": secs, "tokens": usage, "created": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        }
        (out_dir / f"{part_id}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
        log.info("%d/%d %s -> %s (%.2f) in %.1f s", i, len(todo), part_id, caption.get("name"),
                 caption.get("confidence", 0), secs)

    log.info("%d/%d beschrieben -> %s", len(todo) - len(failed), len(todo), out_dir.relative_to(ROOT))
    if failed:
        log.error("fehlgeschlagen: %s", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
