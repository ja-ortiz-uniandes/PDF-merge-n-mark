"""Filename ordering that matches what the user sees in their file manager."""

from __future__ import annotations

import functools
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, List

_SEGMENT = re.compile(r"(\d+)")


def _natural_key(name: str) -> tuple[Any, ...]:
    """Portable approximation of Explorer ordering.

    Splits the name into digit and non-digit runs so that digit runs compare
    numerically ("2" before "10") and text runs compare case-insensitively.
    """
    parts: List[Any] = []
    for segment in _SEGMENT.split(name):
        if not segment:
            continue
        if segment.isdigit():
            parts.append((0, int(segment), ""))
        else:
            parts.append((1, 0, segment.casefold()))
    return tuple(parts)


def _load_windows_comparer() -> Callable[[str, str], int] | None:
    """Return Explorer's own name comparison, or None when unavailable.

    ``StrCmpLogicalW`` is the shlwapi function Windows Explorer uses to sort
    the Name column, so borrowing it keeps the merge order identical to the
    folder listing the user is looking at.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        compare = ctypes.windll.shlwapi.StrCmpLogicalW  # type: ignore[attr-defined]
        compare.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        compare.restype = ctypes.c_int
        compare("a", "b")  # fail here rather than mid-sort
    except Exception:
        return None
    return compare  # type: ignore[no-any-return]


_WINDOWS_COMPARER = _load_windows_comparer()


def uses_explorer_rules() -> bool:
    """True when sorting uses Explorer's own comparison rather than the fallback."""
    return _WINDOWS_COMPARER is not None


def sort_like_explorer(paths: Iterable[Path]) -> List[Path]:
    """Order paths by filename the way Windows Explorer orders them."""
    items = list(paths)
    comparer = _WINDOWS_COMPARER
    if comparer is not None:

        def compare(a: Path, b: Path) -> int:
            return comparer(a.name, b.name)

        return sorted(items, key=functools.cmp_to_key(compare))
    return sorted(items, key=lambda p: _natural_key(p.name))
