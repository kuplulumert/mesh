"""Turn geometry metrics into a concrete mesh recipe.

The heuristics here are the ones a CFD engineer applies by hand:

* resolve the smallest real feature with a few cells,
* keep the global cell size tied to the model size, not to an absolute number,
* tighten curvature/proximity refinement as the part gets more detailed,
* size the first prism layer from y+ when the flow is known,
* and finally check the resulting cell count against the machine's budget
  before Fluent ever gets to see it.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

from ..config import Config
from ..logging_utils import get_logger
from ..models import (
    BLOffsetMethod,
    BoundaryLayerPlan,
    GeometryMetrics,
    MeshPlan,
    SizeFunction,
    VolumeFill,
    WorkflowType,
)
from ..units import pick_working_unit

#: Cells produced per ``h**3`` of volume, by fill type.
_CELLS_PER_VOLUME = {
    VolumeFill.TETRAHEDRAL: 5.5,
    VolumeFill.POLYHEDRA: 0.9,
    VolumeFill.POLY_HEXCORE: 0.75,
    VolumeFill.HEXCORE: 0.7,
}


def _lerp(lo: float, hi: float, t: float) -> float:
    return lo + (hi - lo) * max(0.0, min(1.0, t))


# --------------------------------------------------------------------------

def plan_mesh(metrics: GeometryMetrics, cfg: Config) -> MeshPlan:
    """Build the initial :class:`MeshPlan` for ``metrics``."""
    log = get_logger()
    plan = MeshPlan()
    complexity = metrics.complexity()
    diag = metrics.diagonal

    plan.length_unit = (
        cfg.geometry.length_unit
        or (metrics.length_unit_hint if metrics.length_unit_hint else None)
        or pick_working_unit(diag)
    )
    plan.workflow = choose_workflow(metrics, cfg)
    plan.volume_fill = cfg.resolved_volume_fill()

    # ---- global surface sizing ----------------------------------------
    if diag <= 0:
        # Nothing is known yet; Fluent will be asked for the bounding box
        # after import and the plan re-derived.  Use placeholders that are
        # obviously provisional rather than silently wrong.
        plan.max_size = 1.0e-2
        plan.min_size = 1.0e-3
        plan.note("Sınır kutusu bilinmiyor: boyutlar içe aktarma sonrası yeniden hesaplanacak.")
    else:
        divisor = _lerp(
            cfg.planning.max_size_divisor,
            cfg.planning.max_size_divisor_complex,
            complexity,
        )
        plan.max_size = diag / divisor
        plan.min_size = _min_size_for(metrics, cfg, plan.max_size)

    gr_lo, gr_hi = cfg.planning.growth_rate_range
    plan.growth_rate = _lerp(gr_hi, gr_lo, complexity)

    ang_lo, ang_hi = cfg.planning.curvature_angle_range
    plan.curvature_normal_angle = _lerp(ang_hi, ang_lo, complexity)

    gap_lo, gap_hi = cfg.planning.cells_per_gap_range
    plan.cells_per_gap = _lerp(gap_lo, gap_hi, complexity)

    plan.size_function = _size_function(metrics)
    plan.scope_proximity_to = "faces-and-edges" if metrics.is_internal_flow else "faces"

    # ---- volume --------------------------------------------------------
    plan.max_cell_length = plan.max_size
    plan.peel_layers = 1
    plan.buffer_layers = 2

    # ---- geometry description -----------------------------------------
    plan.geometry_setup = _geometry_setup(metrics)
    plan.capping_required = plan.workflow is WorkflowType.FAULT_TOLERANT
    plan.share_topology = metrics.body_count > 1

    # ---- boundary layers ----------------------------------------------
    plan.boundary_layer = plan_boundary_layer(metrics, cfg, plan, complexity)

    # ---- budget --------------------------------------------------------
    plan.estimated_cell_count = estimate_cell_count(plan, metrics)
    _apply_cell_budget(plan, metrics, cfg)

    plan.note(
        "Karmaşıklık skoru {0:.2f} (özellik aralığı 1:{1:.0f}, eğrisel yüzey oranı {2:.0%})."
        .format(complexity, metrics.feature_span, metrics.curved_face_ratio)
    )
    plan.clamp()
    log.info(
        "Plan: %s akışı, min=%.4g m, max=%.4g m, büyüme=%.3f, ~%s hücre",
        plan.workflow.value, plan.min_size, plan.max_size, plan.growth_rate,
        "{:,}".format(plan.estimated_cell_count),
    )
    return plan


# --------------------------------------------------------------------------

def _min_size_for(metrics: GeometryMetrics, cfg: Config, max_size: float) -> float:
    """Smallest cell: enough to resolve the smallest real feature."""
    feature = metrics.min_feature_size
    if feature <= 0:
        return max_size / 20.0
    min_size = feature / max(cfg.planning.min_cells_across_feature, 1.0)

    # Guard against sliver-driven explosions: never finer than max/ratio_cap.
    floor = max_size / max(cfg.planning.size_min_max_ratio_cap, 2.0)
    if min_size < floor:
        min_size = floor
    # ... and never coarser than half the global size, otherwise the control
    # does nothing at all.
    return min(min_size, max_size / 2.0)


def _size_function(metrics: GeometryMetrics) -> SizeFunction:
    curved = metrics.curved_face_ratio > 0.05
    narrow = metrics.is_internal_flow or (
        metrics.thinnest_section > 0
        and metrics.diagonal > 0
        and metrics.thinnest_section < metrics.diagonal * 0.05
    )
    if curved and narrow:
        return SizeFunction.CURVATURE_AND_PROXIMITY
    if curved:
        return SizeFunction.CURVATURE
    if narrow:
        return SizeFunction.PROXIMITY
    return SizeFunction.CURVATURE_AND_PROXIMITY


def choose_workflow(metrics: GeometryMetrics, cfg: Config) -> WorkflowType:
    """Watertight when the geometry is clean, fault-tolerant when it is not."""
    forced = cfg.resolved_workflow()
    if forced is not None:
        return forced
    if metrics.watertight is False or metrics.has_free_edges:
        return WorkflowType.FAULT_TOLERANT
    if metrics.free_edge_count > 0:
        return WorkflowType.FAULT_TOLERANT
    return WorkflowType.WATERTIGHT


def _geometry_setup(metrics: GeometryMetrics) -> str:
    if metrics.body_count > 1:
        return "The geometry consists of both fluid and solid regions and/or voids"
    if metrics.is_internal_flow:
        return "The geometry consists of only fluid regions with no voids"
    return "The geometry consists of only fluid regions with no voids"


# --------------------------------------------------------------------------
# boundary layer
# --------------------------------------------------------------------------

def plan_boundary_layer(
    metrics: GeometryMetrics,
    cfg: Config,
    plan: MeshPlan,
    complexity: float,
) -> BoundaryLayerPlan:
    bl = BoundaryLayerPlan()
    if not cfg.planning.boundary_layers:
        bl.enabled = False
        bl.layer_count = 0
        return bl

    lo, hi = cfg.planning.layer_count_range
    bl.layer_count = int(round(_lerp(lo, hi, complexity)))
    bl.growth_rate = cfg.planning.bl_growth_rate
    bl.control_name = "automesh-bl-1"

    first_height, note = first_layer_height(metrics, cfg)
    if first_height and first_height > 0:
        bl.offset_method = BLOffsetMethod.LAST_RATIO
        bl.first_height = first_height
        bl.y_plus_target = cfg.geometry.y_plus_target
        # Enough layers to bridge from the wall to the core cell size.
        needed = _layers_to_bridge(first_height, plan.max_size, bl.growth_rate)
        bl.layer_count = max(bl.layer_count, min(needed, 30))
        if note:
            plan.note(note)
        plan.note(
            "İlk katman yüksekliği y+={0:g} hedefiyle {1:.3e} m olarak hesaplandı."
            .format(cfg.geometry.y_plus_target or 0.0, first_height)
        )
    else:
        bl.offset_method = BLOffsetMethod.SMOOTH_TRANSITION
        bl.transition_ratio = 0.272
        if note:
            plan.note(note)

    # Prisms thicker than the local feature collapse - keep total growth sane.
    if metrics.thinnest_section > 0:
        total = _stack_height(bl, plan.max_size)
        limit = metrics.thinnest_section * 0.4
        while bl.layer_count > 2 and total > limit:
            bl.layer_count -= 1
            total = _stack_height(bl, plan.max_size)
        if bl.layer_count < int(round(_lerp(lo, hi, complexity))):
            plan.note(
                "İnce kesit ({0:.3e} m) nedeniyle prizma katman sayısı {1}'e düşürüldü."
                .format(metrics.thinnest_section, bl.layer_count)
            )
    return bl


def first_layer_height(metrics: GeometryMetrics, cfg: Config) -> Tuple[float, Optional[str]]:
    """First cell height for the requested y+ (flat-plate correlation).

    ``Cf = 0.058 Re^-0.2`` is the standard turbulent flat-plate skin friction;
    it is the correlation every y+ calculator uses and it is accurate enough
    to land within a factor of two of the target, which is all that matters
    for a first mesh.
    """
    y_plus = cfg.geometry.y_plus_target
    if not y_plus or y_plus <= 0:
        return 0.0, None
    velocity = cfg.geometry.velocity
    if not velocity or velocity <= 0:
        return 0.0, ("y+ hedefi verildi ama geometry.velocity yok; "
                     "sınır tabakası smooth-transition ile kuruldu.")
    rho = cfg.geometry.density
    mu = cfg.geometry.viscosity
    length = cfg.geometry.characteristic_length or metrics.diagonal
    if length <= 0 or rho <= 0 or mu <= 0:
        return 0.0, "Akış parametreleri eksik; y+ tabanlı katman kurulamadı."

    reynolds = rho * velocity * length / mu
    if reynolds < 1.0:
        return 0.0, "Reynolds sayısı çok küçük; y+ tabanlı katman atlandı."
    cf = 0.058 * reynolds ** -0.2
    tau_w = 0.5 * rho * velocity * velocity * cf
    u_tau = math.sqrt(tau_w / rho)
    if u_tau <= 0:
        return 0.0, "Sürtünme hızı hesaplanamadı."
    centroid = y_plus * mu / (rho * u_tau)
    note = "Re = {0:.3g}, Cf = {1:.4g}, u_tau = {2:.4g} m/s".format(reynolds, cf, u_tau)
    return 2.0 * centroid, note


def _stack_height(bl: BoundaryLayerPlan, max_size: float) -> float:
    """Total thickness of the prism stack."""
    first = bl.first_height if bl.first_height > 0 else max_size * bl.transition_ratio * 0.5
    rate = max(bl.growth_rate, 1.0001)
    n = max(bl.layer_count, 0)
    if n == 0:
        return 0.0
    return first * (rate ** n - 1.0) / (rate - 1.0)


def _layers_to_bridge(first_height: float, target: float, rate: float) -> int:
    """How many layers it takes to grow from ``first_height`` to ``target``."""
    if first_height <= 0 or target <= first_height:
        return 1
    rate = max(rate, 1.0001)
    return int(math.ceil(math.log(target / first_height) / math.log(rate)))


# --------------------------------------------------------------------------
# cell budget
# --------------------------------------------------------------------------

def effective_volume(metrics: GeometryMetrics) -> float:
    """Best available estimate of the fluid volume."""
    if metrics.volume > 0:
        return metrics.volume
    bbox_volume = 1.0
    sizes = metrics.bbox.sizes
    if not any(sizes):
        return 0.0
    for s in sizes:
        bbox_volume *= max(s, 1e-12)
    # Without a CAD kernel assume the part fills about half its bounding box.
    return bbox_volume * 0.5


def effective_area(metrics: GeometryMetrics) -> float:
    if metrics.area > 0:
        return metrics.area
    dx, dy, dz = metrics.bbox.sizes
    return 2.0 * (dx * dy + dy * dz + dx * dz)


def estimate_cell_count(plan: MeshPlan, metrics: GeometryMetrics) -> int:
    """Rough cell count for ``plan`` - good to roughly a factor of two."""
    volume = effective_volume(metrics)
    if volume <= 0 or plan.max_size <= 0:
        return 0
    density = _CELLS_PER_VOLUME.get(plan.volume_fill, 1.0)
    core = density * volume / (plan.max_size ** 3)

    # Curvature/proximity refinement inflates the count near walls.
    refinement = 1.0 + 2.0 * metrics.complexity()
    core *= refinement

    bl_cells = 0.0
    if plan.boundary_layer.enabled and plan.boundary_layer.layer_count > 0:
        area = effective_area(metrics)
        surface_cells = area / (plan.max_size ** 2) if plan.max_size > 0 else 0.0
        bl_cells = surface_cells * plan.boundary_layer.layer_count
    return int(max(core + bl_cells, 0))


def _apply_cell_budget(plan: MeshPlan, metrics: GeometryMetrics, cfg: Config) -> None:
    """Coarsen until the estimate fits in the configured budget."""
    target = cfg.planning.target_cell_count or 0
    limit = cfg.planning.max_cell_count or 0
    estimate = plan.estimated_cell_count
    if estimate <= 0:
        return

    goal = target if target > 0 else limit
    if goal <= 0 or estimate <= goal:
        if target > 0 and estimate < target * 0.4:
            # Plenty of headroom - refine towards the requested size.
            scale = (estimate / float(target)) ** (1.0 / 3.0)
            scale = max(scale, 0.5)
            plan.max_size *= scale
            plan.min_size *= scale
            plan.max_cell_length = plan.max_size
            plan.estimated_cell_count = estimate_cell_count(plan, metrics)
            plan.note(
                "Hedef hücre sayısına yaklaşmak için boyutlar {0:.2f}x inceltildi."
                .format(scale)
            )
        return

    # Prism cells scale with 1/h^2 while the core scales with 1/h^3, so one
    # cube-root correction undershoots on boundary-layer heavy meshes.
    # Iterate instead of pretending a single pass is enough.
    original = estimate
    total_scale = 1.0
    current = estimate
    for _ in range(6):
        if current <= goal:
            break
        scale = min((current / float(goal)) ** (1.0 / 3.0), 3.0)
        total_scale *= scale
        plan.max_size *= scale
        plan.min_size *= scale
        plan.max_cell_length = plan.max_size
        current = estimate_cell_count(plan, metrics)
    plan.estimated_cell_count = current
    plan.note(
        "Tahmini {0:,} hücre bütçeyi ({1:,}) aştığı için boyutlar {2:.2f}x kabalaştırıldı "
        "(yeni tahmin {3:,})."
        .format(original, goal, total_scale, plan.estimated_cell_count)
    )
