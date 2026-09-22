"""Yüzey gruplarına özel hücre boyutu testleri."""

import math

import pytest

from automesh.config import Config
from automesh.models import BoundingBox, FaceGroup, GeometryMetrics, MeshPlan
from automesh.planning.local_sizing import (
    build_local_sizings,
    size_for_group,
    spaceclaim_params,
    summarise,
)
from automesh.planning.sizing import plan_mesh


def _metrics(groups=None):
    metrics = GeometryMetrics(min_feature_size=0.002, face_count=600,
                              volume=0.001, area=0.5)
    metrics.bbox = BoundingBox(0, 0, 0, 0.3, 0.1, 0.1)
    metrics.face_groups = list(groups or [])
    return metrics


def _group(name="automesh_cylinder_r0p80mm", radius=0.0008, faces=12, created=True):
    return FaceGroup(name=name, kind="cylinder", face_count=faces,
                     representative_radius=radius, min_radius=radius,
                     max_radius=radius * 2, created=created)


# --------------------------------------------------------------------------

def test_size_resolves_the_circumference():
    """16 hücre/çevre kuralı: 2·pi·r / 16."""
    assert size_for_group(_group(radius=0.001), 16.0) == pytest.approx(
        2 * math.pi * 0.001 / 16)
    assert size_for_group(_group(radius=0.001), 32.0) == pytest.approx(
        2 * math.pi * 0.001 / 32)


def test_size_falls_back_to_face_extent_without_a_radius():
    group = FaceGroup(name="x", representative_radius=0.0, min_face_size=0.001)
    assert size_for_group(group, 16.0) == pytest.approx(0.0005)
    assert size_for_group(FaceGroup(name="x"), 16.0) == 0.0


def test_smaller_features_get_smaller_cells():
    cfg = Config()
    metrics = _metrics([
        _group("automesh_torus_r0p20mm", radius=0.0002, faces=8),
        _group("automesh_cylinder_r0p80mm", radius=0.0008),
        _group("automesh_cylinder_r4p00mm", radius=0.004, faces=6),
    ])
    plan = plan_mesh(metrics, cfg)

    sizes = {s.name: s.size for s in plan.local_sizings}
    assert len(sizes) == 3
    assert sizes["automesh_torus_r0p20mm"] < sizes["automesh_cylinder_r0p80mm"]
    assert sizes["automesh_cylinder_r0p80mm"] < sizes["automesh_cylinder_r4p00mm"]
    # Hepsi global boyuttan ince olmalı, yoksa kontrolün anlamı yok
    assert all(size < plan.max_size for size in sizes.values())
    assert all(s.size_control_type == "Face Size" for s in plan.local_sizings)


def test_controls_near_the_global_size_are_skipped():
    """Global boyuta yakın kontrol eklemek boşuna maliyet."""
    cfg = Config()
    plan = MeshPlan(min_size=1e-4, max_size=1e-3, length_unit="mm")
    # 2·pi·r/16 = 0.95 mm -> global 1 mm'ye çok yakın
    group = _group(radius=0.95e-3 * 16 / (2 * math.pi))
    sizings, notes = build_local_sizings([group], plan, cfg)
    assert sizings == []
    assert any("global boyuta" in note for note in notes)


def test_too_fine_controls_are_clamped_not_dropped():
    cfg = Config()
    cfg.local_sizing.min_size_ratio = 50.0
    plan = MeshPlan(min_size=1e-4, max_size=1e-2, length_unit="mm")
    sizings, notes = build_local_sizings([_group(radius=1e-6)], plan, cfg)
    assert len(sizings) == 1
    assert sizings[0].size == pytest.approx(plan.max_size / 50.0)
    assert any("tabana" in note for note in notes)


def test_failed_groups_are_reported_and_skipped():
    cfg = Config()
    plan = MeshPlan(min_size=1e-4, max_size=3e-3, length_unit="mm")
    group = _group(created=False)
    group.note = "ad verilemedi"
    sizings, notes = build_local_sizings([group], plan, cfg)
    assert sizings == []
    assert any("oluşturulamadı" in note and "ad verilemedi" in note
               for note in notes)


def test_control_count_is_capped_finest_first():
    cfg = Config()
    cfg.local_sizing.max_controls = 2
    plan = MeshPlan(min_size=1e-5, max_size=5e-3, length_unit="mm")
    groups = [_group("g{0}".format(i), radius=r)
              for i, r in enumerate((0.004, 0.0002, 0.0008))]
    sizings, notes = build_local_sizings(groups, plan, cfg)
    assert len(sizings) == 2
    # En ince gruplar korunmalı
    assert [s.name for s in sizings] == ["g1", "g2"]
    assert any("sınırına" in note for note in notes)


def test_local_sizing_can_be_switched_off():
    cfg = Config()
    cfg.local_sizing.enabled = False
    plan = plan_mesh(_metrics([_group()]), cfg)
    assert plan.local_sizings == []


def test_no_groups_means_no_controls():
    assert plan_mesh(_metrics(), Config()).local_sizings == []


def test_plan_explains_each_control():
    """Notlar hangi grubun neden hangi boyutu aldığını söylemeli."""
    plan = plan_mesh(_metrics([_group()]), Config())
    notes = " ".join(plan.notes)
    assert "automesh_cylinder_r0p80mm" in notes
    assert "12 yüzey" in notes
    assert "eğrilik yarıçapı" in notes      # boyutu belirleyen ölçüt
    assert "hücre" in notes


def test_spaceclaim_params_follow_the_config():
    cfg = Config()
    cfg.local_sizing.max_controls = 5
    cfg.local_sizing.name_prefix = "ns"
    params = spaceclaim_params(cfg)
    assert params["group_faces"] is True
    assert params["max_groups"] == 5
    assert params["group_prefix"] == "ns"
    cfg.local_sizing.enabled = False
    assert spaceclaim_params(cfg)["group_faces"] is False


def test_summarise_is_human_readable():
    plan = plan_mesh(_metrics([_group()]), Config())
    lines = summarise(plan.local_sizings, "mm")
    assert lines and "mm" in lines[0]


# --------------------------------------------------------------------------
# Fluent'e aktarım
# --------------------------------------------------------------------------

def test_controls_reach_fluent_as_face_size_tasks(cfg, tmp_path):
    from automesh.fluent import build_driver
    from automesh.fluent.workflows import WorkflowRunner

    driver = build_driver(cfg, str(tmp_path))
    driver.launch()
    plan = plan_mesh(_metrics([_group(), _group("automesh_torus_r0p20mm",
                                                radius=0.0002, faces=8)]), cfg)
    runner = WorkflowRunner(driver, plan, "part.scdoc")
    runner.initialize()
    runner.local_sizing()

    args = driver.arguments["Add Local Sizing"]
    assert args["BOIExecution"] == "Face Size"
    assert args["BOIZoneorLabel"] == "label"
    assert args["AddChild"] == "yes"
    assert args["BOIFaceLabelList"] == [args["BOIControlName"]]
    # Boyut Fluent'in içe aktarma birimine çevrilmiş olmalı
    matching = [s for s in plan.local_sizings if s.name == args["BOIControlName"]][0]
    from automesh.units import from_metres
    assert args["BOISize"] == pytest.approx(
        from_metres(matching.size, plan.length_unit), rel=1e-6)


def test_export_switches_to_scdoc_when_grouping(tmp_path):
    """STEP named selection taşımaz; gruplar varsa .scdoc gerekir."""
    from automesh.geometry.spaceclaim import SpaceClaimAnalyzer

    exe = tmp_path / "SpaceClaim.exe"
    exe.write_text("")
    analyzer = SpaceClaimAnalyzer(exe=str(exe), script_api="252")

    cfg = Config()
    cfg.geometry.export_format = "auto"
    cfg.local_sizing.enabled = True
    target = analyzer._export_target("C:/cad/part.x_t", str(tmp_path), cfg)
    assert target.endswith(".scdoc")

    cfg.local_sizing.enabled = False
    target = analyzer._export_target("C:/cad/part.x_t", str(tmp_path), cfg)
    assert target.endswith(".stp")


def test_groups_survive_the_json_roundtrip():
    metrics = _metrics([_group()])
    restored = GeometryMetrics.from_dict(metrics.to_dict())
    assert restored.face_groups[0].name == "automesh_cylinder_r0p80mm"
    assert restored.face_groups[0].representative_radius == pytest.approx(0.0008)


# --------------------------------------------------------------------------
# kullanıcının kendi grupları
# --------------------------------------------------------------------------

def _existing(name="inlet", size=0.0006, faces=2):
    return FaceGroup(name=name, source="existing", driver="curv",
                     face_count=faces, recommended_size=size,
                     representative_radius=0.0015, created=True,
                     note="kullanicinin grubu - degistirilmedi")


def test_existing_groups_get_their_own_control():
    plan = plan_mesh(_metrics([_existing()]), Config())
    assert [s.name for s in plan.local_sizings] == ["inlet"]
    assert plan.local_sizings[0].size == pytest.approx(0.0006)


def test_existing_groups_are_marked_in_the_notes():
    plan = plan_mesh(_metrics([_existing()]), Config())
    notes = " ".join(plan.notes)
    assert "sizin grubunuz" in notes and "değiştirilmedi" in notes


def test_existing_groups_come_first_in_the_control_budget():
    """Kontrol bütçesi dolarsa kullanıcının grubu elenmemeli."""
    cfg = Config()
    cfg.local_sizing.max_controls = 2
    groups = [
        FaceGroup(name="automesh_curv_a", recommended_size=1e-5, face_count=4),
        FaceGroup(name="automesh_curv_b", recommended_size=2e-5, face_count=4),
        _existing("outlet", size=3e-4),
    ]
    plan = plan_mesh(_metrics(groups), cfg)
    names = [s.name for s in plan.local_sizings]
    assert "outlet" in names
    assert len(names) == 2


def test_sizing_existing_groups_can_be_disabled():
    cfg = Config()
    cfg.local_sizing.size_existing_groups = False
    plan = plan_mesh(_metrics([_existing(), _group()]), cfg)
    names = [s.name for s in plan.local_sizings]
    assert "inlet" not in names
    assert "automesh_cylinder_r0p80mm" in names


def test_recommended_size_wins_over_the_radius_formula():
    """Betik ölçütleri yüzey yüzey hesapladı; onun sonucu kullanılmalı."""
    group = FaceGroup(name="automesh_width_0p03mm", driver="width",
                      face_count=5, recommended_size=3.3e-5, min_width=1e-4,
                      representative_radius=0.01)
    assert size_for_group(group, 16.0) == pytest.approx(3.3e-5)


def test_falls_back_to_width_then_face_extent():
    from_width = FaceGroup(name="x", min_width=0.0009)
    assert size_for_group(from_width, 16.0) == pytest.approx(0.0003)
    from_extent = FaceGroup(name="x", min_face_size=0.001)
    assert size_for_group(from_extent, 16.0) == pytest.approx(0.0005)


@pytest.mark.parametrize("driver,field,value,expected", [
    ("curv", "representative_radius", 0.0008, "eğrilik yarıçapı"),
    ("width", "min_width", 0.0001, "dar bant genişliği"),
    ("gap", "min_gap", 0.0009, "ince kesit"),
])
def test_notes_name_the_driving_measurement(driver, field, value, expected):
    group = FaceGroup(name="g", driver=driver, face_count=3,
                      recommended_size=2e-4, **{field: value})
    plan = plan_mesh(_metrics([group]), Config())
    assert any(expected in note for note in plan.notes)


def test_spaceclaim_params_carry_every_criterion():
    cfg = Config()
    cfg.local_sizing.cells_across_width = 4.0
    cfg.local_sizing.cells_across_gap = 5.0
    cfg.local_sizing.read_existing_groups = False
    params = spaceclaim_params(cfg)
    assert params["cells_per_circle"] == pytest.approx(16.0)
    assert params["cells_across_width"] == pytest.approx(4.0)
    assert params["cells_across_gap"] == pytest.approx(5.0)
    assert params["read_existing_groups"] is False
