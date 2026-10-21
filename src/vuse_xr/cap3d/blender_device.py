"""Läuft IN Blender vor dem eigentlichen Renderskript (-P blender_device.py -P <script>).

Setzt das Cycles-Device aus der Umgebungsvariable RENDER_DEVICE (CPU | METAL | CUDA | OPTIX).
DiffuRank setzt selbst kein Device, die Szene-Einstellung bleibt daher erhalten.
"""
import os

import bpy

device = os.environ.get("RENDER_DEVICE", "CPU").upper()
if device != "CPU":
    prefs = bpy.context.preferences.addons["cycles"].preferences
    try:
        prefs.compute_device_type = device
        prefs.get_devices()
        for d in prefs.devices:
            d.use = True
        bpy.context.scene.cycles.device = "GPU"
        print(f"[render] Cycles-Device: {device} ({', '.join(d.name for d in prefs.devices if d.use)})")
    except TypeError:
        print(f"[render] Device {device} nicht verfügbar, rendere auf CPU")
