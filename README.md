# Session Debug Copilot

An AI-assisted root-cause analyzer for BrowserStack Automate / App Automate
test sessions. Paste in a Session ID (and Build ID for device sessions), and
it fetches the session's logs directly via the BrowserStack REST API, extracts
structured signals, and asks an LLM to classify the likely root cause with
cited evidence.

No raw log pasting required — everything is pulled via API using your
BrowserStack credentials.

## How it works

```
Session ID / Build ID
        │
        ▼
  fetcher.py   -> calls BrowserStack REST API (text/console/network/device logs)
        │
        ▼
  parser.py    -> extracts structured signals (exceptions, failed requests,
        │          slow requests, JS errors, device log errors)
        ▼
  analyzer.py  -> sends ONLY the structured signals to Gemini, gets back a
        │          grounded root-cause classification + evidence
        ▼
  app.py       -> Streamlit UI displays the verdict
```

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in your real credentials:
   ```bash
   cp .env.example .env
   ```
   You'll need:
   - `BROWSERSTACK_USERNAME` / `BROWSERSTACK_ACCESS_KEY` — from your
     BrowserStack account settings.
   - `GEMINI_API_KEY` — from aistudio.google.com/apikey.

3. **Never commit `.env`** — it's already in `.gitignore`.

## Run

```bash
streamlit run app.py
```

This opens `http://localhost:8501` in your browser. Enter a Session ID
(and Build ID if it's an App Automate/device session), click Analyze.

## Root cause categories

The analyzer classifies each session into one of:

- `flaky_timing` — race conditions, timeouts, waits
- `browser_specific` — rendering/API differences across browsers
- `network_api_failure` — backend/API errors, slow or failed requests
- `capability_misconfig` — wrong or unsupported capability settings
- `app_or_page_bug` — the application itself has a defect
- `platform_issue` — looks like a BrowserStack infrastructure issue
- `unknown` — insufficient evidence to conclude

## Notes / next steps

- Currently analyzes one session at a time. A natural v2 is a "Build" view
  that loops over every session in a build and summarizes failures across
  the whole run.
- The parser regex patterns cover the most common Selenium/Appium exceptions
  — extend `EXCEPTION_PATTERNS` in `parser.py` as you see new failure types.
- Consider adding a small local SQLite/JSON store of past verdicts so you
  can build similarity matching against previously seen failure patterns.
