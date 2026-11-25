"""LLM-Judge: generierte Namen gegen die richtigen Namen bewerten (Score 0/1/2 + Fehlerkategorie), nur Text.

    uv run python -m vuse_xr.cap3d.judge --list-candidates                          # Judge-Modelle mit Preisen
    uv run python -m vuse_xr.cap3d.judge --judge-model <id> --dry-run               # was kostet ein Lauf?
    uv run python -m vuse_xr.cap3d.judge --judge-model <id> --models gemini-3.8-flash --variants baseline
    uv run python -m vuse_xr.cap3d.judge --judge-model <id> --parts eval/human/cases.csv   # Judge-Validierung

Input:
    Teile + richtige Namen aus eval_data.py (uid_mapping.json + ground_truth.csv)
    <paths.output>/captions/<modell>/<variante>/<part_id>.json aus Pipeline 3
    configs/models.yaml (judge:), Prompt aus judge_prompts/<version>.md, OPENROUTER_API_KEY in .env

Der Judge sieht Objekt, richtige Namen (Mesh + Hüllen), Pfad im Baum, Art des Teils und name/type/function/
description der Caption – nie Modell, Variante, part_id oder Confidence. Teile ohne Caption oder mit leerem
Namen gehen nicht an den Judge (evaluate.py: Score 0, no_result).

Output (Cache):
    <paths.output>/<judge.out_dir>/<judge>/<modell>/<variante>/<part_id>.json
        {"part_id", "key", "judgement": {reason, category, score, function_ok, hallucination, gt_unusable},
         "meta": {judge_requested, judge_model, provider, prompt_version, prompt_sha, tokens, cost_usd, seconds, …}}
    key = sha256 aus Judge-Input, Prompt-Inhalt, Judge-Modell und Schema-Version. Passt der key, wird nicht
    neu bewertet; ändert sich Caption, Ground Truth, Prompt oder Modell, wird neu bewertet.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from ..log import add_log_args, setup_logging
from .caption import load_env, safe_name
from .eval_data import Caption, Part, captions_dir, iter_captions, load_parts
from .render import ROOT, load_cfg

log = logging.getLogger("vuse_xr.cap3d.judge")  # fester Name: bei python -m wäre __name__ == "__main__"

API = "https://openrouter.ai/api/v1"
PROMPT_DIR = Path(__file__).resolve().parent / "judge_prompts"
SCHEMA_VERSION = 1  # bei Änderung an SCHEMA hochzählen -> Cache wird ungültig
RETRY_STATUS = {408, 429, 500, 502, 503, 504}
EST_OUTPUT_TOKENS = 120  # Schätzung für --dry-run / --list-candidates
EST_USER_TOKENS = 150

# Kategorie -> erlaubte Scores (Bewertungsregel 4.2)
SCORES = {
    "correct": {2},
    "too_generic": {1},
    "neighbor_part": {1},
    "other_part": {0},
    "wrong_function": {0, 1},
    "hallucinated": {0},
    "no_result": {0},
}

SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string", "description": "one short sentence explaining the score"},
        "category": {"type": "string", "enum": list(SCORES)},
        "score": {"type": "integer", "enum": [0, 1, 2]},
        "function_ok": {"type": "boolean"},
        "hallucination": {"type": "boolean"},
        "gt_unusable": {"type": "boolean"},
    },
    "required": ["reason", "category", "score", "function_ok", "hallucination", "gt_unusable"],
    "additionalProperties": False,
}


class BudgetExceeded(RuntimeError):
    """--max-cost erreicht oder OpenRouter-Guthaben leer – keine weiteren Anfragen."""


@dataclass(frozen=True)
class Prompt:
    version: str
    text: str
    sha: str


def load_prompt(version: str) -> Prompt:
    f = PROMPT_DIR / f"{version}.md"
    if not f.exists():
        sys.exit(f"Judge-Prompt {f} fehlt – vorhanden: {', '.join(p.stem for p in PROMPT_DIR.glob('*.md'))}")
    text = f.read_text(encoding="utf-8")
    return Prompt(version, text, hashlib.sha256(text.encode()).hexdigest()[:12])


def judge_input(part: Part, cap: dict) -> str:
    """User-Nachricht für den Judge. Bewusst ohne Modell, Variante, part_id und Confidence."""
    names = part.names[0] + (f"  (also: {', '.join(part.names[1:])})" if len(part.names) > 1 else "")
    kind = f"sub-assembly of {part.n_meshes} parts" if part.kind == "group" else "single part"
    lines = [
        f"Object: {part.asset_label}",
        f"Correct name(s): {names}",
        f"Position in tree: {part.path or '(top level)'}",
        f"Kind: {kind}",
        "Generated:",
    ]
    for k in ("name", "type", "function", "description"):
        lines.append(f"  {k}: {str(cap.get(k) or '').strip() or '(empty)'}")
    return "\n".join(lines)


def cache_key(user: str, prompt: Prompt, judge_model: str) -> str:
    blob = json.dumps({"input": user, "prompt": prompt.sha, "judge": judge_model, "schema": SCHEMA_VERSION},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def cache_file(out_root: Path, judge_model: str, c: Caption) -> Path:
    return out_root / safe_name(judge_model) / c.model / c.variant / f"{c.part_id}.json"


def read_cache(f: Path, key: str) -> dict | None:
    """Gültiger Cache-Eintrag (gleicher key) oder None."""
    try:
        d = json.loads(f.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return d if d.get("key") == key else None


def validate(j: object) -> str | None:
    """Fehlertext, wenn die Antwort nicht zum Schema oder zur Bewertungsregel passt, sonst None."""
    if not isinstance(j, dict):
        return "kein JSON-Objekt"
    missing = [k for k in SCHEMA["required"] if k not in j]
    if missing:
        return f"Felder fehlen: {missing}"
    if j["category"] not in SCORES:
        return f"unbekannte Kategorie {j['category']!r}"
    if type(j["score"]) is not int or j["score"] not in (0, 1, 2):
        return f"Score {j['score']!r} ungültig"
    if j["score"] not in SCORES[j["category"]]:
        return f"Score {j['score']} passt nicht zu {j['category']}"
    for k in ("function_ok", "hallucination", "gt_unusable"):
        if not isinstance(j[k], bool):
            return f"{k} ist kein bool"
    if not isinstance(j["reason"], str) or not j["reason"].strip():
        return "reason leer"
    return None


def parse_content(text: str | None) -> object:
    """JSON aus der Antwort; manche Anbieter setzen trotz Schema ```json … ``` drumherum."""
    if not text:
        raise ValueError("leere Antwort")
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text)
    return json.loads(text)


def http(url: str, body: dict | None, headers: dict, timeout: float) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def request_body(system: str, user: str, model: str, jc: dict) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": jc.get("temperature", 0),
        "max_tokens": jc.get("max_tokens", 400),
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "judgement", "strict": True, "schema": SCHEMA}},
        "usage": {"include": True},
    }
    if jc.get("reasoning"):
        body["reasoning"] = jc["reasoning"]
    if jc.get("require_parameters", True):
        body["provider"] = {"require_parameters": True}
    return body


def call_openrouter(system: str, user: str, model: str, jc: dict) -> dict:
    """Eine Chat-Completion mit Retries bei 429/5xx/Timeout. Gibt die rohe Response zurück."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY fehlt – in .env eintragen (siehe .env.example).")
    headers = {"Authorization": f"Bearer {key}", "X-Title": "vuse-xr-dhbw judge"}
    body = request_body(system, user, model, jc)
    retries = jc.get("retries", 5)
    for attempt in range(retries + 1):
        try:
            r = http(f"{API}/chat/completions", body, headers, jc.get("timeout", 60))
            if "error" in r:  # OpenRouter meldet manche Anbieterfehler mit HTTP 200
                raise RuntimeError(f"OpenRouter: {r['error']}")
            return r
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code == 402:
                raise BudgetExceeded(f"OpenRouter-Guthaben reicht nicht (HTTP 402): {detail}") from e
            if e.code not in RETRY_STATUS or attempt == retries:
                raise RuntimeError(f"HTTP {e.code}: {detail}") from e
            reason = f"HTTP {e.code}"
        except (TimeoutError, urllib.error.URLError) as e:
            if attempt == retries:
                raise RuntimeError(f"keine Antwort: {e}") from e
            reason = type(e).__name__
        wait = min(5 * 2**attempt, 120)
        log.warning("%s – neuer Versuch in %d s (%d/%d)", reason, wait, attempt + 1, retries)
        time.sleep(wait)
    raise AssertionError("unreachable")


def judge_one(part: Part, c: Caption, prompt: Prompt, judge_model: str, jc: dict, out_root: Path,
              force: bool = False) -> dict:
    """Bewertet eine Caption (oder nimmt den Cache) und schreibt das Ergebnis. Gibt den Cache-Eintrag zurück."""
    user = judge_input(part, c.caption)
    key = cache_key(user, prompt, judge_model)
    f = cache_file(out_root, judge_model, c)
    if not force and (hit := read_cache(f, key)) is not None:
        return {**hit, "_cached": True}

    err, cost, t0 = None, 0.0, time.time()
    for attempt in range(jc.get("invalid_retries", 1) + 1):
        r = call_openrouter(prompt.text, user, judge_model, jc)
        u = r.get("usage") or {}
        cost += float(u.get("cost") or 0)
        try:
            j = parse_content(r["choices"][0]["message"].get("content"))
            err = validate(j)
        except (ValueError, KeyError, IndexError, TypeError) as e:
            err = str(e)
        if err is None:
            break
        log.warning("%s/%s/%s: ungültige Antwort (%s)%s", c.model, c.variant, c.part_id, err,
                    " – neuer Versuch" if attempt < jc.get("invalid_retries", 1) else "")
    if err is not None:
        raise RuntimeError(f"ungültige Antwort: {err}")

    details = u.get("completion_tokens_details") or {}
    result = {
        "part_id": c.part_id,
        "key": key,
        "judgement": j,
        "meta": {
            "judge_requested": judge_model,
            "judge_model": r.get("model"),           # tatsächlich ausgelieferte Version
            "provider": r.get("provider"),
            "generation_id": r.get("id"),
            "prompt_version": prompt.version,
            "prompt_sha": prompt.sha,
            "schema_version": SCHEMA_VERSION,
            "caption_model": c.model,
            "variant": c.variant,
            "tokens": {"input": u.get("prompt_tokens"), "output": u.get("completion_tokens"),
                       "reasoning": details.get("reasoning_tokens")},
            "cost_usd": round(cost, 8),               # inkl. verworfener Versuche
            "seconds": round(time.time() - t0, 2),
            "input": user,
        },
    }
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def read_cases(path: Path) -> set[tuple]:
    """CSV mit part_id (und optional model, variant) -> Filter für die Judge-Validierung."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows or "part_id" not in rows[0]:
        sys.exit(f"{path}: Spalte part_id fehlt")
    full = "model" in rows[0] and "variant" in rows[0]
    return {(r["part_id"], r["model"], r["variant"]) if full else (r["part_id"],) for r in rows}


def select(parts: dict[str, Part], caps: list[Caption], only: str | None, cases: set[tuple] | None):
    """-> (zu bewertende Captions, Anzahl ohne Namen)."""
    todo, empty = [], 0
    for c in caps:
        if c.part_id not in parts or (only and only not in c.part_id):
            continue
        if cases is not None and (c.part_id,) not in cases and (c.part_id, c.model, c.variant) not in cases:
            continue
        if not str(c.caption.get("name") or "").strip():
            empty += 1
            continue
        todo.append(c)
    return todo, empty


def fetch_models(timeout: float = 30) -> list[dict]:
    return http(f"{API}/models", None, {}, timeout)["data"]


def price(m: dict) -> tuple[float, float]:
    """$ pro 1 Mio. Tokens (input, output)."""
    p = m.get("pricing") or {}
    return float(p.get("prompt") or 0) * 1e6, float(p.get("completion") or 0) * 1e6


def estimate(n: int, prompt: Prompt, p_in: float, p_out: float) -> float:
    tokens_in = len(prompt.text) / 4 + EST_USER_TOKENS
    return n * (tokens_in * p_in + EST_OUTPUT_TOKENS * p_out) / 1e6


def list_candidates(prompt: Prompt, exclude: list[str], n: int, top: int) -> None:
    rows = []
    for m in fetch_models():
        arch = m.get("architecture") or {}
        if "text" not in (arch.get("output_modalities") or ["text"]):
            continue
        if "structured_outputs" not in (m.get("supported_parameters") or []):
            continue
        fam = m["id"].lstrip("~").split("/")[0]
        if any(fam.startswith(x) for x in exclude):
            continue
        p_in, p_out = price(m)
        if p_in < 0 or p_out < 0:  # Router-Modelle mit variablem Preis
            continue
        rows.append((estimate(n, prompt, p_in, p_out), m["id"], p_in, p_out,
                     "reasoning" in (m.get("supported_parameters") or [])))
    print(f"{'~$ für ' + str(n):>10}  {'$/1M in':>8} {'$/1M out':>8}  reas.  Modell")
    for cost, mid, p_in, p_out, reas in sorted(rows)[:top]:
        print(f"{cost:>10.2f}  {p_in:>8.3f} {p_out:>8.3f}  {'ja' if reas else '-':>5}  {mid}")
    print(f"\n{len(rows)} Modelle mit JSON-Schema, ohne {', '.join(exclude)}; Schätzung ohne Reasoning-Tokens.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "configs" / "cap3d_uuid.yaml"))
    ap.add_argument("--models-config", default=str(ROOT / "configs" / "models.yaml"),
                    help="Modelle + Judge (Standard: configs/models.yaml)")
    ap.add_argument("--judge-model", help="überschreibt judge.model (OpenRouter-ID)")
    ap.add_argument("--models", nargs="+", help="nur diese Caption-Modelle (Ordnernamen)")
    ap.add_argument("--variants", nargs="+", help="nur diese Varianten")
    ap.add_argument("--only", help="nur part_ids, die diesen Text enthalten (z. B. Assetname)")
    ap.add_argument("--parts", help="CSV mit part_id[,model,variant]: nur diese Fälle (Judge-Validierung)")
    ap.add_argument("--limit", type=int, help="höchstens N neue Bewertungen (zum Testen)")
    ap.add_argument("--max-cost", type=float, help="Abbruch, sobald die Kosten dieses Laufs in $ erreicht sind")
    ap.add_argument("--workers", type=int, help="parallele Anfragen (überschreibt judge.workers)")
    ap.add_argument("--force", action="store_true", help="Cache ignorieren und neu bewerten")
    ap.add_argument("--dry-run", action="store_true", help="nur zählen und Kosten schätzen, keine Anfragen")
    ap.add_argument("--list-candidates", action="store_true", help="Judge-Modelle mit Preis auflisten")
    ap.add_argument("--n", type=int, default=4500, help="Anzahl Bewertungen für --list-candidates")
    add_log_args(ap)
    args = ap.parse_args()
    setup_logging(args.verbose, args.quiet)
    load_env(ROOT / ".env")

    jc = dict(load_cfg(Path(args.models_config))["judge"])
    prompt = load_prompt(jc.get("prompt", "v1"))
    if args.list_candidates:
        try:
            list_candidates(prompt, jc.get("exclude_families", []), args.n, top=30)
        except (urllib.error.URLError, TimeoutError) as e:
            sys.exit(f"OpenRouter-Modellliste nicht abrufbar: {e}")
        return
    judge_model = args.judge_model or jc.get("model")
    if not judge_model:
        sys.exit("Kein Judge-Modell: judge.model in configs/models.yaml setzen oder --judge-model angeben.")
    family = judge_model.lstrip("~").split("/")[0]
    if any(family.startswith(x) for x in jc.get("exclude_families", [])):
        log.warning("Judge %s gehört zur Familie eines bewerteten Modells (Self-Preference-Bias, 4.3)", judge_model)

    cfg = load_cfg(Path(args.config))
    try:
        parts = load_parts(cfg)
    except FileNotFoundError as e:
        sys.exit(str(e))
    caps = list(iter_captions(captions_dir(cfg), args.models, args.variants))
    cases = read_cases(Path(args.parts)) if args.parts else None
    todo, empty = select(parts, caps, args.only, cases)
    out_root = ROOT / cfg["paths"]["output"] / jc.get("out_dir", "eval/judge")

    def is_cached(c: Caption) -> bool:
        return not args.force and read_cache(cache_file(out_root, judge_model, c),
                                             cache_key(judge_input(parts[c.part_id], c.caption), prompt,
                                                       judge_model)) is not None

    new = [c for c in todo if not is_cached(c)]
    n_cached = len(todo) - len(new)
    if args.limit is not None:
        new = new[:args.limit]
    log.info("Judge %s, Prompt %s (%s): %d Captions, %d im Cache, %d neu, %d ohne Namen (-> no_result)",
             judge_model, prompt.version, prompt.sha, len(todo), n_cached, len(new), empty)

    if args.dry_run:
        try:
            m = next((m for m in fetch_models() if m["id"] == judge_model), None)
        except (urllib.error.URLError, TimeoutError) as e:
            log.warning("Preise nicht abrufbar: %s", e)
            return
        if m is None:
            log.warning("%s nicht in der OpenRouter-Modellliste – ID prüfen", judge_model)
            return
        p_in, p_out = price(m)
        log.info("Schätzung: %d × ca. %d Tokens -> ca. $%.3f ($%.3f / $%.3f pro 1M, ohne Reasoning-Tokens)",
                 len(new), len(prompt.text) / 4 + EST_USER_TOKENS + EST_OUTPUT_TOKENS,
                 estimate(len(new), prompt, p_in, p_out), p_in, p_out)
        return
    if not new:
        return

    spent, lock, stop = 0.0, threading.Lock(), threading.Event()
    done, failed, models_seen = 0, [], set()

    def work(c: Caption) -> dict | None:
        if stop.is_set():
            return None
        return judge_one(parts[c.part_id], c, prompt, judge_model, jc, out_root, force=args.force)

    with ThreadPoolExecutor(max_workers=args.workers or jc.get("workers", 4)) as pool:
        futures = {pool.submit(work, c): c for c in new}
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                res = fut.result()
            except BudgetExceeded as e:
                stop.set()
                log.error("%s – Abbruch", e)
                continue
            except Exception as e:  # einzelnes Teil fehlgeschlagen, Lauf geht weiter
                failed.append(c)
                log.error("%s/%s/%s fehlgeschlagen: %s", c.model, c.variant, c.part_id, e)
                continue
            if res is None:
                continue
            done += 1
            meta, j = res["meta"], res["judgement"]
            models_seen.add(meta.get("judge_model"))
            with lock:
                spent += meta.get("cost_usd") or 0
                if args.max_cost is not None and spent >= args.max_cost and not stop.is_set():
                    stop.set()
                    log.error("--max-cost $%.2f erreicht ($%.4f) – keine neuen Anfragen mehr", args.max_cost, spent)
            log.debug("%s/%s/%s: %d %s – %s", c.model, c.variant, c.part_id, j["score"], j["category"], j["reason"])

    log.info("%d/%d bewertet, %d fehlgeschlagen, Kosten $%.4f, ausgeliefert: %s -> %s", done, len(new),
             len(failed), spent, ", ".join(sorted(m for m in models_seen if m)) or "-",
             (out_root / safe_name(judge_model)).relative_to(ROOT))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
