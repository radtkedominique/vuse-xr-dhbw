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
