from __future__ import annotations

from typing import Any, Iterable, cast

from pypdf import PdfReader, PdfWriter
from pypdf.generic import Fit, NameObject, NumberObject, TreeObject


def _normalize_fit_args(fit: str, raw_args: list[Any]) -> tuple[Any, ...]:
    def _num(v: Any) -> Any:
        try:
            return float(v)
        except Exception:
            return None

    counts = {
        "/Fit": 0,
        "/FitB": 0,
        "/FitH": 1,
        "/FitBH": 1,
        "/FitV": 1,
        "/FitBV": 1,
        "/XYZ": 3,
        "/FitR": 4,
    }
    n = counts.get(fit, 0)
    vals = [_num(x) for x in raw_args[:n]]
    while len(vals) < n:
        vals.append(None)
    return tuple(vals)


def _parse_dest_array(
    reader: PdfReader, dest: list[Any]
) -> tuple[int | None, str | None, tuple[Any, ...] | None]:
    try:
        if dest:
            page_ref: Any = dest[0]
            page_idx: int | None = reader.get_page_number(page_ref)
            fit: str = str(dest[1]) if len(dest) >= 2 else "/Fit"
            raw_args: list[Any] = list(dest[2:]) if len(dest) > 2 else []
            fit_args = _normalize_fit_args(fit, raw_args)
            return page_idx, fit, fit_args
    except Exception:
        pass
    return None, None, None


def _resolve_target(
    reader: PdfReader, d: dict[str, Any]
) -> tuple[int | None, str | None, tuple[Any, ...] | None]:
    # 1) Direct /Dest (named or array)
    if "/Dest" in d:
        dest: Any = d["/Dest"]
        if isinstance(dest, (str, bytes)):
            dest_name: str = (
                dest.decode("utf-8", "replace") if isinstance(dest, bytes) else dest
            )
            nd: Any = None
            try:
                nd = reader.named_destinations.get(dest_name)  # type: ignore[attr-defined]
            except Exception:
                try:
                    nd = reader.getNamedDestinations().get(dest_name)  # type: ignore[attr-defined]
                except Exception:
                    nd = None
            if nd is not None:
                try:
                    idx: int | None = reader.get_destination_page_number(nd)  # type: ignore[arg-type]
                except Exception:
                    idx = None
                nd_any: Any = cast(Any, nd)
                nd_fit: str = cast(str, getattr(nd_any, "fit", "/Fit"))
                nd_fit_args_val: Any = getattr(nd_any, "fit_args", ())
                nd_fit_args = _normalize_fit_args(
                    nd_fit,
                    (
                        list(nd_fit_args_val)
                        if isinstance(nd_fit_args_val, (list, tuple))
                        else []
                    ),
                )
                return idx, nd_fit, nd_fit_args
        if isinstance(dest, list):
            try:
                arr_idx, arr_fit, arr_fit_args = _parse_dest_array(
                    reader, cast(list[Any], dest)
                )
            except Exception:
                arr_idx, arr_fit, arr_fit_args = None, None, None
            if arr_idx is not None:
                return arr_idx, arr_fit or "/Fit", arr_fit_args

    # 2) GoTo action in /A with /D
    a: Any = d.get("/A")
    if (
        isinstance(a, dict)
        and a.get("/S") == "/GoTo"
        and "/D" in a
        and isinstance(a["/D"], list)
    ):
        act_idx, act_fit, act_fit_args = _parse_dest_array(
            reader, cast(list[Any], a["/D"])
        )
        if act_idx is not None:
            return act_idx, act_fit or "/Fit", act_fit_args

    # 2.5) Explicit dict-based destination using /Type and positional keys
    if "/Type" in d and "/Page" in d:
        try:
            page_idx = reader.get_page_number(d["/Page"])  # type: ignore[arg-type]
        except Exception:
            page_idx = None
        fit_name = str(d.get("/Type", "/Fit"))
        if fit_name == "/XYZ":
            args_list: list[Any] = [d.get("/Left"), d.get("/Top"), d.get("/Zoom")]
        elif fit_name in ("/FitH", "/FitBH"):
            args_list = [d.get("/Top")]
        elif fit_name in ("/FitV", "/FitBV"):
            args_list = [d.get("/Left")]
        elif fit_name == "/FitR":
            args_list = [
                d.get("/Left"),
                d.get("/Bottom"),
                d.get("/Right"),
                d.get("/Top"),
            ]
        elif fit_name in ("/Fit", "/FitB"):
            args_list = []
        else:
            args_list = []
        fit_args = _normalize_fit_args(fit_name, args_list)
        return page_idx, fit_name, fit_args

    # 3) Fallback /Page
    if "/Page" in d:
        try:
            idx = reader.get_page_number(d["/Page"])  # type: ignore[arg-type]
            return idx, "/Fit", ()
        except Exception:
            pass

    return None, None, None


def _fit_obj(fit: str | None, fit_args: tuple[Any, ...] | None) -> Fit:
    return Fit(fit_type=fit or "/Fit", fit_args=fit_args or ())


# --- Outline state helpers ---
def is_outline_node_open(node: Any) -> bool:
    """Report whether an outline node is expanded.

    Handles both node flavours pypdf produces: reader-side ``Destination``
    objects (which carry ``/Count``, negative when the node was collapsed) and
    writer-side ``TreeObject`` items (which carry pypdf's internal
    ``/%is_open%`` marker). The marker is a ``BooleanObject``, which does not
    implement ``__bool__``, so it must be compared by value rather than passed
    to ``bool()`` - pypdf's own tree code does the same.
    """
    if isinstance(node, dict):
        node_dict = cast(dict[str, Any], node)
        marker: Any = node_dict.get("/%is_open%")
        if marker is not None:
            return marker == True  # noqa: E712 - BooleanObject needs ==, not bool()
        count_val = node_dict.get("/Count")
        if isinstance(count_val, (int, float)):
            return int(count_val) >= 0
    open_attr = getattr(cast(Any, node), "open", None)
    if isinstance(open_attr, bool):
        return open_attr
    return True


def _tree_object_from(obj: Any) -> TreeObject | None:
    if isinstance(obj, TreeObject):
        return obj
    if hasattr(obj, "get_object"):
        try:
            real = obj.get_object()
        except Exception:
            return None
        if isinstance(real, TreeObject):
            return real
    return None


def _recalculate_counts(node: TreeObject) -> int:
    """Recompute ``/Count`` for ``node`` and its subtree, returning the number
    of items that are visible when ``node`` itself is expanded.

    Per PDF 32000-1 §12.3.3 that is the direct children plus the visible
    descendants of any child that is itself open - not just the direct child
    count. A collapsed node stores the same figure negated.
    """
    visible = 0
    child_ref: Any = node.get("/First")
    while child_ref is not None:
        child_obj = _tree_object_from(child_ref)
        if child_obj is None:
            break
        below = _recalculate_counts(child_obj)
        visible += 1 + (below if is_outline_node_open(child_obj) else 0)
        child_ref = child_obj.get("/Next")

    sign = 1 if is_outline_node_open(node) else -1
    node[NameObject("/Count")] = NumberObject(sign * visible)
    return visible


def update_outline_counts(writer: PdfWriter) -> None:
    try:
        root = writer.get_outline_root()
    except Exception:
        return
    if not root:
        return

    root_obj = _tree_object_from(root)
    if root_obj is None:
        return

    _recalculate_counts(root_obj)


# --- Footnote filtering helpers ---
def _extract_top_from_fit(
    fit: str | None, fit_args: tuple[Any, ...] | None
) -> float | None:
    """Extract a Top Y position from fit args when available.
    Coordinates are in points from the bottom-left origin (PDF default).
    """
    if not fit:
        return None
    try:
        if fit == "/XYZ":
            # (left, top, zoom)
            if fit_args and len(fit_args) >= 2 and fit_args[1] is not None:
                return float(fit_args[1])
        if fit in ("/FitH", "/FitBH"):
            # (top)
            if fit_args and len(fit_args) >= 1 and fit_args[0] is not None:
                return float(fit_args[0])
        if fit == "/FitR":
            # (left, bottom, right, top)
            if fit_args and len(fit_args) >= 4 and fit_args[3] is not None:
                return float(fit_args[3])
    except Exception:
        return None
    return None


def _is_footnote_title(title: str) -> bool:
    s = title.strip()
    if not s:
        return False
    # Common footnote-like title patterns
    if s.lower().startswith("footnote"):
        return True
    # Pure numeric (e.g., "1", "12")
    if s.isdigit():
        return True
    # Bracketed numeric (e.g., "[1]")
    if s.startswith("[") and s.endswith("]") and s[1:-1].isdigit():
        return True
    # Very short numeric-like labels (<= 3 chars) are likely footnote markers
    if len(s) <= 3 and all(ch.isdigit() for ch in s):
        return True
    return False


def _is_footnote_entry(
    title: str, fit: str | None, fit_args: tuple[Any, ...] | None
) -> bool:
    """Heuristic to detect and exclude footnote outline entries.

    Exclude if:
    - Title matches common footnote patterns, OR
    - The destination "top" is near the bottom of the page (<= 150pt) and the title looks like a numeric marker
    """
    if _is_footnote_title(title):
        return True
    top = _extract_top_from_fit(fit, fit_args)
    s = title.strip()
    if (
        top is not None
        and top <= 150.0
        and (s.isdigit() or (s.startswith("[") and s.endswith("]")))
    ):
        return True
    return False


def _recreate_outline_under_parent(
    reader: PdfReader,
    outline: Any,
    writer: PdfWriter,
    parent: Any,
    page_offset: int = 0,
    skip_footnotes: bool = True,
) -> None:
    # Normalize to list of nodes
    try:
        outline_iter: Iterable[Any]
        if not isinstance(outline, list):
            outline_iter = cast(Iterable[Any], outline)
        else:
            outline_iter = cast(Iterable[Any], outline)
        nodes = list(outline_iter)
    except Exception:
        nodes = [cast(Any, outline)]

    last_created: Any = None

    for node in nodes:
        if isinstance(node, list):
            if last_created is not None:
                _recreate_outline_under_parent(
                    reader, node, writer, last_created, page_offset, skip_footnotes
                )
            continue

        if isinstance(node, dict):
            node_dict = cast(dict[str, Any], node)
            title = str(node_dict.get("/Title", "Untitled"))
            idx, fit, fit_args = _resolve_target(reader, node_dict)
            # Skip footnote-like entries
            if skip_footnotes and _is_footnote_entry(title, fit, fit_args):
                continue
            if idx is not None:
                last_created = writer.add_outline_item(
                    title=title,
                    page_number=page_offset + int(idx),
                    parent=parent,
                    fit=_fit_obj(fit, fit_args),
                    is_open=is_outline_node_open(node),
                )
            else:
                last_created = writer.add_outline_item(
                    title=title,
                    page_number=0,
                    parent=parent,
                    is_open=is_outline_node_open(node),
                )
        else:
            # Fallback object-like node
            node_any = node
            title = (
                getattr(node_any, "title", None)
                or getattr(node_any, "name", None)
                or "Untitled"
            )
            try:
                idx2 = reader.get_destination_page_number(node_any)  # type: ignore[arg-type]
            except Exception:
                idx2 = None
            fit2: str = cast(str, getattr(node_any, "fit", "/Fit"))
            fit_args_val: Any = getattr(node_any, "fit_args", ())
            fit_args2 = _normalize_fit_args(
                fit2,
                (
                    list(cast(Iterable[Any], fit_args_val))
                    if isinstance(fit_args_val, (list, tuple))
                    else []
                ),
            )
            # Skip footnote-like entries
            if skip_footnotes and _is_footnote_entry(str(title), fit2, fit_args2):
                continue
            if idx2 is not None:
                last_created = writer.add_outline_item(
                    title=str(title),
                    page_number=page_offset + int(idx2),
                    parent=parent,
                    fit=_fit_obj(fit2, fit_args2),
                    is_open=is_outline_node_open(node),
                )
            else:
                last_created = writer.add_outline_item(
                    title=str(title),
                    page_number=0,
                    parent=parent,
                    is_open=is_outline_node_open(node),
                )


def _source_outline(reader: PdfReader) -> Any:
    """Return the reader's outline tree, tolerating pypdf naming differences."""
    outline: Any = getattr(reader, "outline", None) or getattr(reader, "outlines", None)
    if outline is None:
        try:
            outline = list(reader.get_outlines())  # type: ignore[attr-defined]
        except Exception:
            outline = None
    return outline


def import_outline_from_reader(
    reader: PdfReader,
    writer: PdfWriter,
    page_offset: int = 0,
    parent: Any = None,
    skip_footnotes: bool = False,
) -> None:
    """Copy ``reader``'s outline into ``writer``, shifted by ``page_offset``.

    Destinations keep their original fit type and arguments, and each node
    keeps its expanded/collapsed state. ``skip_footnotes`` defaults to False
    here: callers merging an already-processed document must not re-apply the
    footnote heuristic, or a top-level label that merely looks numeric (say a
    file named ``12.pdf``) would be dropped.
    """
    outline = _source_outline(reader)
    if not outline:
        return
    _recreate_outline_under_parent(
        reader, outline, writer, parent, page_offset, skip_footnotes
    )


def rebuild_outline_under_parent(reader: PdfReader, label: str) -> PdfWriter:
    """
    Given a source document (reader) and a label, return a new PdfWriter where:
    - All pages from the reader are copied into the writer
    - A new top-level outline item with the given label is created
    - The original outline (with exact fit types and args like XYZ left/top/zoom) is recreated under that top-level item

    Parameters:
        reader: PdfReader - the source PDF to copy and extract outline from
        label: str - the name of the new top-level outline item

    Returns:
        PdfWriter - a writer containing copied pages and rebuilt outline under the top-level label
    """
    writer = PdfWriter()

    # Copy all pages
    for page in reader.pages:
        writer.add_page(page)

    # Top-level outline for this document
    parent = writer.add_outline_item(
        title=label,
        page_number=0,
        fit=_fit_obj("/Fit", ()),
        is_open=False,
    )

    # Source outline, nested under the new top-level label
    outline = _source_outline(reader)
    if outline:
        _recreate_outline_under_parent(
            reader, outline, writer, parent, page_offset=0, skip_footnotes=True
        )

    # /%is_open% is stripped when pypdf serializes the document, so the
    # collapsed state only survives a write/read round-trip through the sign of
    # /Count. Recompute it here so callers can re-read this writer's output.
    update_outline_counts(writer)

    return writer
