"""Grounding-Test: Findet ein Prompt mit dem echten Teilnamen das richtige Teil über die generierten Captions?

    uv run python -m vuse_xr.cap3d.grounding --config configs/cap3d_uuid.yaml --only 01_laboratory_centrifuge
    uv run python -m vuse_xr.cap3d.grounding --config configs/cap3d_uuid.yaml --models qwen2.5vl_7b --variants baseline

Anwendungsfall von VUSE (Q1/Q2): „highlight the ratchet head“ -> welcher Knoten? Pro Modell × Variante:
    Query       Originalname ohne Nummern (Ratchet_Head -> "ratchet head", Sample_Tube_5 -> "sample tube")
    Kandidaten  alle Teile desselben Assets mit Caption in dieser Kombination, Text "name. type. function."
    Ranking     Kosinus-Ähnlichkeit lokaler Embeddings (grounding.embedding_model in configs/models.yaml)
    Treffer     das Teil selbst oder ein Zwilling (gleicher twin_key, z. B. Rubber_Foot_1 = Rubber_Foot_2)
Teil ohne Caption -> nicht gefunden (rank leer, zählt 0). Teile mit nichtssagendem Originalnamen (Judge-Flag
gt_unusable, Mehrheit über alle Bewertungen) werden nicht als Query genutzt, bleiben aber Kandidaten.
Zufallsniveau pro Query = (Teil + Zwillinge unter den Kandidaten) / Anzahl Kandidaten.

Output:
    <paths.output>/eval/grounding.csv  part_id, asset, model, variant, query, rank, n_candidates, hit1, hit3, rr,
                                       random_hit1, top1 – evaluate.py übernimmt es in long.csv und summary.csv
Keine API-Aufrufe; das Embedding-Modell wird beim ersten Lauf einmal heruntergeladen (data/interim/fastembed_cache).
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..log import add_log_args, setup_logging
from .caption import safe_name
from .eval_data import Caption, Part, captions_dir, iter_captions, load_parts, twin_key
from .evaluate import write_csv
from .render import ROOT, load_cfg

log = logging.getLogger("vuse_xr.cap3d.grounding")  # fester Name: bei python -m wäre __name__ == "__main__"

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
COLUMNS = ["part_id", "asset", "model", "variant", "query", "rank", "n_candidates", "hit1", "hit3", "rr",
           "random_hit1", "top1"]

Embed = Callable[[list[str]], np.ndarray]


def make_embedder(model_name: str) -> Embed:
    """fastembed-Modell laden; gibt eine Funktion Texte -> L2-normierte Matrix zurück (mit Cache pro Text)."""
    from fastembed import TextEmbedding  # erst hier importieren: Tests und --help brauchen das Modell nicht

    try:
        model = TextEmbedding(model_name=model_name, cache_dir=str(ROOT / "data" / "interim" / "fastembed_cache"))
    except ValueError as e:  # Download fehlgeschlagen (offline, Proxy) oder unbekannter Name
        raise SystemExit(f"Embedding-Modell {model_name} nicht ladbar ({e}) – Internet für den einmaligen "
                         "Download nötig, Name in configs/models.yaml (grounding.embedding_model) prüfen.") from e
    memo: dict[str, np.ndarray] = {}

    def embed(texts: list[str]) -> np.ndarray:
        new = [t for t in dict.fromkeys(texts) if t not in memo]
        if new:
            for t, v in zip(new, model.embed(new)):
                memo[t] = np.asarray(v, dtype=np.float32)
        m = np.stack([memo[t] for t in texts])
        return m / np.linalg.norm(m, axis=1, keepdims=True)

    return embed


def candidate_text(cap: dict) -> str:
    parts = [str(cap.get(k) or "").strip().rstrip(".") for k in ("name", "type", "function")]
    return ". ".join(p for p in parts if p) + "."


def query_text(part: Part) -> str:
    """Wie ein Nutzer das Teil nennt: ohne Nummern (Sample_Tube_5 -> "sample tube", Pin_-0.145 -> "pin")."""
    return twin_key(part.node)


def rank_combo(parts: dict[str, Part], caps: dict[str, Caption], embed: Embed,
               skip_queries: set[str] = frozenset()) -> list[dict]:
    """Rang des richtigen Teils für jedes erwartete Teil einer Kombination Modell × Variante."""
    by_asset: dict[str, list[str]] = defaultdict(list)
    for pid in sorted(parts):
        by_asset[parts[pid].asset].append(pid)

    rows = []
    for asset, pids in sorted(by_asset.items()):
        cands = [p for p in pids if p in caps and str(caps[p].caption.get("name") or "").strip()]
        cand_emb = embed([candidate_text(caps[p].caption) for p in cands]) if cands else None
        queries = [p for p in pids if p not in skip_queries]
        q_emb = embed([query_text(parts[p]) for p in queries]) if queries and cands else None
        for i, pid in enumerate(queries):
            key = twin_key(parts[pid].node)
            correct = {c for c in cands if twin_key(parts[c].node) == key}
            row = {"part_id": pid, "asset": asset, "query": query_text(parts[pid]), "n_candidates": len(cands),
                   "random_hit1": round(len(correct) / len(cands), 4) if cands and pid in cands else 0.0}
            if pid not in cands:  # keine Caption -> Nutzer findet das Teil nicht
                rows.append({**row, "rank": None, "hit1": 0, "hit3": 0, "rr": 0.0, "top1": None})
                continue
            sims = cand_emb @ q_emb[i]
            order = sorted(range(len(cands)), key=lambda k: (-float(sims[k]), cands[k]))  # Gleichstand stabil
            rank = next(r for r, k in enumerate(order, 1) if cands[k] in correct)
            rows.append({**row, "rank": rank, "hit1": int(rank == 1), "hit3": int(rank <= 3),
                         "rr": round(1 / rank, 4), "top1": caps[cands[order[0]]].caption.get("name")})
    return rows


def gt_unusable_parts(judge_dir: Path) -> set[str]:
    """part_ids, die der Judge mehrheitlich als nichtssagend markiert hat (über alle Modelle/Varianten)."""
    votes: dict[str, list[bool]] = defaultdict(list)
    for f in judge_dir.glob("*/*/*.json"):
        try:
            d = json.loads(f.read_text())
            votes[d["part_id"]].append(bool(d["judgement"]["gt_unusable"]))
        except (json.JSONDecodeError, KeyError):
            continue
    return {p for p, v in votes.items() if sum(v) > len(v) / 2}


def run(parts: dict[str, Part], caps_root: Path, embed: Embed, skip_queries: set[str] = frozenset(),
        models: list[str] | None = None, variants: list[str] | None = None,
        only_captioned: bool = False) -> list[dict]:
    combos: dict[tuple[str, str], dict[str, Caption]] = defaultdict(dict)
    for c in iter_captions(caps_root, models, variants):
        if c.part_id in parts:
            combos[(c.model, c.variant)][c.part_id] = c
    if only_captioned:
        seen = {p for caps in combos.values() for p in caps}
        parts = {k: v for k, v in parts.items() if k in seen}
    rows = []
    for (model, variant), caps in sorted(combos.items()):
        rows += [{**r, "model": model, "variant": variant} for r in rank_combo(parts, caps, embed, skip_queries)]
    return rows


def summarize(rows: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["variant"])].append(r)
    out = []
    for (model, variant), rs in sorted(groups.items()):
        n = len(rs)
        out.append({"model": model, "variant": variant, "n": n,
                    "grounding_at1_pct": round(100 * sum(r["hit1"] for r in rs) / n, 1),
                    "hit3_pct": round(100 * sum(r["hit3"] for r in rs) / n, 1),
                    "mrr": round(sum(r["rr"] for r in rs) / n, 3),
                    "random_at1_pct": round(100 * sum(r["random_hit1"] for r in rs) / n, 1)})
    return out


def table(summary: list[dict]) -> str:
    head = f"{'Modell':<36} {'Variante':<9} {'n':>4} {'G@1%':>6} {'Hit@3%':>7} {'MRR':>6} {'Zufall%':>8}"
    lines = [head, "-" * len(head)]
    for s in summary:
        lines.append(f"{s['model'][:36]:<36} {s['variant'][:9]:<9} {s['n']:>4} {s['grounding_at1_pct']:>6.1f} "
                     f"{s['hit3_pct']:>7.1f} {s['mrr']:>6.3f} {s['random_at1_pct']:>8.1f}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "configs" / "cap3d_uuid.yaml"))
    ap.add_argument("--models-config", default=str(ROOT / "configs" / "models.yaml"))
    ap.add_argument("--only", help="nur part_ids, die diesen Text enthalten (bestimmt Queries und Kandidaten)")
    ap.add_argument("--models", nargs="+", help="nur diese Caption-Modelle (Ordnernamen)")
    ap.add_argument("--variants", nargs="+", help="nur diese Varianten")
    ap.add_argument("--only-captioned", action="store_true",
                    help="nur Teile, die in irgendeiner Kombination eine Caption haben (Pilot, --limit)")
    ap.add_argument("--judge-model", help="Judge-Cache für gt_unusable (Standard: judge.model)")
    add_log_args(ap)
    args = ap.parse_args()
    setup_logging(args.verbose, args.quiet)

    cfg = load_cfg(Path(args.config))
    mcfg = load_cfg(Path(args.models_config))
    jc, gc = mcfg.get("judge") or {}, mcfg.get("grounding") or {}
    model_name = gc.get("embedding_model", DEFAULT_EMBEDDING_MODEL)
    parts = {k: v for k, v in load_parts(cfg).items() if not args.only or args.only in k}

    judge_model = args.judge_model or jc.get("model")
    skip: set[str] = set()
    if judge_model:
        judge_dir = ROOT / cfg["paths"]["output"] / jc.get("out_dir", "eval/judge") / safe_name(judge_model)
        skip = gt_unusable_parts(judge_dir) & set(parts)
    log.info("Embedding-Modell %s, %d Teile, %d ohne Query (gt_unusable laut %s)", model_name, len(parts),
             len(skip), judge_model or "-")

    rows = run(parts, captions_dir(cfg), make_embedder(model_name), skip, args.models, args.variants,
               args.only_captioned)
    if not rows:
        log.warning("keine Captions gefunden")
        return
    out = ROOT / cfg["paths"]["output"] / "eval" / "grounding.csv"
    write_csv(out, rows, COLUMNS)
    log.info("-> %s\n%s", out.relative_to(ROOT), table(summarize(rows)))


if __name__ == "__main__":
    main()
