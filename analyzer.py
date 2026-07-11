"""
analyzer.py
Sends the small, structured Signals object (never raw logs) to Gemini
and asks for a grounded root-cause classification.
"""

import os
import json
from dataclasses import dataclass
from typing import List

from google import genai
from google.genai import types
from dotenv import load_dotenv

from parser import Signals

load_dotenv()

CLIENT = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = """You are a test automation debugging assistant used by an \
engineer who supports customers running Selenium/Appium/Playwright tests on \
BrowserStack.

You will be given a JSON object of signals extracted from one test session: \
exceptions found in logs, JS console errors, failed/slow network requests, \
device log errors, and session metadata (browser, OS, device, status, reason).

Classify the likely root cause into exactly one of these categories:
- "flaky_timing" (race conditions, timeouts, waits)
- "browser_specific" (rendering or API differences across browsers)
- "network_api_failure" (backend/API errors, slow or failed requests)
- "capability_misconfig" (wrong or unsupported capability settings)
- "app_or_page_bug" (the application/page itself has a defect)
- "platform_issue" (looks like a BrowserStack infrastructure problem)
- "unknown" (insufficient evidence)

Rules:
- Base your reasoning ONLY on the evidence provided. Do not invent facts.
- If evidence is thin or contradictory, say so and lower your confidence.
- Cite the specific signal(s) that led to your conclusion.
- Respond ONLY with valid JSON, no markdown fences, matching this schema:
{
  "category": "<one of the categories above>",
  "confidence": "<low|medium|high>",
  "summary": "<1-2 sentence plain-language explanation>",
  "evidence": ["<specific signal 1>", "<specific signal 2>"],
  "suggested_next_step": "<concrete, actionable next step for the engineer>"
}
"""


@dataclass
class Verdict:
    category: str
    confidence: str
    summary: str
    evidence: List[str]
    suggested_next_step: str
    raw_error: str = ""


def analyze(signals: Signals) -> Verdict:
    payload = json.dumps(signals.to_dict(), indent=2)

    try:
        response = CLIENT.models.generate_content(
            model=MODEL,
            contents=payload,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=800,
            ),
        )
        text = (response.text or "").strip()

        # Defensive cleanup in case the model wraps output in fences anyway
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]

        data = json.loads(text)
        return Verdict(
            category=data.get("category", "unknown"),
            confidence=data.get("confidence", "low"),
            summary=data.get("summary", ""),
            evidence=data.get("evidence", []),
            suggested_next_step=data.get("suggested_next_step", ""),
        )

    except json.JSONDecodeError as e:
        return Verdict(
            category="unknown", confidence="low",
            summary="Model response could not be parsed as JSON.",
            evidence=[], suggested_next_step="Re-run analysis; inspect raw output.",
            raw_error=str(e),
        )
    except Exception as e:
        return Verdict(
            category="unknown", confidence="low",
            summary="Analysis failed due to an unexpected error.",
            evidence=[], suggested_next_step="Check GEMINI_API_KEY and network access.",
            raw_error=str(e),
        )
