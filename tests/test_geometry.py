import pytest

from automesh.geometry import analyze_geometry
from automesh.geometry.base import finalise, robust_min
from automesh.geometry.discrete import analyze_triangles, read_stl
from automesh.geometry.step import detect_unit, parse_step
from automesh.models import BoundingBox, GeometryMetrics


def test_step_parser_reads_units_and_extents(step_file):
    data = parse_step(step_file)
    # File is in millimetres; metrics come back in metres.
    assert data["length_unit_hint"] == "mm"
    assert data["bbox"][3] == pytest.approx(0.200)
    assert data["bbox"][0] == pytest.approx(-0.020)
    assert data["face_count"] == 2
    assert data["body_count"] == 1
    assert data["watertight"] is True
    # 1.5 mm circle and 6 mm cylinder, both converted to metres.
    assert 0.0015 <= data["min_curvature_radius"] <= 0.006


def test_step_unit_detection_variants():
    assert detect_unit("SI_UNIT ( .MILLI., .METRE. )")[1] == "mm"
    assert detect_unit("LENGTH_UNIT ( ) NAMED_UNIT ( * ) SI_UNIT ( $, .METRE. )")[1] == "m"
    assert detect_unit("CONVERSION_BASED_UNIT('INCH',#5)")[1] == "in"
    assert detect_unit("nothing here")[1] == "mm"   # CAD default


def test_stl_reader_and_metrics(stl_file):
    triangles = read_stl(stl_file)
    assert len(triangles) == 12
    data = analyze_triangles(triangles)
    assert data["watertight"] is True
    assert data["volume"] == pytest.approx(0.05 ** 3, rel=1e-6)
    assert data["area"] == pytest.approx(6 * 0.05 ** 2, rel=1e-6)
    assert data["free_edge_count"] == 0


def test_open_surface_is_not_watertight():
    tris = [((0, 0, 0), (1, 0, 0), (0, 1, 0)), ((1, 0, 0), (1, 1, 0), (0, 1, 0))]
    data = analyze_triangles(tris)
    assert data["watertight"] is False
    assert data["free_edge_count"] > 0


def test_analyze_geometry_dispatches_to_step(step_file, cfg):
    metrics = analyze_geometry(step_file, cfg)
    assert metrics.analyzer == "step"
    assert metrics.diagonal > 0
    assert metrics.min_feature_size > 0


def test_analyze_geometry_dispatches_to_stl(stl_file, cfg):
    metrics = analyze_geometry(stl_file, cfg)
    assert metrics.analyzer.startswith("discrete")
    assert metrics.watertight is True


def test_unknown_format_falls_back(tmp_path, cfg):
    path = tmp_path / "model.xyzzy"
    path.write_text("not a cad file")
    metrics = analyze_geometry(str(path), cfg)
    assert metrics.analyzer == "fallback"
    assert metrics.warnings


def test_missing_file_raises(cfg):
    from automesh.geometry.base import GeometryAnalyzerError

    with pytest.raises(GeometryAnalyzerError):
        analyze_geometry("/no/such/file.step", cfg)


def test_finalise_clamps_sliver_features():
    metrics = GeometryMetrics(min_edge_length=1e-12)
    metrics.bbox = BoundingBox(0, 0, 0, 1, 1, 1)
    finalise(metrics)
    assert metrics.min_feature_size == pytest.approx(metrics.diagonal / 1e5)
    assert any("sliver" in w for w in metrics.warnings)


def test_robust_min_ignores_outliers():
    values = [1e-9] + [1.0] * 200
    assert robust_min(values) > 1e-9
