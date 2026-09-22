"""Remediation actions - the vocabulary the agent uses to fix things.

An action is declarative on purpose: it is a name plus parameters, so it can
be written into the run report, replayed later, and produced by the optional
LLM advisor without letting anything arbitrary execute.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---- action kinds ---------------------------------------------------------
PLAN = "plan"                    # edit the mesh plan, then mesh again
SURFACE_REPAIR = "surface_repair"  # fix the surface mesh in place
VOLUME_REPAIR = "volume_repair"    # fix the volume mesh in place
WORKFLOW = "workflow"            # change the meshing workflow
RETRY = "retry"                  # try again unchanged (transient failure)
ABORT = "abort"                  # nothing to be done, stop and explain

KINDS = (PLAN, SURFACE_REPAIR, VOLUME_REPAIR, WORKFLOW, RETRY, ABORT)

#: Plan edits the agent is allowed to make, mapped to ``planning.adjust``.
PLAN_OPERATIONS = {
    "scale_sizes": "scale_sizes",
    "scale_min_size": "scale_min_size",
    "scale_max_size": "scale_max_size",
    "adjust_growth_rate": "adjust_growth_rate",
    "adjust_curvature_angle": "adjust_curvature_angle",
    "adjust_cells_per_gap": "adjust_cells_per_gap",
    "set_size_function": "set_size_function",
    "set_scope_proximity": "set_scope_proximity",
    "reduce_layers": "reduce_layers",
    "scale_first_height": "scale_first_height",
    "set_bl_method": "set_bl_method",
    "disable_boundary_layers": "disable_boundary_layers",
    "downgrade_volume_fill": "downgrade_volume_fill",
    "set_volume_fill": "set_volume_fill",
    "switch_to_fault_tolerant": "switch_to_fault_tolerant",
    "set_geometry_setup": "set_geometry_setup",
    "relax_hexcore": "relax_hexcore",
}

#: In-place repair recipes.
SURFACE_OPERATIONS = (
    "improve_surface_mesh",   # Fluent's "Improve Surface Mesh" workflow task
    "surface_ladder",         # the TUI improve/smooth/swap/collapse ladder
    "delete_unused",
    "merge_nodes",
    "repair_face_handedness",
    "remesh_faces",
)
VOLUME_OPERATIONS = (
    "improve_volume_mesh",    # Fluent's "Improve Volume Mesh" workflow task
    "auto_node_move",
    "repair_improve",
    "volume_ladder",
)


@dataclass
class Action:
    kind: str
    operation: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError("bilinmeyen eylem tipi: {0!r}".format(self.kind))
        if self.kind == PLAN and self.operation not in PLAN_OPERATIONS:
            raise ValueError("bilinmeyen plan işlemi: {0!r}".format(self.operation))
        if self.kind == SURFACE_REPAIR and self.operation not in SURFACE_OPERATIONS:
            raise ValueError("bilinmeyen yüzey onarımı: {0!r}".format(self.operation))
        if self.kind == VOLUME_REPAIR and self.operation not in VOLUME_OPERATIONS:
            raise ValueError("bilinmeyen hacim onarımı: {0!r}".format(self.operation))

    @property
    def requires_remesh(self) -> bool:
        """Plan and workflow changes invalidate the current mesh."""
        return self.kind in (PLAN, WORKFLOW)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Action":
        return cls(
            kind=data.get("kind", RETRY),
            operation=data.get("operation", ""),
            params=dict(data.get("params") or {}),
            description=data.get("description", ""),
        )

    def label(self) -> str:
        if self.description:
            return self.description
        if self.params:
            args = ", ".join("{0}={1}".format(k, v) for k, v in sorted(self.params.items()))
            return "{0}:{1}({2})".format(self.kind, self.operation, args)
        return "{0}:{1}".format(self.kind, self.operation)


@dataclass
class Step:
    """One escalation level: everything here is applied together."""

    actions: List[Action] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"actions": [a.to_dict() for a in self.actions], "note": self.note}


@dataclass
class Diagnosis:
    """A rule that matched, plus the remedy chosen for this occurrence."""

    rule_id: str
    title: str
    stage: str
    severity: str
    explanation: str
    evidence: str = ""
    occurrence: int = 1
    actions: List[Action] = field(default_factory=list)
    source: str = "knowledge-base"     # or "advisor"
    exhausted: bool = False

    @property
    def is_fatal(self) -> bool:
        return self.severity == "fatal" or any(a.kind == ABORT for a in self.actions)

    @property
    def requires_remesh(self) -> bool:
        return any(a.requires_remesh for a in self.actions)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "stage": self.stage,
            "severity": self.severity,
            "explanation": self.explanation,
            "evidence": self.evidence[:500],
            "occurrence": self.occurrence,
            "actions": [a.to_dict() for a in self.actions],
            "source": self.source,
            "exhausted": self.exhausted,
        }


def plan_action(operation: str, description: str, **params: Any) -> Action:
    return Action(kind=PLAN, operation=operation, params=params, description=description)


def surface_action(operation: str, description: str, **params: Any) -> Action:
    return Action(kind=SURFACE_REPAIR, operation=operation, params=params,
                  description=description)


def volume_action(operation: str, description: str, **params: Any) -> Action:
    return Action(kind=VOLUME_REPAIR, operation=operation, params=params,
                  description=description)


def abort_action(description: str) -> Action:
    return Action(kind=ABORT, operation="", params={}, description=description)


def retry_action(description: str) -> Action:
    return Action(kind=RETRY, operation="", params={}, description=description)
