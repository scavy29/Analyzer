"""
analyzer.py
Sends the structured Signals object (never raw logs) to Gemini and asks for
a deep, evidence-grounded debugging report - not just a one-line label.

This is deliberately slow-and-thorough: we give the model a large thinking
budget and token budget because a genuinely useful root-cause writeup is
worth waiting a bit longer for.
"""

import os
import json
from dataclasses import dataclass, field
from typing import List

from google import genai
from google.genai import types
from dotenv import load_dotenv

from parser import Signals

load_dotenv()

# Long timeout: thinking + a long report can take well over the client's
# default timeout, especially on a cold request.
CLIENT = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY"),
    http_options=types.HttpOptions(timeout=180_000),  # ms
)
MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = """You are a senior test automation debugging expert who \
supports engineers running Selenium/Appium/Playwright tests on BrowserStack. \
Your job is to produce a genuinely useful root-cause debugging report, not a \
generic guess - the engineer reading this should be able to act on it \
immediately without re-reading the raw logs themselves.

You will be given a JSON object of signals extracted from one test session: \
exceptions found in text logs, JS console errors, failed/slow network \
requests, device log errors, and session metadata (browser, OS, device, \
status, reason).

Work through this deliberately before answering:
1. Build a mental timeline of the session: what likely happened first, and \
   what followed. Correlate signals across categories (e.g. a slow/failed \
   network request that lines up with a timeout exception a few lines later \
   is a strong causal link - call that out explicitly).
2. Identify the PRIMARY root cause, and separately note any secondary or \
   contributing factors. Do not flatten a multi-cause failure into a single \
   label if the evidence shows more nuance.
3. Rule out alternative explanations using the evidence - state briefly why \
   you did not pick the other plausible categories, if more than one seemed \
   possible.
4. Only THEN produce the structured output.

Classify the primary root cause into exactly one of these categories:
- "flaky_timing" (race conditions, timeouts, waits)
- "browser_specific" (rendering or API differences across browsers)
- "network_api_failure" (backend/API errors, slow or failed requests)
- "capability_misconfig" (wrong or unsupported capability settings)
- "app_or_page_bug" (the application/page itself has a defect)
- "platform_issue" (looks like a BrowserStack infrastructure problem)
- "unknown" (insufficient evidence)

Rules:
- Base every claim ONLY on the evidence provided. Do not invent facts, URLs, \
  timestamps, or error strings that are not present in the input.
- Quote or closely paraphrase the actual signal values (exact exception \
  names, exact URLs and status codes, exact log lines) rather than speaking \
  generically - "a request to /api/checkout returned 503 twice" is useful, \
  "there were some network problems" is not.
- If evidence is thin or contradictory, say so explicitly and lower your \
  confidence rather than forcing a confident-sounding answer.
- remediation_steps must be concrete and ordered by priority - things an \
  engineer can actually go do (e.g. "increase the explicit wait on the \
  #submit-button click from 2s to 8s", not "improve wait handling").
- verification_steps should describe how the engineer can confirm the fix \
  worked (e.g. what to re-run, what log signal should disappear/appear).
- If the signals are too sparse to diagnose confidently, populate \
  additional_logs_needed with what specifically would help, instead of \
  guessing.
- Respond ONLY with valid JSON, no markdown fences, matching this schema:
{
  "category": "<one of the categories above>",
  "confidence": "<low|medium|high>",
  "confidence_reasoning": "<1-2 sentences on why this confidence level>",
  "executive_summary": "<2-3 sentence plain-language summary for someone skimming>",
  "detailed_analysis": "<multi-paragraph markdown: the reconstructed timeline, how the signals correlate, the causal chain, and why this is the primary root cause vs. alternatives you ruled out>",
  "evidence": ["<specific signal + why it matters>", "..."],
  "remediation_steps": ["<concrete, prioritized action 1>", "..."],
  "verification_steps": ["<how to confirm the fix worked>", "..."],
  "additional_logs_needed": ["<only if evidence is insufficient>"]
}
"""


@dataclass
class Verdict:
    category: str
    confidence: str
    confidence_reasoning: str
    executive_summary: str
    detailed_analysis: str
    evidence: List[str] = field(default_factory=list)
    remediation_steps: List[str] = field(default_factory=list)
    verification_steps: List[str] = field(default_factory=list)
    additional_logs_needed: List[str] = field(default_factory=list)
    raw_error: str = ""


def analyze(signals: Signals) -> Verdict:
    payload = json.dumps(signals.to_dict(), indent=2)

    try:
        response = CLIENT.models.generate_content(
            model=MODEL,
            contents=payload,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=8192,
                temperature=0.2,
                response_mime_type="application/json",
                # Let the model spend as much reasoning as it judges useful -
                # thoroughness matters more than latency here.
                thinking_config=types.ThinkingConfig(thinking_budget=-1),
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
            confidence_reasoning=data.get("confidence_reasoning", ""),
            executive_summary=data.get("executive_summary", ""),
            detailed_analysis=data.get("detailed_analysis", ""),
            evidence=data.get("evidence", []),
            remediation_steps=data.get("remediation_steps", []),
            verification_steps=data.get("verification_steps", []),
            additional_logs_needed=data.get("additional_logs_needed", []),
        )

    except json.JSONDecodeError as e:
        return Verdict(
            category="unknown", confidence="low", confidence_reasoning="",
            executive_summary="Model response could not be parsed as JSON.",
            detailed_analysis="",
            remediation_steps=["Re-run analysis; inspect raw output."],
            raw_error=str(e),
        )
    except Exception as e:
        return Verdict(
            category="unknown", confidence="low", confidence_reasoning="",
            executive_summary="Analysis failed due to an unexpected error.",
            detailed_analysis="",
            remediation_steps=["Check GEMINI_API_KEY and network access."],
            raw_error=str(e),
        )
