"""
app.py
Streamlit UI: enter a Build ID + Session ID, get an AI-grounded
root-cause analysis of a BrowserStack test session.

Run with:  streamlit run app.py
"""

import streamlit as st

from fetcher import fetch_all, BrowserStackAuthError
from parser import extract_signals
from analyzer import analyze

st.set_page_config(page_title="Session Debug Copilot", page_icon="🔍", layout="centered")

st.title("🔍 Session Debug Copilot")
st.caption("Paste a BrowserStack session ID, get an AI-grounded root-cause analysis.")

with st.form("session_form"):
    platform = st.radio(
        "Session type",
        options=["automate", "app_automate"],
        format_func=lambda x: "Automate (web/Selenium)" if x == "automate" else "App Automate (mobile/device)",
        horizontal=True,
    )
    session_id = st.text_input("Session ID", placeholder="e.g. a1b2c3d4e5...")
    build_id = st.text_input(
        "Build ID (required for App Automate device logs, optional otherwise)",
        placeholder="e.g. f6g7h8i9...",
    )
    submitted = st.form_submit_button("Analyze")

if submitted:
    if not session_id:
        st.error("Session ID is required.")
        st.stop()

    with st.spinner("Fetching logs from BrowserStack..."):
        try:
            artifacts = fetch_all(session_id=session_id.strip(),
                                   build_id=build_id.strip() or None,
                                   platform=platform)
        except BrowserStackAuthError as e:
            st.error(f"Authentication error: {e}")
            st.stop()

    if artifacts.fetch_errors:
        with st.expander("⚠️ Some artifacts could not be fetched (click to see details)"):
            for err in artifacts.fetch_errors:
                st.write(f"- {err}")

    with st.spinner("Extracting structured signals..."):
        signals = extract_signals(artifacts)

    with st.spinner("Running AI analysis..."):
        verdict = analyze(signals)

    st.divider()
    st.subheader("Result")

    category_labels = {
        "flaky_timing": "🟡 Flaky / Timing Issue",
        "browser_specific": "🌐 Browser-Specific Issue",
        "network_api_failure": "📡 Network / API Failure",
        "capability_misconfig": "⚙️ Capability Misconfiguration",
        "app_or_page_bug": "🐛 Application Bug",
        "platform_issue": "🚧 Possible Platform Issue",
        "unknown": "❓ Unknown / Insufficient Evidence",
    }
    st.markdown(f"### {category_labels.get(verdict.category, verdict.category)}")
    st.markdown(f"**Confidence:** {verdict.confidence}")
    st.write(verdict.summary)

    if verdict.evidence:
        st.markdown("**Evidence:**")
        for e in verdict.evidence:
            st.markdown(f"- {e}")

    st.markdown("**Suggested next step:**")
    st.info(verdict.suggested_next_step)

    if verdict.raw_error:
        with st.expander("Debug info"):
            st.code(verdict.raw_error)

    with st.expander("View raw extracted signals (what the AI actually saw)"):
        st.json(signals.to_dict())

    with st.expander("Session metadata"):
        st.json(artifacts.metadata)
