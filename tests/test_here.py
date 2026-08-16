"""Folder mode (--here), the manifest scaffold, and the CLI entry point."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

import pytest
import yaml
from pypdf import PdfReader

import merge_pdf
from pdf_combine.naming import sort_like_explorer
from conftest import link_targets, outline_tree, page_texts, titles

NUMBERED = ["1 Intro", "2 Body", "10 Annex"]


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run each test from inside the temp folder, the way --here is used."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def expected_output(folder: Path) -> Path:
    return folder / f"{folder.name}.pdf"


def test_here_merges_the_folder_in_explorer_order(
    folder: Path, make_pdf: Callable[..., Path]
):
    for name in NUMBERED:
        make_pdf(name, pages=1)

    merge_pdf.main(["--here"])

    out = expected_output(folder)
    assert out.exists(), "output defaults to the folder's own name"
    reader = PdfReader(str(out))
    # Explorer order, not string order: 10 sorts after 2
    assert titles(outline_tree(reader)) == NUMBERED
    assert len(reader.pages) == 3
    # No ToC unless asked for
    assert all("Table of Contents" not in t for t in page_texts(reader))


def test_here_output_can_be_overridden(folder: Path, make_pdf: Callable[..., Path]):
    for name in NUMBERED:
        make_pdf(name, pages=1)
    out = folder / "custom.pdf"

    merge_pdf.main(["--here", "-o", str(out)])

    assert out.exists()
    assert not expected_output(folder).exists()


def test_here_never_merges_its_own_output(
    folder: Path, make_pdf: Callable[..., Path]
):
    for name in NUMBERED:
        make_pdf(name, pages=1)

    merge_pdf.main(["--here"])
    first = len(PdfReader(str(expected_output(folder))).pages)
    merge_pdf.main(["--here", "-f"])
    second = len(PdfReader(str(expected_output(folder))).pages)

    assert first == second == 3


def test_here_with_toc(folder: Path, make_pdf: Callable[..., Path]):
    for name in NUMBERED:
        make_pdf(name, pages=1)

    merge_pdf.main(["--here", "--toc"])

    reader = PdfReader(str(expected_output(folder)))
    assert len(reader.pages) == 4
    assert "Table of Contents" in page_texts(reader)[0]
    assert link_targets(reader, 0) == [1, 2, 3]


def test_here_with_pre_toc_leads_and_is_not_repeated(
    folder: Path, make_pdf: Callable[..., Path]
):
    make_pdf("cover", pages=1)
    for name in NUMBERED:
        make_pdf(name, pages=1)

    merge_pdf.main(["--here", "--toc", "--pre-toc", "cover.pdf"])

    reader = PdfReader(str(expected_output(folder)))
    # cover + ToC + three files, with cover merged exactly once
    assert len(reader.pages) == 5
    assert titles(outline_tree(reader)) == ["cover", *NUMBERED]
    assert "cover page 1" in page_texts(reader)[0]
    assert "Table of Contents" in page_texts(reader)[1]
    # Pre-ToC files are labelled but not listed
    assert link_targets(reader, 1) == [2, 3, 4]


def test_here_rejects_a_manifest(folder: Path, make_pdf: Callable[..., Path]):
    make_pdf("A", pages=1)
    make_pdf("B", pages=1)
    (folder / "m.json").write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot be combined"):
        merge_pdf.main(["--here", "-m", "m.json"])


def test_here_rejects_file_arguments(folder: Path, make_pdf: Callable[..., Path]):
    make_pdf("A", pages=1)
    make_pdf("B", pages=1)

    with pytest.raises(ValueError, match="cannot be combined"):
        merge_pdf.main(["--here", ".", "A.pdf"])


def test_here_rejects_a_non_directory(folder: Path, make_pdf: Callable[..., Path]):
    make_pdf("A", pages=1)

    with pytest.raises(NotADirectoryError, match="needs a folder"):
        merge_pdf.main(["--here", "A.pdf"])


# --- explicit directory ---------------------------------------------------


def test_here_accepts_a_directory(
    folder: Path, make_pdf: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
):
    """The whole point: operate on a folder without changing directory."""
    for name in NUMBERED:
        make_pdf(name, pages=1)
    elsewhere = folder.parent / f"{folder.name}-elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    merge_pdf.main(["--here", str(folder)])

    out = expected_output(folder)
    assert out.exists(), "output lands beside the inputs, not in the cwd"
    assert not (elsewhere / out.name).exists()
    assert titles(outline_tree(PdfReader(str(out)))) == NUMBERED


def test_relative_output_belongs_to_the_target_folder(
    folder: Path, make_pdf: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
):
    for name in NUMBERED:
        make_pdf(name, pages=1)
    elsewhere = folder.parent / f"{folder.name}-elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    merge_pdf.main(["--here", str(folder), "-o", "Case 42.pdf"])

    assert (folder / "Case 42.pdf").exists()
    assert not (elsewhere / "Case 42.pdf").exists()


def test_absolute_output_is_left_alone(
    folder: Path, make_pdf: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
):
    for name in NUMBERED:
        make_pdf(name, pages=1)
    elsewhere = folder.parent / f"{folder.name}-elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    out = elsewhere / "somewhere-else.pdf"

    merge_pdf.main(["--here", str(folder), "-o", str(out)])

    assert out.exists()


def test_here_directory_with_pre_toc_and_manifest_scaffold(
    folder: Path, make_pdf: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
):
    make_pdf("cover", pages=1)
    for name in NUMBERED:
        make_pdf(name, pages=1)
    elsewhere = folder.parent / f"{folder.name}-elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    # A bare --pre-toc name refers to the target folder, and the manifest is
    # written there too, beside the files it lists.
    merge_pdf.main(
        ["--here", str(folder), "--write-manifest", "--pre-toc", "cover.pdf"]
    )

    manifest = folder / "manifest.yml"
    assert manifest.exists()
    assert not (elsewhere / "manifest.yml").exists()
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert [entry["file"] for entry in data["files"]][0] == "cover.pdf"
    assert data["output"] == f"{folder.name}.pdf"


@pytest.mark.parametrize("count", [0, 1])
def test_here_needs_at_least_two_pdfs(
    folder: Path, make_pdf: Callable[..., Path], count: int
):
    for i in range(count):
        make_pdf(f"only{i}", pages=1)

    with pytest.raises(ValueError, match="at least two PDFs"):
        merge_pdf.main(["--here"])


# --- manifest scaffold ----------------------------------------------------


def test_write_manifest_lists_the_folder_and_merges_nothing(
    folder: Path, make_pdf: Callable[..., Path]
):
    for name in NUMBERED:
        make_pdf(name, pages=1)

    merge_pdf.main(["--here", "--write-manifest"])

    manifest = folder / "manifest.yml"
    assert manifest.exists()
    assert not expected_output(folder).exists(), "scaffolding must not merge"

    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert data["output"] == f"{folder.name}.pdf"
    assert data["overwrite"] is False
    assert data["toc"] is False
    assert [entry["file"] for entry in data["files"]] == [f"{n}.pdf" for n in NUMBERED]
    assert [entry["label"] for entry in data["files"]] == NUMBERED


def test_written_manifest_round_trips_through_the_loader(
    folder: Path, make_pdf: Callable[..., Path]
):
    for name in NUMBERED:
        make_pdf(name, pages=1)

    merge_pdf.main(["--here", "--write-manifest"])
    loaded = merge_pdf._load_manifest(folder / "manifest.yml")

    assert [item["path"].name for item in loaded["items"]] == [
        f"{n}.pdf" for n in NUMBERED
    ]
    assert loaded["output"] == expected_output(folder)

    # And the merge it describes actually runs
    merge_pdf.main(["-m", "manifest.yml"])
    assert titles(outline_tree(PdfReader(str(expected_output(folder))))) == NUMBERED


def test_write_manifest_accepts_a_name_and_guards_overwrites(
    folder: Path, make_pdf: Callable[..., Path]
):
    for name in NUMBERED:
        make_pdf(name, pages=1)

    merge_pdf.main(["--here", "--write-manifest", "order.yml"])
    assert (folder / "order.yml").exists()

    with pytest.raises(FileExistsError, match="use -f to replace"):
        merge_pdf.main(["--here", "--write-manifest", "order.yml"])

    merge_pdf.main(["--here", "--write-manifest", "order.yml", "-f"])


def test_write_manifest_requires_here(folder: Path, make_pdf: Callable[..., Path]):
    make_pdf("A", pages=1)
    make_pdf("B", pages=1)

    with pytest.raises(ValueError, match="only available together with --here"):
        merge_pdf.main(["--write-manifest", "-o", "out.pdf", "A.pdf", "B.pdf"])


# --- ordering -------------------------------------------------------------


def test_sort_like_explorer_orders_digit_runs_numerically():
    names = [
        "10 Annex.pdf",
        "2 Body.pdf",
        "1 Intro.pdf",
        "a-10.pdf",
        "a-2.pdf",
        "B.pdf",
        "a.pdf",
    ]
    ordered = [p.name for p in sort_like_explorer(Path(n) for n in names)]
    assert ordered[:3] == ["1 Intro.pdf", "2 Body.pdf", "10 Annex.pdf"]
    assert ordered.index("a-2.pdf") < ordered.index("a-10.pdf")
    assert ordered.index("a.pdf") < ordered.index("B.pdf"), "case-insensitive"


# --- entry point and help -------------------------------------------------


def test_bare_invocation_prints_help(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(SystemExit) as excinfo:
        merge_pdf.main([])

    assert excinfo.value.code != 0
    assert "--here" in capsys.readouterr().out


def test_cli_reports_errors_without_a_traceback(
    folder: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(sys, "argv", ["pdfmerge", "--here"])

    with pytest.raises(SystemExit) as excinfo:
        merge_pdf.cli()

    assert excinfo.value.code == 1
    assert capsys.readouterr().err.startswith("pdfmerge: ")


def test_help_documents_every_option():
    parser = merge_pdf.build_parser()
    text = parser.format_help()
    for action in parser._actions:
        assert action.help, f"{action.option_strings or action.dest} has no help text"
        for option in action.option_strings:
            assert option in text, f"{option} missing from --help"


def test_help_documents_every_manifest_key():
    text = merge_pdf.build_parser().format_help()
    for key in (
        "output",
        "overwrite",
        "toc",
        "file",
        "label",
        "bookmark",
        "pre_toc",
        "outline",
    ):
        assert key in text, f"manifest key {key} missing from --help"
    assert "examples" in text
