from mission_control.opener import AutoOpener


def make(pages=0):
    t = {"now": 1000.0, "pages": pages}
    opened = []
    o = AutoOpener(
        url_for=lambda sid: f"http://x/#session={sid}",
        pages_open=lambda: t["pages"],
        launch=opened.append,
        clock=lambda: t["now"],
    )
    return o, t, opened


def test_opens_when_no_page_is_open():
    o, _, opened = make()
    assert o.maybe_open("s1") is True
    assert opened == ["http://x/#session=s1"]


def test_skips_when_a_page_is_open():
    o, _, opened = make(pages=1)
    assert o.maybe_open("s1") is False and opened == []


def test_cooldown():
    o, t, opened = make()
    o.maybe_open("s1")
    t["now"] += 1799
    assert o.maybe_open("s2") is False
    t["now"] += 2
    assert o.maybe_open("s2") is True
    assert len(opened) == 2


def test_launch_failure_is_swallowed():
    def boom(url):
        raise OSError("no open")

    o = AutoOpener(url_for=str, pages_open=lambda: 0, launch=boom, clock=lambda: 0.0)
    assert o.maybe_open("s1") is False
