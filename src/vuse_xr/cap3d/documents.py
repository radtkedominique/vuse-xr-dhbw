"""Pipeline 3h: ein Zusatzdokument pro Asset (Anleitung, Stückliste, …) als Text-Kontext für caption.py.

    uv run python -m vuse_xr.cap3d.documents --config configs/cap3d_uuid.yaml   # welche Assets haben ein Dokument?

Zuordnung über den Dateinamen: <caption.documents_dir>/<asset>.<ext>
    data/raw/3d_assets/04_magnetic_stirrer.glb  ->  data/raw/documents/04_magnetic_stirrer.pdf
Unterstützt: .pdf, .md, .txt. Kein Dokument -> None, das Teil läuft ohne.
Inhalt, Sprache und Länge sind vorher unbekannt. Texte über caption.document_max_chars werden gekürzt (Warnung);
Auswahl passender Abschnitte (RAG) folgt in Schritt 2.
"""
from __future__ import annotations

import argparse
import logging
import re
from functools import cache
from pathlib import Path

from ..log import add_log_args, setup_logging
from .render import ROOT, load_cfg

log = logging.getLogger("vuse_xr.cap3d.documents")  # fester Name: bei python -m wäre __name__ == "__main__"

SUFFIXES = (".pdf", ".md", ".txt")  # Reihenfolge = Priorität, falls mehrere da sind
DEFAULT_MAX_CHARS = 12000           # ~3.000–4.000 Tokens


def find_document(docs_dir: Path, asset_stem: str) -> Path | None:
    """<docs_dir>/<asset_stem>.<ext> oder None."""
    hits = [p for s in SUFFIXES if (p := docs_dir / f"{asset_stem}{s}").is_file()]
    if len(hits) > 1:
        log.warning("%s: mehrere Dokumente (%s) – nehme %s",
                    asset_stem, ", ".join(h.name for h in hits), hits[0].name)
    return hits[0] if hits else None


def extract_text(path: Path) -> str:
    """Reiner Text aus PDF / Markdown / Text, Leerraum zusammengefasst."""
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader  # nur laden, wenn wirklich ein PDF da ist

        text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


@cache
def load_document(path: Path, max_chars: int) -> tuple[str, int, bool]:
    """(Text, Länge vor dem Kürzen, gekürzt?) – einmal pro Datei und Lauf, nicht pro Teil."""
    text = extract_text(path)
    if not text:
        log.warning("%s: kein Text gefunden (gescanntes PDF?) – Teile laufen ohne Dokument", path.name)
    total = len(text)
    if total > max_chars:
        log.warning("%s: %d Zeichen, gekürzt auf %d (RAG folgt)", path.name, total, max_chars)
        text = text[:max_chars]
    return text, total, total > max_chars


def document_for(asset_stem: str, docs_dir: Path, max_chars: int = DEFAULT_MAX_CHARS) -> dict | None:
    """Dokument zu einem Asset: {file, chars, chars_total, truncated, text} oder None."""
    path = find_document(docs_dir, asset_stem)
    if path is None:
        return None
    text, total, truncated = load_document(path, max_chars)
    if not text:
        return None
    return {"file": path.name, "chars": len(text), "chars_total": total, "truncated": truncated, "text": text}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "configs" / "cap3d.yaml"))
    add_log_args(ap)
    args = ap.parse_args()
    setup_logging(args.verbose, args.quiet)

    cfg = load_cfg(Path(args.config))
    cc = cfg["caption"]
    docs_dir = ROOT / cc.get("documents_dir", "data/raw/documents")
    max_chars = cc.get("document_max_chars", DEFAULT_MAX_CHARS)
    for asset in sorted(ROOT.glob(cfg["render"]["asset_glob"])):
        d = document_for(asset.stem, docs_dir, max_chars)
        info = f"{d['file']}  {d['chars_total']} Zeichen" + (" (gekürzt)" if d["truncated"] else "") if d else "–"
        print(f"{asset.stem:<34} {info}")


if __name__ == "__main__":
    main()
