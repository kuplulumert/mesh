"""Primitive edits applied to a :class:`MeshPlan` between attempts.

Every function mutates the plan in place and returns a short human readable
description of what it did - that string ends up in the run report, which is
how the user can see *why* attempt 3 differs from attempt 2.
"""

from __future__ import annotations

from typing import Optional

from ..models import BLOffsetMethod, MeshPlan, SizeFunction, VolumeFill, WorkflowType


def scale_sizes(plan: MeshPlan, factor: float) -> str:
    plan.min_size *= factor
    plan.max_size *= factor
    if plan.max_cell_length:
        plan.max_cell_length *= factor
    plan.clamp()
    verb = "kabalaştırıldı" if factor > 1 else "inceltildi"
    return "Genel hücre boyutu {0:.2f}x {1} (min={2:.4g} m, max={3:.4g} m)".format(
        factor, verb, plan.min_size, plan.max_size)


def scale_min_size(plan: MeshPlan, factor: float) -> str:
    plan.min_size *= factor
    plan.clamp()
    return "Minimum hücre boyutu {0:.2f}x ölçeklendi -> {1:.4g} m".format(
        factor, plan.min_size)


def scale_max_size(plan: MeshPlan, factor: float) -> str:
    plan.max_size *= factor
    plan.max_cell_length = plan.max_size
    plan.clamp()
    return "Maksimum hücre boyutu {0:.2f}x ölçeklendi -> {1:.4g} m".format(
        factor, plan.max_size)


def adjust_growth_rate(plan: MeshPlan, delta: float) -> str:
    plan.growth_rate += delta
    plan.clamp()
    return "Büyüme oranı {0:+.3f} -> {1:.3f}".format(delta, plan.growth_rate)


def adjust_curvature_angle(plan: MeshPlan, delta: float) -> str:
    plan.curvature_normal_angle += delta
    plan.clamp()
    return "Eğrilik normal açısı {0:+.1f} -> {1:.1f} derece".format(
        delta, plan.curvature_normal_angle)


def adjust_cells_per_gap(plan: MeshPlan, delta: float) -> str:
    plan.cells_per_gap += delta
    plan.clamp()
    return "Boşluk başına hücre {0:+.1f} -> {1:.1f}".format(delta, plan.cells_per_gap)


def set_size_function(plan: MeshPlan, function: SizeFunction) -> str:
    plan.size_function = function
    return "Boyut fonksiyonu '{0}' olarak ayarlandı".format(function.value)


def set_scope_proximity(plan: MeshPlan, scope: str) -> str:
    plan.scope_proximity_to = scope
    return "Yakınlık kapsamı '{0}' olarak ayarlandı".format(scope)


def reduce_layers(plan: MeshPlan, count: int = 2) -> str:
    bl = plan.boundary_layer
    before = bl.layer_count
    bl.layer_count = max(0, bl.layer_count - count)
    plan.clamp()
    if bl.layer_count == 0:
        return "Prizma katmanları kapatıldı ({0} -> 0)".format(before)
    return "Prizma katman sayısı {0} -> {1}".format(before, bl.layer_count)


def scale_first_height(plan: MeshPlan, factor: float) -> str:
    bl = plan.boundary_layer
    if bl.first_height > 0:
        bl.first_height *= factor
        return "İlk katman yüksekliği {0:.2f}x -> {1:.3e} m".format(factor, bl.first_height)
    bl.transition_ratio = min(max(bl.transition_ratio * factor, 0.05), 0.9)
    return "Geçiş oranı {0:.2f}x -> {1:.3f}".format(factor, bl.transition_ratio)


def set_bl_method(plan: MeshPlan, method: BLOffsetMethod) -> str:
    plan.boundary_layer.offset_method = method
    return "Sınır tabaka yöntemi '{0}'".format(method.value)


def disable_boundary_layers(plan: MeshPlan) -> str:
    plan.boundary_layer.enabled = False
    plan.boundary_layer.layer_count = 0
    return "Sınır tabakası tamamen devre dışı bırakıldı (son çare)"


def downgrade_volume_fill(plan: MeshPlan) -> str:
    before = plan.volume_fill
    plan.volume_fill = before.softer()
    if plan.volume_fill is before:
        return "Hacim doldurma tipi zaten en toleranslı seçenekte ({0})".format(before.value)
    return "Hacim doldurma tipi {0} -> {1}".format(before.value, plan.volume_fill.value)


def set_volume_fill(plan: MeshPlan, fill: VolumeFill) -> str:
    plan.volume_fill = fill
    return "Hacim doldurma tipi '{0}' olarak ayarlandı".format(fill.value)


def switch_to_fault_tolerant(plan: MeshPlan) -> str:
    if plan.workflow is WorkflowType.FAULT_TOLERANT:
        return "Zaten fault-tolerant akışındayız"
    plan.workflow = WorkflowType.FAULT_TOLERANT
    plan.capping_required = True
    return "Akış watertight -> fault-tolerant olarak değiştirildi"


def set_geometry_setup(plan: MeshPlan, setup: str) -> str:
    plan.geometry_setup = setup
    return "Geometri tanımı: '{0}'".format(setup)


def relax_hexcore(plan: MeshPlan) -> str:
    plan.peel_layers = max(plan.peel_layers, 1)
    plan.buffer_layers = min(plan.buffer_layers + 1, 4)
    return "Hexcore tampon katmanı {0} yapıldı".format(plan.buffer_layers)
