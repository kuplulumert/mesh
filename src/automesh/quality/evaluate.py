"""Read Fluent's quality output and decide what to do about it.

Fluent phrases the same number half a dozen ways depending on the release,
the mode (meshing vs. solver) and the selected quality method, so the parser
matches on the metric rather than on one exact sentence.
"""

from __future__ import annotations

import re
from typing import List, Optional

from ..config import QualityThresholds
from ..models import QualityReport, QualityVerdict

_NUM = r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)"


def _search(patterns, text: str) -> Optional[float]:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            try:
                return float(match.group(1))
            except (TypeError, ValueError):
                continue
    return None


def _compile(*fragments) -> List[re.Pattern]:
    return [re.compile(f, re.IGNORECASE) for f in fragments]


_SKEW_PATTERNS = _compile(
    r"maximum\s+cell\s+skewness\s*[:=]\s*" + _NUM,
    r"maximum\s+ortho\s+skew\s*[:=]\s*" + _NUM,
    r"max(?:imum)?\s+skewness\s*[:=]\s*" + _NUM,
    r"worst\s+cell\s+skewness\s*[:=]\s*" + _NUM,
    r"skewness\s*\(max\)\s*[:=]\s*" + _NUM,
)
_SURFACE_SKEW_PATTERNS = _compile(
    r"maximum\s+(?:surface|face|boundary)\s+skewness\s*[:=]\s*" + _NUM,
    r"max(?:imum)?\s+face\s+skew(?:ness)?\s*[:=]\s*" + _NUM,
    r"maximum\s+skewness\s*[:=]\s*" + _NUM,
)
_ORTHO_PATTERNS = _compile(
    r"minimum\s+orthogonal\s+quality\s*[:=]\s*" + _NUM,
    r"min(?:imum)?\s+ortho(?:gonal)?\s+quality\s*[:=]\s*" + _NUM,
    r"orthogonal\s+quality\s*\(min\)\s*[:=]\s*" + _NUM,
)
_SURFACE_ORTHO_PATTERNS = _compile(
    r"minimum\s+(?:surface|face|boundary)\s+orthogonal\s+quality\s*[:=]\s*" + _NUM,
    r"min(?:imum)?\s+face\s+ortho(?:gonal)?\s+quality\s*[:=]\s*" + _NUM,
) + _ORTHO_PATTERNS
_ASPECT_PATTERNS = _compile(
    r"maximum\s+aspect\s+ratio\s*[:=]\s*" + _NUM,
    r"aspect\s+ratio\s*\(max\)\s*[:=]\s*" + _NUM,
)
_MIN_VOLUME_PATTERNS = _compile(
    r"minimum\s+(?:cell\s+)?volume\s*(?:\([^)]*\))?\s*[:=]\s*" + _NUM,
)
_COUNT_PATTERNS = _compile(
    r"([\d,]+)\s+cells?\s*,",
    r"number\s+of\s+cells?\s*[:=]\s*([\d,]+)",
    r"total\s+cells?\s*[:=]\s*([\d,]+)",
)
_FACE_COUNT_PATTERNS = _compile(
    r"([\d,]+)\s+faces?\s*,",
    r"number\s+of\s+faces?\s*[:=]\s*([\d,]+)",
)
_NODE_COUNT_PATTERNS = _compile(
    r"([\d,]+)\s+nodes?\s*[.,]",
    r"number\s+of\s+nodes?\s*[:=]\s*([\d,]+)",
)
_NEGATIVE_VOLUME_PATTERNS = _compile(
    r"([\d,]+)\s+cells?\s+with\s+negative\s+volume",
    r"negative\s+(?:cell\s+)?volume[^\d]*([\d,]+)",
)
_LEFT_HANDED_PATTERNS = _compile(
    r"([\d,]+)\s+left[- ]handed\s+faces?",
    r"left[- ]handed\s+faces?[^\d]*([\d,]+)",
)
_FREE_FACE_PATTERNS = _compile(
    r"([\d,]+)\s+free\s+faces?",
    r"free\s+faces?\s*[:=]\s*([\d,]+)",
)


def _int_search(patterns, text: str) -> int:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            try:
                return int(match.group(1).replace(",", ""))
            except (TypeError, ValueError):
                continue
    return 0


def parse_quality(text: str, stage: str = "volume") -> QualityReport:
    """Extract every metric Fluent mentioned in ``text``."""
    report = QualityReport(stage=stage, raw_text=text or "")
    if not text:
        return report

    if stage == "surface":
        report.max_skewness = _search(_SURFACE_SKEW_PATTERNS, text)
        report.min_orthogonal_quality = _search(_SURFACE_ORTHO_PATTERNS, text)
        report.face_count = _int_search(_FACE_COUNT_PATTERNS, text)
    else:
        report.max_skewness = _search(_SKEW_PATTERNS, text)
        report.min_orthogonal_quality = _search(_ORTHO_PATTERNS, text)
        report.cell_count = _int_search(_COUNT_PATTERNS, text)
        report.face_count = _int_search(_FACE_COUNT_PATTERNS, text)

    report.max_aspect_ratio = _search(_ASPECT_PATTERNS, text)
    report.min_volume = _search(_MIN_VOLUME_PATTERNS, text)
    report.node_count = _int_search(_NODE_COUNT_PATTERNS, text)
    report.negative_volume_count = _int_search(_NEGATIVE_VOLUME_PATTERNS, text)
    report.left_handed_face_count = _int_search(_LEFT_HANDED_PATTERNS, text)

    # "negative volume" mentioned without a count still means trouble.
    low = text.lower()
    if report.negative_volume_count == 0 and "negative volume" in low:
        report.negative_volume_count = 1
    if report.left_handed_face_count == 0 and "left-handed" in low:
        report.left_handed_face_count = 1
    if report.min_volume is not None and report.min_volume < 0:
        report.negative_volume_count = max(report.negative_volume_count, 1)
    return report


def free_face_count(text: str) -> int:
    return _int_search(_FREE_FACE_PATTERNS, text or "")


# --------------------------------------------------------------------------

def judge(report: QualityReport, thresholds: QualityThresholds) -> QualityReport:
    """Set ``report.verdict`` and list which metrics failed."""
    failed: List[str] = []
    verdict = QualityVerdict.GOOD

    def worse(candidate: QualityVerdict) -> None:
        nonlocal verdict
        order = [QualityVerdict.GOOD, QualityVerdict.ACCEPTABLE,
                 QualityVerdict.POOR, QualityVerdict.UNUSABLE]
        if order.index(candidate) > order.index(verdict):
            verdict = candidate

    if report.stage == "surface":
        good = thresholds.surface_max_skewness_good
        accept = thresholds.surface_max_skewness_accept
        hard = thresholds.surface_max_skewness_hard
    else:
        good = thresholds.max_skewness_good
        accept = thresholds.max_skewness_accept
        hard = thresholds.max_skewness_hard

    skew = report.max_skewness
    if skew is not None:
        if skew >= hard:
            failed.append("skewness={0:.4f} >= {1:.2f} (kritik)".format(skew, hard))
            worse(QualityVerdict.UNUSABLE)
        elif skew > accept:
            failed.append("skewness={0:.4f} > {1:.2f}".format(skew, accept))
            worse(QualityVerdict.POOR)
        elif skew > good:
            failed.append("skewness={0:.4f} > {1:.2f} (hedef)".format(skew, good))
            worse(QualityVerdict.ACCEPTABLE)

    ortho = report.min_orthogonal_quality
    if ortho is not None and report.stage != "surface":
        if ortho <= thresholds.min_orthogonal_hard:
            failed.append("ortho={0:.4f} <= {1:.3f} (kritik)".format(
                ortho, thresholds.min_orthogonal_hard))
            worse(QualityVerdict.UNUSABLE)
        elif ortho < thresholds.min_orthogonal_accept:
            failed.append("ortho={0:.4f} < {1:.2f}".format(
                ortho, thresholds.min_orthogonal_accept))
            worse(QualityVerdict.POOR)
        elif ortho < thresholds.min_orthogonal_good:
            failed.append("ortho={0:.4f} < {1:.2f} (hedef)".format(
                ortho, thresholds.min_orthogonal_good))
            worse(QualityVerdict.ACCEPTABLE)

    aspect = report.max_aspect_ratio
    if aspect is not None:
        if aspect > thresholds.max_aspect_ratio_hard:
            failed.append("aspect_ratio={0:.1f} > {1:.0f} (kritik)".format(
                aspect, thresholds.max_aspect_ratio_hard))
            worse(QualityVerdict.POOR)
        elif aspect > thresholds.max_aspect_ratio_accept:
            failed.append("aspect_ratio={0:.1f} > {1:.0f}".format(
                aspect, thresholds.max_aspect_ratio_accept))
            worse(QualityVerdict.ACCEPTABLE)

    if report.negative_volume_count and not thresholds.allow_negative_volume:
        failed.append("{0} negatif hacimli hücre".format(report.negative_volume_count))
        worse(QualityVerdict.UNUSABLE)
    if report.left_handed_face_count and not thresholds.allow_left_handed_faces:
        failed.append("{0} sol-elli yüzey".format(report.left_handed_face_count))
        worse(QualityVerdict.POOR)

    if (report.max_skewness is None and report.min_orthogonal_quality is None
            and report.stage != "surface"):
        failed.append("kalite metrikleri okunamadı")
        worse(QualityVerdict.POOR)

    report.failed_metrics = failed
    report.verdict = verdict
    return report


def evaluate(text: str, thresholds: QualityThresholds, stage: str = "volume") -> QualityReport:
    return judge(parse_quality(text, stage=stage), thresholds)
