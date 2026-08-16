"""Tell the user when a newer release exists.

Deliberately passive: it prints a notice and the command to run. It never
upgrades by itself - on Windows the running `pdfmerge.exe` is locked, so a
self-upgrade would fail halfway - and it never blocks or delays a merge.

Every failure mode is silent. Being offline, behind a proxy, or rate-limited
must never turn a working merge into an error.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from pdf_combine.marking import installed_version

RELEASES_API = (
    "https://api.github.com/repos/ja-ortiz-uniandes/PDF-merge-n-mark/releases/latest"
)
UPGRADE_COMMAND = "uv tool upgrade pdf-merge-n-mark"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
NETWORK_TIMEOUT_SECONDS = 2.0
OPT_OUT_ENV = "PDFMERGE_NO_UPDATE_CHECK"


def _parse(version: str) -> tuple[int, ...] | None:
    core = version.strip().lstrip("vV").split("-")[0].split("+")[0]
    try:
        return tuple(int(part) for part in core.split("."))
    except ValueError:
        return None


def is_newer(candidate: str, current: str) -> bool:
    """Whether `candidate` is a later release than `current`.

    Pre-releases (`v1.3.0-rc1`) never trigger a notice: someone running the
    stable tool has not opted into testing.
    """
    if "-" in candidate.strip().lstrip("vV"):
        return False
    new, old = _parse(candidate), _parse(current)
    if new is None or old is None:
        return False
    width = max(len(new), len(old))
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def cache_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "pdfmerge" / "update-check.json"


def _read_cache(path: Path) -> dict[str, Any]:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass  # a cache we cannot write is not worth an error


def fetch_latest_tag() -> str | None:
    """Latest release tag from GitHub, or None if it cannot be determined."""
    request = urllib.request.Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "pdfmerge-update-check",
        },
    )
    with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
        payload: Any = json.loads(response.read().decode("utf-8"))
    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    return tag if isinstance(tag, str) else None


def check_for_update(
    current: str,
    *,
    now: float | None = None,
    fetch: Callable[[], str | None] = fetch_latest_tag,
    cache_file: Path | None = None,
    force: bool = False,
) -> str | None:
    """Return the newer tag if one exists, else None.

    Consults a cache first so the network is touched at most once a day.
    """
    if _parse(current) is None:
        return None  # running from source, no meaningful version to compare
    moment = time.time() if now is None else now
    path = cache_path() if cache_file is None else cache_file

    cache = _read_cache(path)
    checked_at = cache.get("checked_at")
    fresh = (
        not force
        and isinstance(checked_at, (int, float))
        and moment - checked_at < CHECK_INTERVAL_SECONDS
    )
    if fresh:
        tag = cache.get("latest")
    else:
        try:
            tag = fetch()
        except Exception:
            tag = None
        _write_cache(path, {"checked_at": moment, "latest": tag})

    if not isinstance(tag, str):
        return None
    return tag if is_newer(tag, current) else None


def notice(latest: str, current: str) -> str:
    return (
        f"\npdfmerge {latest.lstrip('vV')} is available (you have {current}).\n"
        f"  Update with: {UPGRADE_COMMAND}\n"
        f"  Silence this with {OPT_OUT_ENV}=1\n"
    )


def notify_if_outdated(stream: Any = None) -> None:
    """Print an update notice on stderr, if one is warranted.

    Skipped when the user opted out, and when stderr is not a terminal, so
    scripts and CI logs stay clean.
    """
    out = sys.stderr if stream is None else stream
    try:
        if os.environ.get(OPT_OUT_ENV):
            return
        if not getattr(out, "isatty", lambda: False)():
            return
        current = installed_version()
        latest = check_for_update(current)
        if latest:
            print(notice(latest, current), file=out)
    except Exception:
        pass  # an update check must never affect the exit status
