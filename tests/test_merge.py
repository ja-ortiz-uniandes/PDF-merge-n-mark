"""Page ordering, ToC numbering, links and input handling."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest
from pypdf import PdfReader

import merge_pdf
from conftest import link_targets, page_texts, titles, outline_tree


def test_pre_toc_flag_merges_the_file(tmp_path: Path, make_pdf: Callable[..., Path]):
    """--pre-toc used to mark a path without ever merging it, so the file was
    silently dropped from the output."""
    pre = make_pdf("intro", pages=2)
    a = make_pdf("A", pages=3)
    b = make_pdf("B", pages=1)
    out = tmp_path / "merged.pdf"

    merge_pdf.main(
        ["-o", str(out), "--toc", "--pre-toc", str(pre), str(a), str(b)]
    )

    reader = PdfReader(str(out))
    # 2 intro + 1 ToC + 3 A + 1 B
    assert len(reader.pages) == 7
    texts = page_texts(reader)
    assert "intro page 1" in texts[0]
    assert "Table of Contents" in texts[2]
    assert "A page 1" in texts[3]


def test_pre_toc_file_is_labelled_but_not_listed(
    tmp_path: Path, make_pdf: Callable[..., Path]
):
    pre = make_pdf("intro", pages=1)
    a = make_pdf("A", pages=1)
    b = make_pdf("B", pages=1)
    out = tmp_path / "merged.pdf"

    merge_pdf.main(
        ["-o", str(out), "--toc", "--pre-toc", str(pre), str(a), str(b)]
    )

    reader = PdfReader(str(out))
    # Present in the outline...
    assert titles(outline_tree(reader)) == ["intro", "A", "B"]
    # ...but not in the ToC, so only A and B are linked.
    assert len(link_targets(reader, 1)) == 2


def test_toc_page_numbers_and_links_match_real_pages(
    tmp_path: Path, make_pdf: Callable[..., Path]
):
    pre = make_pdf("intro", pages=2)
    a = make_pdf("A", pages=3)
    b = make_pdf("B", pages=2)
    out = tmp_path / "merged.pdf"

    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "output": str(out),
                "overwrite": True,
                "toc": True,
                "files": [
                    {"file": str(pre), "label": "Intro", "pre_toc": True, "toc": True},
                    {"file": str(a), "label": "Section A"},
                    {"file": str(b), "label": "Section B"},
                ],
            }
        ),
        encoding="utf-8",
    )

    merge_pdf.main(["-m", str(manifest)])

    reader = PdfReader(str(out))
    # intro(2) + ToC(1) + A(3) + B(2)
    assert len(reader.pages) == 8
    toc_index = 2
    toc_text = page_texts(reader)[toc_index]
    assert "Intro" in toc_text and "Section A" in toc_text and "Section B" in toc_text

    # Each clickable row targets the 0-based start page of its entry, which is
    # one less than the 1-based number printed on that row.
    assert link_targets(reader, toc_index) == [0, 3, 6]


def test_collect_toc_entries_numbers_pages(
    tmp_path: Path, make_pdf: Callable[..., Path]
):
    """Printed page numbers: pre-ToC items count from 1, everything else is
    offset by the pre-ToC pages plus the ToC page itself."""
    pre = make_pdf("intro", pages=2)
    a = make_pdf("A", pages=3)
    b = make_pdf("B", pages=2)

    def item(path: Path, label: str, pre_toc: bool, toc: bool):
        return {
            "path": path,
            "label": label,
            "pre_toc": pre_toc,
            "toc": toc,
            "outline": True,
            "toc_explicit": False,
        }

    pre_items = [item(pre, "Intro", True, True)]
    normals = [item(a, "Section A", False, True), item(b, "Section B", False, True)]

    entries = merge_pdf._collect_toc_entries(
        pre_items, normals, lambda it: merge_pdf._open_reader(it["path"])
    )
    assert entries == [("Intro", 1), ("Section A", 4), ("Section B", 7)]


def test_toc_entry_excluded_by_manifest_is_not_linked(
    tmp_path: Path, make_pdf: Callable[..., Path]
):
    a = make_pdf("A", pages=1)
    b = make_pdf("B", pages=1)
    c = make_pdf("C", pages=1)
    out = tmp_path / "merged.pdf"
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "output": str(out),
                "toc": True,
                "files": [
                    {"file": str(a)},
                    {"file": str(b), "toc": False},
                    {"file": str(c)},
                ],
            }
        ),
        encoding="utf-8",
    )

    merge_pdf.main(["-m", str(manifest)])

    reader = PdfReader(str(out))
    toc_text = page_texts(reader)[0]
    assert "B" not in toc_text
    # ToC page 0, then A(1), B(2), C(3)
    assert link_targets(reader, 0) == [1, 3]


def test_pre_toc_flag_marks_a_manifest_entry_without_duplicating_it(
    tmp_path: Path, make_pdf: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
):
    a = make_pdf("A", pages=1)
    b = make_pdf("B", pages=1)
    out = tmp_path / "merged.pdf"
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "output": str(out),
                "toc": True,
                "files": [
                    {"file": "A.pdf", "label": "Section A"},
                    {"file": "B.pdf", "label": "Section B"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    merge_pdf.main(["-m", str(manifest), "--pre-toc", "A.pdf"])

    reader = PdfReader(str(out))
    # A is merged once, before the ToC, and keeps its manifest label
    assert len(reader.pages) == 3
    assert titles(outline_tree(reader)) == ["Section A", "Section B"]
    assert "A page 1" in page_texts(reader)[0]
    assert "Table of Contents" in page_texts(reader)[1]
    # Promoted to pre-ToC, so it drops out of the ToC listing; only B is linked
    assert b.exists()
    assert link_targets(reader, 1) == [2]


def test_encrypted_input_raises(tmp_path: Path, make_pdf: Callable[..., Path]):
    """decrypt() reports failure by return value, not by raising."""
    locked = make_pdf("locked", pages=1, password="secret")
    plain = make_pdf("plain", pages=1)
    out = tmp_path / "merged.pdf"

    with pytest.raises(ValueError, match="Encrypted PDF requires a password"):
        merge_pdf.main(["-o", str(out), str(locked), str(plain)])


def test_missing_input_raises(tmp_path: Path, make_pdf: Callable[..., Path]):
    a = make_pdf("A", pages=1)
    out = tmp_path / "merged.pdf"

    with pytest.raises(FileNotFoundError):
        merge_pdf.main(["-o", str(out), str(a), str(tmp_path / "nope.pdf")])


def test_per_file_toc_object_is_rejected(
    tmp_path: Path, make_pdf: Callable[..., Path]
):
    a = make_pdf("A", pages=1)
    b = make_pdf("B", pages=1)
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "output": str(tmp_path / "merged.pdf"),
                "files": [
                    {"file": str(a), "toc": {"outline": True}},
                    {"file": str(b)},
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Per-file 'toc' must be a boolean"):
        merge_pdf.main(["-m", str(manifest)])


def test_toc_overflow_is_reported(
    tmp_path: Path, make_pdf: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
):
    """Entries past the bottom of the single ToC page used to vanish silently."""
    monkeypatch.setattr(merge_pdf, "TOC_LINE_GAP", 150.0)
    capacity = merge_pdf._toc_capacity()
    files = [str(make_pdf(f"F{i}", pages=1)) for i in range(capacity + 1)]
    out = tmp_path / "merged.pdf"

    with pytest.raises(ValueError, match="does not fit on one page"):
        merge_pdf.main(["-o", str(out), "--toc", *files])


def test_toc_capacity_matches_rendered_rows():
    rows = merge_pdf._toc_row_positions(10_000)
    assert len(rows) == merge_pdf._toc_capacity()
    _, height = merge_pdf.TOC_PAGE_SIZE
    assert rows[0] == height - merge_pdf.TOC_MARGIN - 2 * merge_pdf.TOC_LINE_GAP
    assert rows[-1] >= merge_pdf.TOC_MARGIN + merge_pdf.TOC_LINE_GAP


def test_single_input_is_rejected(tmp_path: Path, make_pdf: Callable[..., Path]):
    a = make_pdf("A", pages=1)
    with pytest.raises(ValueError, match="at least two"):
        merge_pdf.main(["-o", str(tmp_path / "merged.pdf"), str(a)])


def test_existing_output_needs_force(tmp_path: Path, make_pdf: Callable[..., Path]):
    a = make_pdf("A", pages=1)
    b = make_pdf("B", pages=1)
    out = tmp_path / "merged.pdf"
    out.write_bytes(b"not a pdf")

    with pytest.raises(FileExistsError):
        merge_pdf.main(["-o", str(out), str(a), str(b)])

    merge_pdf.main(["-o", str(out), "-f", str(a), str(b)])
    assert len(PdfReader(str(out)).pages) == 2
