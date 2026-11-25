import pytest


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch):
    # cli.build_config wires the real `open -g` on macOS; tests must never open a browser.
    monkeypatch.setenv("MC_AUTO_OPEN", "off")
