"""Open the page in the background when a session starts editing and no page is open."""

from __future__ import annotations

import subprocess
import time
from typing import Callable

COOLDOWN_S = 1800
QUIET_S = 60  # no page for this long before opening: covers restarts, reloads and reconnects


def open_in_background(url: str) -> None:
    # -g: macOS opens the URL without bringing the browser to the front.
    subprocess.Popen(["open", "-g", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class AutoOpener:
    """Opens at most one tab, then waits until a page is actually looked at before it may open another."""

    def __init__(
        self,
        url_for: Callable[[str], str],
        pages_open: Callable[[], int],
        cooldown_s: float = COOLDOWN_S,
        launch: Callable[[str], None] = open_in_background,
        clock: Callable[[], float] = time.monotonic,
        quiet_s: float = QUIET_S,
    ) -> None:
        self.url_for, self.pages_open, self.cooldown_s = url_for, pages_open, cooldown_s
        self.launch, self.clock, self.quiet_s = launch, clock, quiet_s
        self.last: float | None = None
        self.last_page = clock()  # server start counts: an open tab may still be reconnecting
        self.armed = True

    def page_event(self) -> None:
        """A page connected or disconnected."""
        self.last_page = self.clock()

    def seen(self) -> None:
        """A page was visible on screen, so a later auto-open is welcome again."""
        self.armed = True

    def maybe_open(self, session_id: str) -> bool:
        now = self.clock()
        if self.pages_open() or not self.armed or now - self.last_page < self.quiet_s:
            return False
        if self.last is not None and now - self.last < self.cooldown_s:
            return False
        try:
            self.launch(self.url_for(session_id))
        except Exception:
            return False
        self.last, self.armed = now, False
        return True
