"""Geometry analysis front-end.

Several backends can produce :class:`~automesh.models.GeometryMetrics`:

``spaceclaim``
    Drives SpaceClaim head-less with an embedded script.  This is the only
    backend that sees the real B-Rep - exact areas, edge lengths, curvature
    radii - and it also re-exports the model in a format Fluent likes.
``discrete``
    Reads a tessellated file (STL/OBJ/PLY) with ``trimesh``.
``fallback``
    Asks Fluent itself (or the user) for the bounding box.  Always available,
    least informative.

Whatever produced the metrics, :func:`finalise` normalises the derived
fields so the planner sees the same shape of data every time.
"""

from __future__ import annotations

import math
import os
from typing import List, Optional, Sequence

from ..config import Config
from ..logging_utils import get_logger
from ..models import GeometryMetrics

#: Extensions SpaceClaim / Fluent understand as CAD (as opposed to faceted).
CAD_EXTENSIONS = (
    ".scdoc", ".scdocx", ".dsco", ".step", ".stp", ".iges", ".igs",
    ".x_t", ".x_b", ".xmt_txt", ".sat", ".catpart", ".catproduct",
    ".prt", ".asm", ".sldprt", ".sldasm", ".ipt", ".iam", ".pmdb", ".agdb",
)
DISCRETE_EXTENSIONS = (".stl", ".obj", ".ply", ".off", ".3mf")


class GeometryAnalyzerError(RuntimeError):
    pass


class GeometryAnalyzer:
    """Base class for the analysis backends."""

    name = "base"

    def available(self) -> bool:  # pragma: no cover - trivial
        return True

    def can_handle(self, path: str) -> bool:  # pragma: no cover - trivial
        return False

    def analyze(self, path: str, cfg: Config) -> GeometryMetrics:  # pragma: no cover
        raise NotImplementedError


# --------------------------------------------------------------------------

def extension(path: str) -> str:
    base = os.path.basename(path).lower()
    # ".msh.h5" style double extensions matter for meshes, not for CAD, so a
    # single split is enough here.
    return os.path.splitext(base)[1]


def is_cad(path: str) -> bool:
    return extension(path) in CAD_EXTENSIONS


def is_discrete(path: str) -> bool:
    return extension(path) in DISCRETE_EXTENSIONS


def percentile(values: Sequence[float], q: float) -> float:
    """Small dependency-free percentile (linear interpolation)."""
    data = sorted(v for v in values if v is not None and not math.isnan(v))
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    pos = (len(data) - 1) * max(0.0, min(1.0, q))
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return data[low]
    return data[low] + (data[high] - data[low]) * (pos - low)


def robust_min(values: Sequence[float], q: float = 0.02) -> float:
    """A minimum that ignores degenerate slivers.

    Real CAD is full of zero-length edges and sub-micron faces left over from
    imports.  Sizing the mesh off those is the classic way to produce a
    100-million-cell mesh of a 10 cm part, so the low percentile is used
    instead of the absolute minimum.
    """
    data = [v for v in values if v is not None and v > 0]
    if not data:
        return 0.0
    return percentile(data, q)


# --------------------------------------------------------------------------

def finalise(metrics: GeometryMetrics, cfg: Optional[Config] = None) -> GeometryMetrics:
    """Fill in derived fields and sanity-check what a backend produced."""
    diag = metrics.bbox.diagonal

    candidates: List[float] = [
        v for v in (
            metrics.min_edge_length,
            metrics.min_face_size,
            metrics.min_curvature_radius * 2.0 if metrics.min_curvature_radius else 0.0,
            metrics.thinnest_section,
        ) if v and v > 0
    ]
    if not metrics.min_feature_size:
        metrics.min_feature_size = min(candidates) if candidates else 0.0

    # A "feature" ten million times smaller than the model is noise, not a
    # feature.  Clamp it so the planner cannot be dragged into absurdity.
    if diag > 0 and metrics.min_feature_size > 0:
        floor = diag / 1.0e5
        if metrics.min_feature_size < floor:
            metrics.warnings.append(
                "En küçük özellik ({0:.3e} m) gövde boyutunun 1/100000'inden küçük; "
                "sliver yüzey olabilir, {1:.3e} m olarak sınırlandı.".format(
                    metrics.min_feature_size, floor
                )
            )
            metrics.min_feature_size = floor
    if metrics.min_feature_size <= 0 and diag > 0:
        metrics.min_feature_size = diag / 100.0
        metrics.warnings.append(
            "Özellik boyutu tespit edilemedi; gövde köşegeninin 1/100'ü varsayıldı."
        )

    if metrics.is_internal_flow is None:
        metrics.is_internal_flow = _guess_internal_flow(metrics)

    if cfg is not None and cfg.geometry.internal_flow is not None:
        metrics.is_internal_flow = cfg.geometry.internal_flow
    if cfg is not None and cfg.geometry.length_unit:
        metrics.length_unit_hint = cfg.geometry.length_unit

    if diag <= 0:
        metrics.warnings.append(
            "Sınır kutusu boş görünüyor - geometri okunamamış olabilir."
        )
    return metrics


def _guess_internal_flow(metrics: GeometryMetrics) -> bool:
    """Internal (duct/manifold) vs. external (body in a wind tunnel).

    A closed fluid volume has a small area-to-volume signature relative to
    its bounding box; a thin shell or an open surface model does not.  When
    there is no volume at all we are certainly not looking at a ready fluid
    domain.
    """
    if metrics.volume <= 0:
        return False
    bbox_volume = 1.0
    for s in metrics.bbox.sizes:
        bbox_volume *= max(s, 1e-12)
    fill = metrics.volume / bbox_volume if bbox_volume > 0 else 0.0
    # A fluid domain extracted from a manifold typically fills a small part of
    # its own bounding box; a bluff body fills most of it.
    return fill < 0.6


# --------------------------------------------------------------------------

def build_analyzers(cfg: Config) -> List[GeometryAnalyzer]:
    """Backends in priority order: exact B-Rep first, guesswork last."""
    from .discrete import DiscreteAnalyzer
    from .fallback import FallbackAnalyzer
    from .spaceclaim import SpaceClaimAnalyzer
    from .step import StepAnalyzer

    return [SpaceClaimAnalyzer(), StepAnalyzer(), DiscreteAnalyzer(), FallbackAnalyzer()]


def select_analyzer(path: str, cfg: Config) -> GeometryAnalyzer:
    from .discrete import DiscreteAnalyzer
    from .fallback import FallbackAnalyzer
    from .spaceclaim import SpaceClaimAnalyzer
    from .step import StepAnalyzer

    requested = (cfg.geometry.analyzer or "auto").lower()
    explicit = {
        "spaceclaim": SpaceClaimAnalyzer,
        "step": StepAnalyzer,
        "discrete": DiscreteAnalyzer,
        "fallback": FallbackAnalyzer,
    }
    if requested in explicit:
        analyzer = explicit[requested]()
        if not analyzer.available():
            raise GeometryAnalyzerError(
                "İstenen analizör '{0}' bu makinede kullanılamıyor.".format(requested)
            )
        return analyzer
    if requested != "auto":
        raise GeometryAnalyzerError("Bilinmeyen analizör: {0!r}".format(requested))

    for analyzer in build_analyzers(cfg):
        if analyzer.available() and analyzer.can_handle(path):
            return analyzer
    return FallbackAnalyzer()


def analyze_geometry(path: str, cfg: Config) -> GeometryMetrics:
    """Analyse ``path`` with the best available backend."""
    log = get_logger()
    if not os.path.isfile(path):
        raise GeometryAnalyzerError("Geometri dosyası bulunamadı: {0}".format(path))

    analyzer = select_analyzer(path, cfg)
    log.info("Geometri analizi: %s (backend: %s)", os.path.basename(path), analyzer.name)
    try:
        metrics = analyzer.analyze(path, cfg)
    except GeometryAnalyzerError:
        raise
    except Exception as exc:  # backend blew up - degrade instead of dying
        from .fallback import FallbackAnalyzer

        log.warning("%s analizörü başarısız oldu (%s); fallback'e düşülüyor.",
                    analyzer.name, exc)
        metrics = FallbackAnalyzer().analyze(path, cfg)
        metrics.warnings.append(
            "{0} analizörü hata verdi: {1}".format(analyzer.name, exc)
        )
    metrics.source_path = os.path.abspath(path)
    metrics.source_format = extension(path).lstrip(".")
    return finalise(metrics, cfg)
