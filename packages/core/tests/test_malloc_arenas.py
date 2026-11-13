"""malloc_arenas.cap: the arena limit is asked for once, and never in the way."""

from agent_media_core import malloc_arenas


class _Libc:
    def __init__(self, answer=1):
        self.calls, self.answer = [], answer

    def mallopt(self, param, value):
        self.calls.append((param, value))
        return self.answer


def test_asks_glibc_for_two_arenas():
    libc = _Libc()
    assert malloc_arenas.cap(libc=libc, environ={}) is True
    assert libc.calls == [(malloc_arenas.M_ARENA_MAX, 2)]


def test_the_environment_setting_wins():
    libc = _Libc()
    assert malloc_arenas.cap(libc=libc, environ={"MALLOC_ARENA_MAX": "8"}) is False
    assert libc.calls == []


def test_a_refusal_is_not_an_error():
    assert malloc_arenas.cap(libc=_Libc(answer=0), environ={}) is False


def test_a_libc_without_mallopt_is_left_alone():
    assert malloc_arenas.cap(libc=object(), environ={}) is False
