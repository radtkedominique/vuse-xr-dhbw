"""Pipeline 2, Modus highlight – läuft IN Blender (3.4.1; getestet auch mit 4.2).

Pro Teil: ganzes Asset aus 8 Views, gesuchtes Teil eingefärbt, Rest grau/halbtransparent.
Kamera wie DiffuRank render_script_type1 (Kreis, leicht erhöht, Abstand 2.0, Szene auf Einheitsgröße).

Aufruf (normalerweise über python -m vuse_xr.cap3d.render):
  blender -b -P src/vuse_xr/cap3d/blender_highlight.py -- --jobs jobs.json --parent_dir data/interim/renders/highlight

jobs.json: [{"asset": "/abs/motor.glb", "parts": [{"part_id": "motor__mesh_13", "node": "mesh_13", "kind": "mesh"}, …]}, …]
"""
import argparse
import json
import math
import os
import re
import sys
import time

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ap = argparse.ArgumentParser()
ap.add_argument("--jobs", required=True)
ap.add_argument("--parent_dir", required=True)
ap.add_argument("--views", type=int, default=8)
ap.add_argument("--resolution", type=int, default=512)
ap.add_argument("--samples", type=int, default=16)
ap.add_argument("--context_alpha", type=float, default=0.15)
ap.add_argument("--color", type=float, nargs=3, default=[1.0, 0.35, 0.0])
args = ap.parse_args(argv)

scene = bpy.context.scene
BASE_OBJECTS = {o.name for o in bpy.data.objects if o.type in ("CAMERA", "LIGHT")}


# --------------------------------------------------------------------------- Szene
def clear_scene():
    for obj in list(bpy.data.objects):
        if obj.name not in BASE_OBJECTS:
            bpy.data.objects.remove(obj, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.cameras, bpy.data.lights):
        for block in list(coll):
            if block.users == 0:
                coll.remove(block)


def import_asset(path):
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    else:
        raise RuntimeError(f"Format nicht unterstützt: {ext}")
    imported = [o for o in bpy.data.objects if o not in before]
    # importierte Kameras/Lichter würden Beleuchtung/Kamera verfälschen
    for o in [o for o in imported if o.type in ("CAMERA", "LIGHT")]:
        bpy.data.objects.remove(o, do_unlink=True)
    return [o for o in bpy.data.objects if o not in before]


def bbox(objs):
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    for o in objs:
        if o.type != "MESH":
            continue
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            lo = [min(a, b) for a, b in zip(lo, w)]
            hi = [max(a, b) for a, b in zip(hi, w)]
    if lo[0] == math.inf:
        raise RuntimeError("keine Meshes im Asset")
    return Vector(lo), Vector(hi)


def normalize(objs):
    """Wie DiffuRank normalize_scene() inkl. VUSE-Patch: nur importierte Objekte, Skalierung um den Weltursprung."""
    roots = [o for o in objs if o.parent is None]
    lo, hi = bbox(objs)
    s = 1 / max(hi - lo)
    for o in roots:
        o.scale = o.scale * s
        o.location = o.location * s
    bpy.context.view_layer.update()
    lo, hi = bbox(objs)
    off = -(lo + hi) / 2
    for o in roots:
        o.matrix_world.translation += off
    bpy.context.view_layer.update()


# --------------------------------------------------------------------------- Materialien
def make_mat(name, color, alpha, emission):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (*color, 1.0)
    bsdf.inputs["Alpha"].default_value = alpha
    bsdf.inputs["Roughness"].default_value = 0.6
    if emission:
        em = bsdf.inputs.get("Emission Color") or bsdf.inputs.get("Emission")  # 4.x / 3.x
        em.default_value = (*color, 1.0)
        bsdf.inputs["Emission Strength"].default_value = emission
    if hasattr(mat, "blend_method"):
        mat.blend_method = "BLEND" if alpha < 1 else "OPAQUE"
    return mat


def set_material(obj, mat):
    """Material auf Objekt-Ebene setzen -> instanzierte Meshes (z. B. 20× dieselbe Schraube) bleiben getrennt."""
    if obj.type != "MESH":
        return
    if not obj.material_slots:
        obj.data.materials.append(None)
    for slot in obj.material_slots:
        slot.link = "OBJECT"
        slot.material = mat


def clean(name):
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def find_target(node, objs, kind="group"):
    """Knotenname aus glTF -> Blender-Objekt(e).

    mesh:  nur das Objekt selbst (Zollstock: Segment hat eigenes Mesh UND Folgesegmente als Kinder)
    group: alle Meshes darunter (Baugruppe, z. B. Lid_Assembly)
    """
    hit = [o for o in objs if o.name == node]
    if not hit:  # Pipeline 1 hat evtl. bereinigte Namen gespeichert, Blender kürzt auf 63 Zeichen
        hit = [o for o in objs if clean(o.name) == clean(node)[:63] or clean(o.name) == clean(node)]
    if not hit:
        return []
    root = hit[0]
    if kind == "mesh":
        return [root] if root.type == "MESH" else []
    return [o for o in [root, *root.children_recursive] if o.type == "MESH"]


# --------------------------------------------------------------------------- Render
def setup_render():
    scene.render.engine = "CYCLES"
    scene.cycles.samples = args.samples
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.transparent_max_bounces = 64  # viele halbtransparente Schichten übereinander
    scene.render.film_transparent = True
    scene.render.resolution_x = scene.render.resolution_y = args.resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.view_transform = "Standard"  # Farben nicht durch Filmic/AgX verwaschen
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get("Background")
    bg.inputs[0].default_value = (1, 1, 1, 1)  # gleichmäßiges Umgebungslicht, Hintergrund bleibt transparent
    bg.inputs[1].default_value = float(os.environ.get("RENDER_AMBIENT", "1.0"))
    if scene.camera is None:
        cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
        scene.collection.objects.link(cam)
        scene.camera = cam
        BASE_OBJECTS.add(cam.name)


def place_camera(i, n, dist=2.0, elevation=-0.2617993878):
    """DiffuRank pan_camera(axis='Z', 'z-circular-elevated'), aber t = i/n (keine doppelte View)."""
    a = (i / n) * 2 * math.pi
    d = Vector((-math.cos(a), -math.sin(a), elevation)).normalized()
    scene.camera.location = -dist * d
    scene.camera.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()


def main():
    jobs = json.load(open(args.jobs))
    setup_render()
    color = tuple(args.color)
    failed = []
    for job in jobs:
        asset = job["asset"]
        t0 = time.time()
        print(f"[highlight] Asset {os.path.basename(asset)}: {len(job['parts'])} Teile")
        clear_scene()
        objs = import_asset(asset)
        normalize(objs)
        meshes = [o for o in objs if o.type == "MESH"]
        mat_ctx = make_mat("ctx_grey", (0.6, 0.6, 0.6), args.context_alpha, 0)
        mat_hl = make_mat("highlight", color, 1.0, 0.3)
        for o in meshes:
            set_material(o, mat_ctx)
        a_lo, a_hi = bbox(meshes)

        for part in job["parts"]:
            pid, node = part["part_id"], part["node"]
            targets = find_target(node, objs, part.get("kind", "group"))
            if not targets:
                print(f"[highlight][FEHLER] Knoten '{node}' ({pid}) nicht im Asset gefunden")
                failed.append(pid)
                continue
            out = os.path.join(args.parent_dir, "Cap3D_imgs", pid)
            os.makedirs(out, exist_ok=True)
            for o in targets:
                set_material(o, mat_hl)
            try:
                for i in range(args.views):
                    place_camera(i, args.views)
                    scene.render.filepath = os.path.join(out, f"{i + 20:05}.png")
                    bpy.ops.render.render(write_still=True)
                t_lo, t_hi = bbox(targets)
                rel = max(t_hi - t_lo) / max(a_hi - a_lo)
                json.dump({"asset": asset, "node": node, "matched": [o.name for o in targets],
                           "rel_size": round(rel, 4)}, open(os.path.join(out, "meta.json"), "w"), indent=2)
                if rel < 0.05:
                    print(f"[highlight][warn] {pid}: Teil ist nur {rel:.1%} der Asset-Größe – im Bild evtl. kaum sichtbar")
            except Exception as e:  # ein kaputtes Teil soll nicht den ganzen Lauf abbrechen
                print(f"[highlight][FEHLER] {pid}: {e}")
                failed.append(pid)
            finally:
                for o in targets:
                    set_material(o, mat_ctx)
        print(f"[highlight] Asset fertig in {time.time() - t0:.0f} s")
    if failed:
        print(f"[highlight] {len(failed)} Teile fehlgeschlagen: {', '.join(failed)}")


main()
