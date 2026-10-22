"""Pipeline 2, Backend threejs: gleiche Modi und gleicher Output wie Blender, gerendert mit three.js.

Wird von render.py aufgerufen (render.backend: threejs oder --backend threejs), nicht direkt.
three.js läuft in headless Chromium (Playwright). Die Seite (threejs/render.html + render.js) und alle
Dateien werden über page.route() aus dem Repo ausgeliefert, es läuft kein Server.

Einmalig: bash scripts/setup_threejs.sh  (three.js nach third_party/three, Chromium für Playwright)
"""
from __future__ import annotations

import base64
import io
import json
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
APP_DIR = HERE / "threejs"
ORIGIN = "http://vuse.local"
MIME = {
    ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".json": "application/json",
    ".wasm": "application/wasm", ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
    ".bin": "application/octet-stream", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".ktx2": "image/ktx2",
}
SOFTWARE_GPU = ("swiftshader", "llvmpipe", "software")


def file_url(path: Path) -> str:
    return f"{ORIGIN}/f" + urllib.parse.quote(str(Path(path).resolve()))


class ThreeRenderer:
    """Ein Browser für den ganzen Lauf; pro Asset wird nur das GLB neu geladen."""

    def __init__(self, cfg: dict, mode: str):
        r = cfg["render"]
        self.t = r.get("threejs", {})
        self.three_dir = ROOT / self.t.get("three_dir", "third_party/three")
        if not (self.three_dir / "build" / "three.module.js").exists():
            raise SystemExit(f"{self.three_dir} fehlt – erst 'bash scripts/setup_threejs.sh' ausführen.")
        self.resolution = int(r.get("resolution", 512))
        self.n_views = int(r.get("n_views", 8))
        self.ss = int(self.t.get("supersample", 1))
        colorspace = self.t.get("colorspace", "auto")
        if colorspace == "auto":  # wie das Blender-Gegenstück: DiffuRank speichert "Raw" (linear), blender_highlight.py sRGB
            colorspace = "srgb" if mode == "highlight" else "linear"
        self.opts = {
            "resolution": self.resolution, "supersample": self.ss,
            "ambient": float(r.get("ambient", 1.0)), "point_light": float(self.t.get("point_light", 79.6)),
            "shadows": bool(self.t.get("shadows", True)), "colorspace": colorspace,
            "energy": self.t.get("energy", "blender"),
            # AO nicht im highlight-Modus: die halbtransparenten Kontextteile würden als massiv verschattet
            "ao": bool(self.t.get("ao", True)) and mode != "highlight",
            "ao_radius": float(self.t.get("ao_radius", 0.25)), "ao_intensity": float(self.t.get("ao_intensity", 1.0)),
        }

    # ------------------------------------------------------------------ Browser
    def _serve(self, route) -> None:
        path = urllib.parse.unquote(urllib.parse.urlparse(route.request.url).path)
        if path == "/":
            f = APP_DIR / "render.html"
        elif path.startswith("/app/"):
            f = APP_DIR / path[len("/app/"):]
        elif path.startswith("/three/"):
            f = self.three_dir / path[len("/three/"):]
        elif path.startswith("/f/"):
            f = Path(path[len("/f"):])  # absoluter Pfad zum Asset (+ evtl. .bin/Texturen daneben)
        else:
            f = None
        if f is None or not f.is_file():
            route.fulfill(status=404, body=f"nicht gefunden: {path}")
            return
        route.fulfill(status=200, body=f.read_bytes(), content_type=MIME.get(f.suffix.lower(), "application/octet-stream"))

    @staticmethod
    def _console(msg) -> None:
        if msg.type == "error" or (msg.type == "warning" and "GL Driver Message" not in msg.text
                                   and "software WebGL" not in msg.text):
            print(f"[browser] {msg.text}")

    def __enter__(self) -> "ThreeRenderer":
        from playwright.sync_api import sync_playwright  # erst hier, damit das Blender-Backend ohne Playwright läuft

        self._pw = sync_playwright().start()
        launch = {"headless": bool(self.t.get("headless", True)), "args": list(self.t.get("chromium_args") or [])}
        if self.t.get("channel"):
            launch["channel"] = self.t["channel"]
        self._browser = self._pw.chromium.launch(**launch)
        self.page = self._browser.new_page(viewport={"width": 64, "height": 64})
        self.page.route(f"{ORIGIN}/**", self._serve)
        self.page.on("console", self._console)
        self.page.on("pageerror", lambda e: print(f"[browser][FEHLER] {e}"))
        self.page.goto(f"{ORIGIN}/")
        self.page.wait_for_function("window.vuseReady === true", timeout=30_000)
        info = self.page.evaluate("o => vuseInit(o)", self.opts)
        print(f"[threejs] three r{info['three']}, WebGL-Renderer: {info['gpu']}")
        if any(s in info["gpu"].lower() for s in SOFTWARE_GPU):
            print("[threejs][warn] Software-Rendering (keine GPU) – langsam. Siehe render.threejs.headless/channel/chromium_args.")
        return self

    def __exit__(self, *exc) -> None:
        self._browser.close()
        self._pw.stop()

    # ------------------------------------------------------------------ Rendern
    def load(self, asset: Path) -> None:
        self.page.evaluate("u => vuseLoad(u)", file_url(asset))

    def save(self, views: list[str], out_dir: Path) -> None:
        from PIL import Image

        out_dir.mkdir(parents=True, exist_ok=True)
        for i, data_url in enumerate(views):
            png = base64.b64decode(data_url.split(",", 1)[1])
            target = out_dir / f"{i + 20:05}.png"
            if self.ss > 1:  # Supersampling: groß rendern, sauber verkleinern (Pillow rechnet RGBA premultiplied)
                Image.open(io.BytesIO(png)).resize((self.resolution, self.resolution), Image.LANCZOS).save(target)
            else:
                target.write_bytes(png)


def render_paths(paths: list[Path], out_root: Path, cfg: dict, mode: str = "asset") -> None:
    """Modi asset und part: ganzes GLB aus n Views, Ordnername = Render-ID wie bei DiffuRank."""
    t_all, n_done = time.time(), 0
    with ThreeRenderer(cfg, mode) as r:
        for k, p in enumerate(paths, 1):
            rid = Path(p).name.split(".")[0]
            t0 = time.time()
            try:
                r.load(p)
                r.save(r.page.evaluate("n => vuseRenderViews(n)", r.n_views), out_root / "Cap3D_imgs" / rid)
                n_done += 1
                print(f"[threejs] {k}/{len(paths)} {rid}: {time.time() - t0:.1f} s")
            except Exception as e:  # ein kaputtes Asset soll nicht den ganzen Lauf abbrechen
                print(f"[threejs][FEHLER] {rid}: {e}")
        _timing(t_all, n_done * r.n_views)


def render_highlight(job_list: list[dict], out_root: Path, cfg: dict) -> None:
    """Modus highlight: ganzes Asset, gesuchtes Teil eingefärbt, Rest grau/halbtransparent."""
    hl = cfg["render"]["highlight"]
    setup = {"context_alpha": float(hl["context_alpha"]), "highlight_color": [float(c) for c in hl["highlight_color"]]}
    t_all, n_done, failed = time.time(), 0, []
    with ThreeRenderer(cfg, "highlight") as r:
        for job in job_list:
            t0 = time.time()
            print(f"[threejs] Asset {Path(job['asset']).name}: {len(job['parts'])} Teile")
            try:
                r.load(Path(job["asset"]))
                r.page.evaluate("o => vuseHighlightSetup(o)", setup)
            except Exception as e:
                print(f"[threejs][FEHLER] {job['asset']}: {e}")
                failed += [p["part_id"] for p in job["parts"]]
                continue
            for part in job["parts"]:
                pid, node = part["part_id"], part["node"]
                res = r.page.evaluate("([n, k, v]) => vuseRenderPart(n, k, v)", [node, part.get("kind", "group"), r.n_views])
                if "error" in res:
                    print(f"[threejs][FEHLER] {pid}: {res['error']}")
                    failed.append(pid)
                    continue
                out = out_root / "Cap3D_imgs" / pid
                r.save(res["views"], out)
                rel = res["rel_size"]
                (out / "meta.json").write_text(json.dumps(
                    {"asset": job["asset"], "node": node, "matched": res["matched"], "rel_size": round(rel, 4),
                     "backend": "threejs"}, indent=2))
                if rel < 0.05:
                    print(f"[threejs][warn] {pid}: Teil ist nur {rel:.1%} der Asset-Größe – im Bild evtl. kaum sichtbar")
                n_done += 1
            print(f"[threejs] Asset fertig in {time.time() - t0:.0f} s")
        _timing(t_all, n_done * r.n_views)
    if failed:
        print(f"[threejs] {len(failed)} Teile fehlgeschlagen: {', '.join(failed[:20])}" + (" …" if len(failed) > 20 else ""))


def _timing(t0: float, n_views: int) -> None:
    dt = time.time() - t0
    if n_views:
        print(f"[threejs] {n_views} Views in {dt:.0f} s ({1000 * dt / n_views:.0f} ms pro View inkl. Laden)")
