"""Outline nesting, collapsed state and /Count bookkeeping."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from pypdf import PdfReader

import merge_pdf
from pdf_combine.processing import is_outline_node_open, update_outline_counts
from conftest import find_node, outline_tree, titles

# One top-level section whose second subsection was collapsed by its author.
NESTED = [
    {
        "title": "Chapter One",
        "page": 0,
        "open": True,
        "children": [
            {"title": "Overview", "page": 0},
            {
                "title": "Details",
                "page": 1,
                "open": False,
                "children": [
                    {"title": "Method", "page": 1},
                    {"title": "Results", "page": 2},
                ],
            },
        ],
    }
]


def test_fixture_source_records_the_collapsed_subsection(
    make_pdf: Callable[..., Path],
):
    """Sanity check on the fixture itself, independent of the merge code."""
    src = PdfReader(str(make_pdf("src", pages=3, outline=NESTED)))
    details = find_node(outline_tree(src), "Details")
    assert details is not None
    assert details["count"] is not None and details["count"] < 0


def test_top_level_labels_are_collapsed(
    tmp_path: Path, make_pdf: Callable[..., Path]
):
    a = make_pdf("A", pages=3, outline=NESTED)
    b = make_pdf("B", pages=1)
    out = tmp_path / "merged.pdf"

    merge_pdf.main(["-o", str(out), str(a), str(b)])

    tree = outline_tree(PdfReader(str(out)))
    assert titles(tree) == ["A", "B"]
    a_node = tree[0]
    assert a_node["children"], "the source outline should be nested under the label"
    assert a_node["count"] is not None and a_node["count"] < 0


def test_nested_collapsed_state_survives_the_merge(
    tmp_path: Path, make_pdf: Callable[..., Path]
):
    """The rebuilt-in-memory document is written and re-read during the merge;
    collapsed state only survives that round-trip through the sign of /Count."""
    a = make_pdf("A", pages=3, outline=NESTED)
    b = make_pdf("B", pages=1)
    out = tmp_path / "merged.pdf"

    merge_pdf.main(["-o", str(out), str(a), str(b)])

    tree = outline_tree(PdfReader(str(out)))
    details = find_node(tree, "Details")
    overview = find_node(tree, "Overview")
    chapter = find_node(tree, "Chapter One")
    assert details is not None and overview is not None and chapter is not None
    assert titles(details["children"]) == ["Method", "Results"]
    # Collapsed in the source, still collapsed in the merge
    assert details["count"] == -2
    # Open in the source, still open
    assert chapter["count"] is not None and chapter["count"] > 0


def test_count_covers_visible_descendants_not_just_children(
    tmp_path: Path, make_pdf: Callable[..., Path]
):
    """An open node counts its open descendants at every level, per
    PDF 32000-1 §12.3.3 - not only its direct children."""
    a = make_pdf("A", pages=3, outline=NESTED)
    b = make_pdf("B", pages=1)
    out = tmp_path / "merged.pdf"

    merge_pdf.main(["-o", str(out), str(a), str(b)])

    tree = outline_tree(PdfReader(str(out)))
    chapter = find_node(tree, "Chapter One")
    assert chapter is not None
    # Overview + Details; Details is closed so its two children stay hidden
    assert chapter["count"] == 2
    # Reopening "A" would reveal Chapter One plus, since Chapter One is itself
    # open, the two entries under it - three items, not one.
    assert tree[0]["count"] == -3


def test_outline_can_be_excluded_per_file(
    tmp_path: Path, make_pdf: Callable[..., Path]
):
    import json

    a = make_pdf("A", pages=1, outline=NESTED)
    b = make_pdf("B", pages=1)
    out = tmp_path / "merged.pdf"
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "output": str(out),
                "files": [
                    {"file": str(a), "label": "Section A", "outline": False},
                    {"file": str(b), "label": "Section B"},
                ],
            }
        ),
        encoding="utf-8",
    )

    merge_pdf.main(["-m", str(manifest)])

    assert titles(outline_tree(PdfReader(str(out)))) == ["Section B"]


def test_footnote_entries_are_dropped_from_the_source_outline(
    tmp_path: Path, make_pdf: Callable[..., Path]
):
    outline = [
        {
            "title": "Body",
            "page": 0,
            "children": [
                {"title": "1", "page": 0},
                {"title": "[2]", "page": 0},
                {"title": "Footnote 3", "page": 0},
                {"title": "Real Subsection", "page": 0},
            ],
        }
    ]
    a = make_pdf("A", pages=1, outline=outline)
    b = make_pdf("B", pages=1)
    out = tmp_path / "merged.pdf"

    merge_pdf.main(["-o", str(out), str(a), str(b)])

    body = find_node(outline_tree(PdfReader(str(out))), "Body")
    assert body is not None
    assert titles(body["children"]) == ["Real Subsection"]


def test_numeric_filename_still_gets_a_top_level_label(
    tmp_path: Path, make_pdf: Callable[..., Path]
):
    """The footnote heuristic must not run again when the already-processed
    document is imported, or a file named 12.pdf loses its label."""
    numeric = make_pdf("12", pages=1, outline=[{"title": "Intro", "page": 0}])
    b = make_pdf("B", pages=1)
    out = tmp_path / "merged.pdf"

    merge_pdf.main(["-o", str(out), str(numeric), str(b)])

    tree = outline_tree(PdfReader(str(out)))
    assert titles(tree) == ["12", "B"]
    assert titles(tree[0]["children"]) == ["Intro"]


def test_toc_outline_entry_is_optional(
    tmp_path: Path, make_pdf: Callable[..., Path]
):
    a = make_pdf("A", pages=1)
    b = make_pdf("B", pages=1)
    out = tmp_path / "merged.pdf"

    merge_pdf.main(["-o", str(out), "--toc", str(a), str(b)])
    assert titles(outline_tree(PdfReader(str(out)))) == ["A", "B"]

    merge_pdf.main(["-o", str(out), "-f", "--toc", "--toc-outline", str(a), str(b)])
    assert titles(outline_tree(PdfReader(str(out)))) == [
        "Table of Contents",
        "A",
        "B",
    ]


def test_is_outline_node_open_reads_boolean_objects():
    """BooleanObject has no __bool__, so bool(BooleanObject(False)) is True and
    the marker has to be compared by value."""
    from pypdf.generic import BooleanObject, NumberObject

    assert bool(BooleanObject(False)) is True  # the trap this guards against
    assert is_outline_node_open({"/%is_open%": BooleanObject(False)}) is False
    assert is_outline_node_open({"/%is_open%": BooleanObject(True)}) is True
    assert is_outline_node_open({"/Count": NumberObject(-3)}) is False
    assert is_outline_node_open({"/Count": NumberObject(2)}) is True
    assert is_outline_node_open({}) is True


def test_update_outline_counts_signs_a_closed_branch():
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(200, 200)
    parent = writer.add_outline_item("Parent", 0, is_open=False)
    child = writer.add_outline_item("Child", 1, parent=parent, is_open=True)
    writer.add_outline_item("Grandchild", 2, parent=child)

    update_outline_counts(writer)

    root = writer.get_outline_root()
    parent_obj = root["/First"].get_object()
    child_obj = parent_obj["/First"].get_object()
    assert int(child_obj["/Count"]) == 1
    # Reopening Parent would reveal Child and, since Child is open, Grandchild
    assert int(parent_obj["/Count"]) == -2
    assert int(root["/Count"]) == 1
