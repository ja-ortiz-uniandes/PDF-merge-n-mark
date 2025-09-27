#!/usr/bin/env python3
# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

import argparse
import json
import io  # <- NEW
from pathlib import Path
from typing import Any, Iterable, List, Sequence, TypedDict

import yaml  # type: ignore[import-not-found]
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link  # type: ignore[import-not-found]
from pypdf.generic import (  # type: ignore[import-not-found]
    BooleanObject,
    Fit,
    NameObject,
    NumberObject,
)
from pdf_combine.processing import rebuild_outline_under_parent

# Optional: reportlab for generating the ToC page
try:
    from reportlab.lib.pagesizes import letter  # type: ignore[import-not-found]
    from reportlab.pdfgen import canvas  # type: ignore[import-not-found]
except ModuleNotFoundError:
    letter = None  # type: ignore[assignment]
    canvas = None  # type: ignore[assignment]


# New: structured input item so we can carry an optional label
class InputItem(TypedDict):
    path: Path
    label: str | None
    pre_toc: bool  # if true, place before ToC; default: not in ToC, in outline
    toc: bool  # include in ToC entries/links (default True; if pre_toc, default False)
    outline: bool  # include in outline (default True)


class ManifestResult(TypedDict):
    items: List[InputItem]
    output: Path | None
    overwrite: bool | None
    toc: bool | None
    toc_outline: bool | None


def _normalize_paths(
    paths: Iterable[str | Path], *, base: Path | None = None
) -> List[Path]:
    out: List[Path] = []
    for p in paths:
        pp: Path = Path(p)
        if base is not None and not pp.is_absolute():
            pp = (base / pp).resolve()
        else:
            pp = pp.resolve()
        out.append(pp)
    return out


def _load_manifest(path: Path) -> ManifestResult:
    """
    Manifest format:
      JSON: [ {"file": "01.pdf", "label": "Part 1"}, {"file": "02.pdf"} ]
      YAML:
        - file: 01.pdf
          label: Part 1
        - file: 02.pdf
    """
    if not path.is_file():
        raise FileNotFoundError(f"Manifest not found: {path}")

    text: str = path.read_text(encoding="utf-8")
    data: Any
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    elif path.suffix.lower() in {".yml", ".yaml"}:
        data = yaml.safe_load(text)
    else:
        raise ValueError("Manifest must be .json, .yml, or .yaml")

    base_dir = path.parent.resolve()
    items: List[InputItem] = []
    manifest_output: Path | None = None
    manifest_overwrite: bool | None = None
    manifest_toc: bool | None = None
    manifest_toc_outline: bool | None = None

    # Accept two shapes:
    # 1) List[files]
    # 2) { output?, overwrite?, toc?, files: [...] }
    files_data: Any
    if isinstance(data, list):
        files_data = data
    elif isinstance(data, dict):
        # Options
        out_str = data.get("output") or data.get("out")
        if isinstance(out_str, str):
            manifest_output = _normalize_paths([out_str], base=base_dir)[0]
        ow = data.get("overwrite")
        if isinstance(ow, bool):
            manifest_overwrite = ow
        toc_val = data.get("toc")
        if isinstance(toc_val, bool):
            manifest_toc = toc_val
        elif isinstance(toc_val, dict):
            # Support: toc: { enabled?: bool, include?: bool, outline?: bool }
            en = toc_val.get("enabled")
            inc = toc_val.get("include")
            if isinstance(en, bool):
                manifest_toc = en
            elif isinstance(inc, bool):
                manifest_toc = inc
            else:
                # If toc object present with no explicit enabled/include, assume enabled
                manifest_toc = True
            ol = toc_val.get("outline")
            if isinstance(ol, bool):
                manifest_toc_outline = ol
        # Files array
        if isinstance(data.get("files"), list):
            files_data = data.get("files")
        elif isinstance(data.get("inputs"), list):
            files_data = data.get("inputs")
        else:
            raise ValueError("Manifest dict must include a 'files' (or 'inputs') list.")
    else:
        raise ValueError("Manifest must be a list or an object with a 'files' list.")

    for item in files_data:
        if not (
            isinstance(item, dict) and "file" in item and isinstance(item["file"], str)
        ):
            raise ValueError(
                "Each manifest entry must be an object with a 'file' string key."
            )
        normalized_path: Path = _normalize_paths([item["file"]], base=base_dir)[0]
        # Prefer 'label'; accept legacy 'bookmark'
        label_val: str | None = None
        if "label" in item:
            if item["label"] is None or isinstance(item["label"], str):
                label_val = item["label"]
            else:
                raise ValueError("'label' must be a string when provided.")
        elif "bookmark" in item:
            if item["bookmark"] is None or isinstance(item["bookmark"], str):
                label_val = item["bookmark"]
            else:
                raise ValueError("'bookmark' must be a string when provided.")
        # Optional flags
        pre_toc_val: bool = bool(item.get("pre_toc", False))
        # Inclusion flags: now named 'toc' and 'outline'; also accept legacy keys
        # Defaults: outline True; toc True unless pre_toc then False by default
        if "outline" in item:
            outline_val: bool = bool(item.get("outline"))
        elif "in_outline" in item:
            outline_val = bool(item.get("in_outline"))
        elif "outline_exclude" in item:
            outline_val = not bool(item.get("outline_exclude"))
        else:
            outline_val = True

        if "toc" in item and not isinstance(item.get("toc"), dict):
            toc_item_val: bool = bool(item.get("toc"))
        elif "in_toc" in item:
            toc_item_val = bool(item.get("in_toc"))
        elif "toc_exclude" in item:
            toc_item_val = not bool(item.get("toc_exclude"))
        else:
            toc_item_val = not pre_toc_val  # True normally; False for pre_toc
        items.append(
            {
                "path": normalized_path,
                "label": label_val,
                "pre_toc": pre_toc_val,
                "toc": toc_item_val,
                "outline": outline_val,
            }
        )

    return {
        "items": items,
        "output": manifest_output,
        "overwrite": manifest_overwrite,
        "toc": manifest_toc,
        "toc_outline": manifest_toc_outline,
    }


def _inputs_from_cli(paths: Iterable[str | Path]) -> List[InputItem]:
    """Parse CLI inputs, supporting a flag preceding a file to mark it as pre-ToC.

        Supported markers (CLI):
            --pre-toc

    Examples:
    merge_pdf.py -o out.pdf -f --toc --pre-toc pre.pdf A.pdf B.pdf
    """
    out: List[InputItem] = []
    tokens: List[str] = [str(p) for p in paths]
    pre_toc_next = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in {"--pre-toc"}:
            pre_toc_next = True
            i += 1
            continue
        # Treat token as a path
        pp: Path = _normalize_paths([tok])[0]
        out.append(
            {
                "path": pp,
                "label": None,
                "pre_toc": pre_toc_next,
                # Defaults: outline True; toc True unless pre_toc
                "toc": (False if pre_toc_next else True),
                "outline": True,
            }
        )
        pre_toc_next = False
        i += 1
    return out


def _build_toc_pdf(entries: Sequence[tuple[str, int]]) -> PdfReader:
    """
    Build a single-page PDF Table of Contents from (title, start_page) entries.
    Returns a PdfReader backed by in-memory bytes.
    """
    if canvas is None:
        raise ModuleNotFoundError(
            "reportlab is required for --toc. Install with: py -m pip install reportlab"
        )

    buf = io.BytesIO()
    pagesize = (
        letter if letter is not None else (612.0, 792.0)
    )  # fallback to US Letter pts
    c = canvas.Canvas(buf, pagesize=pagesize)

    width, height = pagesize
    left = 54
    right = width - 54
    top = height - 54
    line_gap = 16

    # Title
    c.setFont("Helvetica-Bold", 18)
    c.drawString(left, top, "Table of Contents")

    # Entries
    y = top - 2 * line_gap
    c.setFont("Helvetica", 11)

    max_title_width = right - left - 60  # leave room for page numbers
    for title, page_num in entries:
        if y < 54 + line_gap:  # simple one-page clamp
            break
        # Truncate title if too wide
        text = title
        while c.stringWidth(text, "Helvetica", 11) > max_title_width and len(text) > 3:
            text = text[:-4] + "…"
        # Dotted leader
        title_x = left
        page_str = str(page_num)
        page_w = c.stringWidth(page_str, "Helvetica", 11)
        number_x = right - page_w
        c.drawString(title_x, y, text)
        # draw dots between end of title and page number
        dots_start = title_x + c.stringWidth(text, "Helvetica", 11) + 6
        dots_end = number_x - 6
        if dots_end > dots_start:
            dot_w = c.stringWidth(".", "Helvetica", 11)
            n_dots = int((dots_end - dots_start) / dot_w)
            c.drawString(dots_start, y, "." * max(0, n_dots))
        c.drawString(number_x, y, page_str)
        y -= line_gap

    c.showPage()
    c.save()
    buf.seek(0)
    return PdfReader(buf)


def _collapse_top_level_outlines(writer: PdfWriter) -> None:
    try:
        root = writer.get_outline_root()
    except Exception:
        return
    if not root:
        return

    current = root.get("/First") if hasattr(root, "get") else None
    while current is not None:
        node = current.get_object()
        if node is None or not hasattr(node, "__getitem__"):
            break
        node[NameObject("/%is_open%")] = BooleanObject(False)

        child = node.get("/First") if hasattr(node, "get") else None
        count = 0
        while child is not None:
            child_obj = child.get_object()
            if child_obj is None:
                break
            count += 1
            child = child_obj.get("/Next") if hasattr(child_obj, "get") else None

        node[NameObject("/Count")] = NumberObject(-count if count else 0)
        current = node.get("/Next") if hasattr(node, "get") else None


def merge_pdfs(
    inputs: Sequence[InputItem],
    output: Path,
    *,
    overwrite: bool = False,
    add_toc: bool = False,
    toc_outline: bool = False,
) -> None:
    if len(inputs) < 2:
        raise ValueError("Provide at least two input PDFs.")

    if not overwrite and output.exists():
        raise FileExistsError(f"Output file already exists: {output}")

    input_paths: List[Path] = [it["path"].resolve() for it in inputs]
    output_resolved: Path = output.resolve()
    if output_resolved in input_paths:
        raise ValueError("Output path must be different from all input paths.")

    writer: PdfWriter = PdfWriter()

    # Separate pre-ToC (formerly covers) and normal items
    pre_tocs: List[InputItem] = [it for it in inputs if it.get("pre_toc", False)]
    normals: List[InputItem] = [it for it in inputs if not it.get("pre_toc", False)]

    # Prepare ToC entries excluding covers; ToC will be inserted after covers
    toc_pages = 0
    toc_page_index: int | None = None
    toc_entries: List[tuple[str, int]] = []
    destinations: List[int] = (
        []
    )  # start pages (0-based) for ToC-linked normal items only

    pre_pages_total = 0
    pre_titles: List[str] = []
    pre_counts: List[int] = []
    pre_include_flags: List[bool] = []  # include in ToC if toc is True
    if add_toc:
        # Count pre-ToC pages and compute include flags for pre-ToC items
        for item in pre_tocs:
            path = item["path"]
            if not path.is_file():
                raise FileNotFoundError(f"Missing file: {path}")
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")  # type: ignore[no-untyped-call]
                except Exception as e:
                    raise ValueError(
                        f"Encrypted PDF requires a password: {path}"
                    ) from e
            cnt = len(reader.pages)
            pre_counts.append(cnt)
            pre_pages_total += cnt
            pre_titles.append(item["label"] if item["label"] else path.stem)
            include_pre = bool(item.get("toc", False))
            pre_include_flags.append(include_pre)

        # Build ToC entries: first any included pre-ToC items (page numbers before the ToC page)
        running_pre = 0
        for idx, (title, count) in enumerate(zip(pre_titles, pre_counts)):
            if pre_include_flags[idx]:
                toc_entries.append((title, 1 + running_pre))
            running_pre += count

        # Build ToC entries for normal items (page numbers after ToC page)
        titles: List[str] = []
        page_counts: List[int] = []
        include_flags: List[bool] = []
        for item in normals:
            path = item["path"]
            if not path.is_file():
                raise FileNotFoundError(f"Missing file: {path}")
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")  # type: ignore[no-untyped-call]
                except Exception as e:
                    raise ValueError(
                        f"Encrypted PDF requires a password: {path}"
                    ) from e
            page_counts.append(len(reader.pages))
            titles.append(item["label"] if item["label"] else path.stem)
            include_flags.append(bool(item.get("toc", True)))

        start_page_normals = (
            pre_pages_total + 1 + 1
        )  # pre-ToC pages + ToC page + 1-based
        running = 0
        for idx, (title, count) in enumerate(zip(titles, page_counts)):
            if include_flags[idx]:
                toc_entries.append((title, start_page_normals + running))
            running += count

    # Utilities to import outlines from a reader into writer preserving fits
    def _normalize_fit_args_local(fit: str, raw_args: list[Any]) -> tuple[Any, ...]:
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

    def _parse_dest_array_local(
        reader: PdfReader, dest: list[Any]
    ) -> tuple[int | None, str | None, tuple[Any, ...] | None]:
        try:
            if dest:
                page_ref: Any = dest[0]
                page_idx: int | None = reader.get_page_number(page_ref)
                fit: str = str(dest[1]) if len(dest) >= 2 else "/Fit"
                raw_args: list[Any] = list(dest[2:]) if len(dest) > 2 else []
                fit_args = _normalize_fit_args_local(fit, raw_args)
                return page_idx, fit, fit_args
        except Exception:
            pass
        return None, None, None

    def _resolve_target_local(
        reader: PdfReader, d: dict[str, Any]
    ) -> tuple[int | None, str | None, tuple[Any, ...] | None]:
        if "/Dest" in d:
            dest: Any = d["/Dest"]
            if isinstance(dest, (str, bytes)):
                nd: Any = None
                try:
                    nd = reader.named_destinations.get(dest)  # type: ignore[attr-defined]
                except Exception:
                    try:
                        nd = reader.getNamedDestinations().get(dest)  # type: ignore[attr-defined]
                    except Exception:
                        nd = None
                if nd is not None:
                    try:
                        idx: int | None = reader.get_destination_page_number(nd)  # type: ignore[arg-type]
                    except Exception:
                        idx = None
                    nd_any: Any = nd
                    nd_fit: str = getattr(nd_any, "fit", "/Fit")  # type: ignore[assignment]
                    nd_fit_args_val: Any = getattr(nd_any, "fit_args", ())
                    nd_fit_args = _normalize_fit_args_local(
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
                    arr_idx, arr_fit, arr_fit_args = _parse_dest_array_local(
                        reader, dest
                    )
                except Exception:
                    arr_idx, arr_fit, arr_fit_args = None, None, None
                if arr_idx is not None:
                    return arr_idx, arr_fit or "/Fit", arr_fit_args

        a: Any = d.get("/A")
        if (
            isinstance(a, dict)
            and a.get("/S") == "/GoTo"
            and "/D" in a
            and isinstance(a["/D"], list)
        ):
            act_idx, act_fit, act_fit_args = _parse_dest_array_local(reader, a["/D"])
            if act_idx is not None:
                return act_idx, act_fit or "/Fit", act_fit_args

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
            fit_args = _normalize_fit_args_local(fit_name, args_list)
            return page_idx, fit_name, fit_args

        if "/Page" in d:
            try:
                idx = reader.get_page_number(d["/Page"])  # type: ignore[arg-type]
                return idx, "/Fit", ()
            except Exception:
                pass
        return None, None, None

    def _fit_obj_local(fit: str | None, fit_args: tuple[Any, ...] | None) -> Fit:
        return Fit(fit_type=fit or "/Fit", fit_args=fit_args or ())

    def _import_outline_from_reader(
        reader: PdfReader, writer_target: PdfWriter, page_offset: int
    ) -> None:
        outline: Any = getattr(reader, "outline", None) or getattr(
            reader, "outlines", None
        )
        if outline is None:
            try:
                outline = list(reader.get_outlines())  # type: ignore[attr-defined]
            except Exception:
                outline = None
        if outline is None:
            return

        # Flatten approach mirroring debug: iterate tree and recreate
        def _recreate(readerL: PdfReader, outlineL: Any, parent: Any | None) -> None:
            try:
                nodes: list[Any] = (
                    list(outlineL) if not isinstance(outlineL, list) else list(outlineL)
                )
            except Exception:
                nodes = [outlineL]

            last_created: Any | None = None
            for node in nodes:
                if isinstance(node, list):
                    if last_created is not None:
                        _recreate(readerL, node, last_created)
                    continue

                if isinstance(node, dict):
                    title = str(node.get("/Title", "Untitled"))
                    idx, fit, fit_args = _resolve_target_local(readerL, node)
                    if idx is not None:
                        last_created = writer_target.add_outline_item(
                            title=title,
                            page_number=page_offset + int(idx),
                            parent=parent,
                            fit=_fit_obj_local(fit, fit_args),
                        )
                    else:
                        last_created = writer_target.add_outline_item(
                            title=title, page_number=0, parent=parent
                        )
                else:
                    # Fallback object-like
                    title = (
                        getattr(node, "title", None)
                        or getattr(node, "name", None)
                        or "Untitled"
                    )
                    try:
                        idx2 = readerL.get_destination_page_number(node)  # type: ignore[arg-type]
                    except Exception:
                        idx2 = None
                    fit2 = getattr(node, "fit", "/Fit")
                    fit_args_val: Any = getattr(node, "fit_args", ())
                    fit_args2 = _normalize_fit_args_local(
                        fit2,
                        (
                            list(fit_args_val)
                            if isinstance(fit_args_val, (list, tuple))
                            else []
                        ),
                    )
                    if idx2 is not None:
                        last_created = writer_target.add_outline_item(
                            title=str(title),
                            page_number=page_offset + int(idx2),
                            parent=parent,
                            fit=_fit_obj_local(fit2, fit_args2),
                        )
                    else:
                        last_created = writer_target.add_outline_item(
                            title=str(title), page_number=0, parent=parent
                        )

        _recreate(reader, outline, None)

    # First, append pre-ToC items (with outline labels unless outline_exclude)
    for i, item in enumerate(pre_tocs):
        path: Path = item["path"]
        if not path.is_file():
            raise FileNotFoundError(f"Missing file: {path}")

        reader_orig: PdfReader = PdfReader(str(path))
        if reader_orig.is_encrypted:
            try:
                reader_orig.decrypt("")  # type: ignore[no-untyped-call]
            except Exception as e:
                raise ValueError(f"Encrypted PDF requires a password: {path}") from e

        # index where this item starts
        start_index: int = len(writer.pages)

        if not bool(item.get("outline", True)):
            # Append pages only; no outline for this item
            for page in reader_orig.pages:
                writer.add_page(page)
        else:
            # Preprocess this input to rebuild its outline under a top-level label
            title_str = item.get("label") or path.stem
            single_writer: PdfWriter = rebuild_outline_under_parent(
                reader_orig, label=str(title_str)
            )

            # Read the rebuilt PDF from memory to import pages and outlines
            buf = io.BytesIO()
            single_writer.write(buf)
            buf.seek(0)
            single_reader = PdfReader(buf)

            # Append pages
            for page in single_reader.pages:
                writer.add_page(page)

            # Import the outlines (which already include the top-level label)
            _import_outline_from_reader(single_reader, writer, page_offset=start_index)

        # If ToC is enabled and this pre-ToC item is included in ToC, record destination now (order must match toc_entries)
        if add_toc and i < len(pre_include_flags) and pre_include_flags[i]:
            destinations.append(start_index)

    # Insert ToC after pre-ToC items
    if add_toc and toc_entries:
        toc_reader = _build_toc_pdf(toc_entries)
        toc_page_index = len(writer.pages)
        for page in toc_reader.pages:
            writer.add_page(page)
            toc_pages += 1
        # Optionally add an outline entry for the ToC itself (top-level)
        if toc_outline:
            writer.add_outline_item(
                title="Table of Contents",
                page_number=toc_page_index,
                fit=Fit(fit_type="/Fit"),
            )

    # Then append normal items (record destinations for ToC links when not excluded)
    for item in normals:
        path: Path = item["path"]
        if not path.is_file():
            raise FileNotFoundError(f"Missing file: {path}")

        reader_orig: PdfReader = PdfReader(str(path))
        if reader_orig.is_encrypted:
            try:
                reader_orig.decrypt("")  # type: ignore[no-untyped-call]
            except Exception as e:
                raise ValueError(f"Encrypted PDF requires a password: {path}") from e

        # index of the first page from this normal file
        start_index: int = len(writer.pages)
        if add_toc and bool(item.get("toc", True)):
            destinations.append(start_index)

        # Append pages and optionally import outline
        if not bool(item.get("outline", True)):
            for page in reader_orig.pages:
                writer.add_page(page)
        else:
            title: str = str(item.get("label") or path.stem)
            single_writer: PdfWriter = rebuild_outline_under_parent(
                reader_orig, label=title
            )
            buf = io.BytesIO()
            single_writer.write(buf)
            buf.seek(0)
            single_reader = PdfReader(buf)
            for page in single_reader.pages:
                writer.add_page(page)
            _import_outline_from_reader(single_reader, writer, page_offset=start_index)

    # Make ToC lines clickable (single-page ToC)
    if add_toc and toc_pages > 0 and toc_page_index is not None:
        # Must match _build_toc_pdf layout
        pagesize = letter if letter is not None else (612.0, 792.0)
        width, height = pagesize
        left = 54
        right = width - 54
        top = height - 54
        line_gap = 16

        y = top - 2 * line_gap
        for dest in destinations:
            if y < 54 + line_gap:  # same clamp used in _build_toc_pdf
                break
            rect = (float(left), float(y - 2), float(right), float(y + 12))
            annotation = Link(
                rect=rect, target_page_index=dest, fit=Fit(fit_type="/Fit")
            )
            writer.add_annotation(page_number=toc_page_index, annotation=annotation)
            y -= line_gap

    _collapse_top_level_outlines(writer)

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output_resolved, "wb") as f:
        writer.write(f)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Merge two or more PDF files into a single PDF (order is preserved)."
    )
    parser.add_argument("-o", "--output", required=False, help="Output PDF path.")
    parser.add_argument(
        "-f", "--force", action="store_true", help="Overwrite output if it exists."
    )
    parser.add_argument(
        "-m",
        "--manifest",
        help=(
            "Path to manifest (.json, .yml, .yaml). Accepts either a list of files or "
            "an object with options and a 'files' list. Options: output/out, overwrite, toc (bool or object with enabled/include + outline)."
        ),
    )
    parser.add_argument(
        "--toc",
        action="store_true",
        help="Prepend a one-page Table of Contents listing each merged file.",
    )
    parser.add_argument(
        "--toc-outline",
        action="store_true",
        help=(
            "Also add a top-level outline entry pointing to the ToC page (off by default)."
        ),
    )
    parser.add_argument(
        "--pre-toc",
        action="append",
        dest="pre_toc",
        default=[],
        help=(
            "Mark a file to be placed before the ToC (pre-ToC). Defaults: included in outline, not in ToC."
        ),
    )
    # Renamed per-file CLI toggles (set inclusion to True for given paths)
    # Fine-grained per-file ToC/outline inclusion is controlled in the manifest via per-file 'toc' and 'outline' booleans.
    parser.add_argument(
        "inputs",
        nargs="*",
        help="Additional input PDFs (after any manifest items), in order.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    manifest_res: ManifestResult | None = (
        _load_manifest(Path(args.manifest)) if args.manifest else None
    )
    from_manifest: List[InputItem] = manifest_res["items"] if manifest_res else []
    # CLI inputs parsed (labels default to filename; cover flags default False)
    from_cli: List[InputItem] = _inputs_from_cli(args.inputs)

    # Merge lists preserving order, then mark pre-ToC items from CLI --pre-toc
    all_inputs: List[InputItem] = [*from_manifest, *from_cli]
    pre_toc_paths = set(_normalize_paths(getattr(args, "pre_toc", [])))
    for it in all_inputs:
        p = it["path"]
        if p in pre_toc_paths:
            it["pre_toc"] = True  # type: ignore[index]
            # If flipped to pre_toc, default toc becomes False unless explicitly set in manifest
            if "toc" not in it:
                it["toc"] = False  # type: ignore[index]
    # Resolve output path with precedence: CLI > manifest
    out_path_val: Path | None = Path(args.output) if args.output else None
    if out_path_val is None and manifest_res and manifest_res.get("output"):
        out_path_val = manifest_res["output"]
    if out_path_val is None:
        raise ValueError(
            "Output path must be provided via -o/--output or manifest 'output'."
        )

    # Resolve overwrite and toc flags with precedence: CLI true overrides, else manifest
    overwrite_final: bool = bool(
        args.force or (manifest_res and manifest_res.get("overwrite") is True)
    )
    toc_final: bool = bool(
        args.toc or (manifest_res and manifest_res.get("toc") is True)
    )
    # ToC outline toggle (default False). CLI true overrides; else manifest toc_outline true enables
    toc_outline_final: bool = bool(
        getattr(args, "toc_outline", False)
        or (manifest_res and manifest_res.get("toc_outline") is True)
    )

    merge_pdfs(
        inputs=all_inputs,
        output=out_path_val,
        overwrite=overwrite_final,
        add_toc=toc_final,
        toc_outline=toc_outline_final,
    )


if __name__ == "__main__":
    main()
