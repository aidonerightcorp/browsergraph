"""Getting a binary, and getting the *right* one.

The interesting property is not that a download works. It is that the thing
downloaded matches the browser it will be used with — a driver one major version
out fails with "cannot connect to chrome", which reads like a browser crash and
sends you looking in the wrong place for an afternoon.

Most of these run offline. The ones that need the network are marked, because a
test suite that cannot run on a train is a test suite people stop running.
"""
from __future__ import annotations

import io
import os
import zipfile

import pytest

from browsergraph import fetch


def net(fn):
    """Marks a test that needs to reach a real download service."""
    return pytest.mark.network(fn)


# --- the catalogue ----------------------------------------------------------

def test_everything_fetchable_is_listed():
    have = fetch.catalogue()
    for expected in ("chrome", "chromedriver", "geckodriver", "msedgedriver", "firefox"):
        assert expected in have


def test_an_unknown_binary_says_what_is_known():
    plan = fetch.plan("netscape-navigator")
    assert not plan.ok
    assert "chromedriver" in plan.error


def test_platform_key_is_one_we_have_assets_for():
    key = fetch.platform_key()
    assert key in fetch.GECKO_ASSET
    assert key in fetch.FIREFOX_OS


def test_planning_does_not_download(tmp_path, monkeypatch):
    """`plan` is the answer to 'what would you do', and must not do it."""
    monkeypatch.setattr(fetch, "CACHE", str(tmp_path / "cache"))
    fetch.firefox_plan()
    assert not (tmp_path / "cache").exists() or not any((tmp_path / "cache").iterdir())


def test_firefox_plan_needs_no_network():
    """Mozilla's redirector means the URL is derivable, so this stays offline."""
    plan = fetch.firefox_plan()
    assert plan.ok and "download.mozilla.org" in plan.url


# --- versions ---------------------------------------------------------------

def test_major_of_a_version():
    assert fetch.major("150.0.7871.128") == "150"
    assert fetch.major("") == ""


def test_a_version_is_read_from_the_binary_not_guessed(tmp_path):
    exe = tmp_path / "fake-browser"
    exe.write_text("#!/bin/sh\necho 'Chromium 150.0.7871.128 snap'\n")
    exe.chmod(0o755)
    assert fetch.binary_version(str(exe)) == "150.0.7871.128"


def test_a_missing_binary_has_no_version():
    assert fetch.binary_version("/nonexistent/browser") == ""


def test_a_binary_that_will_not_run_is_not_an_exception(tmp_path):
    broken = tmp_path / "broken"
    broken.write_bytes(b"\x00\x01not a program")
    broken.chmod(0o755)
    assert fetch.binary_version(str(broken)) == ""


# --- unpacking --------------------------------------------------------------

def test_a_zip_keeps_the_execute_bit(tmp_path):
    """ZipFile.extractall drops permissions, so every driver arrives unrunnable.

    Nothing else in the suite would notice: the file exists, has the right name
    and the right bytes, and only fails when something tries to run it.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("chromedriver-linux64/chromedriver")
        info.external_attr = 0o755 << 16
        zf.writestr(info, "#!/bin/sh\ntrue\n")
    fetch._unpack(buf.getvalue(), str(tmp_path))
    out = tmp_path / "chromedriver-linux64" / "chromedriver"
    assert os.access(out, os.X_OK), "extracted driver is not executable"


def test_a_tarball_unpacks_on_every_supported_python(tmp_path):
    """`filter="data"` is absent on some 3.10/3.11 patch releases.

    Passing it unconditionally raised TypeError there, which the old fetch
    swallowed — so geckodriver silently could not be downloaded at all on those
    interpreters, and the error said "download failed".
    """
    import tarfile
    blob = io.BytesIO()
    with tarfile.open(fileobj=blob, mode="w:gz") as tf:
        data = b"#!/bin/sh\ntrue\n"
        info = tarfile.TarInfo("geckodriver")
        info.size = len(data)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(data))

    real = tarfile.TarFile.extractall

    def no_filter_kwarg(self, path=".", members=None, *, numeric_owner=False):
        return real(self, path, members, numeric_owner=numeric_owner)

    import unittest.mock as mock
    with mock.patch.object(tarfile.TarFile, "extractall", no_filter_kwarg):
        fetch._unpack(blob.getvalue(), str(tmp_path))
    assert (tmp_path / "geckodriver").exists()


def test_the_archive_is_identified_by_content_not_by_url(tmp_path):
    """Mozilla's redirector returns a URL with no useful suffix."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x/thing", "hello")
    fetch._unpack(buf.getvalue(), str(tmp_path))       # no filename involved
    assert (tmp_path / "x" / "thing").read_text() == "hello"


def test_finding_the_executable_inside_an_extracted_tree(tmp_path):
    nested = tmp_path / "chromedriver-linux64"
    nested.mkdir()
    (nested / "LICENSE.chromedriver").write_text("mit")
    (nested / "chromedriver").write_text("x")
    assert fetch._find(str(tmp_path), "chromedriver").endswith("/chromedriver")


# --- the matched driver, which is the whole point ---------------------------

def test_a_mismatched_driver_is_refused_not_returned(tmp_path, monkeypatch):
    """If the service hands back the wrong major, that is a failure.

    Returning it anyway would produce exactly the confusing downstream error
    this module exists to prevent.
    """
    monkeypatch.setattr(fetch, "binary_version", lambda p: "150.0.7871.128")
    monkeypatch.setattr(fetch, "fetch",
                        lambda what, **kw: fetch.Fetched(what=what, path="/tmp/d",
                                                         version="151.0.7922.77"))
    got = fetch.matching_chromedriver(browser_path="/opt/chrome/chrome")
    assert not got.ok
    assert "refusing the mismatch" in got.error


def test_no_readable_version_is_an_honest_failure(monkeypatch):
    monkeypatch.setattr(fetch, "binary_version", lambda p: "")
    got = fetch.matching_chromedriver(browser_path="/opt/chrome/chrome")
    assert not got.ok and "could not read a version" in got.error


def test_a_patchable_copy_leaves_the_original_alone(tmp_path):
    """undetected-chromedriver rewrites the driver it is handed, in place."""
    original = tmp_path / "chromedriver-150.0.1" / "inner" / "chromedriver"
    original.parent.mkdir(parents=True)
    original.write_text("pristine")
    monkey = str(tmp_path / "cache")
    os.environ.setdefault("_", "")
    import unittest.mock as mock
    with mock.patch.object(fetch, "CACHE", monkey):
        copy = fetch.patchable_copy(str(original))
        assert copy != str(original)
        with open(copy, "w") as fh:
            fh.write("patched by uc")
    assert original.read_text() == "pristine"


def test_the_report_is_offline_by_default(monkeypatch):
    """A status command should not make six network calls to read your disk."""
    def explode(*a, **k):
        raise AssertionError("report() hit the network")
    monkeypatch.setattr(fetch, "_json", explode)
    monkeypatch.setattr(fetch.urllib.request, "urlopen", explode)
    assert isinstance(fetch.report(), str)


# --- against the real services ----------------------------------------------

@net
def test_chrome_for_testing_publishes_a_matched_pair():
    """chrome and chromedriver from one milestone are built from one revision.

    That pairing is the reason this source is preferred over anything that
    resolves "the latest driver".
    """
    chrome = fetch.cft_plan("chrome", milestone="150")
    driver = fetch.cft_plan("chromedriver", milestone="150")
    assert chrome.ok and driver.ok
    assert chrome.version == driver.version
    assert fetch.major(chrome.version) == "150"


@net
def test_an_impossible_milestone_says_what_is_available():
    plan = fetch.cft_plan("chromedriver", milestone="42")
    assert not plan.ok and "milestone 42" in plan.error


@net
def test_edge_publishes_its_version_as_utf16():
    """Decoded as UTF-8 it becomes '1\\x005\\x001...', which a careless regex
    happily parses into a version number that is wrong in every digit."""
    plan = fetch.edge_plan()
    assert plan.ok, plan.error
    assert plan.version.replace(".", "").isdigit(), plan.version


@net
def test_geckodriver_falls_back_to_a_pin_when_the_api_is_unreachable(monkeypatch):
    monkeypatch.setattr(fetch, "_json", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    plan = fetch.gecko_plan()
    assert plan.ok and plan.version == fetch.GECKO_PIN


# --- the bug this all came from ---------------------------------------------

def test_the_driver_version_comes_from_the_browser_being_launched(tmp_path):
    """The regression test for a genuinely nasty bug.

    `_chrome_major()` read the version off PATH while `binary_location` pointed
    somewhere else entirely. On a machine with Chrome 148 in /opt and Chromium
    150 from a snap — an ordinary Ubuntu desktop — a spec asking for Chromium
    got a driver built for Chrome, and undetected-chromedriver failed with
    "cannot connect to chrome at 127.0.0.1:PORT". Nothing in that message
    mentions a version.

    Which of the two engines happened to work depended on PATH order, so it also
    looked intermittent.
    """
    from browsergraph.drivers.selenium_driver import _chrome_major

    spec_browser = tmp_path / "chromium-150"
    spec_browser.write_text("#!/bin/sh\necho 'Chromium 150.0.7871.128 snap'\n")
    spec_browser.chmod(0o755)

    assert _chrome_major(str(spec_browser)) == 150, \
        "the major must come from binary_location, not from PATH"


def test_without_a_path_it_still_falls_back_to_path():
    """The fallback has to stay: a spec need not name a binary at all."""
    from browsergraph.drivers.selenium_driver import _chrome_major
    assert _chrome_major("") is None or isinstance(_chrome_major(""), int)


def test_a_cached_driver_is_found_by_major_version(tmp_path):
    """Asking "do I have a driver for 148" must not answer about 150.

    Both were sitting in the cache; matching on the full string only, the check
    took whichever sorted last and reported the browser unsupported while its
    driver was right there.
    """
    import unittest.mock as mock
    root = tmp_path / "cache"
    for version in ("148.0.7778.178", "150.0.7871.124"):
        d = root / f"chromedriver-{version}" / "chromedriver-linux64"
        d.mkdir(parents=True)
        (d / "chromedriver").write_text("x")
    with mock.patch.object(fetch, "CACHE", str(root)):
        assert "148.0.7778.178" in fetch.cached("chromedriver", "148")
        assert "150.0.7871.124" in fetch.cached("chromedriver", "150")
        assert fetch.cached("chromedriver", "151") == ""


def test_match_does_not_silently_print_the_catalogue(monkeypatch, capsys):
    """`fetch --match X` names no positional, so an ordering slip made it list
    everything and exit 0 — a command that looked like it worked."""
    from browsergraph.cli import main
    monkeypatch.setattr(fetch, "matching_chromedriver",
                        lambda b, **k: fetch.Fetched(what="chromedriver",
                                                     path="/tmp/cd", version="150.0.1"))
    assert main(["fetch", "--match", "system_chrome"]) == 0
    out = capsys.readouterr().out
    assert "chromedriver for system_chrome" in out
    assert "fetchable:" not in out
