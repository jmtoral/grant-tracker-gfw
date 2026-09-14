"""Streamlit UI. Only renders and calls src/; all logic and state live in src/ and the database."""
import html
import json
import re

import streamlit as st

from src import APP_VERSION, config, review
from src.db import ApprovedRecord, Document, Run, audit_trail, document_pages, pilot_metrics, save_run, session_factory
from src.ingestion import IngestionError, parse_document
from src.llm import make_provider
from src.models import FIELDS, MULTI_FIELDS, ReviewStatus, is_empty
from src.pipeline import run_pipeline

st.set_page_config(page_title="Grant Intake Copilot", layout="wide")

STATUS = {ReviewStatus.pending: ("Pending", "gray"), ReviewStatus.approved: ("Approved", "green"),
          ReviewStatus.edited: ("Edited", "blue"), ReviewStatus.rejected: ("Rejected", "red"),
          ReviewStatus.manual_review: ("Needs review", "orange")}
JSON_COLUMNS = ["predicted_value", "reviewed_value", "evidence"]
PROVIDER_LABELS = {"fake": "Fake (offline demo)", "gemini": "Google Gemini", "claude": "Anthropic Claude",
                   "openai": "OpenAI", "deepseek": "DeepSeek"}


@st.cache_resource
def sessions():
    return session_factory()


def pct(x):
    return "—" if x is None else f"{x:.0%}"


def as_text(df):
    """JSON columns -> strings, for display and CSV export."""
    return df.assign(**{c: df[c].map(json.dumps) for c in JSON_COLUMNS})


def highlight(text: str, quotes: list[str]) -> str:
    """Escape the (untrusted) document text, then wrap quote matches in <mark>."""
    spans = []
    for q in quotes:
        m = re.search(r"\s+".join(map(re.escape, q.split())), text, re.IGNORECASE)
        if m:
            spans.append(list(m.span()))
    merged = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out, pos = [], 0
    for a, b in merged:
        out += [html.escape(text[pos:a]), "<mark>", html.escape(text[a:b]), "</mark>"]
        pos = b
    out.append(html.escape(text[pos:]))
    return ('<div style="white-space:pre-wrap;line-height:1.5;max-height:70vh;overflow-y:auto;'
            'border:1px solid #e0e0e0;border-radius:6px;padding:12px">' + "".join(out) + "</div>")


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("Grant Intake Copilot")
    nav = st.radio("Navigation", ["Review", "Audit trail", "Pilot metrics"], key="nav")
    provider = st.selectbox("LLM provider", list(PROVIDER_LABELS), format_func=PROVIDER_LABELS.get,
                            index=list(PROVIDER_LABELS).index(config.provider_name()), key="provider")
    api_key, model = "", "fake"
    if provider != "fake":
        # key lives only in this browser session's memory: never written to disk, DB or logs
        typed = st.text_input("API key", type="password", key=f"api_key_{provider}",
                              help="Kept only in this browser session. Never saved to disk or the database.")
        api_key = typed.strip() or config.env_key(provider)
        model = st.selectbox("Model", config.models(provider), accept_new_options=True, key=f"model_{provider}",
                             help="Pick a suggested model or type another model name.")
    if api_key:
        st.success(f"Using {PROVIDER_LABELS[provider]} · {model}" + ("" if typed.strip() else " (key from .env)"))
        st.caption(f"The document text is sent to {PROVIDER_LABELS[provider]}.")
    else:
        st.warning("Fake provider (offline, deterministic)"
                   + ("" if provider == "fake" else " — enter an API key to use " + PROVIDER_LABELS[provider]))
        model = "fake"
    reviewer = st.text_input("Reviewer", key="reviewer")
    st.caption(f"App {APP_VERSION} · model {model}"
               f" · prompt {config.prompt_version()} · taxonomy {config.taxonomy_version()}")

S = sessions()


# ---------------------------------------------------------------- review
def start_run(name: str, data: bytes):
    settings = config.settings()
    try:
        doc = parse_document(name, data, settings)
    except IngestionError as e:
        st.error(str(e))
        return
    llm = make_provider(provider, api_key, model)
    with st.spinner(f"Running AI extraction ({llm.name})..."):
        app, result, error = run_pipeline(doc, llm, settings, config.taxonomies())
    with S() as s:
        run_id = save_run(s, doc, app, result, llm.name, settings)
    st.session_state.run_id = run_id
    st.query_params["run"] = str(run_id)  # survives a page reload
    if error:
        st.error(error)


def decide(run_id, field, action, value=None):
    try:
        with S() as s:
            review.decide(s, run_id, field, action, value, reviewer)
    except review.ReviewError as e:
        st.error(str(e))
        return
    st.rerun()


def go_to_page(key, page):
    st.session_state[key] = page


def value_widget(f, key, disabled):
    current = f.predicted_value if f.review_status in review.OPEN else f.reviewed_value
    options = config.taxonomies().get(f.field)
    kw = dict(label=f.field, key=key, disabled=disabled, label_visibility="collapsed")
    if f.field in MULTI_FIELDS:
        return st.multiselect(options=options, default=[v for v in current or [] if v in options], **kw)
    if options:
        return st.selectbox(options=options, index=options.index(current) if current in options else None, **kw)
    if f.field == "amount_requested":
        return st.number_input(value=None if current is None else float(current), min_value=0.0,
                               step=1000.0, format="%.2f", **kw)
    if f.field == "project_summary":
        return st.text_area(value=current or "", **kw)
    return st.text_input(value=current or "", **kw)


def field_card(run_id, f, unit, page_key, finalized):
    with st.container(border=True):
        title, badge = st.columns([4, 1])
        title.markdown(f"**{f.field.replace('_', ' ').capitalize()}**")
        label, color = STATUS[f.review_status]
        with badge:
            st.badge(label, color=color)
        value = value_widget(f, f"w_{run_id}_{f.field}_{f.review_status}", finalized)
        st.caption(f"{f.confidence:.0%} — {f.confidence_level.value.title()} confidence (heuristic)")
        for i, e in enumerate(f.evidence):
            quote, jump = st.columns([5, 1])
            with quote:
                st.html('<blockquote style="margin:0;padding-left:8px;border-left:3px solid #bbb;color:#333">'
                        f"{html.escape(e.text)}</blockquote>")
                if e.verified:
                    st.markdown(f":gray-badge[{unit} {e.page}] :blue-badge[{e.evidence_type.value}]")
                else:
                    st.markdown(f":orange-badge[⚠ Unverified quote] :blue-badge[{e.evidence_type.value}]")
            if e.verified:
                jump.button("Go", key=f"go_{run_id}_{f.field}_{i}", help=f"Show {unit.lower()} {e.page}",
                            on_click=go_to_page, args=(page_key, e.page))
        a, b, c = st.columns(3)
        if a.button("Approve", key=f"approve_{run_id}_{f.field}",
                    disabled=finalized or is_empty(f.predicted_value)):
            decide(run_id, f.field, "approve")
        if b.button("Save edit", key=f"edit_{run_id}_{f.field}", disabled=finalized):
            decide(run_id, f.field, "edit", value)
        if c.button("Reject", key=f"reject_{run_id}_{f.field}", disabled=finalized):
            decide(run_id, f.field, "reject")


def approved_tabs(run_id):
    st.divider()
    st.subheader("Approved record")
    with S() as s:
        record = s.get(ApprovedRecord, run_id)
        trail = as_text(audit_trail(s, run_id))
    canonical, salesforce, trail_tab = st.tabs(["Canonical JSON", "Salesforce payload", "Audit trail"])
    with canonical:
        st.json(record.canonical_json)
        st.download_button("Download canonical JSON", json.dumps(record.canonical_json, indent=2),
                           f"grant_{run_id}_canonical.json", "application/json")
    with salesforce:
        st.caption("Salesforce-compatible payload. It is NOT sent anywhere (no Salesforce connection).")
        payload = record.salesforce_payload
        if st.button("Generate Salesforce Payload", key="sf_payload"):
            with S() as s:
                payload = review.generate_payload(s, run_id)
        if payload:
            st.json(payload)
            st.download_button("Download Salesforce payload", json.dumps(payload, indent=2),
                               f"grant_{run_id}_salesforce.json", "application/json")
    with trail_tab:
        st.dataframe(trail, hide_index=True)
        st.download_button("Download audit trail (CSV)", trail.to_csv(index=False),
                           f"grant_{run_id}_audit.csv", "text/csv")


def show_run(run_id: int):
    with S() as s:
        run = s.get(Run, run_id)
        if run is None:
            st.error("Run not found.")
            return
        doc = s.get(Document, run.document_id)
        pages = document_pages(s, doc.id)
        app = review.load_application(s, run_id)
    finalized = run.finalized_at is not None
    unit = "Section" if doc.file_type == "docx" else "Page"
    if run.status == "llm_failed":
        st.warning("The LLM output could not be used: every field needs manual review.")

    st.html("<style>.st-key-doc_panel{position:sticky;top:1rem;align-self:flex-start}</style>")
    left, right = st.columns([1, 1.2], gap="large")
    with left, st.container(key="doc_panel"):
        st.html(f"<h3 style='margin:0'>{html.escape(doc.filename)}</h3>")  # filename is untrusted
        st.caption(f"{len(pages)} {unit.lower()}s · {run.model_name} {run.model_version} · run #{run_id}")
        page_key = f"page_{run_id}"
        page = st.selectbox(unit, range(1, len(pages) + 1), key=page_key, format_func=lambda i: f"{unit} {i}")
        st.html(highlight(pages[page - 1], [e.text for f in app.fields() for e in f.evidence
                                             if e.verified and e.page == page]))
    with right:
        st.subheader("GRANT REVIEW")
        decided = sum(f.review_status not in review.OPEN for f in app.fields())
        st.progress(decided / len(FIELDS), text=f"{decided}/{len(FIELDS)} fields decided")
        bulk, fin = st.columns(2)
        if bulk.button("Approve all High-confidence fields", key="bulk", disabled=finalized):
            with S() as s:
                review.approve_all_high(s, run_id, reviewer)
            st.rerun()
        if fin.button("Finalize review", key="finalize", type="primary",
                      disabled=finalized or not review.can_finalize(app)):
            with S() as s:
                review.finalize(s, run_id, reviewer)
            st.rerun()
        if finalized:
            st.success("Review finalized.")
        for f in app.fields():
            field_card(run_id, f, unit, page_key, finalized)
    if finalized:
        approved_tabs(run_id)


def review_page():
    upload_col, sample_col = st.columns([2, 1])
    upload = upload_col.file_uploader("Upload a grant application", type=["pdf", "docx", "txt"])
    samples = sorted(p.name for p in config.SAMPLES.iterdir() if p.suffix in (".pdf", ".docx", ".txt"))
    sample = sample_col.selectbox("Load sample document", samples, index=None, key="sample")
    if st.button("Run AI extraction", key="run", type="primary", disabled=not (upload or sample)):
        if upload:
            start_run(upload.name, upload.getvalue())
        else:
            start_run(sample, (config.SAMPLES / sample).read_bytes())
    run_id = st.session_state.get("run_id") or st.query_params.get("run")
    if run_id:
        show_run(int(run_id))
    else:
        st.info("Upload a document or load a sample, then run the AI extraction.")


# ---------------------------------------------------------------- audit trail
def audit_page():
    st.header("Audit trail")
    with S() as s:
        df = audit_trail(s)
    if df.empty:
        st.info("No review decisions yet.")
        return
    doc_col, field_col = st.columns(2)
    docs = doc_col.multiselect("Document", sorted(df.filename.unique()))
    fields = field_col.multiselect("Field", FIELDS)
    if docs:
        df = df[df.filename.isin(docs)]
    if fields:
        df = df[df.field_name.isin(fields)]
    df = as_text(df)
    st.dataframe(df, hide_index=True)
    st.download_button("Export CSV", df.to_csv(index=False), "audit_trail.csv", "text/csv")


# ---------------------------------------------------------------- metrics
def metrics_page():
    st.header("Pilot metrics")
    st.warning("These metrics treat the human decision as ground truth, so they can carry automation bias "
               "(reviewers tend to accept what the AI proposes). Only finalized reviews are counted.")
    with S() as s:
        m = pilot_metrics(s)
    if m is None:
        st.info("Finalize at least one review to see metrics.")
        return
    main, no_bulk = st.columns(2)
    main.metric("AI-populated fields accepted without modification", pct(m["acceptance_rate"]),
                help="approved / (approved + edited + rejected), latest decision per field, non-null predictions")
    no_bulk.metric("Same, excluding bulk approvals", pct(m["acceptance_rate_excl_bulk"]),
                   help=f"{m['bulk_approvals']} of {m['fields_decided']} decided fields were bulk-approved")
    cols = st.columns(5)
    cols[0].metric("Manual review rate", pct(m["manual_review_rate"]))
    cols[1].metric("Error rate, Low confidence", pct(m["low_confidence_error_rate"]))
    cols[2].metric("Avg confidence, accepted", pct(m["avg_confidence_accepted"]))
    cols[3].metric("Avg confidence, corrected", pct(m["avg_confidence_corrected"]))
    cols[4].metric("Avg review time", f"{m['avg_review_seconds']:.0f} s", help=f"{m['runs_finalized']} reviews")
    st.subheader("By field")
    st.caption("Accuracy by field is a proxy: an approved field counts as correct.")
    st.dataframe(m["by_field"].rename(columns={"acceptance_rate": "acceptance = accuracy (proxy)"}),
                 column_config={c: st.column_config.NumberColumn(format="percent")
                                for c in ["acceptance = accuracy (proxy)", "correction_rate"]})


{"Review": review_page, "Audit trail": audit_page, "Pilot metrics": metrics_page}[nav]()
