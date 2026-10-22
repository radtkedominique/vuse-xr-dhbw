"""Logging-Setup für die CLI-Einstiegspunkte (render, split_nodes).

Module loggen nur über logging.getLogger("vuse_xr...."); Handler und Level werden einmal pro Prozess hier gesetzt.
Ausgabe auf stderr, z. B.:  12:03:41 INFO    [render] Modus asset: 3 zu rendern, 0 übersprungen/gefiltert
"""
from __future__ import annotations

import argparse
import logging

FORMAT = "%(asctime)s %(levelname)-7s [%(short)s] %(message)s"


class _Formatter(logging.Formatter):
    """Kurzer Loggername im Log: vuse_xr.cap3d.threejs_render -> threejs_render."""

    def format(self, record: logging.LogRecord) -> str:
        record.short = record.name.rsplit(".", 1)[-1] if record.name.startswith("vuse_xr") else record.name
        return super().format(record)


def add_log_args(ap: argparse.ArgumentParser) -> None:
    g = ap.add_mutually_exclusive_group()
    g.add_argument("-v", "--verbose", action="store_true", help="Debug-Ausgaben (inkl. Tracebacks bei Fehlern)")
    g.add_argument("-q", "--quiet", action="store_true", help="nur Warnungen und Fehler")


def setup_logging(verbose: bool = False, quiet: bool = False) -> None:
    handler = logging.StreamHandler()  # stderr
    handler.setFormatter(_Formatter(FORMAT, datefmt="%H:%M:%S"))
    # Root auf WARNING: Fremdbibliotheken (trimesh, playwright, …) melden nur Warnungen/Fehler
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
    level = logging.DEBUG if verbose else logging.WARNING if quiet else logging.INFO
    logging.getLogger("vuse_xr").setLevel(level)
