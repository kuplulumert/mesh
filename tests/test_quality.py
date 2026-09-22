import pytest

from automesh.config import QualityThresholds
from automesh.models import QualityVerdict
from automesh.quality import evaluate, free_face_count, parse_quality

MESHING_OUTPUT = """
Generating volume mesh (poly-hexcore)...
  Mesh generated: 1,234,567 cells, 6,172,835 faces, 2,469,134 nodes.
  Maximum cell skewness = 0.8532
  Minimum orthogonal quality = 0.1912
  Maximum aspect ratio = 42.310
Done.
"""

SOLVER_OUTPUT = """
 Mesh Quality:

 Minimum Orthogonal Quality = 1.234560e-01
 Maximum Ortho Skew = 8.700000e-01
 Maximum Aspect Ratio = 2.345670e+01
"""

BROKEN_OUTPUT = """
 WARNING: 12 cells with negative volume detected.
 Maximum cell skewness = 0.9950
 Minimum orthogonal quality = 0.0021
 3 left-handed faces found.
"""


def test_parses_meshing_mode_output():
    report = parse_quality(MESHING_OUTPUT)
    assert report.max_skewness == pytest.approx(0.8532)
    assert report.min_orthogonal_quality == pytest.approx(0.1912)
    assert report.max_aspect_ratio == pytest.approx(42.310)
    assert report.cell_count == 1234567
    assert report.node_count == 2469134


def test_parses_solver_style_scientific_notation():
    report = parse_quality(SOLVER_OUTPUT)
    assert report.max_skewness == pytest.approx(0.87)
    assert report.min_orthogonal_quality == pytest.approx(0.123456)
    assert report.max_aspect_ratio == pytest.approx(23.4567)


def test_detects_invalid_cells():
    report = parse_quality(BROKEN_OUTPUT)
    assert report.negative_volume_count == 12
    assert report.left_handed_face_count == 3


def test_verdicts_follow_the_thresholds():
    th = QualityThresholds()
    assert evaluate(MESHING_OUTPUT, th).verdict is QualityVerdict.ACCEPTABLE
    assert evaluate(BROKEN_OUTPUT, th).verdict is QualityVerdict.UNUSABLE
    good = " Maximum cell skewness = 0.55\n Minimum orthogonal quality = 0.42\n"
    assert evaluate(good, th).verdict is QualityVerdict.GOOD
    poor = " Maximum cell skewness = 0.93\n Minimum orthogonal quality = 0.07\n"
    assert evaluate(poor, th).verdict is QualityVerdict.POOR


def test_surface_stage_is_judged_more_strictly():
    th = QualityThresholds()
    text = " Maximum face skewness = 0.72\n"
    surface = evaluate(text, th, stage="surface")
    volume = evaluate(" Maximum cell skewness = 0.72\n", th, stage="volume")
    assert surface.verdict is QualityVerdict.ACCEPTABLE
    assert volume.verdict is QualityVerdict.GOOD


def test_unreadable_output_is_not_silently_good():
    report = evaluate("nothing useful here", QualityThresholds())
    assert report.verdict is QualityVerdict.POOR
    assert "okunamadı" in " ".join(report.failed_metrics)


def test_free_face_count():
    assert free_face_count("WARNING: 148 free faces found") == 148
    assert free_face_count("all good") == 0
