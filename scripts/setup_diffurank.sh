#!/usr/bin/env bash
# Pipeline 2 – Setup: DiffuRank-Renderskript holen und für macOS patchen.
# Aufruf aus dem Repo-Root:  bash scripts/setup_diffurank.sh
set -euo pipefail

REPO_URL="https://github.com/tiangeluo/DiffuRank.git"
COMMIT="cd15dcdade7e81f9024ca9e20853a0ef2675a753"   # Stand 09/2024, gepinnt
TARGET="third_party/DiffuRank"
SCRIPT="$TARGET/render_script_type1.py"

if [ ! -d "$TARGET/.git" ]; then
  git clone "$REPO_URL" "$TARGET"
fi
git -C "$TARGET" checkout -q "$COMMIT"
git -C "$TARGET" checkout -q -- render_script_type1.py   # Patch idempotent: immer vom Original aus

# 1) OPTIX gibt es nur auf NVIDIA -> OpenImageDenoise (läuft auf CPU, auch Apple Silicon)
perl -pi -e "s/'OPTIX'/'OPENIMAGEDENOISE'/g" "$SCRIPT"

# 2) PIL wird importiert, aber nie benutzt. Blenders Python hat kein Pillow -> Import entfernen
perl -pi -e 's/^from PIL import Image$/# from PIL import Image  # (VUSE-Patch: ungenutzt, fehlt in Blender-Python)/' "$SCRIPT"

# 3) Bug im Original: t = i/(n-1) legt Kamera 0 (0°) und Kamera 7 (360°) auf dieselbe Position
#    -> nur 7 verschiedene Views. Fix: t = i/n -> 8 Views im 45°-Abstand.
perl -pi -e 's|t = i / max\(num_images - 1, 1\).*$|t = i / num_images  # VUSE-Patch: 8 verschiedene Views statt 7 + Duplikat|' "$SCRIPT"

# 4) Bug im Original: normalize_scene() skaliert jedes Wurzelobjekt um seinen EIGENEN Ursprung.
#    Bei Baugruppen mit mehreren Wurzelknoten (CAD-Export) fällt die Anordnung auseinander.
#    Fix: Position mitskalieren (= Skalierung um den Weltursprung).
perl -pi -e 's/^(\s+)obj\.scale = obj\.scale \* scale$/$1obj.scale = obj.scale * scale\n$1obj.location = obj.location * scale  # VUSE-Patch: Baugruppe bleibt zusammen/' "$SCRIPT"

# 5) Kamera und Licht der Startszene sind auch Wurzelobjekte und wurden pro Asset mit verschoben
#    (Licht wandert über viele Assets weg). Fix: nur Modell-Objekte normalisieren.
perl -pi -e 's/^(\s+)if not obj\.parent:$/$1if not obj.parent and obj.type not in ("CAMERA", "LIGHT"):  # VUSE-Patch/' "$SCRIPT"

# 6) Belichtung: Im finalen Render ist die Welt schwarz -> nur ein Punktlicht, Rückseiten fast schwarz,
#    Metall spiegelt Schwarz. Fix: weißes Umgebungslicht (Hintergrund im Bild bleibt transparent).
#    Stärke über env RENDER_AMBIENT (kommt aus configs/cap3d.yaml -> render.ambient).
perl -pi -e 's/^(\s+)bpy\.context\.scene\.world\.node_tree\.nodes\["Background"\]\.inputs\[0\]\.default_value = \(0, 0, 0, 0\)$/$1bpy.context.scene.world.node_tree.nodes["Background"].inputs[0].default_value = (1, 1, 1, 1)  # VUSE-Patch: Umgebungslicht\n$1bpy.context.scene.world.node_tree.nodes["Background"].inputs[1].default_value = float(os.environ.get("RENDER_AMBIENT", "1.0"))/' "$SCRIPT"

echo "Patch-Check:"
grep -q "obj.location = obj.location \* scale" "$SCRIPT" && echo "  Normalisierung um Weltursprung"
grep -q 'obj.type not in ("CAMERA", "LIGHT")' "$SCRIPT" && echo "  Kamera/Licht nicht mehr normalisiert"
grep -c "OPENIMAGEDENOISE" "$SCRIPT" | xargs echo "  OPENIMAGEDENOISE-Stellen (erwartet 2):"
grep -q "^# from PIL import Image" "$SCRIPT" && echo "  PIL-Import entfernt"
grep -q "t = i / num_images" "$SCRIPT" && echo "  View-Duplikat behoben"
grep -q "RENDER_AMBIENT" "$SCRIPT" && echo "  Umgebungslicht aktiv"
