"""Tests für eval_data.py mit Fake-Mapping (UUIDs) und Fake-Ground-Truth, ohne echte Assets."""
import json

import pytest

from vuse_xr.cap3d.eval_data import build_parts, iter_captions, missing, norm_name, twin_key

A = "data/raw/3d_assets_uuid/99_test.glb"


def gt_row(uuid: str, node: str, path: str, kind: str = "mesh") -> dict:
    return {"uuid": uuid, "asset": "99_test", "node": node, "path": path, "kind": kind,
            "n_meshes": "1", "parent_uuid": ""}


# 99_test (Wurzel r0)
# └── Base (u1, group)
#     ├── Plate (u2)
#     ├── Foot_1 (u3)
#     └── Foot_2_Hull (u4, Hülle) └── Foot_2 (u5)
GT = {r["uuid"]: r for r in [
    gt_row("r0", "99_test", "", kind="root"),
    gt_row("u1", "Base", "", kind="group"),
    gt_row("u2", "Plate", "Base"),
    gt_row("u3", "Foot_1", "Base"),
    gt_row("u4", "Foot_2_Hull", "Base", kind="group"),
    gt_row("u5", "Foot_2", "Base/Foot_2_Hull"),
]}


def entry(node: str, kind: str = "mesh", aliases: list[str] | None = None, **kw) -> dict:
    return {"asset": A, "node": node, "kind": kind, "path": "u1", "aliases": aliases or [],
            "size": [0.1, 0.1, 0.1], "rel_size": 0.1, **kw}


MAPPING = {
    "99_test__u1": entry("u1", kind="group", n_meshes=3),
    "99_test__u2": entry("u2"),
    "99_test__u3": entry("u3"),
    "99_test__u5": entry("u5", aliases=["u4"]),
}


def test_original_names_and_path():
    parts = build_parts(MAPPING, GT)
    p = parts["99_test__u2"]
    assert (p.node, p.names, p.path, p.asset, p.asset_label) == ("Plate", ("Plate",), "Base", "99_test", "test")


def test_hull_is_correct_name():
    p = build_parts(MAPPING, GT)["99_test__u5"]
    assert p.names == ("Foot_2", "Foot_2_Hull")
    assert p.path == "Base/Foot_2_Hull"


def test_group_keeps_n_meshes():
    p = build_parts(MAPPING, GT)["99_test__u1"]
    assert (p.kind, p.n_meshes, p.path) == ("group", 3, "")


def test_root_is_not_a_part():
    parts = build_parts({**MAPPING, "99_test__r0": entry("r0", kind="group")}, GT)
    assert "99_test__r0" not in parts and len(parts) == 4


def test_unknown_uuid_raises():
    with pytest.raises(KeyError, match="u9"):
        build_parts({"99_test__u9": entry("u9")}, GT)


def test_asset_mismatch_raises():
    with pytest.raises(ValueError, match="Asset"):
        build_parts({"98_other__u2": {**entry("u2"), "asset": "data/raw/3d_assets_uuid/98_other.glb"}}, GT)


def write_cap(root, model, variant, part_id, name="plate"):
    d = root / model / variant
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{part_id}.json").write_text(json.dumps({
        "part_id": part_id,
        "caption": {"name": name, "type": "t", "function": "f", "description": "d", "confidence": 0.5},
        "meta": {"model": model, "variant": variant},
    }))


def test_iter_captions_filters_and_skips(tmp_path):
    write_cap(tmp_path, "m1", "baseline", "99_test__u2")
    write_cap(tmp_path, "m1", "hier", "99_test__u2")
    write_cap(tmp_path, "m2", "baseline", "99_test__u3")
    (tmp_path / "m1" / "baseline" / "99_test__u3.json").write_text("{kaputt")
    assets = tmp_path / "m1" / "topdown" / "_assets"
    assets.mkdir(parents=True)
    (assets / "99_test.json").write_text(json.dumps({"caption": {"name": "x"}}))

    caps = list(iter_captions(tmp_path, models=["m1"], variants=["baseline"]))
    assert [(c.model, c.variant, c.part_id, c.caption["name"]) for c in caps] == [
        ("m1", "baseline", "99_test__u2", "plate")]
    assert len(list(iter_captions(tmp_path))) == 3  # kaputte Datei und _assets nicht dabei


def test_missing(tmp_path):
    write_cap(tmp_path, "m1", "baseline", "99_test__u2")
    assert missing(["99_test__u2", "99_test__u3"], tmp_path, "m1", "baseline") == ["99_test__u3"]


@pytest.mark.parametrize("raw, norm, twin", [
    ("Ratchet_Head", "ratchet head", "ratchet head"),
    ("RatchetHead.001", "ratchet head", "ratchet head"),
    ("Rubber_Foot_2", "rubber foot 2", "rubber foot"),
    ("Foot2", "foot 2", "foot"),
    ("M6_Screw", "m6 screw", "m6 screw"),
    ("PCBBoard", "pcb board", "pcb board"),
    ("Beaker_Graduation_0", "beaker graduation 0", "beaker graduation"),
])
def test_norm_and_twin(raw, norm, twin):
    assert norm_name(raw) == norm
    assert twin_key(raw) == twin
