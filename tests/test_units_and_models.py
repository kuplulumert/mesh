import math

import pytest

from automesh import units
from automesh.models import (
    BoundingBox,
    GeometryMetrics,
    MeshPlan,
    QualityReport,
    QualityVerdict,
    VolumeFill,
)


def test_unit_roundtrip():
    assert units.to_metres(25.4, "mm") == pytest.approx(0.0254)
    assert units.from_metres(0.0254, "in") == pytest.approx(1.0)
    assert units.normalise("Millimeter") == "mm"
    with pytest.raises(ValueError):
        units.normalise("parsec")


def test_working_unit_follows_model_size():
    assert units.pick_working_unit(0.2) == "mm"
    assert units.pick_working_unit(5.0) == "m"
    assert units.pick_working_unit(1e-5) == "um"


def test_bounding_box_geometry():
    box = BoundingBox(0, 0, 0, 3, 4, 0)
    assert box.sizes == (3, 4, 0)
    assert box.diagonal == pytest.approx(5.0)
    assert box.max_extent == 4
    assert box.min_extent == 3


def test_complexity_grows_with_detail():
    simple = GeometryMetrics(min_feature_size=0.05, face_count=20)
    simple.bbox = BoundingBox(0, 0, 0, 1, 1, 1)
    detailed = GeometryMetrics(min_feature_size=0.0002, face_count=5000,
                               curved_face_ratio=0.8, small_feature_ratio=0.3)
    detailed.bbox = BoundingBox(0, 0, 0, 1, 1, 1)
    assert detailed.complexity() > simple.complexity()
    assert 0.0 <= simple.complexity() <= 1.0
    assert 0.0 <= detailed.complexity() <= 1.0


def test_plan_roundtrip_and_clamp():
    plan = MeshPlan(min_size=1e-6, max_size=1.0, growth_rate=3.0)
    plan.volume_fill = VolumeFill.TETRAHEDRAL
    plan.clamp()
    assert plan.growth_rate == pytest.approx(1.5)
    assert plan.max_size / plan.min_size <= 1000.0 + 1e-9
    restored = MeshPlan.from_dict(plan.to_dict())
    assert restored.to_dict() == plan.to_dict()
    assert restored.volume_fill is VolumeFill.TETRAHEDRAL


def test_volume_fill_softens_towards_tets():
    assert VolumeFill.POLY_HEXCORE.softer() is VolumeFill.POLYHEDRA
    assert VolumeFill.POLYHEDRA.softer() is VolumeFill.TETRAHEDRAL
    assert VolumeFill.TETRAHEDRAL.softer() is VolumeFill.TETRAHEDRAL


def test_quality_report_roundtrip():
    report = QualityReport(max_skewness=0.9, verdict=QualityVerdict.POOR,
                           raw_text="x" * 5000)
    data = report.to_dict()
    assert data["raw_text"].endswith("[truncated]")
    assert QualityReport.from_dict(data).verdict is QualityVerdict.POOR
    assert QualityVerdict.ACCEPTABLE.is_success
    assert not QualityVerdict.UNUSABLE.is_success
