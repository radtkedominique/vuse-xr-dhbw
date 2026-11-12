"""Pipeline 3: Teile mit einem Vision-Modell beschreiben (Name, Typ, Funktion, Beschreibung, Confidence).

    uv run python -m vuse_xr.cap3d.caption --only torque_wrench        # alle Teile eines Assets
    uv run python -m vuse_xr.cap3d.caption --limit 3                    # erste 3 Teile (zum Testen)
    uv run python -m vuse_xr.cap3d.caption --backend ollama --model qwen2.5vl:7b --only torque_wrench
    uv run python -m vuse_xr.cap3d.caption --variant noname --only torque_wrench   # andere Variante
    uv run python -m vuse_xr.cap3d.caption --variant topdown --assets-only          # nur Stufe 1 (Pipeline 3e)

Input:
    Renders aus Pipeline 2 (render_root(cfg, mode)/Cap3D_imgs/<part_id>/00020.png … 00027.png)
    data/interim/uid_mapping.json aus Pipeline 1 (welche Teile es gibt, zu welchem Asset sie gehören)
    Die 8 Views je Modus werden zu einem Rasterbild (4×2) zusammengesetzt, das spart ~75 % Tokens
    (siehe docs/pipeline3.md). Welche Modi ins Modell gehen, steuert caption.input_views.

Das Modell sieht nur die Bilder und den Asset-Typ (z. B. "torque wrench") – nie die part_id oder den
Knotennamen, sonst könnte es die Lösung ablesen.

Varianten (caption.variants): jede Variante überschreibt einzelne caption-Einstellungen, z. B.
    baseline: {input_views: part+highlight, asset_name: true}
    noname:   {input_views: part+highlight, asset_name: false}
    full:     {input_views: asset+part+highlight+group, asset_name: true}
    hier:     {input_views: part+highlight, asset_name: true, hierarchy: true}
    topdown:  {input_views: part+highlight, asset_name: predicted, parent_caption: true}
View group = part-Render der nächsten Baugruppe über dem Teil (group_of); Teile ohne Baugruppe bekommen
kein Gruppenbild. hierarchy: true hängt Struktur- und Geometriefakten als Text an (structure.describe,
Pipeline 3d). Auswahl mit --variant, sonst caption.variant. Prompt und Bilder baut build_input().

Mehrstufig / Top-down (Pipeline 3e):
    Stufe 1: bei asset_name: predicted oder parent_caption: true wird zuerst jedes Asset nur aus seinen
             Asset-Renders beschrieben (ASSET_SCHEMA: name, description) -> <out>/_assets/<asset>.json.
             Der Dateiname geht dabei nicht ins Modell.
    Stufe 2: der Teile-Prompt bekommt die Asset-Beschreibung als Kontext; predicted nimmt deren name als
             Asset-Typ statt des Dateinamens (Q3).
    parent_caption: true: Teile nach Tiefe sortiert (Eltern vor Kindern), die Caption der Baugruppe
             (group_of) kommt aus dem Output-Ordner in den Prompt. Fehlt sie, wird das Kind übersprungen.

Output:
    <paths.output>/captions/<modell>/<variante>/<part_id>.json
        {"part_id", "caption": {name, type, function, description, confidence},
         "meta": {backend, model, variant, variant_cfg, input_views, group, asset_caption, prompt, …}}
    <paths.output>/captions/<modell>/<variante>/_assets/<asset>.json   (nur Stufe 1, Pipeline 3e)
Ein Ordner pro Modell und Variante, damit sich Ergebnisse vergleichen lassen.
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
from .structure import build_tree, describe, group_of

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
    "group": "8 views of the sub-assembly of the {asset} that contains the part (part not highlighted)",
}
VIEW_RENDER = {"group": "part"}  # View -> Render-Modus aus Pipeline 2 (Rest: gleicher Name)

PROMPT = """You see renders of one part of a 3D model of {a_asset}.
{images}
Identify the part as an engineer would name it in the assembly. Use the highlight to judge where the part sits
and what it does. If several identical-looking parts exist, describe this one and lower the confidence.
Answer only with JSON matching the schema."""

# Pipeline 3e, Stufe 1: ganzes Asset ohne Dateinamen beschreiben
ASSET_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "short English name of the whole object, e.g. 'bench drill'"},
        "description": {"type": "string", "description": "what the object is used for and its main components, 1-2 sentences"},
    },
    "required": ["name", "description"],
}

ASSET_PROMPT = """You see renders of a 3D model of a technical object.
Image 1: 8 views of the whole object.
Identify what the object is as an engineer would name it. Answer only with JSON matching the schema."""


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


def safe_name(x: str) -> str:
    """Für Ordnernamen: qwen2.5vl:7b -> qwen2.5vl_7b"""
    return re.sub(r"[^\w.-]", "_", x)


def asset_ref(entry: dict, cc: dict, asset_cap: dict | None = None) -> str:
    """Wie das Asset im Prompt heißt, gesteuert über caption.asset_name:
    true -> Asset-Typ aus dem Dateinamen ('torque wrench'), false -> neutral ('object'),
    predicted -> name aus Stufe 1 (Pipeline 3e, asset_cap)
    """
    mode = cc.get("asset_name", True)
    if mode is True:
        return asset_label(entry["asset"])
    if mode is False:
        return "object"
    if mode == "predicted":
        if not asset_cap:
            raise ValueError("asset_name: predicted braucht die Asset-Caption aus Stufe 1")
        return asset_cap["name"].strip().lower()
    raise ValueError(f"caption.asset_name: unbekannter Wert {mode!r} (true | false | predicted)")


def needs_asset_stage(cc: dict) -> bool:
    """Pipeline 3e, Stufe 1 nötig?"""
    return cc.get("asset_name") == "predicted" or bool(cc.get("parent_caption"))


def topdown_order(part_ids: list[str], mapping: dict) -> list[str]:
    """Eltern vor Kindern: nach Tiefe im Baum (Anzahl Knoten im path), bei gleicher Tiefe alphabetisch."""
    return sorted(part_ids, key=lambda p: (mapping[p]["path"].count("/") + bool(mapping[p]["path"]), p))


def context_lines(asset_cap: dict | None, parent_cap: dict | None) -> list[str]:
    """Pipeline 3e: Beschreibungen aus früheren Stufen als Text. Enthält nur Modell-Output, keine Knotennamen."""
    lines = []
    if asset_cap:
        lines.append(f"The whole object was identified as: {asset_cap['name']}. {asset_cap['description']}")
    if parent_cap:
        lines.append(f"This part belongs to a sub-assembly identified as: {parent_cap['name']} "
                     f"({parent_cap['function']}). {parent_cap['description']}")
    if lines:
        lines.append("These descriptions were generated automatically and may be wrong; use them as hints.")
    return lines


def view_id(view: str, part_id: str, entry: dict, mapping: dict) -> str | None:
    """Render-ID (Ordnername unter Cap3D_imgs) für einen View; None = View entfällt für dieses Teil."""
    if view == "asset":
        return Path(entry["asset"]).stem
    if view == "group":
        return group_of(entry, mapping)
    return part_id


def build_input(part_id: str, entry: dict, cc: dict, roots: dict[str, Path],
                mapping: dict, tree: dict | None = None,
                asset_cap: dict | None = None, parent_cap: dict | None = None) -> tuple[str, list[bytes]]:
    """Prompt und Bilder für ein Teil. Jede Variante (caption.variants) ist ein Baustein hierin.

    cc: aufgelöste caption-Einstellungen (Defaults + Variante), roots: Render-Ordner je View,
    mapping: uid_mapping.json (für die Baugruppe im View group), tree: structure.build_tree(mapping),
    nur nötig bei hierarchy: true. asset_cap / parent_cap: Captions aus Stufe 1 bzw. der Baugruppe (Pipeline 3e).
    Die part_id selbst geht nie in den Prompt (enthält den Knotennamen), ebenso wenig die group_id."""
    asset = asset_ref(entry, cc, asset_cap)
    a_asset = ("an " if asset[0].lower() in "aeiou" else "a ") + asset
    images, lines = [], []
    for m in cc["input_views"].split("+"):
        vid = view_id(m, part_id, entry, mapping)
        if vid is None:  # kein Elternknoten -> kein Gruppenbild
            continue
        images.append(grid_png(roots[m] / "Cap3D_imgs" / vid, cc.get("grid_tile", 256)))
        lines.append(f"Image {len(images)}: " + IMAGE_TEXT[m].format(asset=asset) + ".")
    if cc.get("hierarchy"):  # Pipeline 3d: nur Zahlen, keine Knotennamen
        lines += ["", describe(part_id, mapping, tree), "Use these structure facts as hints.", ""]
    ctx = context_lines(asset_cap, parent_cap)
    if ctx:  # Pipeline 3e
        lines += ["", *ctx, ""]
    return PROMPT.format(a_asset=a_asset, images="\n".join(lines)), images


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


def call_gemini(prompt: str, images: list[bytes], cc: dict, schema: dict = SCHEMA) -> tuple[dict, dict]:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY fehlt – in .env eintragen (siehe .env.example).")
    parts = [{"text": prompt}] + [
        {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(img).decode()}} for img in images
    ]
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema},
    }
    r = post_json(GEMINI_URL.format(model=cc["model"]), body, {"x-goog-api-key": key}, cc.get("timeout", 120))
    text = "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"] if not p.get("thought"))
    u = r.get("usageMetadata", {})
    usage = {"input": u.get("promptTokenCount"), "output": u.get("candidatesTokenCount"),
             "thinking": u.get("thoughtsTokenCount"), "model_version": r.get("modelVersion")}
    return json.loads(text), usage


def call_ollama(prompt: str, images: list[bytes], cc: dict, schema: dict = SCHEMA) -> tuple[dict, dict]:
    body = {
        "model": cc["model"],
        "stream": False,
        "format": schema,
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

def call_with_retry(backend: str, prompt: str, images: list[bytes], cc: dict,
                    schema: dict = SCHEMA) -> tuple[dict, dict]:
    retries = cc.get("retries", 5)
    for attempt in range(retries + 1):
        try:
            return BACKENDS[backend](prompt, images, cc, schema)
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


def caption_assets(stems: list[str], cc: dict, cfg: dict, asset_dir: Path, force: bool) -> dict[str, dict]:
    """Pipeline 3e, Stufe 1: jedes Asset einmal aus seinen Asset-Renders beschreiben (ASSET_SCHEMA).
    Fertige Assets werden aus asset_dir gelesen. Rückgabe {asset_stem: caption}; fehlt ein Asset, fehlt der Key."""
    root = render_root(cfg, "asset")
    asset_dir.mkdir(parents=True, exist_ok=True)
    caps = {}
    for stem in stems:
        f = asset_dir / f"{stem}.json"
        if f.exists() and not force:
            caps[stem] = json.loads(f.read_text())["caption"]
            continue
        if not is_done(root, stem):
            log.warning("Asset %s: keine Asset-Renders (render --mode asset) – Teile werden übersprungen", stem)
            continue
        t = time.time()
        try:
            cap, usage = call_with_retry(cc["backend"], ASSET_PROMPT,
                                         [grid_png(root / "Cap3D_imgs" / stem, cc.get("grid_tile", 256))],
                                         cc, ASSET_SCHEMA)
        except (RuntimeError, json.JSONDecodeError, KeyError) as e:
            log.error("Asset %s: %s", stem, e)
            continue
        secs = round(time.time() - t, 1)
        f.write_text(json.dumps({
            "asset": stem,
            "caption": cap,
            "meta": {"backend": cc["backend"], "model": cc["model"], "prompt": ASSET_PROMPT, "seconds": secs,
                     "tokens": usage, "created": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        }, indent=2, ensure_ascii=False))
        log.info("Asset %s -> %s in %.1f s", stem, cap.get("name"), secs)
        caps[stem] = cap
    return caps


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "configs" / "cap3d.yaml"))
    ap.add_argument("--only", help="nur part_ids, die diesen Text enthalten (z. B. Assetname)")
    ap.add_argument("--limit", type=int, help="höchstens N Teile beschreiben (zum Testen)")
    ap.add_argument("--force", action="store_true", help="auch bereits beschriebene Teile neu beschreiben")
    ap.add_argument("--backend", choices=sorted(BACKENDS), help="überschreibt caption.backend")
    ap.add_argument("--model", help="überschreibt caption.model")
    ap.add_argument("--variant", help="Variante aus caption.variants (Standard: caption.variant)")
    ap.add_argument("--assets-only", action="store_true",
                    help="nur Stufe 1 (Pipeline 3e): alle Assets beschreiben, keine Teile")
    add_log_args(ap)
    args = ap.parse_args()
    setup_logging(args.verbose, args.quiet)
    load_env(ROOT / ".env")

    cfg = load_cfg(Path(args.config))
    cc = dict(cfg["caption"])
    variants = cc.pop("variants", None) or {}
    default_variant = cc.pop("variant", None)
    variant = args.variant or default_variant
    if not variants:
        sys.exit(f"caption.variants fehlt in {args.config}.")
    if variant not in variants:
        sys.exit(f"Variante {variant!r} unbekannt – vorhanden: {', '.join(variants)}")
    variant_cfg = dict(variants[variant] or {})
    cc.update(variant_cfg)  # Variante überschreibt die Defaults
    cc["backend"] = args.backend or cc["backend"]
    cc["model"] = args.model or cc["model"]
    if not cc["model"]:
        sys.exit("caption.model ist nicht gesetzt.")
    modes = cc["input_views"].split("+")

    mp = ROOT / cfg["split"]["uid_mapping"]
    if not mp.exists():
        sys.exit(f"{mp} fehlt – erst Pipeline 1 (split_nodes.py) laufen lassen.")
    mapping = json.loads(mp.read_text())
    tree = build_tree(mapping) if cc.get("hierarchy") else None

    out_dir = ROOT / cfg["paths"]["output"] / "captions" / safe_name(cc["model"]) / safe_name(variant)
    out_dir.mkdir(parents=True, exist_ok=True)
    roots = {m: render_root(cfg, VIEW_RENDER.get(m, m)) for m in modes}

    if args.assets_only:  # Pipeline 3e, nur Stufe 1
        if not needs_asset_stage(cc):
            sys.exit(f"Variante {variant!r} nutzt keine Stufe 1 – z. B. --variant topdown")
        stems = sorted({Path(e["asset"]).stem for p, e in mapping.items() if not args.only or args.only in p})
        try:
            caps = caption_assets(stems, cc, cfg, out_dir / "_assets", args.force)
        except QuotaExceeded as e:
            sys.exit(f"{e} – Rest wird beim nächsten Lauf nachgeholt")
        log.info("%d/%d Assets beschrieben -> %s", len(caps), len(stems), (out_dir / "_assets").relative_to(ROOT))
        sys.exit(0 if len(caps) == len(stems) else 1)

    todo, missing = [], []
    for part_id in sorted(mapping):
        if args.only and args.only not in part_id:
            continue
        if not args.force and (out_dir / f"{part_id}.json").exists():
            continue
        ids = {m: view_id(m, part_id, mapping[part_id], mapping) for m in modes}
        if not all(is_done(roots[m], i) for m, i in ids.items() if i is not None):
            missing.append(part_id)
            continue
        todo.append(part_id)
    if cc.get("parent_caption"):  # Pipeline 3e: Eltern vor Kindern
        todo = topdown_order(todo, mapping)
    todo = todo[: args.limit]
    if missing:
        log.warning("%d Teile ohne vollständige Renders (%s) übersprungen, z. B. %s",
                    len(missing), cc["input_views"], missing[0])
    log.info("Backend %s, Modell %s, Variante %s (Views %s): %d zu beschreiben",
             cc["backend"], cc["model"], variant, cc["input_views"], len(todo))

    asset_caps = {}
    if needs_asset_stage(cc):  # Pipeline 3e, Stufe 1
        stems = sorted({Path(mapping[p]["asset"]).stem for p in todo})
        try:
            asset_caps = caption_assets(stems, cc, cfg, out_dir / "_assets", args.force)
        except QuotaExceeded as e:
            log.error("%s – Abbruch in Stufe 1, Rest wird beim nächsten Lauf nachgeholt", e)
            sys.exit(1)

    failed, skipped = [], []
    for i, part_id in enumerate(todo, 1):
        entry = mapping[part_id]
        asset_cap = parent_cap = None
        if needs_asset_stage(cc):
            asset_cap = asset_caps.get(Path(entry["asset"]).stem)
            if asset_cap is None:
                skipped.append(part_id)
                continue
        parent = group_of(entry, mapping)
        if cc.get("parent_caption") and parent:
            pf = out_dir / f"{parent}.json"
            if not pf.exists():  # nicht ohne Kontext beschreiben
                log.debug("%s: Baugruppe %s noch nicht beschrieben – übersprungen", part_id, parent)
                skipped.append(part_id)
                continue
            parent_cap = json.loads(pf.read_text())["caption"]
        prompt, images = build_input(part_id, entry, cc, roots, mapping, tree, asset_cap, parent_cap)

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
            "meta": {"backend": cc["backend"], "model": cc["model"], "variant": variant, "variant_cfg": variant_cfg,
                     "input_views": cc["input_views"], "group": parent,
                     "structure": describe(part_id, mapping, tree) if tree else None,
                     "asset_caption": asset_cap, "parent_caption": parent_cap,
                     "prompt": prompt, "seconds": secs, "tokens": usage,
                     "created": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        }
        (out_dir / f"{part_id}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
        log.info("%d/%d %s -> %s (%.2f) in %.1f s", i, len(todo), part_id, caption.get("name"),
                 caption.get("confidence", 0), secs)

    log.info("%d/%d beschrieben -> %s", len(todo) - len(failed) - len(skipped), len(todo), out_dir.relative_to(ROOT))
    if skipped:
        log.warning("%d Teile ohne Kontext (Asset- oder Baugruppen-Caption fehlt) übersprungen, z. B. %s",
                    len(skipped), skipped[0])
    if failed:
        log.error("fehlgeschlagen: %s", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
