"""Open the page in the background when a session starts editing and no page is open."""

from __future__ import annotations

import subprocess
import time
from typing import Callable

COOLDOWN_S = 1800


def open_in_background(url: str) -> None:
    # -g: macOS opens the URL without bringing the browser to the front.
    subprocess.Popen(["open", "-g", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class AutoOpener:
    def __init__(
        self,
        url_for: Callable[[str], str],
        pages_open: Callable[[], int],
        cooldown_s: float = COOLDOWN_S,
        launch: Callable[[str], None] = open_in_background,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url_for, self.pages_open, self.cooldown_s = url_for, pages_open, cooldown_s
        self.launch, self.clock = launch, clock
        self.last: float | None = None

    def maybe_open(self, session_id: str) -> bool:
        now = self.clock()
        if self.pages_open() or (self.last is not None and now - self.last < self.cooldown_s):
            return False
        try:
            self.launch(self.url_for(session_id))
        except Exception:
            return False
        self.last = now
        return True
