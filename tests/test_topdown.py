"""Tests für Pipeline 3e (Top-down in caption.py) mit einem kleinen Fake-Mapping, ohne Renders und ohne Modell."""
from pathlib import Path

import pytest

from vuse_xr.cap3d import caption
from vuse_xr.cap3d.caption import asset_ref, build_input, context_lines, needs_asset_stage, topdown_order

A = "data/raw/3d_assets/99_test.glb"


def part(node: str, path: str, kind: str = "mesh") -> dict:
    return {"asset": A, "node": node, "kind": kind, "path": path, "aliases": []}


# 99_test
# ├── Base (group)
# │   ├── Plate
# │   └── Knob_Group (group)
# │       └── Knob_A (steckt in einer Hülle Knob_A_Hull)
# └── Lid (oberste Ebene)
MAPPING = {
    "99_test__Base": part("Base", "", "group"),
    "99_test__Plate": part("Plate", "Base"),
    "99_test__Knob_Group": part("Knob_Group", "Base", "group"),
    "99_test__Knob_A": part("Knob_A", "Base/Knob_Group/Knob_A_Hull"),
    "99_test__Lid": part("Lid", ""),
}
ASSET_CAP = {"name": "Electric Hotplate", "description": "A heating device with a control knob."}
PARENT_CAP = {"name": "control panel", "type": "panel", "function": "holds the controls",
              "description": "Front panel.", "confidence": 0.8}
CC = {"input_views": "part+highlight", "asset_name": "predicted", "parent_caption": True}


def test_topdown_order_parents_first():
    order = topdown_order(sorted(MAPPING), MAPPING)
    for child, parent in [("Plate", "Base"), ("Knob_Group", "Base"), ("Knob_A", "Knob_Group")]:
        assert order.index(f"99_test__{parent}") < order.index(f"99_test__{child}")


def test_needs_asset_stage():
    assert needs_asset_stage({"asset_name": "predicted"})
    assert needs_asset_stage({"asset_name": True, "parent_caption": True})
    assert not needs_asset_stage({"asset_name": True, "parent_caption": False})


def test_asset_ref_predicted_uses_stage1_name():
    assert asset_ref(MAPPING["99_test__Lid"], CC, ASSET_CAP) == "electric hotplate"
    with pytest.raises(ValueError):
        asset_ref(MAPPING["99_test__Lid"], CC, None)


def test_context_lines():
    assert context_lines(None, None) == []
    text = "\n".join(context_lines(ASSET_CAP, PARENT_CAP))
    assert "Electric Hotplate" in text and "control panel" in text and "holds the controls" in text


def test_prompt_has_context_but_no_node_names(monkeypatch):
    monkeypatch.setattr(caption, "grid_png", lambda view_dir, tile: b"png")
    prompt, images = build_input("99_test__Knob_A", MAPPING["99_test__Knob_A"], CC, {"part": Path("r"), "highlight": Path("r")},
                                 MAPPING, asset_cap=ASSET_CAP, parent_cap=PARENT_CAP)
    assert len(images) == 2
    assert "an electric hotplate" in prompt and "control panel" in prompt
    for name in ["99_test", "Knob", "Base", "Hull", "test"]:
        assert name not in prompt
