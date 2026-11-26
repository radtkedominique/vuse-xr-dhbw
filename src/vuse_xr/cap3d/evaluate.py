"""Auswertung: Captions + Judge-Bewertungen pro Modell × Variante zusammenführen, ohne API-Aufrufe.

    uv run python -m vuse_xr.cap3d.evaluate --config configs/cap3d_uuid.yaml --only 01_laboratory_centrifuge
    uv run python -m vuse_xr.cap3d.evaluate --config configs/cap3d_uuid.yaml --models qwen2.5vl_7b --variants baseline hier

Input:
    Teile + richtige Namen aus eval_data.py, Captions aus <paths.output>/captions/<modell>/<variante>/
    Judge-Cache aus judge.py (<paths.output>/<judge.out_dir>/<judge>/…), Judge und Preise aus configs/models.yaml

Regeln (Evaluationskonzept 2.3, 4.1):
    Erwartet sind pro Modell × Variante alle ausgewählten Teile (--only). Fehlt die Caption-Datei (fehlgeschlagen
    oder übersprungen) -> status failed, Score 0. Leerer Name -> status empty, Score 0, no_result.
    Caption ohne gültige Bewertung (fehlt oder key passt nicht mehr) -> status unjudged, zählt nicht in den Score.
    Name-Score in % = Mittelwert(0/1/2) / 2; strikt = Anteil Score 2.
    Kosten: meta.tokens.cost (OpenRouter), sonst Tokens × price aus models.yaml; Asset-Stufe (topdown) zählt mit.
    Grounding: aus <paths.output>/eval/grounding.csv (grounding.py), falls vorhanden; sonst leer.

Output:
    <paths.output>/eval/long.csv     eine Zeile pro Teil × Modell × Variante
    <paths.output>/eval/summary.csv  eine Zeile pro Modell × Variante (Tabelle auch im Log)
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from collections import Counter, defaultdict
from pathlib import Path

from ..log import add_log_args, setup_logging
from .caption import safe_name
from .eval_data import Caption, Part, captions_dir, iter_captions, load_parts
from .judge import SCORES, Prompt, cache_file, cache_key, judge_input, load_prompt, read_cache
from .render import ROOT, load_cfg

log = logging.getLogger("vuse_xr.cap3d.evaluate")  # fester Name: bei python -m wäre __name__ == "__main__"

LONG_COLUMNS = [
    "part_id", "asset", "kind", "rel_size", "model", "model_id", "model_version", "variant", "status",
    "name", "correct_names", "score", "category", "function_ok", "hallucination", "gt_unusable", "reason",
    "confidence", "grounding_rank", "grounding_hit1", "cost_usd", "seconds", "tokens_in", "tokens_out",
    "tokens_thinking",
]
SUMMARY_COLUMNS = [
    "model", "model_id", "variant", "n", "ok", "failed", "empty", "unjudged", "name_score_pct", "strict_pct",
    "mean_score", "function_ok_pct", "hallucination_pct", *[f"cat_{c}" for c in SCORES],
    "grounding_n", "grounding_at1_pct", "hit3_pct", "mrr", "random_at1_pct",
    "cost_usd", "usd_per_1000", "seconds_mean", "tokens_in_mean", "tokens_out_mean", "tokens_thinking_mean",
]


def price_table(models: dict) -> dict[str, dict]:
    """Caption-Ordnername (safe_name(model)) -> Eintrag aus models.yaml inkl. ID."""
    return {safe_name(m["model"]): {**m, "id": mid} for mid, m in (models or {}).items()}


def call_cost(tokens: dict | None, price: list | None) -> float | None:
    """$ eines Aufrufs: gemeldete Kosten, sonst Tokens × Preis (Thinking wird wie Output abgerechnet)."""
    t = tokens or {}
    if t.get("cost") is not None:
        return float(t["cost"])
    if not price or t.get("input") is None:
        return None
    out = (t.get("output") or 0)
    if t.get("model_version", "").startswith("gemini"):  # Gemini zählt Thinking getrennt von candidatesTokenCount
        out += t.get("thinking") or 0
    return (t["input"] * price[0] + out * price[1]) / 1e6


def build_rows(parts: dict[str, Part], caps_root: Path, judge_root: Path, judge_model: str, prompt: Prompt,
               prices: dict[str, dict], models: list[str] | None = None, variants: list[str] | None = None,
               only_captioned: bool = False) -> list[dict]:
    """Eine Zeile pro erwartetem Teil × (Modell, Variante) mit mindestens einer Caption.

    only_captioned: erwartet nur Teile, die in irgendeiner Kombination eine Caption haben (Pilot mit --limit).
    """
    by_combo: dict[tuple[str, str], dict[str, Caption]] = defaultdict(dict)
    for c in iter_captions(caps_root, models, variants):
        if c.part_id in parts:
            by_combo[(c.model, c.variant)][c.part_id] = c
    if only_captioned:
        seen = {p for caps in by_combo.values() for p in caps}
        parts = {k: v for k, v in parts.items() if k in seen}

    rows = []
    for (model, variant), caps in sorted(by_combo.items()):
        info = prices.get(model, {})
        for part_id, part in sorted(parts.items()):
            row = {"part_id": part_id, "asset": part.asset, "kind": part.kind, "rel_size": part.rel_size,
                   "model": model, "model_id": info.get("id"), "variant": variant,
                   "correct_names": " | ".join(part.names)}
            c = caps.get(part_id)
            if c is None:
                rows.append({**row, "status": "failed", "score": 0, "category": "no_result"})
                continue
            tokens = c.meta.get("tokens") or {}
            row.update(name=c.caption.get("name"), confidence=c.caption.get("confidence"),
                       model_version=tokens.get("model_version"), seconds=c.meta.get("seconds"),
                       tokens_in=tokens.get("input"), tokens_out=tokens.get("output"),
                       tokens_thinking=tokens.get("thinking"), cost_usd=call_cost(tokens, info.get("price")))
            if not str(c.caption.get("name") or "").strip():
                rows.append({**row, "status": "empty", "score": 0, "category": "no_result"})
                continue
            key = cache_key(judge_input(part, c.caption), prompt, judge_model)
            hit = read_cache(cache_file(judge_root, judge_model, c), key)
            if hit is None:
                rows.append({**row, "status": "unjudged"})
                continue
            j = hit["judgement"]
            rows.append({**row, "status": "ok", **{k: j[k] for k in (
                "score", "category", "function_ok", "hallucination", "gt_unusable", "reason")}})
    return rows


def load_grounding(path: Path) -> dict[tuple[str, str, str], dict]:
    """grounding.csv -> {(model, variant, part_id): Zeile}; fehlt die Datei -> leer."""
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {(r["model"], r["variant"], r["part_id"]): r for r in csv.DictReader(f)}


def add_grounding(rows: list[dict], grounding: dict[tuple[str, str, str], dict]) -> None:
    """grounding_rank/hit1 in die Zeilen schreiben; Teile ohne Grounding-Zeile (z. B. gt_unusable) bleiben leer."""
    for r in rows:
        g = grounding.get((r["model"], r["variant"], r["part_id"]))
        if g is not None:
            r.update(grounding_rank=int(g["rank"]) if g["rank"] else None, grounding_hit1=int(g["hit1"]),
                     _hit3=int(g["hit3"]), _rr=float(g["rr"]), _random=float(g["random_hit1"]))


def asset_stage_costs(caps_root: Path, prices: dict[str, dict]) -> dict[tuple[str, str], float]:
    """Kosten der Asset-Captions (Pipeline 3e, Stufe 1) pro (Modell, Variante)."""
    out: dict[tuple[str, str], float] = defaultdict(float)
    for f in caps_root.glob("*/*/_assets/*.json"):
        model, variant = f.parent.parent.parent.name, f.parent.parent.name
        try:
            tokens = json.loads(f.read_text()).get("meta", {}).get("tokens")
        except json.JSONDecodeError:
            continue
        out[(model, variant)] += call_cost(tokens, prices.get(model, {}).get("price")) or 0
    return out


def _mean(xs: list) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def _pct(n: int, d: int) -> float | None:
    return round(100 * n / d, 1) if d else None


def summarize(rows: list[dict], extra_cost: dict[tuple[str, str], float] | None = None) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["variant"])].append(r)
    out = []
    for (model, variant), rs in sorted(groups.items()):
        status = Counter(r["status"] for r in rs)
        scored = [r for r in rs if r["status"] != "unjudged"]       # failed/empty zählen als 0
        judged = [r for r in rs if r["status"] == "ok"]
        cats = Counter(r["category"] for r in scored)
        called = [r for r in rs if r["status"] in ("ok", "empty", "unjudged")]
        cost = sum(r.get("cost_usd") or 0 for r in rs) + (extra_cost or {}).get((model, variant), 0)
        mean = _mean([r["score"] for r in scored])
        gr = [r for r in rs if r.get("grounding_hit1") is not None]
        out.append({
            "model": model, "model_id": rs[0].get("model_id"), "variant": variant, "n": len(rs),
            "ok": status["ok"], "failed": status["failed"], "empty": status["empty"], "unjudged": status["unjudged"],
            "name_score_pct": round(50 * mean, 1) if mean is not None else None,
            "strict_pct": _pct(sum(r["score"] == 2 for r in scored), len(scored)),
            "mean_score": mean,
            "function_ok_pct": _pct(sum(bool(r["function_ok"]) for r in judged), len(judged)),
            "hallucination_pct": _pct(sum(bool(r["hallucination"]) for r in judged), len(judged)),
            **{f"cat_{c}": cats[c] for c in SCORES},
            "grounding_n": len(gr),
            "grounding_at1_pct": _pct(sum(r["grounding_hit1"] for r in gr), len(gr)),
            "hit3_pct": _pct(sum(r["_hit3"] for r in gr), len(gr)),
            "mrr": round(sum(r["_rr"] for r in gr) / len(gr), 3) if gr else None,
            "random_at1_pct": round(100 * sum(r["_random"] for r in gr) / len(gr), 1) if gr else None,
            "cost_usd": round(cost, 6),
            "usd_per_1000": round(1000 * cost / len(called), 4) if called else None,
            "seconds_mean": _mean([r.get("seconds") for r in called]),
            "tokens_in_mean": _mean([r.get("tokens_in") for r in called]),
            "tokens_out_mean": _mean([r.get("tokens_out") for r in called]),
            "tokens_thinking_mean": _mean([r.get("tokens_thinking") for r in called]),
        })
    return out


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def table(summary: list[dict]) -> str:
    """Kurze Übersicht fürs Terminal."""
    def fmt(v, spec):
        return "-" if v is None else format(v, spec)
    head = f"{'Modell':<36} {'Variante':<9} {'n':>4} {'Name%':>6} {'strikt%':>7} {'fail':>4} {'offen':>5} " \
           f"{'Halluz%':>7} {'G@1%':>5} {'Zuf%':>5} {'$/1000':>7} {'s/Teil':>6} {'Think':>6}"
    lines = [head, "-" * len(head)]
    for s in summary:
        lines.append(
            f"{s['model'][:36]:<36} {s['variant'][:9]:<9} {s['n']:>4} {fmt(s['name_score_pct'], '6.1f')} "
            f"{fmt(s['strict_pct'], '7.1f')} {s['failed'] + s['empty']:>4} {s['unjudged']:>5} "
            f"{fmt(s['hallucination_pct'], '7.1f')} {fmt(s['grounding_at1_pct'], '5.1f')} "
            f"{fmt(s['random_at1_pct'], '5.1f')} {fmt(s['usd_per_1000'], '7.3f')} "
            f"{fmt(s['seconds_mean'], '6.1f')} {fmt(s['tokens_thinking_mean'], '6.0f')}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "configs" / "cap3d_uuid.yaml"))
    ap.add_argument("--models-config", default=str(ROOT / "configs" / "models.yaml"))
    ap.add_argument("--judge-model", help="überschreibt judge.model (welcher Judge-Cache gelesen wird)")
    ap.add_argument("--only", help="nur part_ids, die diesen Text enthalten (bestimmt die erwarteten Teile!)")
    ap.add_argument("--models", nargs="+", help="nur diese Caption-Modelle (Ordnernamen)")
    ap.add_argument("--variants", nargs="+", help="nur diese Varianten")
    ap.add_argument("--only-captioned", action="store_true",
                    help="nur Teile erwarten, die in irgendeiner Kombination eine Caption haben (Pilot, --limit)")
    add_log_args(ap)
    args = ap.parse_args()
    setup_logging(args.verbose, args.quiet)

    cfg = load_cfg(Path(args.config))
    mcfg = load_cfg(Path(args.models_config))
    jc = mcfg["judge"]
    judge_model = args.judge_model or jc.get("model")
    if not judge_model:
        raise SystemExit("Kein Judge-Modell: judge.model in configs/models.yaml setzen oder --judge-model angeben.")
    prompt = load_prompt(jc.get("prompt", "v1"))
    prices = price_table(mcfg.get("models"))

    parts = {k: v for k, v in load_parts(cfg).items() if not args.only or args.only in k}
    caps_root = captions_dir(cfg)
    out_dir = ROOT / cfg["paths"]["output"] / "eval"
    judge_root = ROOT / cfg["paths"]["output"] / jc.get("out_dir", "eval/judge")

    rows = build_rows(parts, caps_root, judge_root, judge_model, prompt, prices, args.models, args.variants,
                      args.only_captioned)
    found = {(r["model"], r["variant"]) for r in rows}
    empty_dirs = sorted(f"{d.parent.name}/{d.name}" for d in caps_root.glob("*/*/")
                        if d.is_dir() and (d.parent.name, d.name) not in found
                        and (not args.models or d.parent.name in args.models)
                        and (not args.variants or d.name in args.variants))
    if empty_dirs:
        log.warning("ohne Captions, nicht in der Auswertung: %s", ", ".join(empty_dirs))
    if not rows:
        log.warning("keine Captions unter %s", caps_root.relative_to(ROOT))
        return
    grounding = load_grounding(out_dir / "grounding.csv")
    if grounding:
        add_grounding(rows, grounding)
    else:
        log.info("keine eval/grounding.csv – Grounding-Spalten bleiben leer (erst grounding.py laufen lassen)")
    summary = summarize(rows, asset_stage_costs(caps_root, prices))
    write_csv(out_dir / "long.csv", rows, LONG_COLUMNS)
    write_csv(out_dir / "summary.csv", summary, SUMMARY_COLUMNS)

    unpriced = sorted({r["model"] for r in rows if r["model"] not in prices})
    if unpriced:
        log.warning("nicht in models.yaml (Kosten nur aus meta.tokens.cost): %s", ", ".join(unpriced))
    unjudged = sum(s["unjudged"] for s in summary)
    if unjudged:
        log.warning("%d Captions ohne gültige Bewertung von %s – erst judge.py laufen lassen", unjudged, judge_model)
    log.info("Judge %s, Prompt %s, %d Teile erwartet -> %s, %s\n%s", judge_model, prompt.version,
             len({r["part_id"] for r in rows}),
             (out_dir / "long.csv").relative_to(ROOT), (out_dir / "summary.csv").relative_to(ROOT), table(summary))


if __name__ == "__main__":
    main()
