"""
parser.py
Turns raw log text / HAR JSON into small, structured signal objects.
This is the piece that keeps the LLM grounded - it never sees raw
multi-MB logs, only extracted facts.
"""

import re
from dataclasses import dataclass, field
from typing import List, Dict, Any

from fetcher import SessionArtifacts

SLOW_REQUEST_THRESHOLD_MS = 2000

# Patterns that commonly show up in Selenium/Appium text logs on failure
EXCEPTION_PATTERNS = [
    r"NoSuchElementException",
    r"TimeoutException",
    r"StaleElementReferenceException",
    r"ElementClickInterceptedException",
    r"ElementNotInteractableException",
    r"WebDriverException",
    r"SessionNotCreatedException",
    r"InvalidSelectorException",
    r"UnreachableBrowserException",
    r"NoSuchWindowException",
]


@dataclass
class Signals:
    test_status: str = "unknown"
    test_reason: str = ""
    browser: str = ""
    os_name: str = ""
    device: str = ""

    exceptions_found: List[Dict[str, str]] = field(default_factory=list)
    js_console_errors: List[str] = field(default_factory=list)
    failed_requests: List[Dict[str, Any]] = field(default_factory=list)
    slow_requests: List[Dict[str, Any]] = field(default_factory=list)
    device_log_errors: List[str] = field(default_factory=list)
    crash_log_errors: List[str] = field(default_factory=list)

    fetch_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "test_status": self.test_status,
            "test_reason": self.test_reason,
            "browser": self.browser,
            "os": self.os_name,
            "device": self.device,
            "exceptions_found": self.exceptions_found,
            "js_console_errors": self.js_console_errors[:100],
            "failed_requests": self.failed_requests[:50],
            "slow_requests": self.slow_requests[:30],
            "device_log_errors": self.device_log_errors[:100],
            "crash_log_errors": self.crash_log_errors[:50],
            "fetch_errors": self.fetch_errors,
        }


def _parse_metadata(signals: Signals, metadata: dict):
    """BrowserStack wraps session info under an 'automation_session' key."""
    session = metadata.get("automation_session", metadata)
    signals.test_status = session.get("status", "unknown")
    signals.test_reason = session.get("reason", "") or ""
    signals.browser = session.get("browser", "") or session.get("browser_version", "")
    signals.os_name = session.get("os", "") or session.get("os_version", "")
    signals.device = session.get("device", "") or ""


def _parse_text_logs(signals: Signals, text_logs: str):
    if not text_logs:
        return
    for pattern in EXCEPTION_PATTERNS:
        for match in re.finditer(pattern, text_logs):
            # grab a short window of context around the match
            start = max(0, match.start() - 80)
            end = min(len(text_logs), match.end() + 80)
            snippet = text_logs[start:end].replace("\n", " ").strip()
            signals.exceptions_found.append({"type": pattern, "context": snippet})
            if len(signals.exceptions_found) >= 50:
                return


def _parse_console_logs(signals: Signals, console_logs: str):
    if not console_logs:
        return
    for line in console_logs.splitlines():
        if "SEVERE" in line or "ERROR" in line.upper():
            signals.js_console_errors.append(line.strip())


def _parse_network_logs(signals: Signals, har: dict):
    if not har:
        return
    entries = har.get("log", {}).get("entries", [])
    for entry in entries:
        try:
            status = entry["response"]["status"]
            url = entry["request"]["url"]
            time_ms = entry.get("time", 0)

            if status >= 400:
                signals.failed_requests.append({
                    "url": url, "status": status, "duration_ms": round(time_ms)
                })
            if time_ms >= SLOW_REQUEST_THRESHOLD_MS:
                signals.slow_requests.append({
                    "url": url, "duration_ms": round(time_ms)
                })
        except (KeyError, TypeError):
            continue


def _parse_device_logs(signals: Signals, device_logs: str):
    if not device_logs:
        return
    for line in device_logs.splitlines():
        upper = line.upper()
        if " E/" in line or "ERROR" in upper or "FATAL" in upper:
            signals.device_log_errors.append(line.strip())


def _parse_crash_logs(signals: Signals, crash_logs: str):
    """Native crash reports look different from regular error logs -
    FATAL EXCEPTION / SIGSEGV / ANR / backtrace markers rather than plain ERROR lines."""
    if not crash_logs:
        return
    for line in crash_logs.splitlines():
        upper = line.upper()
        if any(marker in upper for marker in
               ("FATAL", "SIGSEGV", "SIGABRT", "ANR", "EXCEPTION", "BACKTRACE")):
            signals.crash_log_errors.append(line.strip())
            if len(signals.crash_log_errors) >= 50:
                return


def extract_signals(artifacts: SessionArtifacts) -> Signals:
    """Main entry point: raw SessionArtifacts -> structured Signals."""
    signals = Signals()
    signals.fetch_errors = artifacts.fetch_errors

    _parse_metadata(signals, artifacts.metadata)
    _parse_text_logs(signals, artifacts.text_logs)
    _parse_text_logs(signals, artifacts.selenium_logs)
    _parse_text_logs(signals, artifacts.appium_logs)
    _parse_console_logs(signals, artifacts.console_logs)
    _parse_network_logs(signals, artifacts.network_logs_har)
    _parse_device_logs(signals, artifacts.device_logs)
    _parse_crash_logs(signals, artifacts.crash_logs)

    return signals
