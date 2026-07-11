"""
fetcher.py
Handles all communication with BrowserStack REST APIs.
Nothing here parses or interprets data - it just retrieves raw artifacts.
"""

import os
import requests
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

USERNAME = os.getenv("BROWSERSTACK_USERNAME")
ACCESS_KEY = os.getenv("BROWSERSTACK_ACCESS_KEY")

AUTOMATE_BASE = "https://api.browserstack.com/automate"
APP_AUTOMATE_BASE = "https://api-cloud.browserstack.com/app-automate"


class BrowserStackAuthError(Exception):
    pass


class BrowserStackNotFoundError(Exception):
    pass


@dataclass
class SessionArtifacts:
    """Container for all raw data pulled about one session."""
    session_id: str
    build_id: Optional[str] = None
    platform: str = "automate"  # "automate" or "app_automate"

    metadata: dict = field(default_factory=dict)
    text_logs: str = ""
    console_logs: str = ""
    network_logs_har: dict = field(default_factory=dict)
    device_logs: str = ""

    fetch_errors: list = field(default_factory=list)


def _check_credentials():
    if not USERNAME or not ACCESS_KEY:
        raise BrowserStackAuthError(
            "Missing BROWSERSTACK_USERNAME or BROWSERSTACK_ACCESS_KEY. "
            "Copy .env.example to .env and fill in your credentials."
        )


def _get(url: str):
    """Shared GET wrapper with auth and basic error handling."""
    _check_credentials()
    resp = requests.get(url, auth=(USERNAME, ACCESS_KEY), timeout=30)

    if resp.status_code == 401:
        raise BrowserStackAuthError("BrowserStack rejected the credentials (401).")
    if resp.status_code == 404:
        raise BrowserStackNotFoundError(f"Not found (404): {url}")

    resp.raise_for_status()
    return resp


def fetch_session_metadata(session_id: str) -> dict:
    """GET /automate/sessions/<session_id> - browser, os, status, reason, etc."""
    url = f"{AUTOMATE_BASE}/sessions/{session_id}.json"
    resp = _get(url)
    return resp.json()


def fetch_text_logs(session_id: str) -> str:
    """GET /automate/sessions/<session_id>/logs - the main session/selenium text log."""
    url = f"{AUTOMATE_BASE}/sessions/{session_id}/logs"
    resp = _get(url)
    return resp.text


def fetch_console_logs(session_id: str) -> str:
    """GET /automate/sessions/<session_id>/consolelogs - JS console output (Chrome only)."""
    url = f"{AUTOMATE_BASE}/sessions/{session_id}/consolelogs"
    try:
        resp = _get(url)
        return resp.text
    except BrowserStackNotFoundError:
        # Console logs aren't available for every browser - not a hard failure.
        return ""


def fetch_network_logs(session_id: str) -> dict:
    """GET /automate/sessions/<session_id>/networklogs - HAR formatted JSON."""
    url = f"{AUTOMATE_BASE}/sessions/{session_id}/networklogs"
    try:
        resp = _get(url)
        return resp.json()
    except (BrowserStackNotFoundError, ValueError):
        # Network logs might not be enabled for this session.
        return {}


def fetch_device_logs(build_id: str, session_id: str) -> str:
    """GET /app-automate/builds/<build_id>/sessions/<session_id>/devicelogs
    Only applicable for App Automate (real device) sessions.
    """
    url = f"{APP_AUTOMATE_BASE}/builds/{build_id}/sessions/{session_id}/devicelogs"
    try:
        resp = _get(url)
        return resp.text
    except BrowserStackNotFoundError:
        return ""


def fetch_all(session_id: str, build_id: Optional[str] = None,
              platform: str = "automate") -> SessionArtifacts:
    """
    Orchestrates every fetch call for a given session and returns
    a single SessionArtifacts object. Individual fetch failures are
    collected in fetch_errors rather than raising, so a partial
    result can still be analyzed.
    """
    artifacts = SessionArtifacts(session_id=session_id, build_id=build_id, platform=platform)

    steps = [
        ("metadata", lambda: fetch_session_metadata(session_id)),
        ("text_logs", lambda: fetch_text_logs(session_id)),
        ("console_logs", lambda: fetch_console_logs(session_id)),
        ("network_logs_har", lambda: fetch_network_logs(session_id)),
    ]

    if platform == "app_automate" and build_id:
        steps.append(("device_logs", lambda: fetch_device_logs(build_id, session_id)))

    for field_name, fn in steps:
        try:
            setattr(artifacts, field_name, fn())
        except (BrowserStackAuthError, BrowserStackNotFoundError) as e:
            artifacts.fetch_errors.append(f"{field_name}: {e}")
        except Exception as e:
            artifacts.fetch_errors.append(f"{field_name}: unexpected error - {e}")

    return artifacts