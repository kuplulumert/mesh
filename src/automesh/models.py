"""Data structures shared by every stage of the pipeline.

All of them are plain dataclasses with ``to_dict``/``from_dict`` so a run can
be written to disk, inspected afterwards and replayed.

Unit convention: **every length is in metres**, every angle in degrees.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _clean(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _clean(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


class _DictMixin:
    """Round-trips a flat-ish dataclass through JSON friendly dicts."""

    def to_dict(self) -> Dict[str, Any]:
        return {k: _clean(v) for k, v in dataclasses.asdict(self).items()}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]):
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in fields})


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------

@dataclass
class BoundingBox(_DictMixin):
    xmin: float = 0.0
    ymin: float = 0.0
    zmin: float = 0.0
    xmax: float = 0.0
    ymax: float = 0.0
    zmax: float = 0.0

    @property
    def sizes(self) -> Tuple[float, float, float]:
        return (self.xmax - self.xmin, self.ymax - self.ymin, self.zmax - self.zmin)

    @property
    def diagonal(self) -> float:
        dx, dy, dz = self.sizes
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    @property
    def max_extent(self) -> float:
        return max(self.sizes) if self.sizes else 0.0

    @property
    def min_extent(self) -> float:
        positive = [s for s in self.sizes if s > 0]
        return min(positive) if positive else 0.0

    @property
    def aspect_ratio(self) -> float:
        lo = self.min_extent
        return (self.max_extent / lo) if lo > 0 else 1.0


@dataclass
class BodyInfo(_DictMixin):
    """Per-body summary, mostly used for reporting and local sizing hints."""

    name: str = ""
    volume: float = 0.0            # m^3
    area: float = 0.0              # m^2
    face_count: int = 0
    edge_count: int = 0
    is_solid: bool = True
    min_face_area: float = 0.0     # m^2
    min_edge_length: float = 0.0   # m


@dataclass
class GeometryMetrics(_DictMixin):
    """Everything the planner is allowed to look at."""

    source_path: str = ""
    source_format: str = ""
    analyzer: str = "unknown"

    bbox: BoundingBox = field(default_factory=BoundingBox)
    volume: float = 0.0             # m^3, total solid volume
    area: float = 0.0               # m^2, total wetted/surface area
    body_count: int = 0
    face_count: int = 0
    edge_count: int = 0

    # Feature scale detection -------------------------------------------------
    min_feature_size: float = 0.0   # m, smallest resolvable detail
    min_edge_length: float = 0.0    # m
    min_face_size: float = 0.0      # m, sqrt of smallest face area
    min_curvature_radius: float = 0.0  # m, tightest fillet / hole radius
    thinnest_section: float = 0.0   # m, smallest wall / gap estimate

    curved_face_ratio: float = 0.0  # 0..1, share of non-planar faces
    small_feature_ratio: float = 0.0  # 0..1, share of faces far below the mean

    watertight: Optional[bool] = None
    has_free_edges: Optional[bool] = None
    free_edge_count: int = 0

    is_internal_flow: Optional[bool] = None  # fluid domain vs. external body
    length_unit_hint: str = "m"
    warnings: List[str] = field(default_factory=list)
    bodies: List[BodyInfo] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @property
    def diagonal(self) -> float:
        return self.bbox.diagonal

    @property
    def feature_span(self) -> float:
        """Ratio between the model size and its smallest feature.

        A span of 1000 means the smallest detail is a thousandth of the
        model - the classic reason a naive uniform mesh explodes.
        """
        if self.min_feature_size <= 0:
            return 1.0
        return self.diagonal / self.min_feature_size

    def complexity(self) -> float:
        """A 0..1 score driving how aggressive the sizing needs to be."""
        span = self.feature_span
        span_score = min(1.0, math.log10(max(span, 1.0)) / 3.5)      # 1 -> 3000x
        face_score = min(1.0, math.log10(max(self.face_count, 1)) / 3.5)  # 1 -> 3000 faces
        curve_score = min(1.0, self.curved_face_ratio * 1.25)
        small_score = min(1.0, self.small_feature_ratio * 2.0)
        score = 0.40 * span_score + 0.25 * face_score + 0.20 * curve_score + 0.15 * small_score
        return max(0.0, min(1.0, score))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GeometryMetrics":
        data = dict(data or {})
        bbox = data.pop("bbox", None)
        bodies = data.pop("bodies", None) or []
        fields = {f.name for f in dataclasses.fields(cls)}
        obj = cls(**{k: v for k, v in data.items() if k in fields})
        if bbox:
            obj.bbox = BoundingBox.from_dict(bbox)
        obj.bodies = [BodyInfo.from_dict(b) for b in bodies]
        return obj


# --------------------------------------------------------------------------
# mesh plan
# --------------------------------------------------------------------------

class WorkflowType(str, Enum):
    WATERTIGHT = "watertight"
    FAULT_TOLERANT = "fault-tolerant"

    @property
    def fluent_name(self) -> str:
        return {
            WorkflowType.WATERTIGHT: "Watertight Geometry",
            WorkflowType.FAULT_TOLERANT: "Fault-tolerant Meshing",
        }[self]


class VolumeFill(str, Enum):
    POLY_HEXCORE = "poly-hexcore"
    POLYHEDRA = "polyhedra"
    HEXCORE = "hexcore"
    TETRAHEDRAL = "tetrahedral"

    def softer(self) -> "VolumeFill":
        """The next fill type to try when this one keeps failing."""
        order = [
            VolumeFill.POLY_HEXCORE,
            VolumeFill.POLYHEDRA,
            VolumeFill.TETRAHEDRAL,
        ]
        if self is VolumeFill.HEXCORE:
            return VolumeFill.POLY_HEXCORE
        idx = order.index(self) if self in order else 0
        return order[min(idx + 1, len(order) - 1)]


class SizeFunction(str, Enum):
    CURVATURE = "Curvature"
    PROXIMITY = "Proximity"
    CURVATURE_AND_PROXIMITY = "Curvature & Proximity"
    UNIFORM = "Uniform"


class BLOffsetMethod(str, Enum):
    SMOOTH_TRANSITION = "smooth-transition"
    LAST_RATIO = "last-ratio"
    ASPECT_RATIO = "aspect-ratio"
    UNIFORM = "uniform"


@dataclass
class BoundaryLayerPlan(_DictMixin):
    enabled: bool = True
    offset_method: BLOffsetMethod = BLOffsetMethod.SMOOTH_TRANSITION
    layer_count: int = 5
    growth_rate: float = 1.2
    transition_ratio: float = 0.272
    first_height: float = 0.0     # m, only for last-ratio/uniform styles
    aspect_ratio: float = 10.0
    y_plus_target: Optional[float] = None
    control_name: str = "auto-bl-1"

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["offset_method"] = self.offset_method.value if isinstance(
            self.offset_method, BLOffsetMethod) else self.offset_method
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BoundaryLayerPlan":
        data = dict(data or {})
        if "offset_method" in data and data["offset_method"] is not None:
            data["offset_method"] = BLOffsetMethod(data["offset_method"])
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class LocalSizing(_DictMixin):
    """A scoped size control ("this small pipe needs 0.4 mm cells")."""

    name: str
    target: str                   # zone / label / object name
    size: float                   # m
    size_control_type: str = "Body Of Influence"   # or Face Size / Curvature / Proximity
    growth_rate: float = 1.2


@dataclass
class MeshPlan(_DictMixin):
    """The full recipe handed to Fluent for one meshing attempt."""

    workflow: WorkflowType = WorkflowType.WATERTIGHT
    length_unit: str = "m"

    # Global surface sizing (metres) ------------------------------------
    min_size: float = 1.0e-3
    max_size: float = 1.0e-2
    growth_rate: float = 1.2
    size_function: SizeFunction = SizeFunction.CURVATURE_AND_PROXIMITY
    curvature_normal_angle: float = 18.0
    cells_per_gap: float = 2.0
    scope_proximity_to: str = "faces"     # faces | edges | faces-and-edges

    # Volume -------------------------------------------------------------
    volume_fill: VolumeFill = VolumeFill.POLY_HEXCORE
    max_cell_length: float = 0.0          # m, hexcore max length (0 -> auto)
    peel_layers: int = 1
    buffer_layers: int = 2

    boundary_layer: BoundaryLayerPlan = field(default_factory=BoundaryLayerPlan)
    local_sizings: List[LocalSizing] = field(default_factory=list)

    # Geometry description ------------------------------------------------
    geometry_setup: str = "The geometry consists of only fluid regions with no voids"
    capping_required: bool = False
    share_topology: bool = False

    # Housekeeping --------------------------------------------------------
    estimated_cell_count: int = 0
    notes: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    def copy(self) -> "MeshPlan":
        return MeshPlan.from_dict(self.to_dict())

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def clamp(self) -> "MeshPlan":
        """Keep the numbers physically sane after a remediation tweak."""
        self.min_size = max(self.min_size, 1.0e-9)
        self.max_size = max(self.max_size, self.min_size * 1.5)
        # Fluent dislikes a min/max spread beyond ~1:1000 on one control.
        if self.max_size / self.min_size > 1000.0:
            self.min_size = self.max_size / 1000.0
        self.growth_rate = min(max(self.growth_rate, 1.05), 1.5)
        self.curvature_normal_angle = min(max(self.curvature_normal_angle, 5.0), 40.0)
        self.cells_per_gap = min(max(self.cells_per_gap, 1.0), 6.0)
        bl = self.boundary_layer
        bl.layer_count = int(min(max(bl.layer_count, 0), 40))
        bl.growth_rate = min(max(bl.growth_rate, 1.02), 1.6)
        bl.transition_ratio = min(max(bl.transition_ratio, 0.05), 0.9)
        if bl.layer_count == 0:
            bl.enabled = False
        return self

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["workflow"] = self.workflow.value if isinstance(self.workflow, WorkflowType) else self.workflow
        d["volume_fill"] = self.volume_fill.value if isinstance(self.volume_fill, VolumeFill) else self.volume_fill
        d["size_function"] = self.size_function.value if isinstance(self.size_function, SizeFunction) else self.size_function
        d["boundary_layer"] = self.boundary_layer.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MeshPlan":
        data = dict(data or {})
        bl = data.pop("boundary_layer", None)
        locals_ = data.pop("local_sizings", None) or []
        for key, enum_cls in (
            ("workflow", WorkflowType),
            ("volume_fill", VolumeFill),
            ("size_function", SizeFunction),
        ):
            if data.get(key) is not None:
                data[key] = enum_cls(data[key])
        fields = {f.name for f in dataclasses.fields(cls)}
        obj = cls(**{k: v for k, v in data.items() if k in fields})
        if bl:
            obj.boundary_layer = BoundaryLayerPlan.from_dict(bl)
        obj.local_sizings = [LocalSizing.from_dict(l) for l in locals_]
        return obj


# --------------------------------------------------------------------------
# quality
# --------------------------------------------------------------------------

class QualityVerdict(str, Enum):
    GOOD = "good"           # ship it
    ACCEPTABLE = "acceptable"  # usable, one cheap improvement pass is worth it
    POOR = "poor"           # repairable in place
    UNUSABLE = "unusable"   # remesh with different parameters

    @property
    def is_success(self) -> bool:
        return self in (QualityVerdict.GOOD, QualityVerdict.ACCEPTABLE)


@dataclass
class QualityReport(_DictMixin):
    stage: str = "volume"                    # surface | volume
    max_skewness: Optional[float] = None
    min_orthogonal_quality: Optional[float] = None
    max_aspect_ratio: Optional[float] = None
    min_volume: Optional[float] = None
    negative_volume_count: int = 0
    left_handed_face_count: int = 0
    cell_count: int = 0
    face_count: int = 0
    node_count: int = 0
    verdict: QualityVerdict = QualityVerdict.POOR
    failed_metrics: List[str] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["verdict"] = self.verdict.value if isinstance(self.verdict, QualityVerdict) else self.verdict
        # The transcript excerpt is kept but trimmed - reports stay readable.
        if self.raw_text and len(self.raw_text) > 4000:
            d["raw_text"] = self.raw_text[:4000] + "\n... [truncated]"
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QualityReport":
        data = dict(data or {})
        if data.get("verdict") is not None:
            data["verdict"] = QualityVerdict(data["verdict"])
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})

    def summary(self) -> str:
        bits = []
        if self.max_skewness is not None:
            bits.append("skew_max={0:.4f}".format(self.max_skewness))
        if self.min_orthogonal_quality is not None:
            bits.append("ortho_min={0:.4f}".format(self.min_orthogonal_quality))
        if self.max_aspect_ratio is not None:
            bits.append("AR_max={0:.1f}".format(self.max_aspect_ratio))
        if self.cell_count:
            bits.append("cells={0}".format(self.cell_count))
        return ", ".join(bits) or "no metrics"


# --------------------------------------------------------------------------
# run bookkeeping
# --------------------------------------------------------------------------

class AttemptStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    REPAIRED = "repaired"
    ABORTED = "aborted"


@dataclass
class StepRecord(_DictMixin):
    name: str
    ok: bool
    duration_s: float = 0.0
    message: str = ""


@dataclass
class AttemptRecord(_DictMixin):
    index: int = 0
    status: AttemptStatus = AttemptStatus.FAILED
    plan: Dict[str, Any] = field(default_factory=dict)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    surface_quality: Optional[Dict[str, Any]] = None
    volume_quality: Optional[Dict[str, Any]] = None
    diagnoses: List[Dict[str, Any]] = field(default_factory=list)
    actions_applied: List[str] = field(default_factory=list)
    error: str = ""
    duration_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["status"] = self.status.value if isinstance(self.status, AttemptStatus) else self.status
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AttemptRecord":
        data = dict(data or {})
        if data.get("status") is not None:
            data["status"] = AttemptStatus(data["status"])
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class RunResult(_DictMixin):
    success: bool = False
    run_dir: str = ""
    geometry: Dict[str, Any] = field(default_factory=dict)
    final_plan: Dict[str, Any] = field(default_factory=dict)
    final_quality: Optional[Dict[str, Any]] = None
    mesh_file: str = ""
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""
    duration_s: float = 0.0
