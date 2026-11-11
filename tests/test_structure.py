"""Tests für Pipeline 3d (structure.py) mit einem kleinen Fake-Mapping, ohne echte Dateien."""
from vuse_xr.cap3d.structure import (
    build_tree,
    describe,
    group_of,
    level,
    same_dims,
    shape,
)

A = "data/raw/3d_assets/99_test.glb"


def part(node: str, path: str, size: list[float], kind: str = "mesh", rel: float = 0.1, **kw) -> dict:
    return {"asset": A, "node": node, "kind": kind, "path": path, "aliases": [], "size": size, "rel_size": rel, **kw}


# 99_test
# ├── Base (group, 4 Meshes)
# │   ├── Plate
# │   ├── Foot_1 … Foot_3 (Foot_3 gedreht, Foot_2 steckt in einer Hülle Foot_2_Hull)
# │   └── Knob_Group (group, 2 Meshes)
# │       └── Knob_A, Knob_B
# └── Lid (oberste Ebene, ohne Baugruppe)
MAPPING = {
    "99_test__Base": part("Base", "", [0.3, 0.05, 0.2], kind="group", rel=1.0, n_meshes=6),
    "99_test__Plate": part("Plate", "Base", [0.3, 0.01, 0.2], rel=1.0),
    "99_test__Foot_1": part("Foot_1", "Base", [0.05, 0.024, 0.05], rel=0.08),
    "99_test__Foot_2": part("Foot_2", "Base/Foot_2_Hull", [0.05, 0.024, 0.05], rel=0.08),
    "99_test__Foot_3": part("Foot_3", "Base", [0.024, 0.05, 0.0505], rel=0.08),
    "99_test__Knob_Group": part("Knob_Group", "Base", [0.1, 0.03, 0.03], kind="group", rel=0.33, n_meshes=2),
    "99_test__Knob_A": part("Knob_A", "Base/Knob_Group", [0.03, 0.03, 0.02], rel=0.1),
    "99_test__Knob_B": part("Knob_B", "Base/Knob_Group", [0.03, 0.03, 0.02], rel=0.1),
    "99_test__Lid": part("Lid", "", [0.3, 0.02, 0.2], rel=1.0),
}
TREE = build_tree(MAPPING)


def test_group_of_skips_hull():
    assert group_of(MAPPING["99_test__Foot_2"], MAPPING) == "99_test__Base"
    assert group_of(MAPPING["99_test__Lid"], MAPPING) is None


def test_siblings_include_part_under_hull():
    assert sorted(TREE["children"]["99_test__Base"]) == [
        "99_test__Foot_1", "99_test__Foot_2", "99_test__Foot_3", "99_test__Knob_Group", "99_test__Plate"]
    assert sorted(TREE["children"]["ROOT:99_test"]) == ["99_test__Base", "99_test__Lid"]


def test_level_ignores_hulls():
    assert level("99_test__Lid", MAPPING) == 1
    assert level("99_test__Foot_2", MAPPING) == 2
    assert level("99_test__Knob_A", MAPPING) == 3
    assert TREE["max_level"]["99_test"] == 3


def test_same_dims_rotated_and_tolerance():
    assert same_dims([0.05, 0.024, 0.05], [0.024, 0.05, 0.05])
    assert same_dims([0.05, 0.024, 0.05], [0.024, 0.05, 0.0505])  # 1 % Abweichung
    assert not same_dims([0.05, 0.024, 0.05], [0.05, 0.03, 0.05])


def test_shape():
    assert shape([0.114, 0.007, 0.007]).startswith("elongated")
    assert shape([0.3, 0.01, 0.2]).startswith("flat")
    assert shape([0.05, 0.024, 0.05]).startswith("compact")
    assert shape([0.1, 0.0, 0.1]).startswith("flat")  # flaches Teil ohne Dicke
    assert shape([0.0, 0.0, 0.0]) == "unknown"


def test_describe_counts_identical_feet():
    text = describe("99_test__Foot_1", MAPPING, TREE)
    assert "Level 2 of 3" in text
    assert "Parent sub-assembly contains 5 parts; 3 of them (incl. this one) have identical dimensions." in text
    assert "Size: 8.0 % of the whole object" in text


def test_describe_top_level_and_group():
    text = describe("99_test__Lid", MAPPING, TREE)
    assert "The top level of the object contains 2 parts; no other part there has identical dimensions." in text
    assert "sub-assembly of 2 meshes" in describe("99_test__Knob_Group", MAPPING, TREE)


def test_describe_contains_no_names():
    for pid, e in MAPPING.items():
        text = describe(pid, MAPPING, TREE)
        names = {e["node"], *e["path"].split("/"), "Foot_2_Hull"} - {""}
        assert not any(n in text for n in names), (pid, text)
