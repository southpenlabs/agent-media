"""media doctor: env-file lines a shell cannot source (intake/_env.py)."""
from agent_media_core.intake._env import env_file_problems


def test_finds_what_a_shell_or_systemd_gets_wrong(tmp_path):
    f = tmp_path / "agent-media.env"
    f.write_text("\n".join([
        "# a comment",
        "",
        "MEDIA_OK=plain",
        "MEDIA_QUOTED='a|b c'",
        'MEDIA_DQ="x y"',
        "export MEDIA_VOL=100",
        "MEDIA_PIPE=https://a|1",
        "MEDIA_CMD=systemctl --user start x",
        "not a setting",
    ]))
    got = env_file_problems(f)
    assert len(got) == 4
    assert "line 6" in got[0] and "export" in got[0]
    assert "line 7" in got[1] and "|" in got[1]
    assert "line 8" in got[2] and "space" in got[2]
    assert "line 9" in got[3]


def test_no_file_no_problems(tmp_path):
    assert env_file_problems(tmp_path / "missing.env") == []
