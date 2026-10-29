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
configs/         Konfigurationen: cap3d.yaml (Originalnamen), cap3d_uuid.yaml (UUID-Testset)
data/            lokal, nicht im Git
  raw/           Original-Assets (3d_assets.zip entpacken)
    3d_assets_uuid/  Testset mit UUID-Knotennamen + ground_truth.csv (Pipeline 4a)
  interim/       Zwischenergebnisse (Renderings)
    uuid/          dasselbe für das UUID-Testset (cap3d_uuid.yaml)
  processed/     Ergebnisse (Captions, Metadaten)
docs/research/   Recherche-Notizen (z. B. Cap3D)
docs/pipeline3.md  Pipeline 3: Vorversuche, Modellvergleich, Kosten
notebooks/       Exploration
scripts/         Einstiegspunkte (u. a. make_uuid_testset.py)
src/vuse_xr/     Python-Package
  cap3d/         Cap3D-Pipeline: render -> caption -> summarize
tests/
```

## Pipeline 1: Assets in Teile zerlegen (`vuse_xr.cap3d.split_nodes`)

```bash
uv run python -m vuse_xr.cap3d.split_nodes          # alle Assets, dauert wenige Sekunden
uv run python -m vuse_xr.cap3d.split_nodes --limit 1 --force  # nur ein Asset, erzwingt Überschreiben
uv run python -m vuse_xr.cap3d.split_nodes --only centrifuge   # nur Assets mit diesem Namensteil
```
Teile (`split.level`): `mesh` = Knoten mit eigenem Mesh, `group` = Baugruppen (z. B. `Lid_Assembly`), `all` = beides.
Hüllen mit nur einem Mesh darunter werden als `aliases` beim Mesh vermerkt, nicht doppelt exportiert.
Output: `data/interim/nodes/<asset>__<knoten>.glb` (Weltkoordinaten, Materialien erhalten), `object_paths.pkl`, `uid_mapping.json`.

**Pipeline 1 muss liefern:** `data/interim/uid_mapping.json` als
`{"<asset>__<knoten>": {"asset": "data/raw/3d_assets/x.glb", "node": "<originaler Knotenname>", "file": "data/interim/nodes/<asset>__<knoten>.glb"}}`
und `object_paths.pkl`. Dateinamen ohne Punkt (die Render-ID endet am ersten Punkt).

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

## Pipeline 3: Teile beschreiben (`vuse_xr.cap3d.caption`)

Ein Vision-Modell bekommt pro Teil die 8 Views als Rasterbild (Teil isoliert + Teil im Gesamtmodell hervorgehoben)
und liefert JSON mit `name`, `type`, `function`, `description`, `confidence`. Das Modell sieht nur Bilder und den
Asset-Typ, nie den Knotennamen. Vorversuche, Modellvergleich und Kosten: [docs/pipeline3.md](docs/pipeline3.md).

**Einmalig (Gemini, Standard)**
```bash
cp .env.example .env    # dann GEMINI_API_KEY eintragen (kostenlos: aistudio.google.com -> "Get API key")
```
`.env` steht in `.gitignore` – den Key nie committen.

**Optional lokal (Ollama)**
```bash
brew install ollama && brew services start ollama
ollama pull qwen2.5vl:7b   # ~6 GB
```

**Beschreiben** (braucht Pipeline 1 und Pipeline 2 `--mode part` + `--mode highlight`)
```bash
uv run python -m vuse_xr.cap3d.caption --only torque_wrench --limit 2       # erst testen
uv run python -m vuse_xr.cap3d.caption                                      # alle Teile mit Renders
uv run python -m vuse_xr.cap3d.caption --backend ollama --model qwen2.5vl:7b # lokal
uv run python -m vuse_xr.cap3d.caption --variant noname --only torque_wrench # andere Variante
```
Optionen: `--only <text>`, `--limit N`, `--force`, `--backend`, `--model`, `--variant`, `-v`/`-q`. Fertige Teile werden übersprungen;
bei Überlastung (HTTP 503) wird automatisch neu versucht. Beim Tageslimit (Gemini-Gratis-Tarif: 20 Anfragen pro Tag
und Modell) bricht der Lauf ab – am nächsten Tag einfach neu starten.
Einstellungen in `configs/cap3d.yaml` unter `caption:` (Backend, Modell, `input_views`, Rastergröße).

**Varianten:** Verschiedene Herangehensweisen stehen als Einträge unter `caption.variants`. Jede Variante überschreibt
nur einzelne `caption:`-Einstellungen; ohne `--variant` gilt `caption.variant` (Standard `baseline`).
```yaml
variants:
  baseline: { input_views: part+highlight, asset_name: true }   # Asset-Typ im Prompt ("torque wrench")
  noname:   { input_views: part+highlight, asset_name: false }  # neutral ("object")
```
Prompt und Bilder baut `build_input()` in `caption.py` – neue Ideen kommen dort als Baustein dazu.
Varianten immer in `cap3d.yaml` und `cap3d_uuid.yaml` gleich halten.

Output: `data/processed/cap3d/captions/<modell>/<variante>/<part_id>.json` – ein Ordner pro Modell und Variante, damit
man vergleichen kann. In `meta` stehen zusätzlich `variant`, `variant_cfg` und der komplette `prompt`.

## Pipeline 4a: Testset mit UUID-Knotennamen (`scripts/make_uuid_testset.py`)

Kopiert alle Assets aus `data/raw/3d_assets/` nach `data/raw/3d_assets_uuid/` und ersetzt jeden Knotennamen durch eine UUID. Die Originalnamen landen in `ground_truth.csv` und dienen Pipeline 4 als Ground Truth.

```bash
uv run python scripts/make_uuid_testset.py           # Testset erzeugen + prüfen
uv run python scripts/make_uuid_testset.py --check   # nur prüfen
```
Optionen: `--src`, `--dst`, `-v`/`-q`.
Entfernt werden: Knotennamen (auch Wurzel), Mesh-Namen, alle `extras`. Materialnamen bleiben.
Nur der JSON-Chunk wird geändert, der BIN-Chunk wird 1:1 übernommen (Geometrie garantiert identisch).
UUIDs sind deterministisch (`uuid5` aus Asset + Knotenpfad). Den Namespace `NS` im Skript nie ändern, sonst passen alte Ergebnisse nicht mehr zur Ground Truth.
`ground_truth.csv`: `uuid, asset, node, path, kind (mesh|group|root), n_meshes, parent_uuid`. `group` mit `n_meshes = 1` = Hülle (in Pipeline 1 ein Alias).

### Pipeline 1–3 auf dem Testset laufen lassen

Für die Evaluation (Pipeline 4b) laufen Pipeline 1–3 auf `3d_assets_uuid` statt auf den Originalen. Dafür gibt es `configs/cap3d_uuid.yaml`: gleiche Einstellungen wie `cap3d.yaml`, aber eigener Input und eigene Output-Ordner (`data/interim/uuid/…`, `data/processed/uuid/…`). So mischen sich Teile mit Originalnamen und UUID-Teile nicht.

```bash
uv run python -m vuse_xr.cap3d.split_nodes --config configs/cap3d_uuid.yaml
uv run python -m vuse_xr.cap3d.render --mode part --config configs/cap3d_uuid.yaml --limit 1
uv run python -m vuse_xr.cap3d.render --mode part --config configs/cap3d_uuid.yaml
```
`--config` bei jedem Aufruf mitgeben, sonst wird `cap3d.yaml` genommen (Originalnamen).
Änderungen an Render-Einstellungen in `cap3d.yaml` auch in `cap3d_uuid.yaml` nachziehen.
Verknüpfung zur Ground Truth: `part_id` → `uid_mapping[part_id]["node"]` (UUID) → `ground_truth.csv.uuid` → `node` (echter Name).
