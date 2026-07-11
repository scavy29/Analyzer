"""
app.py
Streamlit UI: enter a Build ID + Session ID, get an AI-grounded
root-cause analysis of a BrowserStack test session.

Run with:  streamlit run app.py
"""

import streamlit as st

from fetcher import fetch_all, parse_session_url, BrowserStackAuthError, InvalidSessionUrlError
from parser import extract_signals
from analyzer import analyze

st.set_page_config(page_title="Session Debug Copilot", page_icon="🔍", layout="centered")

st.title("🔍 Session Debug Copilot")
st.caption("Paste a BrowserStack session ID, get an AI-grounded root-cause analysis.")

with st.form("session_form"):
    session_url = st.text_input(
        "Session URL",
        placeholder="https://automate.browserstack.com/dashboard/v2/builds/<build_id>/sessions/<session_id>",
    )
    submitted = st.form_submit_button("Analyze")

if submitted:
    try:
        session_id, build_id, platform = parse_session_url(session_url)
    except InvalidSessionUrlError as e:
        st.error(str(e))
        st.stop()

    with st.spinner("Fetching logs from BrowserStack..."):
        try:
            artifacts = fetch_all(session_id=session_id,
                                   build_id=build_id,
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

    with st.spinner("Running deep AI analysis (this can take a minute or two for a thorough report)..."):
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
    if verdict.confidence_reasoning:
        st.caption(verdict.confidence_reasoning)
    st.write(verdict.executive_summary)

    if verdict.detailed_analysis:
        st.markdown("### Detailed analysis")
        st.markdown(verdict.detailed_analysis)

    if verdict.evidence:
        st.markdown("### Evidence")
        for e in verdict.evidence:
            st.markdown(f"- {e}")

    if verdict.remediation_steps:
        st.markdown("### Suggested remediation steps")
        for i, step in enumerate(verdict.remediation_steps, start=1):
            st.markdown(f"{i}. {step}")

    if verdict.verification_steps:
        st.markdown("### How to verify the fix")
        for step in verdict.verification_steps:
            st.markdown(f"- {step}")

    if verdict.additional_logs_needed:
        st.warning("More evidence would sharpen this diagnosis:")
        for item in verdict.additional_logs_needed:
            st.markdown(f"- {item}")

    if verdict.raw_error:
        with st.expander("Debug info"):
            st.code(verdict.raw_error)

    with st.expander("View raw extracted signals (what the AI actually saw)"):
        st.json(signals.to_dict())

    with st.expander("Session metadata"):
        st.json(artifacts.metadata)
