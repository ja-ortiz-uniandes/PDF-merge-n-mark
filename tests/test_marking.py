"""Merged output is marked, and folder scans skip it.

The rule under test: a folder scan (--here) holds back PDFs this tool produced,
but a file named explicitly - in a manifest, as an argument, or via --pre-toc -
is always merged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import pytest
from pypdf import PdfReader

import merge_pdf
from pdf_combine.marking import MARKER_KEY, is_our_output
from conftest import outline_tree, titles

NUMBERED = ["1 Intro", "2 Body", "10 Annex"]


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_merged_output_is_marked(folder: Path, make_pdf: Callable[..., Path]):
    make_pdf("A", pages=1)
    make_pdf("B", pages=1)
    out = folder / "merged.pdf"

    merge_pdf.main(["-o", str(out), str(folder / "A.pdf"), str(folder / "B.pdf")])

    metadata = PdfReader(str(out)).metadata
    assert metadata is not None
    assert MARKER_KEY in metadata
    assert str(metadata["/Producer"]).startswith("pdfmerge")
    assert is_our_output(out)


def test_plain_pdfs_are_not_marked(folder: Path, make_pdf: Callable[..., Path]):
    assert not is_our_output(make_pdf("A", pages=1))


def test_second_here_run_skips_the_first_runs_output(
    folder: Path, make_pdf: Callable[..., Path], capsys: pytest.CaptureFixture[str]
):
    """The trap this fixes: without the marker, run two swallows run one."""
    for name in NUMBERED:
        make_pdf(name, pages=1)

    merge_pdf.main(["--here", "-o", "first.pdf"])
    assert len(PdfReader(str(folder / "first.pdf")).pages) == 3

    capsys.readouterr()
    merge_pdf.main(["--here", "-o", "second.pdf"])
    out = capsys.readouterr().out

    # Still three source pages, not six
    assert len(PdfReader(str(folder / "second.pdf")).pages) == 3
    assert titles(outline_tree(PdfReader(str(folder / "second.pdf")))) == NUMBERED
    assert "first.pdf" in out and "--include-merged" in out


def test_include_merged_opts_back_in(folder: Path, make_pdf: Callable[..., Path]):
    for name in NUMBERED:
        make_pdf(name, pages=1)

    merge_pdf.main(["--here", "-o", "first.pdf"])
    merge_pdf.main(["--here", "--include-merged", "-o", "second.pdf"])

    reader = PdfReader(str(folder / "second.pdf"))
    assert len(reader.pages) == 6  # the three sources plus first.pdf's three
    assert "first" in titles(outline_tree(reader))


def test_a_manifest_always_merges_a_marked_file(
    folder: Path, make_pdf: Callable[..., Path]
):
    """Explicitly listed means merged, marker or not."""
    for name in NUMBERED:
        make_pdf(name, pages=1)
    merge_pdf.main(["--here", "-o", "first.pdf"])
    assert is_our_output(folder / "first.pdf")

    manifest = folder / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "output": "combined.pdf",
                "files": [
                    {"file": "first.pdf", "label": "Earlier merge"},
                    {"file": "1 Intro.pdf", "label": "Intro"},
                ],
            }
        ),
        encoding="utf-8",
    )

    merge_pdf.main(["-m", str(manifest)])

    reader = PdfReader(str(folder / "combined.pdf"))
    assert titles(outline_tree(reader)) == ["Earlier merge", "Intro"]
    assert len(reader.pages) == 4


def test_positional_arguments_always_merge_a_marked_file(
    folder: Path, make_pdf: Callable[..., Path]
):
    for name in NUMBERED:
        make_pdf(name, pages=1)
    merge_pdf.main(["--here", "-o", "first.pdf"])

    merge_pdf.main(["-o", "combined.pdf", "first.pdf", "1 Intro.pdf"])

    assert len(PdfReader(str(folder / "combined.pdf")).pages) == 4


def test_pre_toc_always_merges_a_marked_file(
    folder: Path, make_pdf: Callable[..., Path]
):
    for name in NUMBERED:
        make_pdf(name, pages=1)
    merge_pdf.main(["--here", "-o", "first.pdf"])

    # first.pdf is marked, but naming it via --pre-toc merges it regardless,
    # while the scan still holds it back from the alphabetical run.
    merge_pdf.main(["--here", "--pre-toc", "first.pdf", "-o", "second.pdf"])

    reader = PdfReader(str(folder / "second.pdf"))
    assert titles(outline_tree(reader)) == ["first", *NUMBERED]
    assert len(reader.pages) == 6


def test_write_manifest_lists_only_unmarked_files(
    folder: Path, make_pdf: Callable[..., Path]
):
    """The scaffold reflects what --here would merge."""
    for name in NUMBERED:
        make_pdf(name, pages=1)
    merge_pdf.main(["--here", "-o", "first.pdf"])

    merge_pdf.main(["--here", "--write-manifest", "-o", "second.pdf"])

    text = (folder / "manifest.yml").read_text(encoding="utf-8")
    assert "first.pdf" not in text
    for name in NUMBERED:
        assert f"{name}.pdf" in text


def test_unreadable_file_is_treated_as_not_ours(folder: Path):
    broken = folder / "broken.pdf"
    broken.write_bytes(b"this is not a PDF")

    # Reported as not ours, so the scan keeps it and the merge raises a real
    # error rather than the file vanishing from the list.
    assert is_our_output(broken) is False
