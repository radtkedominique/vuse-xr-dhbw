# vuse-xr-dhbw
DHBW student project with VUSE XR: AI-based grounding of natural language to 3D assets and scene-graph nodes.

## Setup
```bash
uv sync          # legt .venv an und installiert alle Dependencies
uv add <paket>   # neue Dependency hinzufügen
uv run <script>  # Skript in der venv ausführen
```

## Struktur
```
configs/         Konfigurationen (z. B. cap3d.yaml)
data/            lokal, nicht im Git
  raw/           Original-Assets (3d_assets.zip entpacken)
  interim/       Zwischenergebnisse (Renderings)
  processed/     Ergebnisse (Captions, Metadaten)
docs/research/   Recherche-Notizen (z. B. Cap3D)
notebooks/       Exploration
scripts/         Einstiegspunkte
src/vuse_xr/     Python-Package
  cap3d/         Cap3D-Pipeline: render -> caption -> summarize
  io/            Laden von 3D-Assets
tests/
```

## Pipeline 2: Rendern mit Blender oder three.js (`vuse_xr.cap3d.render`)

**Einmalig**
1. Blender **3.4.1** (macOS arm64) als `/Applications/Blender-3.4.app` installieren – neuere Versionen laufen nicht mit DiffuRank.
2. `bash scripts/setup_diffurank.sh` – klont DiffuRank nach `third_party/` und patcht es (OPTIX → OpenImageDenoise u. a.).

**Rendern**
```bash
uv run python -m vuse_xr.cap3d.render --mode asset --limit 1   # erst ein Asset testen
uv run python -m vuse_xr.cap3d.render --mode asset             # alle Assets aus data/raw/3d_assets
uv run python -m vuse_xr.cap3d.render --mode part              # braucht Pipeline 1
uv run python -m vuse_xr.cap3d.render --mode highlight         # optional, braucht Pipeline 1
```
Optionen: `--only <text>`, `--limit N`, `--force`. Fertige IDs werden übersprungen.
Output: `data/interim/renders/<modus>/Cap3D_imgs/<id>/00020.png … 00027.png`, Log: `data/interim/renders/<modus>/_blender.log`.

### Alternatives Backend: three.js (`render.backend: threejs`)
Gleiche Modi, Kamera, Normalisierung und Ordnerstruktur wie Blender, gerendert mit three.js in headless Chromium (Playwright). Kein Blender nötig.

```bash
bash scripts/setup_threejs.sh                                          # einmalig: three.js nach third_party/, Chromium für Playwright
uv run python -m vuse_xr.cap3d.render --mode asset --backend threejs   # oder render.backend: threejs in configs/cap3d.yaml
uv run python scripts/compare_renders.py --mode asset                  # Vergleichsbilder Blender oben / three.js unten
```
Output: `data/interim/renders_threejs/<modus>/…` (getrennt von Blender, damit beide nebeneinander liegen).
Pipeline 3 holt den Ordner über `render_root(cfg, mode)` aus `vuse_xr.cap3d.render`, dann folgt sie automatisch `render.backend`.
Beim Start steht im Log, welche GPU WebGL nutzt. Bei `SwiftShader` läuft es ohne GPU (langsamer); dann `render.threejs.headless: false` testen.
Abgleich mit Blender: `energy: blender` (Glanz wird addiert wie in Cycles 3.4), `ao: true` (Spalten/Innenräume verschattet, Screen-Space-AO). Restunterschied: Metall spiegelt nur die weiße Umgebung, nicht die Szene; keine `*_depth.png`/`*.json` pro View.

**Pipeline 1 muss liefern:** `data/interim/uid_mapping.json` als
`{"<asset>__<knoten>": {"asset": "data/raw/3d_assets/x.glb", "node": "<originaler Knotenname>", "file": "data/interim/nodes/<asset>__<knoten>.glb"}}`
und `object_paths.pkl`. Dateinamen ohne Punkt (DiffuRank schneidet dort ab).

## Pipeline 1: Assets in Teile zerlegen (`vuse_xr.cap3d.split_nodes`)

```bash
uv run python -m vuse_xr.cap3d.split_nodes          # alle Assets, dauert wenige Sekunden
uv run python -m vuse_xr.cap3d.split_nodes --only centrifuge --force
```
Teile (`split.level`): `mesh` = Knoten mit eigenem Mesh, `group` = Baugruppen (z. B. `Lid_Assembly`), `all` = beides.
Hüllen mit nur einem Mesh darunter werden als `aliases` beim Mesh vermerkt, nicht doppelt exportiert.
Output: `data/interim/nodes/<asset>__<knoten>.glb` (Weltkoordinaten, Materialien erhalten), `object_paths.pkl`, `uid_mapping.json`.
