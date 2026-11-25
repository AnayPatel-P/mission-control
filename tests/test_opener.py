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
    t["now"] += 61  # past the quiet window that starts at server start
    return o, t, opened


def test_opens_when_no_page_is_open():
    o, _, opened = make()
    assert o.maybe_open("s1") is True
    assert opened == ["http://x/#session=s1"]


def test_skips_when_a_page_is_open():
    o, _, opened = make(pages=1)
    assert o.maybe_open("s1") is False and opened == []


def test_quiet_right_after_start():
    opened = []
    o = AutoOpener(url_for=str, pages_open=lambda: 0, launch=opened.append, clock=lambda: 5.0)
    assert o.maybe_open("s1") is False  # an open tab may still be reconnecting after a restart


def test_quiet_after_a_page_disconnects():
    o, t, opened = make()
    o.page_event()  # a reload or reconnect drops the count to 0 briefly
    t["now"] += 5
    assert o.maybe_open("s1") is False
    t["now"] += 60
    assert o.maybe_open("s1") is True


def test_waits_until_the_opened_page_is_seen():
    o, t, opened = make()
    assert o.maybe_open("s1")
    t["now"] += 7200  # tab opened in the background, never looked at, then discarded
    assert o.maybe_open("s2") is False
    o.seen()
    assert o.maybe_open("s2") is True
    assert len(opened) == 2


def test_cooldown_even_when_seen():
    o, t, opened = make()
    o.maybe_open("s1")
    o.seen()
    t["now"] += 1799
    assert o.maybe_open("s2") is False
    t["now"] += 2
    assert o.maybe_open("s2") is True


def test_launch_failure_is_swallowed():
    def boom(url):
        raise OSError("no open")

    o = AutoOpener(url_for=str, pages_open=lambda: 0, launch=boom, clock=lambda: 1000.0)
    o.last_page = 0.0
    assert o.maybe_open("s1") is False
