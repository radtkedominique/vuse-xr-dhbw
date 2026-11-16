"""Tests für Pipeline 3h (documents.py) mit Dateien in tmp_path, ohne echte Dokumente."""
from pathlib import Path

from vuse_xr.cap3d.documents import document_for, find_document


def write_pdf(path: Path, text: str) -> None:
    """Minimale PDF mit einer Textzeile (Helvetica), damit der PDF-Pfad ohne Fixture-Datei testbar ist."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
         b"/Resources << /Font << /F1 5 0 R >> >> >>"),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(bytes(out))


def test_found_by_asset_name(tmp_path):
    (tmp_path / "04_magnetic_stirrer.txt").write_text("Manual")
    assert find_document(tmp_path, "04_magnetic_stirrer").name == "04_magnetic_stirrer.txt"
    assert find_document(tmp_path, "04_torque_wrench") is None


def test_missing_document_is_none(tmp_path):
    assert document_for("04_torque_wrench", tmp_path) is None


def test_priority_when_several(tmp_path):
    (tmp_path / "a.txt").write_text("txt")
    (tmp_path / "a.md").write_text("md")
    assert document_for("a", tmp_path)["text"] == "md"


def test_empty_file_is_none(tmp_path):
    (tmp_path / "a.txt").write_text("  \n ")
    assert document_for("a", tmp_path) is None


def test_truncated(tmp_path):
    (tmp_path / "a.txt").write_text("x" * 500)
    d = document_for("a", tmp_path, max_chars=100)
    assert (d["chars"], d["chars_total"], d["truncated"]) == (100, 500, True)


def test_short_document_complete(tmp_path):
    (tmp_path / "a.txt").write_text("Tighten the clamping screw.")
    d = document_for("a", tmp_path, max_chars=100)
    assert (d["text"], d["truncated"], d["file"]) == ("Tighten the clamping screw.", False, "a.txt")


def test_whitespace_normalized(tmp_path):
    (tmp_path / "a.md").write_text("Step   1\n\n\n\nStep 2")
    assert document_for("a", tmp_path)["text"] == "Step 1\n\nStep 2"


def test_pdf(tmp_path):
    write_pdf(tmp_path / "a.pdf", "Remove the stir bar from the beaker")
    d = document_for("a", tmp_path)
    assert d["file"] == "a.pdf"
    assert "stir bar" in d["text"]
