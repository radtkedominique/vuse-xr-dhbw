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

## Pipeline 2: Rendern mit Blender (`vuse_xr.cap3d.render`)

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

**Pipeline 1 muss liefern:** `data/interim/uid_mapping.json` als
`{"<asset>__<knoten>": {"asset": "data/raw/3d_assets/x.glb", "node": "<originaler Knotenname>", "file": "data/interim/nodes/<asset>__<knoten>.glb"}}`
und `object_paths.pkl`. Dateinamen ohne Punkt (DiffuRank schneidet dort ab).
