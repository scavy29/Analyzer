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

# Generic catch-all: any (optionally package-qualified) CamelCase identifier
# ending in Exception/Error - covers named exceptions we haven't seen before
# (custom app exceptions, less common Selenium/Appium/Java exceptions, etc.)
# instead of relying on a fixed allowlist that silently misses anything not on it.
EXCEPTION_RE = re.compile(r"\b(?:[\w$]+\.)*[A-Z][\w$]*(?:Exception|Error)\b")


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
    requested_capabilities: Dict[str, Any] = field(default_factory=dict)

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
            "requested_capabilities": self.requested_capabilities,
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

    # Best-effort: BrowserStack's documented /sessions/<id>.json schema does not
    # publicly list a requested-capabilities field, so this will likely stay
    # empty unless your account/API version returns one under a different key -
    # check a real payload and adjust the keys below if you need this populated.
    signals.requested_capabilities = (
        session.get("capabilities")
        or session.get("raw_capabilities")
        or session.get("desired_capabilities")
        or {}
    )


def _parse_text_logs(signals: Signals, text_logs: str):
    if not text_logs:
        return
    for match in EXCEPTION_RE.finditer(text_logs):
        # grab a short window of context around the match
        start = max(0, match.start() - 80)
        end = min(len(text_logs), match.end() + 80)
        snippet = text_logs[start:end].replace("\n", " ").strip()
        signals.exceptions_found.append({"type": match.group(0), "context": snippet})
        if len(signals.exceptions_found) >= 50:
            return


def _parse_console_logs(signals: Signals, console_logs: str):
    if not console_logs:
        return
    for line in console_logs.splitlines():
        upper = line.upper()
        if "SEVERE" in upper or "ERROR" in upper or "WARN" in upper:
            signals.js_console_errors.append(line.strip())


def _parse_network_logs(signals: Signals, har: dict):
    if not har:
        return
    entries = har.get("log", {}).get("entries", [])
    for i, entry in enumerate(entries):
        try:
            request = entry.get("request") or {}
            response = entry.get("response") or {}
            url = request.get("url", "<unknown url>")
            status = response.get("status", 0)
            time_ms = entry.get("time", 0)
            started_at = entry.get("startedDateTime", "")

            # A request that never got a response at all (DNS failure,
            # connection refused, client-side timeout) shows up with status
            # 0 or negative, not >= 400 - that used to be silently dropped
            # even though it's often the most causally important entry.
            if status == 0 or status < 0:
                signals.failed_requests.append({
                    "url": url, "status": "no_response",
                    "duration_ms": round(time_ms), "started_at": started_at,
                })
            elif status >= 400:
                signals.failed_requests.append({
                    "url": url, "status": status,
                    "duration_ms": round(time_ms), "started_at": started_at,
                })

            if time_ms >= SLOW_REQUEST_THRESHOLD_MS:
                signals.slow_requests.append({
                    "url": url, "duration_ms": round(time_ms), "started_at": started_at,
                })
        except (AttributeError, TypeError) as e:
            # Entry didn't match the expected HAR shape - record that data was
            # lost instead of silently continuing with no trace of it.
            signals.fetch_errors.append(f"network_logs: could not parse HAR entry #{i} ({e})")
            continue


def _parse_device_logs(signals: Signals, device_logs: str):
    """Logcat marks severity per-line (V/D/I/W/E/F). We used to only catch
    E/FATAL, which misses relevant W-level lines (e.g. WindowManager/
    InputDispatcher warnings around orientation/rotation handling). We now
    also catch W, but collect E/FATAL first so a later truncation to a fixed
    cap (see Signals.to_dict) can't crowd real errors out with warning noise."""
    if not device_logs:
        return
    high, low = [], []
    for line in device_logs.splitlines():
        stripped = line.strip()
        upper = line.upper()
        if " E/" in line or "ERROR" in upper or "FATAL" in upper:
            high.append(stripped)
        elif " W/" in line or "WARN" in upper:
            low.append(stripped)
    signals.device_log_errors.extend(high)
    signals.device_log_errors.extend(low)


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
