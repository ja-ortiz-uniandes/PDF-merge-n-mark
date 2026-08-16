"""Shared fixtures.

Source PDFs are generated at test time with reportlab and pypdf; nothing
binary is committed. Fixture outlines are built with pypdf's public API only,
so they do not depend on the code under test.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

OutlineSpec = Sequence[dict[str, Any]]


def _add_outline(writer: PdfWriter, spec: OutlineSpec, parent: Any) -> None:
    for entry in spec:
        ref = writer.add_outline_item(
            title=entry["title"],
            page_number=entry.get("page", 0),
            parent=parent,
            is_open=entry.get("open", True),
        )
        children = entry.get("children")
        if children:
            _add_outline(writer, children, ref)


@pytest.fixture
def make_pdf(tmp_path: Path) -> Callable[..., Path]:
    """Build a PDF with `pages` pages and an optional outline.

    An outline entry is ``{"title": str, "page": int, "open": bool,
    "children": [...]}``.
    """

    def _make(
        name: str,
        pages: int = 1,
        outline: OutlineSpec | None = None,
        password: str | None = None,
    ) -> Path:
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        for i in range(pages):
            c.drawString(72, 720, f"{name} page {i + 1}")
            c.showPage()
        c.save()
        buf.seek(0)

        writer = PdfWriter()
        for page in PdfReader(buf).pages:
            writer.add_page(page)
        if outline:
            _add_outline(writer, outline, None)
        if password is not None:
            writer.encrypt(password)

        path = tmp_path / f"{name}.pdf"
        with open(path, "wb") as f:
            writer.write(f)
        return path

    return _make


# --- Assertion helpers ----------------------------------------------------


def outline_tree(reader: PdfReader) -> list[dict[str, Any]]:
    """Walk the raw /Outlines tree into nested dicts.

    Reads the document structure directly rather than going through
    ``reader.outline`` so that ``/Count`` - the only carrier of collapsed state
    in a serialized PDF - can be asserted on.
    """
    catalog: Any = reader.trailer["/Root"].get_object()
    root: Any = catalog.get("/Outlines")
    if root is None:
        return []
    return _siblings(reader, root.get_object().get("/First"))


def _siblings(reader: PdfReader, ref: Any) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    while ref is not None:
        node = ref.get_object()
        nodes.append(
            {
                "title": str(node.get("/Title")),
                "count": int(node["/Count"]) if "/Count" in node else None,
                "page": destination_page(reader, node),
                "children": _siblings(reader, node.get("/First")),
            }
        )
        ref = node.get("/Next")
    return nodes


def destination_page(reader: PdfReader, node: Any) -> int | None:
    """0-based page index an outline item or link annotation points at."""
    dest: Any = None
    action = node.get("/A")
    if action is not None:
        action = action.get_object()
        if action.get("/S") == "/GoTo":
            dest = action.get("/D")
    if dest is None:
        dest = node.get("/Dest")
    if dest is None:
        return None
    dest = dest.get_object()
    if not isinstance(dest, list) or not dest:
        return None
    target = dest[0].get_object()
    # Outline items point at a page via an indirect reference; the Link
    # annotations pypdf builds from `target_page_index` store a bare page
    # number instead.
    if isinstance(target, (int, float)):
        return int(target)
    try:
        return reader.get_page_number(target)
    except Exception:
        return None


def titles(nodes: Iterable[dict[str, Any]]) -> list[str]:
    return [n["title"] for n in nodes]


def find_node(nodes: Iterable[dict[str, Any]], title: str) -> dict[str, Any] | None:
    for node in nodes:
        if node["title"] == title:
            return node
        hit = find_node(node["children"], title)
        if hit is not None:
            return hit
    return None


def page_texts(reader: PdfReader) -> list[str]:
    return [page.extract_text() or "" for page in reader.pages]


def link_targets(reader: PdfReader, page_index: int) -> list[int]:
    """Link annotation targets on a page, ordered top of page to bottom."""
    annots = reader.pages[page_index].get("/Annots")
    if annots is None:
        return []
    rows: list[tuple[float, int]] = []
    for ref in annots:
        annot = ref.get_object()
        target = destination_page(reader, annot)
        if target is None:
            continue
        rect = annot["/Rect"]
        rows.append((float(rect[1]), target))
    return [target for _, target in sorted(rows, key=lambda r: -r[0])]
