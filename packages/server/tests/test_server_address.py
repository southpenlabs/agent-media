"""`media server rename|check|status` and the server saying it moved
(server_address.py; docs/proposals/2026-11-12-the-server-moves-home.md, "The
migration feature"). Every file is a throwaway under tmp_path: no real env
file, drop-in, tunnel or sasonica.com is touched, and no restart is run.
"""

from __future__ import annotations

import http.client
import json
from pathlib import Path

import pytest

from agent_media_server import server_address as sa

from test_contract import AUTH, call, server, signed_in, typed  # noqa: F401

OLD = "https://red5.sasonica.com"
NEW = "https://red2-ryer.sasonica.com"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An env file, a canvas drop-in and a tunnel config naming red5."""
    cfg = tmp_path / "config"
    env = cfg / "agent-media.env"
    env.parent.mkdir(parents=True)
    env.write_text(
        "# red5's settings\n"
        f"MEDIA_PUBLIC_URL={OLD}\n"
        f"MEDIA_SPEECH_CLIP_BASEURL_SASONICA={OLD}/speech/clips\n"
        f"MEDIA_BOOK_BASEURL_PHONE={OLD}/books/files\n"
        f"# a comment naming {OLD} is left alone\n"
        "MEDIA_OIDC_ISSUERS=https://cms.sasonica.com\n"
        f"MEDIA_OTHER=https://red5.sasonica.com.example.net/x\n")
    drop = cfg / "systemd" / "user" / "agent-media-visual-canvas.service.d" / "servers.conf"
    drop.parent.mkdir(parents=True)
    drop.write_text(f"[Service]\nEnvironment=MEDIA_PUBLIC_URL={OLD}\n"
                    "Environment=MEDIA_WEB_APP_DIR=%h/app\n")
    tun = tmp_path / "cloudflared" / "config.yml"
    tun.parent.mkdir(parents=True)
    tun.write_text("tunnel: abc\ningress:\n  - hostname: red5.sasonica.com\n"
                   "    service: http://127.0.0.1:8789\n  - service: http_status:404\n")
    monkeypatch.setenv("MEDIA_ENV_FILE", str(env))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("MEDIA_TUNNEL_CONFIG", str(tun))
    monkeypatch.setenv("MEDIA_PUBLIC_URL", OLD)
    monkeypatch.setenv("MEDIA_SERVERS_REPORT", "0")
    monkeypatch.setattr(sa, "reaches_here", lambda url, timeout=15.0: (True, ""))
    return env, drop, tun


def _rename(*a, **kw) -> tuple[int, str]:
    lines: list[str] = []
    kw.setdefault("restart", False)
    rc = sa.rename(*a, out=lines.append, **kw)
    return rc, "\n".join(lines)


def test_origin_and_host():
    assert sa.origin("red2-ryer.sasonica.com/app/t/x") == NEW
    assert sa.origin("HTTPS://Red5.Sasonica.com:443/") == OLD
    assert sa.origin("http://box:8788") == "http://box:8788"
    assert sa.origin("ftp://x") == "" and sa.origin("") == ""


def test_a_rename_changes_every_setting_that_names_the_old_address(home):
    env, drop, tun = home
    rc, said = _rename(NEW, [])
    assert rc == 0, said
    text = env.read_text()
    assert f"MEDIA_PUBLIC_URL={NEW}\n" in text
    assert f"MEDIA_SPEECH_CLIP_BASEURL_SASONICA={NEW}/speech/clips" in text
    assert f"MEDIA_BOOK_BASEURL_PHONE={NEW}/books/files" in text
    # A comment, another host and a name that merely starts the same: untouched.
    assert f"# a comment naming {OLD} is left alone" in text
    assert "MEDIA_OIDC_ISSUERS=https://cms.sasonica.com" in text
    assert "https://red5.sasonica.com.example.net/x" in text
    assert f"Environment=MEDIA_PUBLIC_URL={NEW}" in drop.read_text()
    assert "%h/app" in drop.read_text()
    # A copy of each file it changed.
    assert list(env.parent.glob("agent-media.env.bak-rename-*"))
    assert list(drop.parent.glob("servers.conf.bak-rename-*"))
    # The tunnel routes the new name, and still the old one, before the catch-all.
    hosts = sa.tunnel_hosts(tun)
    assert hosts == ["red5.sasonica.com", "red2-ryer.sasonica.com"]
    assert tun.read_text().rstrip().endswith("- service: http_status:404")
    # The old address is a previous name, and the check is clean.
    assert sa.current() == NEW and sa.previous() == [OLD]
    assert sa.problems() == []


def test_a_dry_run_changes_nothing(home):
    env, drop, tun = home
    before = (env.read_text(), drop.read_text(), tun.read_text())
    rc, said = _rename(NEW, [], dry_run=True)
    assert rc == 0 and "MEDIA_BOOK_BASEURL_PHONE" in said and "dry run" in said
    assert (env.read_text(), drop.read_text(), tun.read_text()) == before
    assert sa.previous() == []


def test_a_name_that_does_not_reach_this_server_changes_nothing(home, monkeypatch):
    env, _drop, _tun = home
    before = env.read_text()
    monkeypatch.setattr(sa, "reaches_here",
                        lambda url, timeout=15.0: (False, f"{url} answers, but as another server"))
    rc, said = _rename(NEW, [])
    assert rc == 1 and "another server" in said and "hostname: red2-ryer.sasonica.com" in said
    assert env.read_text() == before


def test_renaming_to_the_same_address_does_nothing(home):
    env, _drop, _tun = home
    before = env.read_text()
    assert _rename(OLD, [])[0] == 0
    assert env.read_text() == before


def test_the_check_finds_a_setting_left_on_the_old_address(home):
    env, _drop, _tun = home
    _rename(NEW, [])
    with open(env, "a") as fh:
        fh.write(f"MEDIA_FEED_BASE_URL={OLD}/feeds\n")
    bad = sa.problems()
    assert len(bad) == 1 and "MEDIA_FEED_BASE_URL" in bad[0] and OLD in bad[0]


def test_the_check_finds_a_clip_address_on_another_host_and_an_unrouted_tunnel(home):
    env, _drop, tun = home
    env.write_text(env.read_text().replace(f"BASEURL_SASONICA={OLD}",
                                           "BASEURL_SASONICA=https://elsewhere.example"))
    tun.write_text(tun.read_text().replace("red5.sasonica.com", "other.sasonica.com"))
    bad = "\n".join(sa.problems())
    assert "MEDIA_SPEECH_CLIP_BASEURL_SASONICA" in bad and "elsewhere.example" in bad
    assert "does not route red5.sasonica.com" in bad


def test_moved_to_only_on_a_previous_name(home):
    _rename(NEW, [])
    assert sa.moved_to_for("red5.sasonica.com") == NEW
    assert sa.moved_to_for("RED5.sasonica.com") == NEW
    assert sa.moved_to_for("red2-ryer.sasonica.com") == ""
    assert sa.moved_to_for("127.0.0.1:8788") == ""
    assert sa.moved_to_for("") == ""


def test_the_probe_proves_this_server_and_no_other():
    got = sa.probe_answer("nonce=abc")
    assert got["ok"] and got["proof"] == sa.proof("abc") and got["proof"] != sa.proof("abd")
    assert not sa.probe_answer("")["ok"]


def test_the_routes_say_it_moved_on_an_old_name(server, signed_in, home):  # noqa: F811
    _rename(NEW, [])
    res, obj = call(server, "GET", "/features", headers={**AUTH, "Host": "red5.sasonica.com"})
    assert res.status == 200 and obj["moved_to"] == NEW
    res, obj = call(server, "GET", "/features", headers={**AUTH, "Host": "red2-ryer.sasonica.com"})
    assert "moved_to" not in obj
    res, obj = call(server, "GET", "/server/probe?nonce=n1")
    assert res.status == 200 and obj["proof"] == sa.proof("n1")
    # The stream's first frame says where it went.
    conn = http.client.HTTPConnection(*server, timeout=10)
    conn.request("GET", "/sessions/events?ping=1",
                 headers={**AUTH, "Host": "red5.sasonica.com"})
    r = conn.getresponse()
    assert r.status == 200
    frame = b""
    while b"event: server_moved" not in frame and len(frame) < 4096:
        line = r.fp.readline()
        if not line:
            break
        frame += line
    data = r.fp.readline()
    conn.close()
    assert b"event: server_moved" in frame
    assert json.loads(data.split(b"data:", 1)[1]) == {"base": NEW}


def test_the_reaches_here_check_against_the_real_route(server):  # noqa: F811
    assert sa.reaches_here(f"http://{server[0]}:{server[1]}") == (True, "")


def test_another_server_on_the_name_is_refused(monkeypatch):
    import io
    import urllib.request

    class Answer(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=0: Answer(b'{"ok": true, "proof": "not-ours"}'))
    ok, why = sa.reaches_here(NEW)
    assert not ok and "another server" in why
