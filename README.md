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
tests/
```

## Pipeline 2: Rendern mit three.js (`vuse_xr.cap3d.render`)

Gerendert wird mit three.js in headless Chromium (Playwright): Kamera im Kreis (leicht erhöht), Szene auf Einheitsgröße normalisiert, transparenter Hintergrund – wie bei Cap3D.

**Einmalig**
```bash
bash scripts/setup_threejs.sh   # three.js nach third_party/, Chromium für Playwright
```

**Rendern**
```bash
uv run python -m vuse_xr.cap3d.render --mode asset --limit 1   # erst ein Asset testen
uv run python -m vuse_xr.cap3d.render --mode asset             # alle Assets aus data/raw/3d_assets
uv run python -m vuse_xr.cap3d.render --mode part              # braucht Pipeline 1
uv run python -m vuse_xr.cap3d.render --mode highlight         # optional, braucht Pipeline 1
```
Optionen: `--only <text>`, `--limit N`, `--force`. Fertige IDs werden übersprungen.
Logging (beide Pipelines, auf stderr): `-v` = Debug inkl. Tracebacks und Browser-Konsole, `-q` = nur Warnungen/Fehler.
Output: `data/interim/renders_threejs/<modus>/Cap3D_imgs/<id>/00020.png … 00027.png`.
Pipeline 3 holt den Ordner über `render_root(cfg, mode)` aus `vuse_xr.cap3d.render`.
Beim Start steht im Log, welche GPU WebGL nutzt. Bei `SwiftShader` läuft es ohne GPU (langsamer); dann `render.threejs.headless: false` testen.
Licht und Materialien sind an das frühere Blender-Backend (Cycles 3.4) angeglichen: `energy: blender` (Glanz wird addiert), `ao: true` (Spalten/Innenräume verschattet, Screen-Space-AO).
Das Blender-Backend wurde entfernt; die Vergleichsbilder Blender vs. three.js liegen lokal in `data/interim/renders_compare/`.

**Pipeline 1 muss liefern:** `data/interim/uid_mapping.json` als
`{"<asset>__<knoten>": {"asset": "data/raw/3d_assets/x.glb", "node": "<originaler Knotenname>", "file": "data/interim/nodes/<asset>__<knoten>.glb"}}`
und `object_paths.pkl`. Dateinamen ohne Punkt (die Render-ID endet am ersten Punkt).

## Pipeline 1: Assets in Teile zerlegen (`vuse_xr.cap3d.split_nodes`)

```bash
uv run python -m vuse_xr.cap3d.split_nodes          # alle Assets, dauert wenige Sekunden
uv run python -m vuse_xr.cap3d.split_nodes --limit 1 --force  # nur ein Asset, erzwingt Überschreiben
uv run python -m vuse_xr.cap3d.split_nodes --only centrifuge   # nur Assets mit diesem Namensteil
```
Teile (`split.level`): `mesh` = Knoten mit eigenem Mesh, `group` = Baugruppen (z. B. `Lid_Assembly`), `all` = beides.
Hüllen mit nur einem Mesh darunter werden als `aliases` beim Mesh vermerkt, nicht doppelt exportiert.
Output: `data/interim/nodes/<asset>__<knoten>.glb` (Weltkoordinaten, Materialien erhalten), `object_paths.pkl`, `uid_mapping.json`.
