"""Stremio add-ons kept by their address (watch_addons.py, server-contract
§6.54 "Stremio add-ons"). The manifest is never fetched from the internet:
`watch_addons._get` is a stub here. The list is kept under XDG_STATE_HOME."""

from __future__ import annotations

import pytest

from agent_media_core import watch_addons


@pytest.fixture(autouse=True)
def _state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


MANIFEST = {"id": "com.stremio.torrentio.addon", "name": "Torrentio", "version": "0.0.15",
            "logo": "https://torrentio.strem.fun/images/logo_v1.png",
            "resources": [{"name": "stream"}], "types": ["movie", "series"]}
TST = "https://torrentio.strem.fun/manifest.json"


def _stub(monkeypatch, answer=None, error=None):
    seen: list = []
    md = MANIFEST if answer is None else answer

    def get(url, timeout=15.0):
        seen.append(url)
        if error:
            raise OSError(error)
        return dict(md)
    monkeypatch.setattr(watch_addons, "_get", get)
    return seen


@pytest.mark.parametrize("raw,want", [
    (TST, TST),
    ("torrentio.strem.fun", TST),
    ("https://torrentio.strem.fun", TST),
    ("stremio://torrentio.strem.fun/manifest.json", TST),
    ("  torrentio.strem.fun/  ", TST),
    ("http://host.tv/a/manifest.json", "http://host.tv/a/manifest.json"),
])
def test_an_address_is_read_as_its_manifest(raw, want):
    assert watch_addons._address(raw) == want


@pytest.mark.parametrize("raw", ["", "ftp://x/y", "file:///x", "https://"])
def test_a_bad_address(raw):
    with pytest.raises(ValueError):
        watch_addons._address(raw)


def test_add_lists_a_row_and_does_not_duplicate(monkeypatch):
    seen = _stub(monkeypatch)
    assert watch_addons.addons() == []
    assert watch_addons.add(TST) == [{
        "id": "com.stremio.torrentio.addon", "name": "Torrentio", "address": TST,
        "install": "stremio://torrentio.strem.fun/manifest.json", "web": watch_addons.WEB,
        "logo": "https://torrentio.strem.fun/images/logo_v1.png"}]
    assert seen == [TST]
    assert len(watch_addons.add("torrentio.strem.fun")) == 1, "the same add-on, not twice"


def test_a_page_that_is_not_an_addon(monkeypatch):
    _stub(monkeypatch, answer={"hello": "world"})
    with pytest.raises(ValueError):
        watch_addons.add("https://x.strem.fun/manifest.json")
    _stub(monkeypatch, answer={"id": "x"})  # no name
    with pytest.raises(ValueError):
        watch_addons.add("https://x.strem.fun/manifest.json")
    _stub(monkeypatch, answer={"id": "x", "name": "X"})  # names nothing it does
    with pytest.raises(ValueError):
        watch_addons.add("https://x.strem.fun/manifest.json")


def test_an_address_that_does_not_answer(monkeypatch):
    _stub(monkeypatch, error="connection refused")
    with pytest.raises(OSError):
        watch_addons.add("https://x.strem.fun/manifest.json")


def test_suggested_are_the_examples_with_install_links():
    got = watch_addons.suggested()
    assert [g["name"] for g in got][:2] == ["Cinemeta", "YouTube"]
    assert all(g["install"] == "stremio://" + g["address"].split("://", 1)[1] for g in got)
    assert all(g["address"].startswith(("http://", "https://")) and g["address"].endswith(".json") for g in got)
    assert all(g["note"] and g["web"] == watch_addons.WEB for g in got)


def test_remove_by_address_or_by_id(monkeypatch):
    _stub(monkeypatch)
    watch_addons.add(TST)
    assert watch_addons.remove("com.stremio.torrentio.addon") == []
    watch_addons.add(TST)
    assert watch_addons.remove(TST) == []
    for which in ("com.stremio.nope", ""):
        with pytest.raises(ValueError):
            watch_addons.remove(which)
