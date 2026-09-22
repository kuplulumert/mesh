"""Last-resort analyzer.

Used when nothing better is available: an unknown CAD format, a licence that
is not free, or a corrupted file that still has to reach Fluent.  It produces
a deliberately conservative metric set and says loudly what it does not know,
so the planner can widen its safety margins and the report can explain why
the mesh is coarser than it could be.
"""

from __future__ import annotations

import os
from typing import Optional

from ..config import Config
from ..models import BoundingBox, GeometryMetrics
from .base import GeometryAnalyzer, extension


class FallbackAnalyzer(GeometryAnalyzer):
    name = "fallback"

    def available(self) -> bool:
        return True

    def can_handle(self, path: str) -> bool:
        return True

    def analyze(self, path: str, cfg: Config) -> GeometryMetrics:
        metrics = GeometryMetrics(analyzer="fallback")
        metrics.length_unit_hint = cfg.geometry.length_unit or "mm"
        metrics.warnings.append(
            "Geometri ölçülemedi: {0} formatı için bir analizör yok. "
            "Boyutlandırma Fluent'in içe aktardığı sınır kutusundan türetilecek."
            .format(extension(path) or "bilinmeyen")
        )
        hint = cfg.geometry.characteristic_length
        if hint and hint > 0:
            half = hint / 2.0
            metrics.bbox = BoundingBox(-half, -half, -half, half, half, half)
            metrics.warnings.append(
                "geometry.characteristic_length ({0} m) sınır kutusu olarak kullanıldı."
                .format(hint)
            )
        metrics.raw["file_size_bytes"] = (
            os.path.getsize(path) if os.path.isfile(path) else 0
        )
        return metrics


def metrics_from_bounding_box(
    xmin: float, ymin: float, zmin: float,
    xmax: float, ymax: float, zmax: float,
    analyzer: str = "fluent-probe",
    length_unit: str = "m",
) -> GeometryMetrics:
    """Build metrics when only Fluent's own bounding box is known.

    Fluent reports the imported model's extents after ``Import Geometry``;
    that is enough to recover from a failed up-front analysis.
    """
    metrics = GeometryMetrics(analyzer=analyzer)
    metrics.bbox = BoundingBox(xmin, ymin, zmin, xmax, ymax, zmax)
    metrics.length_unit_hint = length_unit
    metrics.warnings.append(
        "Boyutlandırma yalnızca Fluent'in bildirdiği sınır kutusuna dayanıyor."
    )
    return metrics
