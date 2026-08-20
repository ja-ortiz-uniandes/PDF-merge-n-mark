"""The update notice: correct, cached, and never able to break a merge.

No test here touches the network; the fetch is always injected.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from pdf_combine import updates
from pdf_combine.updates import check_for_update, is_newer, notice, notify_if_outdated


@pytest.mark.parametrize(
    "candidate, current, expected",
    [
        ("v1.3.0", "1.2.0", True),
        ("1.3.0", "1.2.0", True),
        ("v1.2.1", "1.2.0", True),
        ("v2.0.0", "1.9.9", True),
        ("v1.2.0", "1.2.0", False),
        ("v1.1.0", "1.2.0", False),
        ("v1.2", "1.2.0", False),  # padded, so equal
        ("v1.3", "1.2.0", True),
        ("v1.3.0-rc1", "1.2.0", False),  # pre-releases never prompt
        ("garbage", "1.2.0", False),
        ("v1.3.0", "unknown", False),  # running from an uninstalled tree
    ],
)
def test_is_newer(candidate: str, current: str, expected: bool):
    assert is_newer(candidate, current) is expected


def test_reports_a_newer_release(tmp_path: Path):
    latest = check_for_update(
        "1.2.0", now=1000.0, fetch=lambda: "v1.3.0", cache_file=tmp_path / "c.json"
    )
    assert latest == "v1.3.0"


def test_says_nothing_when_current(tmp_path: Path):
    assert (
        check_for_update(
            "1.3.0", now=1000.0, fetch=lambda: "v1.3.0", cache_file=tmp_path / "c.json"
        )
        is None
    )


def test_result_is_cached_for_a_day(tmp_path: Path):
    cache = tmp_path / "c.json"
    calls = 0

    def fetch() -> str:
        nonlocal calls
        calls += 1
        return "v1.3.0"

    check_for_update("1.2.0", now=1000.0, fetch=fetch, cache_file=cache)
    check_for_update("1.2.0", now=1000.0 + 3600, fetch=fetch, cache_file=cache)
    assert calls == 1, "a second run within the day must not hit the network"

    # A day later it asks again
    check_for_update("1.2.0", now=1000.0 + 90_000, fetch=fetch, cache_file=cache)
    assert calls == 2

    stored = json.loads(cache.read_text(encoding="utf-8"))
    assert stored["latest"] == "v1.3.0"


def test_network_failure_is_silent_and_cached(tmp_path: Path):
    cache = tmp_path / "c.json"

    def boom() -> str:
        raise OSError("no network")

    assert check_for_update("1.2.0", now=1.0, fetch=boom, cache_file=cache) is None
    # The failed attempt is recorded, so an offline machine is not hammered
    assert json.loads(cache.read_text(encoding="utf-8"))["latest"] is None


def test_corrupt_cache_is_ignored(tmp_path: Path):
    cache = tmp_path / "c.json"
    cache.write_text("not json", encoding="utf-8")

    assert (
        check_for_update(
            "1.2.0", now=1.0, fetch=lambda: "v1.3.0", cache_file=cache
        )
        == "v1.3.0"
    )


def test_unknown_version_skips_the_check(tmp_path: Path):
    def fetch() -> str:
        raise AssertionError("must not be called")

    assert (
        check_for_update(
            "unknown", now=1.0, fetch=fetch, cache_file=tmp_path / "c.json"
        )
        is None
    )


def test_notice_names_the_versions_and_the_command():
    text = notice("v1.3.0", "1.2.0")
    assert "1.3.0" in text and "1.2.0" in text
    assert "uv tool upgrade pdf-merge-n-mark" in text
    assert "PDFMERGE_NO_UPDATE_CHECK" in text


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_notify_respects_the_opt_out(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(updates.OPT_OUT_ENV, "1")
    monkeypatch.setattr(
        updates, "check_for_update", lambda *a, **k: pytest.fail("should not check")
    )
    stream = _Tty()

    notify_if_outdated(stream)

    assert stream.getvalue() == ""


def test_notify_stays_quiet_when_not_a_terminal(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(updates.OPT_OUT_ENV, raising=False)
    monkeypatch.setattr(
        updates, "check_for_update", lambda *a, **k: pytest.fail("should not check")
    )
    stream = io.StringIO()  # no isatty -> piped output, e.g. a script or CI

    notify_if_outdated(stream)

    assert stream.getvalue() == ""


def test_notify_prints_on_a_terminal(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(updates.OPT_OUT_ENV, raising=False)
    monkeypatch.setattr(updates, "installed_version", lambda: "1.2.0")
    monkeypatch.setattr(updates, "check_for_update", lambda current, **k: "v1.3.0")
    stream = _Tty()

    notify_if_outdated(stream)

    assert "1.3.0 is available" in stream.getvalue()


@pytest.mark.parametrize(
    "argv, expected_code",
    [
        (["--help"], 0),
        (["--version"], 0),
        ([], 1),  # bare invocation prints help
        (["-o", "out.pdf", "missing-a.pdf", "missing-b.pdf"], 1),  # failed run
    ],
)
def test_every_invocation_gets_the_check(
    argv: list[str],
    expected_code: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """Not just merges: --help, --version, a bare run and a failure all check."""
    import merge_pdf

    monkeypatch.chdir(tmp_path)
    calls: list[int] = []
    monkeypatch.setattr(merge_pdf, "notify_if_outdated", lambda *a: calls.append(1))
    monkeypatch.setattr("sys.argv", ["pdfmerge", *argv])

    with pytest.raises(SystemExit) as excinfo:
        merge_pdf.cli()

    capsys.readouterr()
    assert excinfo.value.code == expected_code
    assert calls == [1], f"update check did not run for {argv or 'bare invocation'}"


def test_successful_run_gets_the_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make_pdf, capsys
):
    import merge_pdf

    a = make_pdf("A", pages=1)
    b = make_pdf("B", pages=1)
    monkeypatch.chdir(tmp_path)
    calls: list[int] = []
    monkeypatch.setattr(merge_pdf, "notify_if_outdated", lambda *a: calls.append(1))
    monkeypatch.setattr("sys.argv", ["pdfmerge", "-o", "out.pdf", str(a), str(b)])

    merge_pdf.cli()  # exits normally, no SystemExit

    capsys.readouterr()
    assert calls == [1]


def test_version_flag_reports_the_installed_version(
    capsys: pytest.CaptureFixture[str],
):
    import merge_pdf
    from pdf_combine.marking import installed_version

    with pytest.raises(SystemExit) as excinfo:
        merge_pdf.build_parser().parse_args(["--version"])

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("pdfmerge ")
    assert installed_version() in out


def test_write_cache_is_atomic(tmp_path: Path):
    """Writes go through a temp file + rename, so a reader never observes a
    partially-written (truncated) cache file, and no temp file is left
    behind."""
    cache = tmp_path / "c.json"

    updates._write_cache(cache, {"latest": "v1.3.0"})

    assert json.loads(cache.read_text(encoding="utf-8")) == {"latest": "v1.3.0"}
    assert list(tmp_path.iterdir()) == [cache]


def test_notify_never_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(updates.OPT_OUT_ENV, raising=False)

    def explode(*args: object, **kwargs: object) -> str:
        raise RuntimeError("everything is broken")

    monkeypatch.setattr(updates, "check_for_update", explode)

    notify_if_outdated(_Tty())  # must not raise
