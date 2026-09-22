import pytest

from automesh.config import Config
from automesh.models import (
    BLOffsetMethod,
    BoundingBox,
    GeometryMetrics,
    MeshPlan,
    VolumeFill,
    WorkflowType,
)
from automesh.planning import adjust
from automesh.planning.sizing import (
    choose_workflow,
    estimate_cell_count,
    first_layer_height,
    plan_mesh,
)


def _metrics(**kwargs):
    metrics = GeometryMetrics(**kwargs)
    if "bbox" not in kwargs:
        metrics.bbox = BoundingBox(0, 0, 0, 1.0, 0.2, 0.2)
    return metrics


def test_sizes_scale_with_the_model_not_absolutes():
    cfg = Config()
    small = _metrics(min_feature_size=0.001, volume=0.01, area=1.0)
    big = _metrics(min_feature_size=0.1, volume=10.0, area=100.0)
    big.bbox = BoundingBox(0, 0, 0, 100.0, 20.0, 20.0)
    small_plan = plan_mesh(small, cfg)
    big_plan = plan_mesh(big, cfg)
    assert big_plan.max_size > small_plan.max_size * 50


def test_min_size_resolves_the_smallest_feature():
    cfg = Config()
    cfg.planning.min_cells_across_feature = 3.0
    metrics = _metrics(min_feature_size=0.003, volume=0.02, area=1.0, face_count=800)
    plan = plan_mesh(metrics, cfg)
    # Either three cells across the feature, or the ratio cap kicked in.
    assert plan.min_size <= 0.003 / 3.0 + 1e-9 or plan.min_size == pytest.approx(
        plan.max_size / cfg.planning.size_min_max_ratio_cap)
    assert plan.min_size < plan.max_size


def test_detailed_geometry_gets_finer_controls():
    cfg = Config()
    plain = _metrics(min_feature_size=0.05, face_count=30, volume=0.02, area=1.0)
    busy = _metrics(min_feature_size=0.0005, face_count=4000, curved_face_ratio=0.9,
                    small_feature_ratio=0.4, volume=0.02, area=1.0)
    plain_plan = plan_mesh(plain, cfg)
    busy_plan = plan_mesh(busy, cfg)
    assert busy_plan.max_size < plain_plan.max_size
    assert busy_plan.growth_rate <= plain_plan.growth_rate
    assert busy_plan.curvature_normal_angle <= plain_plan.curvature_normal_angle


def test_cell_budget_coarsens_the_plan():
    cfg = Config()
    cfg.planning.max_cell_count = 100_000
    metrics = _metrics(min_feature_size=0.0002, volume=1.0, area=10.0, face_count=2000)
    plan = plan_mesh(metrics, cfg)
    assert plan.estimated_cell_count <= 100_000 * 1.35
    assert any("bütçe" in note for note in plan.notes)


def test_y_plus_drives_the_first_layer():
    cfg = Config()
    cfg.geometry.y_plus_target = 1.0
    cfg.geometry.velocity = 10.0
    cfg.geometry.characteristic_length = 1.0
    metrics = _metrics(min_feature_size=0.01, volume=0.04, area=2.0)
    height, note = first_layer_height(metrics, cfg)
    assert 1e-6 < height < 1e-3
    assert "Re" in note
    plan = plan_mesh(metrics, cfg)
    assert plan.boundary_layer.offset_method is BLOffsetMethod.LAST_RATIO
    assert plan.boundary_layer.first_height == pytest.approx(height)
    # A y+ of 30 needs a much thicker first cell than y+ of 1.
    cfg.geometry.y_plus_target = 30.0
    coarse, _ = first_layer_height(metrics, cfg)
    assert coarse > height * 20


def test_y_plus_without_velocity_falls_back_gracefully():
    cfg = Config()
    cfg.geometry.y_plus_target = 1.0
    height, note = first_layer_height(_metrics(), cfg)
    assert height == 0.0
    assert "velocity" in note


def test_thin_sections_limit_the_prism_stack():
    cfg = Config()
    thick = _metrics(min_feature_size=0.01, thinnest_section=1.0, volume=0.04, area=2.0)
    thin = _metrics(min_feature_size=0.01, thinnest_section=0.0008, volume=0.04, area=2.0)
    assert (plan_mesh(thin, cfg).boundary_layer.layer_count
            <= plan_mesh(thick, cfg).boundary_layer.layer_count)


def test_workflow_selection():
    cfg = Config()
    clean = _metrics(watertight=True)
    dirty = _metrics(watertight=False, has_free_edges=True)
    assert choose_workflow(clean, cfg) is WorkflowType.WATERTIGHT
    assert choose_workflow(dirty, cfg) is WorkflowType.FAULT_TOLERANT
    cfg.planning.workflow = "watertight"
    assert choose_workflow(dirty, cfg) is WorkflowType.WATERTIGHT


def test_cell_estimate_reacts_to_fill_type():
    metrics = _metrics(volume=1.0, area=6.0)
    tet = MeshPlan(max_size=0.05, volume_fill=VolumeFill.TETRAHEDRAL)
    poly = MeshPlan(max_size=0.05, volume_fill=VolumeFill.POLYHEDRA)
    assert estimate_cell_count(tet, metrics) > estimate_cell_count(poly, metrics)


def test_adjust_operations_are_reversible_in_spirit():
    plan = MeshPlan(min_size=1e-3, max_size=1e-2, growth_rate=1.2)
    adjust.scale_sizes(plan, 2.0)
    assert plan.max_size == pytest.approx(2e-2)
    adjust.adjust_growth_rate(plan, -0.05)
    assert plan.growth_rate == pytest.approx(1.15)
    adjust.reduce_layers(plan, 10)
    assert plan.boundary_layer.layer_count == 0
    assert plan.boundary_layer.enabled is False
    assert "fault-tolerant" in adjust.switch_to_fault_tolerant(plan)
    assert plan.workflow is WorkflowType.FAULT_TOLERANT
