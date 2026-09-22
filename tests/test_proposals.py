import pytest

from automesh.config import Config
from automesh.geometry import analyze_geometry
from automesh.planning.proposals import (
    GB_PER_MILLION_CELLS,
    LEVELS,
    apply_proposal,
    build_proposals,
    format_table,
    measurements,
    recommended,
)
from automesh.planning.sizing import plan_mesh


@pytest.fixture
def metrics(step_file, cfg):
    return analyze_geometry(step_file, cfg)


def test_measurements_cover_the_lengths_a_user_asks_about(metrics):
    labels = [row.label for row in measurements(metrics)]
    for expected in ("Sınır kutusu", "Köşegen", "En küçük özellik",
                     "Özellik aralığı", "Karmaşıklık skoru"):
        assert expected in labels
    # Her satırın bir değeri olmalı - boş hücre göstermeyelim
    assert all(row.value for row in measurements(metrics))


def test_unknown_volume_is_stated_not_hidden(metrics):
    """STEP okuyucu hacmi bilmez; bunu gizlemek yerine söylemeli."""
    volume_row = [r for r in measurements(metrics) if r.label == "Hacim"][0]
    assert volume_row.value == "bilinmiyor"
    assert "CAD çekirdeği" in volume_row.note


def test_levels_are_ordered_coarse_to_fine(metrics, cfg):
    proposals = build_proposals(metrics, cfg)
    assert len(proposals) == len(LEVELS)
    sizes = [p.plan.max_size for p in proposals]
    assert sizes == sorted(sizes, reverse=True)
    cells = [p.cells for p in proposals]
    assert cells == sorted(cells)


def test_exactly_one_level_is_recommended(metrics, cfg):
    proposals = build_proposals(metrics, cfg)
    marked = [p for p in proposals if p.recommended]
    assert len(marked) == 1
    assert marked[0].key == "balanced"


def test_balanced_level_matches_the_automatic_plan(metrics, cfg):
    """Önerilen kademe, agent'ın kendi seçeceği planla aynı olmalı."""
    proposals = build_proposals(metrics, cfg)
    balanced = [p for p in proposals if p.key == "balanced"][0]
    auto = plan_mesh(metrics, cfg)
    assert balanced.plan.max_size == pytest.approx(auto.max_size, rel=1e-6)
    assert balanced.plan.min_size == pytest.approx(auto.min_size, rel=1e-6)


def test_each_level_explains_itself(metrics, cfg):
    for proposal in build_proposals(metrics, cfg):
        assert proposal.rationale
        assert "köşegenin" in proposal.rationale
        assert "hücreyle çözülüyor" in proposal.rationale
        assert proposal.size_text() and proposal.cells_text() and proposal.ram_text()


def test_ram_estimate_tracks_cell_count(metrics, cfg):
    for proposal in build_proposals(metrics, cfg):
        expected = proposal.cells / 1_000_000.0 * GB_PER_MILLION_CELLS
        assert proposal.ram_gb == pytest.approx(expected)


def test_budget_overrun_is_flagged(metrics, cfg):
    cfg.planning.max_cell_count = 50_000
    proposals = build_proposals(metrics, cfg)
    fine = [p for p in proposals if p.key == "very_fine"][0]
    assert any("bütçe" in w for w in fine.warnings)
    assert not fine.usable


def test_coarse_levels_warn_when_features_are_lost(cfg):
    """Detayı çok olan bir parçada kaba kademeler uyarı vermeli."""
    from automesh.models import BoundingBox, GeometryMetrics

    detailed = GeometryMetrics(min_feature_size=0.0004, face_count=3000,
                               curved_face_ratio=0.8, volume=0.01, area=2.0)
    detailed.bbox = BoundingBox(0, 0, 0, 1.0, 0.3, 0.3)
    proposals = build_proposals(detailed, cfg)
    coarsest = proposals[0]
    if coarsest.cells_across_feature < 2.0:
        assert any("kaybolur" in w for w in coarsest.warnings)


def test_recommendation_falls_back_when_the_default_does_not_fit(metrics, cfg):
    cfg.planning.max_cell_count = 30_000      # dengeli kademe sığmaz
    proposals = build_proposals(metrics, cfg)
    choice = recommended(proposals)
    assert choice is not None
    assert choice.usable or all(not p.usable for p in proposals)


def test_apply_proposal_writes_overrides(metrics, cfg):
    proposal = build_proposals(metrics, cfg)[-1]
    apply_proposal(cfg, proposal)
    assert cfg.planning.override_min_size == pytest.approx(proposal.plan.min_size)
    plan = plan_mesh(metrics, cfg)
    assert plan.max_size == pytest.approx(proposal.plan.max_size)


def test_level_config_scales_the_plan(metrics, cfg):
    cfg.planning.level = "coarse"
    coarse = plan_mesh(metrics, cfg)
    cfg.planning.level = "fine"
    fine = plan_mesh(metrics, cfg)
    assert coarse.max_size > fine.max_size
    assert any("kademesi seçildi" in n for n in coarse.notes)


def test_unknown_level_is_rejected(metrics, cfg):
    cfg.planning.level = "orta-karar"
    with pytest.raises(ValueError):
        plan_mesh(metrics, cfg)


def test_table_renders_without_crashing(metrics, cfg):
    text = format_table(metrics, build_proposals(metrics, cfg))
    assert "Ölçümler" in text and "Mesh seçenekleri" in text
    assert "önerilen" in text


def test_proposals_serialise_for_json_output(metrics, cfg):
    import json

    data = [p.to_dict() for p in build_proposals(metrics, cfg)]
    assert json.loads(json.dumps(data, ensure_ascii=False))[0]["plan"]["max_size"] > 0
