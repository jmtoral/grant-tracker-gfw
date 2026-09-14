"""Heuristic confidence (NOT a calibrated probability).

`score` is pure: (field, prediction, evidence, settings) -> float. Swap it for a calibrated model
trained on accumulated human decisions without touching the rest of the pipeline.
"""
# ponytail: pesos a mano; calibrar con decisiones humanas acumuladas (review_decisions)
import re
from statistics import mean

from src.models import CLASSIFIED_FIELDS, ConfidenceLevel, Evidence, is_empty

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'",
                         "“": '"', "”": '"', "„": '"', "‟": '"'})
_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(million|thousand|m\b|k\b)?")
_SCALE = {"million": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}


def normalize(text: str) -> str:
    """Lowercase, straight quotes, collapsed whitespace. Shared with evidence verification."""
    return " ".join(text.translate(_QUOTES).lower().split())


def score(field: str, prediction, evidence: list[Evidence], settings: dict) -> float:
    """prediction: the LLM's per-field output (.value, .self_confidence)."""
    c = settings["confidence"]
    value = prediction.value
    if is_empty(value):
        return 0.0
    qs, w = c["quote_scores"], c["weights"]
    quality = mean(
        (qs["explicit_verified"] if e.evidence_type == "explicit" else qs["inferred_verified"])
        if e.verified else qs["unverified"]
        for e in evidence
    ) if evidence else 0.0
    verified = [normalize(e.text) for e in evidence if e.verified]
    quantity = min(len(verified), 2) / 2
    s = (w["evidence_quality"] * quality + w["evidence_quantity"] * quantity
         + w["value_text_coherence"] * _coherence(field, value, verified, c)
         + w["llm_self_report"] * prediction.self_confidence)
    if field in CLASSIFIED_FIELDS and not verified:
        s = min(s, c["caps"]["classified_without_verified_evidence"])
    if "Other" in (value if isinstance(value, list) else [value]):
        s = min(s, c["caps"]["other_value"])
    return round(s, 4)


def level(confidence: float, settings: dict) -> ConfidenceLevel:
    lv = settings["confidence"]["levels"]
    if confidence >= lv["high"]:
        return ConfidenceLevel.high
    return ConfidenceLevel.medium if confidence >= lv["medium"] else ConfidenceLevel.low


def _coherence(field, value, quotes: list[str], c: dict) -> float:
    """1.0 if the value is visible in a verified quote, else 0.5 (mean over values for lists)."""
    text = " || ".join(quotes)
    if field in CLASSIFIED_FIELDS:
        values = value if isinstance(value, list) else [value]
        return mean(
            1.0 if any(normalize(t) in text for t in [v, *c["synonyms"].get(v, [])]) else 0.5
            for v in values)
    if field == "amount_requested":
        amounts = [float(n.replace(",", "")) * _SCALE.get(unit, 1) for n, unit in _NUMBER.findall(text)]
        return 1.0 if any(abs(a - float(value)) < 0.5 for a in amounts) else 0.5
    if field == "project_summary":  # a synthesis never appears verbatim: measure word grounding instead
        words = set(re.findall(r"[a-z]{4,}", normalize(value)))
        found = sum(w in text for w in words) / len(words) if words else 0
        return 1.0 if found >= c["summary_overlap"] else 0.5
    return 1.0 if normalize(str(value)) in text else 0.5
