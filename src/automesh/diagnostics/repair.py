"""Quality-driven repair ladders.

Failures are handled by :mod:`knowledge_base`; *this* module handles the
other half of the problem - the mesh was produced but it is not good enough.
The ladders below go from "polish what we have" to "change the recipe", and
the orchestrator walks one rung per repair pass.
"""

from __future__ import annotations

from typing import List

from ..models import QualityReport, QualityVerdict
from .actions import Action, plan_action, surface_action, volume_action


def surface_ladder(report: QualityReport, rung: int) -> List[Action]:
    """What to try for a poor *surface* mesh at escalation level ``rung``."""
    skew = report.max_skewness if report.max_skewness is not None else 0.85
    # Ask Fluent to fix everything a little worse than what we have; aiming
    # straight at the target in one go usually just fails.
    limit = max(0.60, min(0.95, skew - 0.05))

    ladder: List[List[Action]] = [
        [surface_action("improve_surface_mesh",
                        "Improve Surface Mesh (limit {0:.2f})".format(limit),
                        face_quality_limit=limit, iterations=5)],
        [surface_action("surface_ladder", "TUI yüzey onarım merdiveni",
                        quality_limit=limit, iterations=5)],
        [surface_action("improve_surface_mesh",
                        "Improve Surface Mesh (agresif, limit {0:.2f})".format(
                            max(0.55, limit - 0.08)),
                        face_quality_limit=max(0.55, limit - 0.08), iterations=10)],
        [plan_action("adjust_curvature_angle", "Eğrilik çözünürlüğünü artır", delta=-3.0),
         plan_action("scale_min_size", "Minimum boyutu incelt", factor=0.7)],
        [plan_action("scale_sizes", "Yüzey ağını genel olarak incelt", factor=0.8),
         plan_action("adjust_growth_rate", "Büyümeyi yavaşlat", delta=-0.03)],
    ]
    return ladder[min(max(rung, 0), len(ladder) - 1)]


def volume_ladder(report: QualityReport, rung: int) -> List[Action]:
    """What to try for a poor *volume* mesh at escalation level ``rung``."""
    ortho = report.min_orthogonal_quality
    limit = 0.05 if ortho is None else max(0.01, min(0.20, ortho + 0.05))

    ladder: List[List[Action]] = [
        [volume_action("improve_volume_mesh",
                       "Improve Volume Mesh (limit {0:.3f})".format(limit),
                       cell_quality_limit=limit, iterations=5)],
        [volume_action("auto_node_move", "Auto node move ile düğümleri kaydır",
                       quality_limit=max(limit, 0.1), iterations=10)],
        [volume_action("volume_ladder", "Hacim onarım merdiveni (repair + improve)",
                       quality_limit=limit, iterations=10)],
        [plan_action("adjust_growth_rate", "Büyüme oranını düşür", delta=-0.03),
         plan_action("adjust_curvature_angle", "Eğrilik açısını sıkılaştır", delta=-3.0)],
        [plan_action("downgrade_volume_fill", "Doldurma tipini yumuşat"),
         plan_action("reduce_layers", "Prizma katmanlarını azalt", count=1)],
        [plan_action("scale_sizes", "Ağı incelt", factor=0.8),
         plan_action("adjust_growth_rate", "Büyümeyi iyice yavaşlat", delta=-0.03)],
    ]
    return ladder[min(max(rung, 0), len(ladder) - 1)]


def ladder_length(stage: str) -> int:
    return 5 if stage == "surface" else 6


def repair_actions(report: QualityReport, rung: int) -> List[Action]:
    """Pick the right ladder for ``report`` and return rung ``rung``."""
    if report.verdict is QualityVerdict.UNUSABLE:
        # In-place repair cannot save an unusable mesh - jump to the plan
        # changing rungs directly.
        rung = max(rung, 3)
    if report.stage == "surface":
        return surface_ladder(report, rung)
    return volume_ladder(report, rung)


def is_plan_change(actions: List[Action]) -> bool:
    return any(action.requires_remesh for action in actions)
