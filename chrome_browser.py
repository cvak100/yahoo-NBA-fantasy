"""Attach to an already-open Chrome with remote debugging."""

from __future__ import annotations

import urllib.error
import urllib.request

from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options

import config


class ChromeNotOpenError(RuntimeError):
    """Chrome remote debugging endpoint is not reachable."""


def is_chrome_debugger_up(address: str | None = None) -> bool:
    host = address or config.CHROME_DEBUGGER_ADDRESS
    try:
        with urllib.request.urlopen(f"http://{host}/json/version", timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def attach_chrome(address: str | None = None) -> webdriver.Chrome:
    """
    Attach to an already-open Chrome.
    If Chrome is not running with --remote-debugging-port, raise an error.
    """
    host = address or config.CHROME_DEBUGGER_ADDRESS

    if not is_chrome_debugger_up(host):
        raise ChromeNotOpenError(
            "Chrome is not open (or remote debugging is off).\n"
            "\n"
            "Do this:\n"
            "  1) Close ALL Chrome windows\n"
            "  2) Run start_chrome_debug.bat\n"
            "  3) Sign in to Yahoo if needed\n"
            "  4) Click Refresh from Yahoo (or: python main.py sync)\n"
            f"\n"
            f"Looking for debugger at: {host}"
        )

    options = Options()
    options.add_experimental_option("debuggerAddress", host)

    try:
        return webdriver.Chrome(options=options)
    except WebDriverException as exc:
        raise ChromeNotOpenError(
            "Could not attach to Chrome.\n"
            "Run start_chrome_debug.bat and try again.\n"
            f"\nDetails: {exc}"
        ) from exc
