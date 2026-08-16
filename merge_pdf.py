#!/usr/bin/env python3
# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, List, Sequence, TypedDict

import yaml  # type: ignore[import-not-found]
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link  # type: ignore[import-not-found]
from pypdf.generic import Fit  # type: ignore[import-not-found]

from pdf_combine.naming import sort_like_explorer
from pdf_combine.processing import (
    import_outline_from_reader,
    rebuild_outline_under_parent,
    update_outline_counts,
)

# Optional: reportlab for generating the ToC page
try:
    from reportlab.lib.pagesizes import letter  # type: ignore[import-not-found]
    from reportlab.pdfgen import canvas  # type: ignore[import-not-found]
except ModuleNotFoundError:
    letter = None  # type: ignore[assignment]
    canvas = None  # type: ignore[assignment]


# --- Table of Contents layout ---------------------------------------------
# Shared by the page renderer and the link-annotation pass. Both must agree on
# every value or the clickable rows drift away from the text they cover.
TOC_TITLE = "Table of Contents"
TOC_PAGE_SIZE: tuple[float, float] = (
    (float(letter[0]), float(letter[1])) if letter is not None else (612.0, 792.0)
)
TOC_MARGIN = 54.0
TOC_LINE_GAP = 16.0
TOC_TITLE_FONT = ("Helvetica-Bold", 18)
TOC_ENTRY_FONT = ("Helvetica", 11)
TOC_PAGE_NUMBER_GUTTER = 60.0  # room reserved on the right for page numbers


# New: structured input item so we can carry an optional label
class InputItem(TypedDict):
    path: Path
    label: str | None
    pre_toc: bool  # if true, place before ToC; default: not in ToC, in outline
    toc: bool  # include in ToC entries/links (default True; if pre_toc, default False)
    outline: bool  # include in outline (default True)
    toc_explicit: bool  # whether 'toc' was stated in the manifest rather than defaulted


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


def _path_key(path: Path) -> str:
    """Comparison key for identifying the same file across inputs.

    Uses ``normcase`` so the Windows-style case-insensitive filesystem does not
    treat ``A.pdf`` and ``a.pdf`` as two different inputs.
    """
    return os.path.normcase(str(path))


def _item_title(item: InputItem) -> str:
    return str(item["label"] or item["path"].stem)


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

        toc_explicit_val = True
        if "toc" in item:
            if isinstance(item.get("toc"), dict):
                raise ValueError(
                    "Per-file 'toc' must be a boolean; the object form "
                    "({enabled, include, outline}) is only valid at the top level "
                    f"of the manifest. Offending entry: {item['file']}"
                )
            toc_item_val: bool = bool(item.get("toc"))
        elif "in_toc" in item:
            toc_item_val = bool(item.get("in_toc"))
        elif "toc_exclude" in item:
            toc_item_val = not bool(item.get("toc_exclude"))
        else:
            toc_item_val = not pre_toc_val  # True normally; False for pre_toc
            toc_explicit_val = False
        items.append(
            {
                "path": normalized_path,
                "label": label_val,
                "pre_toc": pre_toc_val,
                "toc": toc_item_val,
                "outline": outline_val,
                "toc_explicit": toc_explicit_val,
            }
        )

    return {
        "items": items,
        "output": manifest_output,
        "overwrite": manifest_overwrite,
        "toc": manifest_toc,
        "toc_outline": manifest_toc_outline,
    }


def _inputs_from_cli(
    paths: Iterable[str | Path], *, pre_toc: bool = False, base: Path | None = None
) -> List[InputItem]:
    """Turn CLI paths into input items.

    Relative paths resolve against the current directory, or against `base` in
    folder mode, where a bare filename means "in the folder being merged".
    Labels are always the filename stem: custom labels require a manifest.
    """
    out: List[InputItem] = []
    for pp in _normalize_paths(paths, base=base):
        out.append(
            {
                "path": pp,
                "label": None,
                "pre_toc": pre_toc,
                # Defaults: outline True; toc True unless pre_toc
                "toc": not pre_toc,
                "outline": True,
                "toc_explicit": False,
            }
        )
    return out


def default_output_for(directory: Path) -> Path:
    """Output path used by --here: the folder's own name."""
    directory = directory.resolve()
    stem = directory.name or "merged"  # a drive root has no name
    return directory / f"{stem}.pdf"


def find_pdfs(directory: Path, *, exclude: Iterable[Path] = ()) -> List[Path]:
    """PDFs directly inside `directory`, ordered the way Explorer orders them.

    Not recursive. `exclude` drops paths that are handled separately - the
    output file itself, and anything already named by --pre-toc.
    """
    skip = {_path_key(p) for p in exclude}
    found = [
        entry.resolve()
        for entry in directory.iterdir()
        if entry.is_file() and entry.suffix.lower() == ".pdf"
    ]
    return sort_like_explorer(p for p in found if _path_key(p) not in skip)


def _inputs_from_directory(
    directory: Path, *, exclude: Iterable[Path] = ()
) -> List[InputItem]:
    """Every PDF in `directory` as input items, in Explorer order."""
    return _inputs_from_cli(find_pdfs(directory, exclude=exclude))


MANIFEST_HEADER = """\
# Generated by `pdfmerge --here --write-manifest`.
# Reorder or delete entries, then run: pdfmerge -m {name}
#
# Per-file keys:
#   label:    outline title for the file (defaults to the filename)
#   pre_toc:  place before the ToC; then toc defaults to false
#   toc:      list this file in the Table of Contents (default true)
#   outline:  give this file an outline entry (default true)
"""


def _manifest_path_value(path: Path, base: Path) -> str:
    """Shortest way to name `path` from a manifest living in `base`.

    Manifest paths resolve against the manifest's own folder, so a plain
    filename is enough for anything alongside it.
    """
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def build_manifest_text(pdfs: Sequence[Path], output: Path, target: Path) -> str:
    """Render an editable manifest listing `pdfs` in order."""

    def quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    base = target.parent
    lines = [MANIFEST_HEADER.format(name=target.name)]
    lines.append(f"output: {quote(_manifest_path_value(output, base))}")
    lines.append("overwrite: false")
    lines.append("toc: false # or: toc: {enabled: true, outline: true}")
    lines.append("files:")
    for pdf in pdfs:
        lines.append(f"  - file: {quote(_manifest_path_value(pdf, base))}")
        lines.append(f"    label: {quote(pdf.stem)}")
    return "\n".join(lines) + "\n"


def write_manifest(
    target: Path, pdfs: Sequence[Path], output: Path, *, overwrite: bool = False
) -> None:
    if not overwrite and target.exists():
        raise FileExistsError(f"Manifest already exists: {target} (use -f to replace)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build_manifest_text(pdfs, output, target), encoding="utf-8")


def _combine_inputs(
    pre_toc_items: Sequence[InputItem],
    manifest_items: Sequence[InputItem],
    cli_items: Sequence[InputItem],
) -> List[InputItem]:
    """Build the final input list from the three sources.

    A path given via ``--pre-toc`` is merged as an input in its own right. When
    that same path is also named positionally or in the manifest, the richer
    entry wins (keeping its label and inclusion flags) and merely gains
    ``pre_toc``; ``toc`` then flips to False unless the manifest stated it.
    """
    ordered: List[InputItem] = []
    by_path: dict[str, InputItem] = {}
    for item in [*manifest_items, *cli_items]:
        key = _path_key(item["path"])
        if key in by_path:
            continue  # same file listed twice: keep the first entry
        by_path[key] = item
        ordered.append(item)

    added_pre_toc: List[InputItem] = []
    for item in pre_toc_items:
        key = _path_key(item["path"])
        existing = by_path.get(key)
        if existing is not None:
            existing["pre_toc"] = True
            if not existing["toc_explicit"]:
                existing["toc"] = False
            continue
        by_path[key] = item
        added_pre_toc.append(item)

    # Pre-ToC files introduced by the flag lead, in flag order.
    return [*added_pre_toc, *ordered]


def _open_reader(path: Path) -> PdfReader:
    """Open an input PDF, decrypting empty-password documents.

    ``PdfReader.decrypt`` reports failure through its return value rather than
    by raising, so the result has to be inspected: otherwise a genuinely
    password-protected file slips through and fails later with an opaque error.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Missing file: {path}")
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            result = reader.decrypt("")  # type: ignore[no-untyped-call]
        except Exception as e:
            raise ValueError(f"Encrypted PDF requires a password: {path}") from e
        if not result:
            raise ValueError(f"Encrypted PDF requires a password: {path}")
    return reader


def _toc_capacity() -> int:
    """How many entries fit on the single ToC page."""
    _, height = TOC_PAGE_SIZE
    first = height - TOC_MARGIN - 2 * TOC_LINE_GAP
    floor = TOC_MARGIN + TOC_LINE_GAP
    if first < floor:
        return 0
    return int((first - floor) // TOC_LINE_GAP) + 1


def _toc_row_positions(count: int) -> List[float]:
    """Text baselines for the first ``count`` ToC rows, top to bottom."""
    _, height = TOC_PAGE_SIZE
    first = height - TOC_MARGIN - 2 * TOC_LINE_GAP
    return [first - i * TOC_LINE_GAP for i in range(min(count, _toc_capacity()))]


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
    width, height = TOC_PAGE_SIZE
    c = canvas.Canvas(buf, pagesize=(width, height))

    left = TOC_MARGIN
    right = width - TOC_MARGIN

    # Title
    c.setFont(*TOC_TITLE_FONT)
    c.drawString(left, height - TOC_MARGIN, TOC_TITLE)

    # Entries
    font_name, font_size = TOC_ENTRY_FONT
    c.setFont(font_name, font_size)

    max_title_width = right - left - TOC_PAGE_NUMBER_GUTTER
    for y, (title, page_num) in zip(_toc_row_positions(len(entries)), entries):
        # Truncate title if too wide
        text = title
        while (
            c.stringWidth(text, font_name, font_size) > max_title_width
            and len(text) > 3
        ):
            text = text[:-4] + "…"
        page_str = str(page_num)
        number_x = right - c.stringWidth(page_str, font_name, font_size)
        c.drawString(left, y, text)
        # draw dots between end of title and page number
        dots_start = left + c.stringWidth(text, font_name, font_size) + 6
        dots_end = number_x - 6
        if dots_end > dots_start:
            dot_w = c.stringWidth(".", font_name, font_size)
            n_dots = int((dots_end - dots_start) / dot_w)
            c.drawString(dots_start, y, "." * max(0, n_dots))
        c.drawString(number_x, y, page_str)

    c.showPage()
    c.save()
    buf.seek(0)
    return PdfReader(buf)


def _add_toc_links(
    writer: PdfWriter, toc_page_index: int, destinations: Sequence[int]
) -> None:
    """Overlay a clickable rectangle on each rendered ToC row."""
    width, _ = TOC_PAGE_SIZE
    left = TOC_MARGIN
    right = width - TOC_MARGIN
    for y, dest in zip(_toc_row_positions(len(destinations)), destinations):
        annotation = Link(
            rect=(left, y - 2.0, right, y + 12.0),
            target_page_index=dest,
            fit=Fit(fit_type="/Fit"),
        )
        writer.add_annotation(page_number=toc_page_index, annotation=annotation)


def _collect_toc_entries(
    pre_tocs: Sequence[InputItem],
    normals: Sequence[InputItem],
    reader_for: Callable[[InputItem], PdfReader],
) -> List[tuple[str, int]]:
    """Build (title, 1-based page number) rows for the ToC.

    Pre-ToC items sit before the ToC page and so count from page 1; everything
    else is offset by the pre-ToC pages plus the single ToC page.
    """
    entries: List[tuple[str, int]] = []

    pre_pages_total = 0
    for item in pre_tocs:
        count = len(reader_for(item).pages)
        if item["toc"]:
            entries.append((_item_title(item), 1 + pre_pages_total))
        pre_pages_total += count

    first_normal_page = pre_pages_total + 1 + 1  # pre-ToC pages + ToC page + 1-based
    running = 0
    for item in normals:
        count = len(reader_for(item).pages)
        if item["toc"]:
            entries.append((_item_title(item), first_normal_page + running))
        running += count

    return entries


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

    # Each input is read once and reused for both the page-count pass and the
    # merge pass.
    readers: dict[str, PdfReader] = {}

    def reader_for(item: InputItem) -> PdfReader:
        key = _path_key(item["path"])
        reader = readers.get(key)
        if reader is None:
            reader = _open_reader(item["path"])
            readers[key] = reader
        return reader

    # Separate pre-ToC (formerly covers) and normal items
    pre_tocs: List[InputItem] = [it for it in inputs if it["pre_toc"]]
    normals: List[InputItem] = [it for it in inputs if not it["pre_toc"]]

    # ToC entries are computed up front: page numbers have to be known before
    # any page is written.
    toc_entries: List[tuple[str, int]] = []
    if add_toc:
        toc_entries = _collect_toc_entries(pre_tocs, normals, reader_for)
        capacity = _toc_capacity()
        if len(toc_entries) > capacity:
            raise ValueError(
                f"Table of Contents does not fit on one page: {len(toc_entries)} "
                f"entries, at most {capacity} fit. Exclude entries with "
                "'toc: false' in the manifest, or drop --toc."
            )

    writer: PdfWriter = PdfWriter()
    destinations: List[int] = []  # start pages (0-based) for ToC-linked items

    def append_item(item: InputItem) -> None:
        reader = reader_for(item)
        start_index = len(writer.pages)
        if add_toc and item["toc"]:
            destinations.append(start_index)

        if not item["outline"]:
            # Append pages only; no outline for this item
            for page in reader.pages:
                writer.add_page(page)
            return

        # Rebuild this input's outline under a top-level label, then round-trip
        # it through memory: the collapsed state only survives as the sign of
        # /Count in a serialized document, and the rebuilt destinations only
        # resolve once they have been written out and read back.
        buf = io.BytesIO()
        rebuild_outline_under_parent(reader, label=_item_title(item)).write(buf)
        buf.seek(0)
        rebuilt = PdfReader(buf)
        for page in rebuilt.pages:
            writer.add_page(page)
        import_outline_from_reader(rebuilt, writer, page_offset=start_index)

    # Pre-ToC items first, in order
    for item in pre_tocs:
        append_item(item)

    # Insert ToC after pre-ToC items
    toc_page_index: int | None = None
    if add_toc and toc_entries:
        toc_reader = _build_toc_pdf(toc_entries)
        toc_page_index = len(writer.pages)
        for page in toc_reader.pages:
            writer.add_page(page)
        # Optionally add an outline entry for the ToC itself (top-level)
        if toc_outline:
            writer.add_outline_item(
                title=TOC_TITLE,
                page_number=toc_page_index,
                fit=Fit(fit_type="/Fit"),
            )

    # Then the normal items
    for item in normals:
        append_item(item)

    # Make ToC lines clickable (single-page ToC)
    if toc_page_index is not None:
        if len(destinations) != len(toc_entries):
            raise RuntimeError(
                "Internal error: ToC rows and link targets are out of step "
                f"({len(toc_entries)} rows, {len(destinations)} targets)."
            )
        _add_toc_links(writer, toc_page_index, destinations)

    # Restate /Count across the whole tree so viewers honour the collapsed state.
    update_outline_counts(writer)

    output_resolved.parent.mkdir(parents=True, exist_ok=True)
    with open(output_resolved, "wb") as f:
        writer.write(f)


DESCRIPTION = """\
Merge two or more PDFs into one document, with an optional linked Table of
Contents and an outline that nests each input's own bookmarks under a
top-level label.

Pick the inputs one of three ways: --here for every PDF in a folder, a manifest
for an explicit order and custom labels, or plain file arguments.
"""

EPILOG = """\
manifest keys (.json, .yml, .yaml)
  A manifest is either a bare list of file entries, or an object with global
  options plus a 'files' list. Paths inside it resolve against the manifest's
  own folder. CLI flags win over manifest values.

  global:
    output, out    output PDF path
    overwrite      replace the output if it exists (same as -f)
    toc            true/false, or {enabled|include: bool, outline: bool}

  per file:
    file           path to the PDF (required)
    label          outline title for this file      (default: the filename)
    bookmark       deprecated alias for label
    pre_toc        place this file before the ToC   (default: false)
    toc            list this file in the ToC        (default: true, or false
                                                     when pre_toc is true)
    outline        give this file an outline entry  (default: true)

examples
  Merge everything in this folder, in Explorer's order, no ToC:
    pdfmerge --here

  Same, with a Table of Contents, replacing a previous run:
    pdfmerge --here --toc -f

  Another folder, without changing directory, naming the output:
    pdfmerge --here "C:\\Cases\\Smith" --toc -o "Case 42.pdf"

  Wrong order? Write a manifest, edit it, then merge:
    pdfmerge --here --write-manifest
    notepad manifest.yml
    pdfmerge -m manifest.yml

  A cover page ahead of the ToC, then the rest of the folder:
    pdfmerge --here --toc --pre-toc cover.pdf

  Explicit files, explicit output:
    pdfmerge -o merged.pdf --toc A.pdf B.pdf
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdfmerge",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--here",
        nargs="?",
        const=".",
        default=None,
        metavar="DIR",
        help=(
            "Merge every PDF in a folder, ordered the way Windows Explorer "
            "orders them. Defaults to the current folder; pass a path to work on "
            "another one. Not recursive. Cannot be combined with -m or with file "
            "arguments."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        required=False,
        metavar="PATH",
        help=(
            "Output PDF path. Required unless --here or a manifest supplies one. "
            "With --here the default is the folder's own name, e.g. C:\\Cases\\Smith "
            "gives Smith.pdf, and a relative path is taken as relative to that "
            "folder rather than to the current one."
        ),
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help=(
            "Overwrite the output if it exists (also replaces an existing file "
            "when used with --write-manifest)."
        ),
    )
    parser.add_argument(
        "-m",
        "--manifest",
        metavar="PATH",
        help=(
            "Read the file list and options from a manifest (.json, .yml, "
            ".yaml). See 'manifest keys' below. CLI flags override it."
        ),
    )
    parser.add_argument(
        "--write-manifest",
        nargs="?",
        const="manifest.yml",
        metavar="PATH",
        help=(
            "With --here: write an editable manifest listing the folder's PDFs "
            "in order and exit without merging. Defaults to manifest.yml in that "
            "same folder; use -f to replace an existing one."
        ),
    )
    parser.add_argument(
        "--toc",
        action="store_true",
        help=(
            "Prepend a one-page Table of Contents with a clickable row per "
            "merged file. Off by default. The page holds a fixed number of rows "
            f"({_toc_capacity()} on US Letter); exceeding it is an error."
        ),
    )
    parser.add_argument(
        "--toc-outline",
        action="store_true",
        help="Also add a top-level outline entry pointing at the ToC page. Off by default.",
    )
    parser.add_argument(
        "--pre-toc",
        action="append",
        dest="pre_toc",
        default=[],
        metavar="PATH",
        help=(
            "Merge a file before the ToC, e.g. a cover. Repeat the flag per "
            "file; they lead in flag order. The flag merges the file on its own, "
            "so do not repeat it as an argument. Such files get an outline label "
            "but are left out of the ToC."
        ),
    )
    # Fine-grained per-file ToC/outline inclusion is controlled in the manifest
    # via per-file 'toc' and 'outline' booleans.
    parser.add_argument(
        "inputs",
        nargs="*",
        metavar="PDF",
        help="Input PDFs, in order, appended after any manifest entries.",
    )
    return parser


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not (args.here or args.manifest or args.inputs or args.pre_toc):
        parser.print_help()
        raise SystemExit(1)

    if args.here is not None and (args.manifest or args.inputs):
        raise ValueError(
            "--here merges the whole folder, so it cannot be combined with "
            "-m/--manifest or with file arguments."
        )
    if args.write_manifest and args.here is None:
        raise ValueError("--write-manifest is only available together with --here.")

    here_dir: Path | None = None
    if args.here is not None:
        here_dir = Path(args.here).resolve()
        if not here_dir.is_dir():
            raise NotADirectoryError(f"--here needs a folder, got: {here_dir}")

    manifest_res: ManifestResult | None = (
        _load_manifest(Path(args.manifest)) if args.manifest else None
    )
    from_manifest: List[InputItem] = manifest_res["items"] if manifest_res else []
    from_cli: List[InputItem] = _inputs_from_cli(args.inputs)
    from_pre_toc: List[InputItem] = _inputs_from_cli(
        args.pre_toc, pre_toc=True, base=here_dir
    )

    # Resolve output path with precedence: CLI > manifest > --here folder name.
    # In folder mode a relative -o belongs to that folder, matching the default.
    out_path_val: Path | None = None
    if args.output:
        out_path_val = Path(args.output)
        if here_dir is not None and not out_path_val.is_absolute():
            out_path_val = here_dir / out_path_val
    if out_path_val is None and manifest_res and manifest_res.get("output"):
        out_path_val = manifest_res["output"]
    if out_path_val is None and here_dir is not None:
        out_path_val = default_output_for(here_dir)
    if out_path_val is None:
        raise ValueError(
            "Output path must be provided via -o/--output or manifest 'output'."
        )

    if here_dir is not None:
        # The output must never be fed back into itself, and --pre-toc files
        # are added separately so they can lead.
        skip = [out_path_val.resolve(), *(it["path"] for it in from_pre_toc)]
        from_cli = _inputs_from_directory(here_dir, exclude=skip)

        if args.write_manifest:
            target = Path(args.write_manifest)
            if not target.is_absolute():
                target = here_dir / target
            pdfs = [it["path"] for it in [*from_pre_toc, *from_cli]]
            if not pdfs:
                raise ValueError(f"No PDFs found in {here_dir}.")
            write_manifest(target, pdfs, out_path_val, overwrite=args.force)
            print(f"Wrote {target} listing {len(pdfs)} PDFs. Edit it, then run:")
            print(f"  pdfmerge -m {target.name}")
            return

        found = len(from_pre_toc) + len(from_cli)
        if found < 2:
            raise ValueError(
                f"--here needs at least two PDFs in {here_dir}, found {found}. "
                f"The output ({out_path_val.name}) is never merged into itself."
            )

    all_inputs: List[InputItem] = _combine_inputs(from_pre_toc, from_manifest, from_cli)

    # Resolve overwrite and toc flags with precedence: CLI true overrides, else manifest
    overwrite_final: bool = bool(
        args.force or (manifest_res and manifest_res.get("overwrite") is True)
    )
    toc_final: bool = bool(
        args.toc or (manifest_res and manifest_res.get("toc") is True)
    )
    # ToC outline toggle (default False). CLI true overrides; else manifest toc_outline true enables
    toc_outline_final: bool = bool(
        args.toc_outline or (manifest_res and manifest_res.get("toc_outline") is True)
    )

    merge_pdfs(
        inputs=all_inputs,
        output=out_path_val,
        overwrite=overwrite_final,
        add_toc=toc_final,
        toc_outline=toc_outline_final,
    )


def cli() -> None:
    """Console-script entry point: report failures as messages, not tracebacks."""
    try:
        main()
    except (
        ValueError,
        FileNotFoundError,
        FileExistsError,
        ModuleNotFoundError,
        OSError,
    ) as e:
        print(f"pdfmerge: {e}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    cli()
