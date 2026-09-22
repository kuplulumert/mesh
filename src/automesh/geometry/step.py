"""Dependency-free STEP (AP203/AP214/AP242) reader.

This is deliberately *not* a CAD kernel.  It scans the exchange file's
entity records and pulls out exactly what the mesh planner needs:

* the bounding box (from ``CARTESIAN_POINT`` control points),
* the smallest circle / cylinder / sphere radius, which is what fixes the
  minimum cell size in practice (bolt holes, fillets, nozzle throats),
* face / edge counts and the planar-vs-curved split,
* the file's length unit.

Control points of a NURBS surface lie slightly outside the surface, so the
bounding box can be a few percent generous.  For sizing decisions that is
completely irrelevant, and it costs no licence and no dependency.
"""

from __future__ import annotations

import math
import os
import re
from typing import Dict, Iterator, List, Optional, Tuple

from .base import percentile

CHUNK = 8 * 1024 * 1024
OVERLAP = 4096
MAX_BYTES = 1024 * 1024 * 1024

# STEP writes "0.", "100.", ".5" and "1.2E-3" - all of them must match.
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?"

_POINT_RE = re.compile(
    r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(\s*(" + _NUM + r")\s*,\s*("
    + _NUM + r")\s*,\s*(" + _NUM + r")\s*\)", re.IGNORECASE)
_CIRCLE_RE = re.compile(
    r"\bCIRCLE\s*\(\s*'[^']*'\s*,\s*#\d+\s*,\s*(" + _NUM + r")\s*\)", re.IGNORECASE)
_CYL_RE = re.compile(
    r"CYLINDRICAL_SURFACE\s*\(\s*'[^']*'\s*,\s*#\d+\s*,\s*(" + _NUM + r")\s*\)",
    re.IGNORECASE)
_SPH_RE = re.compile(
    r"SPHERICAL_SURFACE\s*\(\s*'[^']*'\s*,\s*#\d+\s*,\s*(" + _NUM + r")\s*\)",
    re.IGNORECASE)
_TOR_RE = re.compile(
    r"TOROIDAL_SURFACE\s*\(\s*'[^']*'\s*,\s*#\d+\s*,\s*" + _NUM + r"\s*,\s*("
    + _NUM + r")\s*\)", re.IGNORECASE)
_CONE_RE = re.compile(
    r"CONICAL_SURFACE\s*\(\s*'[^']*'\s*,\s*#\d+\s*,\s*(" + _NUM + r")\s*,",
    re.IGNORECASE)

_FACE_RE = re.compile(r"\bADVANCED_FACE\s*\(", re.IGNORECASE)
_PLANE_RE = re.compile(r"=\s*PLANE\s*\(", re.IGNORECASE)
_EDGE_RE = re.compile(r"\bEDGE_CURVE\s*\(", re.IGNORECASE)
_SOLID_RE = re.compile(r"\b(?:MANIFOLD_SOLID_BREP|BREP_WITH_VOIDS)\s*\(", re.IGNORECASE)
_SHELL_RE = re.compile(r"\bCLOSED_SHELL\s*\(", re.IGNORECASE)
_OPEN_SHELL_RE = re.compile(r"\bOPEN_SHELL\s*\(", re.IGNORECASE)

# STEP writes the prefix either as ".MILLI." or, when there is none, as "$"
# or "*".  The length unit is usually inside a LENGTH_UNIT record, but some
# writers emit a bare SI_UNIT, so both spellings are tried in that order.
_SI_UNIT_STRICT_RE = re.compile(
    r"LENGTH_UNIT\s*\(\s*\)[^;]{0,400}?SI_UNIT\s*\(\s*([.$*\w]*?)\s*,\s*\.(\w+)\.\s*\)",
    re.IGNORECASE | re.DOTALL)
_SI_UNIT_LOOSE_RE = re.compile(
    r"SI_UNIT\s*\(\s*([.$*\w]*?)\s*,\s*\.(\w+)\.\s*\)",
    re.IGNORECASE | re.DOTALL)
_CONV_UNIT_RE = re.compile(
    r"CONVERSION_BASED_UNIT\s*\(\s*'([^']*)'", re.IGNORECASE)

_SI_PREFIX = {
    "": 1.0, "MILLI": 1.0e-3, "CENTI": 1.0e-2, "DECI": 1.0e-1,
    "MICRO": 1.0e-6, "NANO": 1.0e-9, "KILO": 1.0e3,
}
_CONV_UNITS = {
    "INCH": 0.0254, "INCHES": 0.0254, "FOOT": 0.3048, "FEET": 0.3048,
    "MIL": 2.54e-5, "THOU": 2.54e-5, "YARD": 0.9144,
}
_UNIT_NAME = {
    1.0: "m", 1.0e-3: "mm", 1.0e-2: "cm", 1.0e-6: "um", 1.0e-9: "nm",
    0.0254: "in", 0.3048: "ft",
}


def _chunks(path: str) -> Iterator[str]:
    """Yield overlapping text chunks so entities are never split in half."""
    size = os.path.getsize(path)
    read = 0
    tail = ""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            read += len(block)
            yield tail + block
            tail = block[-OVERLAP:]
            if read >= MAX_BYTES:
                break


def detect_unit(text: str) -> Tuple[float, str]:
    """Return ``(metres_per_unit, unit_name)`` for the STEP header block."""
    conv = _CONV_UNIT_RE.search(text)
    if conv:
        factor = _CONV_UNITS.get(conv.group(1).strip().upper())
        if factor:
            return factor, _UNIT_NAME.get(factor, conv.group(1).lower())
    for regex in (_SI_UNIT_STRICT_RE, _SI_UNIT_LOOSE_RE):
        si = regex.search(text)
        if not si:
            continue
        prefix = (si.group(1) or "").strip().strip(".$*").upper()
        base = (si.group(2) or "").upper()
        if base.startswith("METRE") or base.startswith("METER"):
            factor = _SI_PREFIX.get(prefix, 1.0)
            return factor, _UNIT_NAME.get(factor, "m")
    return 1.0e-3, "mm"   # overwhelmingly the most common CAD default


def parse_step(path: str) -> Dict:
    """Scan ``path`` and return a raw metrics dictionary (SI units)."""
    lo = [math.inf, math.inf, math.inf]
    hi = [-math.inf, -math.inf, -math.inf]
    radii: List[float] = []
    counts = {"faces": 0, "planes": 0, "edges": 0, "solids": 0,
              "closed_shells": 0, "open_shells": 0, "points": 0}

    factor, unit_name = 1.0e-3, "mm"
    header_seen = False
    truncated = False
    total = os.path.getsize(path)

    for index, chunk in enumerate(_chunks(path)):
        if not header_seen:
            factor, unit_name = detect_unit(chunk)
            header_seen = True
        for match in _POINT_RE.finditer(chunk):
            counts["points"] += 1
            for axis in range(3):
                value = float(match.group(axis + 1).replace("D", "E").replace("d", "e"))
                if value < lo[axis]:
                    lo[axis] = value
                if value > hi[axis]:
                    hi[axis] = value
        for regex in (_CIRCLE_RE, _CYL_RE, _SPH_RE, _TOR_RE, _CONE_RE):
            for match in regex.finditer(chunk):
                value = float(match.group(1).replace("D", "E").replace("d", "e"))
                if value > 0:
                    radii.append(value)
        counts["faces"] += len(_FACE_RE.findall(chunk))
        counts["planes"] += len(_PLANE_RE.findall(chunk))
        counts["edges"] += len(_EDGE_RE.findall(chunk))
        counts["solids"] += len(_SOLID_RE.findall(chunk))
        counts["closed_shells"] += len(_SHELL_RE.findall(chunk))
        counts["open_shells"] += len(_OPEN_SHELL_RE.findall(chunk))
        if (index + 1) * CHUNK >= MAX_BYTES and total > MAX_BYTES:
            truncated = True

    if counts["points"] == 0:
        raise ValueError("STEP dosyasında CARTESIAN_POINT bulunamadı: {0}".format(path))

    bbox = tuple(v * factor for v in (lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]))
    radii_m = sorted(r * factor for r in radii)
    curved = max(counts["faces"] - counts["planes"], 0)
    curved_ratio = (curved / counts["faces"]) if counts["faces"] else 0.0

    diag = math.sqrt(sum((bbox[i + 3] - bbox[i]) ** 2 for i in range(3)))
    # Ignore absurdly small radii - they are almost always modelling debris.
    useful_radii = [r for r in radii_m if r > diag * 1.0e-6] if diag > 0 else radii_m
    min_radius = percentile(useful_radii, 0.02) if useful_radii else 0.0

    small_radii = [r for r in useful_radii if diag > 0 and r < diag * 0.01]
    small_ratio = (len(small_radii) / counts["faces"]) if counts["faces"] else 0.0

    result = {
        "bbox": bbox,
        "volume": 0.0,
        "area": 0.0,
        "face_count": counts["faces"],
        "edge_count": counts["edges"],
        "body_count": max(counts["solids"], counts["closed_shells"], 1),
        "min_curvature_radius": min_radius,
        "min_edge_length": 0.0,
        "min_face_size": 0.0,
        "curved_face_ratio": curved_ratio,
        "small_feature_ratio": min(small_ratio, 1.0),
        "watertight": counts["open_shells"] == 0 and counts["closed_shells"] > 0,
        "has_free_edges": counts["open_shells"] > 0,
        "length_unit_hint": unit_name,
        "unit_factor": factor,
        "radius_count": len(useful_radii),
        "truncated": truncated,
    }
    return result


# --------------------------------------------------------------------------

class StepAnalyzer:
    """Analyzer backend wrapping :func:`parse_step`."""

    name = "step"

    def available(self) -> bool:
        return True

    def can_handle(self, path: str) -> bool:
        return os.path.splitext(path)[1].lower() in (".step", ".stp")

    def analyze(self, path: str, cfg) -> "object":
        from ..models import BodyInfo, BoundingBox, GeometryMetrics

        data = parse_step(path)
        metrics = GeometryMetrics(analyzer="step")
        metrics.bbox = BoundingBox(*[float(v) for v in data["bbox"]])
        for key in (
            "volume", "area", "min_curvature_radius", "min_edge_length",
            "min_face_size", "curved_face_ratio", "small_feature_ratio",
        ):
            if data.get(key) is not None:
                setattr(metrics, key, float(data[key]))
        for key in ("face_count", "edge_count", "body_count"):
            setattr(metrics, key, int(data.get(key) or 0))
        metrics.watertight = data.get("watertight")
        metrics.has_free_edges = data.get("has_free_edges")
        metrics.length_unit_hint = data.get("length_unit_hint", "mm")
        metrics.raw = dict(data)
        metrics.raw.pop("bbox", None)
        metrics.bodies = [
            BodyInfo(name="step-body", face_count=metrics.face_count,
                     edge_count=metrics.edge_count, is_solid=bool(metrics.watertight))
        ]
        metrics.warnings.append(
            "STEP dosyası CAD çekirdeği olmadan okundu: hacim/alan bilinmiyor, "
            "sınır kutusu kontrol noktalarından türetildi (birkaç % büyük olabilir)."
        )
        if data.get("truncated"):
            metrics.warnings.append(
                "STEP dosyası çok büyük olduğu için kısmen tarandı.")
        if not data.get("radius_count"):
            metrics.warnings.append(
                "Dosyada yay/silindir bulunamadı; en küçük özellik tahmini zayıf.")
        return metrics
