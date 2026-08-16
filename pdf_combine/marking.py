"""Marking merged output so a later folder scan can recognise it.

A merged PDF is indistinguishable from any other PDF, so `--here` would happily
feed yesterday's output back in as today's input. Stamping our own output with
a private Info-dictionary key lets the scan skip it.

Only folder scans consult this. Files named explicitly - in a manifest, as
arguments, or via --pre-toc - are always merged, marker or not.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

# Private key in the document Info dictionary. Custom keys are permitted there
# (PDF 32000-1 14.3.3), and viewers ignore ones they do not know.
MARKER_KEY = "/PDFMergeNMark"
PRODUCER = "pdfmerge"


def installed_version() -> str:
    """The installed release, or "unknown" when running from an uninstalled tree."""
    try:
        from importlib.metadata import version

        return version("pdf-merge-n-mark")
    except Exception:
        return "unknown"


def stamp_output(writer: PdfWriter) -> None:
    """Record that this document is our own merged output."""
    release = installed_version()
    writer.add_metadata(
        {
            "/Producer": f"{PRODUCER} {release}",
            MARKER_KEY: release,
        }
    )


def is_our_output(path: Path) -> bool:
    """Whether `path` looks like a PDF this tool produced.

    Errors are swallowed deliberately: an unreadable or encrypted file is
    reported as "not ours" so the scan keeps it, and the merge raises a proper
    error later instead of the file disappearing silently here.
    """
    try:
        metadata: Any = PdfReader(str(path)).metadata
    except Exception:
        return False
    if not metadata:
        return False
    if MARKER_KEY in metadata:
        return True
    producer = metadata.get("/Producer")
    return isinstance(producer, str) and producer.startswith(PRODUCER)
