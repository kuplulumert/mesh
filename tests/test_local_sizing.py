"""Yüzey gruplarına özel hücre boyutu testleri."""

import math
import os

import pytest

from automesh.config import Config
from automesh.models import BoundingBox, FaceGroup, GeometryMetrics, MeshPlan
from automesh.planning.local_sizing import (
    build_local_sizings,
    format_review_table,
    review_groups,
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
    """Gerçek bir SpaceClaim koşusunun ürettiğine benzer bir grup."""
    return FaceGroup(name=name, kind="cylinder", driver="curv", face_count=faces,
                     representative_radius=radius, min_radius=radius,
                     max_radius=radius * 2, created=created,
                     recommended_size=2 * math.pi * radius / 16,
                     total_area=faces * math.pi * radius * radius)


# --------------------------------------------------------------------------

def test_size_resolves_the_circumference():
    """16 hücre/çevre kuralı: 2·pi·r / 16."""
    plain = FaceGroup(name="g", driver="curv", representative_radius=0.001)
    assert size_for_group(plain, 16.0) == pytest.approx(2 * math.pi * 0.001 / 16)
    assert size_for_group(plain, 32.0) == pytest.approx(2 * math.pi * 0.001 / 32)
    # Betik zaten hesapladıysa o değer kullanılır, formül tekrar işletilmez.
    assert size_for_group(_group(radius=0.001), 32.0) == pytest.approx(
        2 * math.pi * 0.001 / 16)


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


def test_plan_explains_the_division(): 
    """Notlar neyin kaça bölündüğünü söylemeli."""
    plan = plan_mesh(_metrics([_group()]), Config())
    notes = " ".join(plan.notes)
    assert "automesh_cylinder_r0p80mm" in notes
    assert "12 yüzey" in notes
    assert "çevre" in notes                 # bölünen uzunluk
    assert "16 bölme" in notes              # rule-of-thumb
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
    # çevre / 16 = 2*pi*0.0015 / 16
    assert plan.local_sizings[0].size == pytest.approx(2 * math.pi * 0.0015 / 16)


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
    ("curv", "representative_radius", 0.0008, "çevre"),
    ("width", "min_width", 0.0001, "dar bant genişliği"),
    ("gap", "min_gap", 0.0009, "ince kesit"),
])
def test_notes_name_the_divided_length(driver, field, value, expected):
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


# --------------------------------------------------------------------------
# bölme sayısı ile gözden geçirme
# --------------------------------------------------------------------------

def _plan(max_size=0.003):
    return MeshPlan(min_size=max_size / 20, max_size=max_size, length_unit="mm")


def test_review_divides_the_circumference():
    """'20 mm çapındaki girişi kaça böleyim' sorusunun karşılığı."""
    inlet = FaceGroup(name="inlet", source="existing", driver="curv",
                      face_count=2, representative_radius=0.010,
                      total_area=0.01, created=True)
    review = review_groups([inlet], _plan(0.05), Config())[0]

    assert review.diameter == pytest.approx(0.020)
    assert "çap" in review.measured_text("mm")
    assert review.base_label == "çevre"
    assert review.base_length == pytest.approx(2 * math.pi * 0.010)
    assert review.recommended_divisions == pytest.approx(16.0)
    assert review.divisions == pytest.approx(16.0)
    assert review.size == pytest.approx(2 * math.pi * 0.010 / 16)
    assert review.rationale                      # kuralın gerekçesi yazılmalı


def test_changing_the_division_changes_the_size():
    inlet = FaceGroup(name="inlet", driver="curv", face_count=2,
                      representative_radius=0.010, total_area=0.01)
    review = review_groups([inlet], _plan(0.05), Config())[0]
    assert review.size_for(8) == pytest.approx(2 * math.pi * 0.010 / 8)
    assert review.size_for(32) == pytest.approx(2 * math.pi * 0.010 / 32)
    assert review.size_for(0) == 0.0


def test_user_division_overrides_the_rule_of_thumb():
    cfg = Config()
    cfg.local_sizing.divisions = {"inlet": 24}
    inlet = FaceGroup(name="inlet", driver="curv", face_count=2,
                      representative_radius=0.010, total_area=0.01)
    review = review_groups([inlet], _plan(0.05), cfg)[0]
    assert review.divisions == pytest.approx(24.0)
    assert review.user_set is True
    assert review.size == pytest.approx(2 * math.pi * 0.010 / 24)
    assert any("sizin tarafınızdan" in m for m in review.messages)


def test_user_division_is_matched_case_insensitively():
    cfg = Config()
    cfg.local_sizing.divisions = {"  INLET ": 8}
    inlet = FaceGroup(name="inlet", driver="curv", face_count=2,
                      representative_radius=0.010, total_area=0.01)
    assert review_groups([inlet], _plan(0.05), cfg)[0].divisions == pytest.approx(8)


def test_very_fine_controls_are_flagged_as_risky():
    """Aşırı ince boyut Fluent'i zorlar; kullanıcı uyarılmalı."""
    tiny = FaceGroup(name="automesh_width_0p01mm", driver="width", face_count=5,
                     min_width=3e-5, total_area=0.001)
    review = review_groups([tiny], _plan(0.01), Config())[0]
    assert review.risk == "high"
    assert any("Fluent zorlanabilir" in m for m in review.messages)
    assert any("Bölme sayısını düşürmeyi" in m for m in review.messages)


def test_moderately_fine_controls_only_warn():
    """Global boyutun 60-150 katı arası: uyarı var, yasak yok."""
    # 2*pi*0.0003/16 = 0.118 mm -> 10 mm / 0.118 mm = 85 kat
    group = FaceGroup(name="g", driver="curv", face_count=4,
                      representative_radius=0.0003, total_area=1e-4)
    review = review_groups([group], _plan(0.01), Config())[0]
    assert review.risk == "warn"
    assert 60 <= review.ratio < 150
    assert review.enabled is True


def test_absolute_floor_stops_runaway_refinement():
    cfg = Config()
    cfg.local_sizing.absolute_floor = 0.0002
    tiny = FaceGroup(name="g", driver="width", face_count=4, min_width=1e-5,
                     total_area=1e-4)
    review = review_groups([tiny], _plan(0.01), cfg)[0]
    assert review.size == pytest.approx(0.0002)
    assert review.clamped is True
    assert any("tabana" in m for m in review.messages)


def test_disabled_controls_are_skipped_but_still_listed():
    cfg = Config()
    cfg.local_sizing.disabled = ["automesh_cylinder_r0p80mm"]
    reviews = review_groups([_group()], _plan(), cfg)
    assert reviews[0].enabled is False
    assert any("siz kapattınız" in m for m in reviews[0].messages)
    plan = plan_mesh(_metrics([_group()]), cfg)
    assert plan.local_sizings == []


def test_face_cell_estimate_grows_as_the_size_shrinks():
    group = FaceGroup(name="g", driver="curv", face_count=4,
                      representative_radius=0.001, total_area=0.01)
    cfg = Config()
    coarse = review_groups([group], _plan(0.05), cfg)[0]
    cfg.local_sizing.divisions = {"g": 64}
    fine = review_groups([group], _plan(0.05), cfg)[0]
    assert fine.face_cells > coarse.face_cells
    assert coarse.face_cells == int(0.01 / coarse.size ** 2)


def test_review_and_fluent_controls_agree():
    """Ekranda görülen sayı ile Fluent'e giden sayı aynı olmalı."""
    cfg = Config()
    cfg.local_sizing.divisions = {"automesh_cylinder_r0p80mm": 24}
    metrics = _metrics([_group()])
    plan = plan_mesh(metrics, cfg)
    review = [r for r in review_groups(metrics.face_groups, plan, cfg)
              if r.enabled][0]
    assert plan.local_sizings[0].size == pytest.approx(review.size)


def test_review_table_is_readable():
    metrics = _metrics([_group(), _existing()])
    plan = plan_mesh(metrics, Config())
    text = format_review_table(review_groups(metrics.face_groups, plan, Config()),
                               plan, "mm")
    assert "Bölme" in text and "Hücre" in text
    assert "--divisions" in text
    assert "mm" in text


def test_review_serialises_for_the_gui():
    import json

    metrics = _metrics([_group()])
    plan = plan_mesh(metrics, Config())
    data = [r.to_dict() for r in review_groups(metrics.face_groups, plan, Config())]
    assert json.loads(json.dumps(data, ensure_ascii=False))[0]["divisions"] == 16


# --------------------------------------------------------------------------
# hazırlanan dosya (gruplar Fluent'e nasıl ulaşıyor)
# --------------------------------------------------------------------------

@pytest.fixture
def analyzer(tmp_path):
    from automesh.geometry.spaceclaim import SpaceClaimAnalyzer

    exe = tmp_path / "SpaceClaim.exe"
    exe.write_text("")
    return SpaceClaimAnalyzer(exe=str(exe), script_api="252")


def test_scdoc_input_is_still_exported_when_grouping(analyzer, tmp_path):
    """Girdi zaten .scdoc olsa bile dosya yazılmalı.

    Gruplar SpaceClaim'in belleğinde oluşur; kaydedilmezse Fluent'e hiç
    ulaşmaz ve özellik sessizce hiçbir şey yapmamış olur.
    """
    cfg = Config()
    target = analyzer._export_target("M:/CAD/Multicyclone.scdoc", str(tmp_path), cfg)
    assert target is not None
    assert target.endswith(".scdoc")
    assert "Multicyclone_automesh" in os.path.basename(target)


def test_scdoc_input_needs_no_export_without_grouping(analyzer, tmp_path):
    cfg = Config()
    cfg.local_sizing.enabled = False
    cfg.geometry.export_format = "scdoc"
    assert analyzer._export_target("M:/CAD/part.scdoc", str(tmp_path), cfg) is None


def test_prepared_file_never_overwrites_the_source(analyzer, tmp_path):
    """Kaynak CAD ağ sürücüsünde ya da salt okunur olabilir."""
    source = tmp_path / "Multicyclone.scdoc"
    source.write_text("")
    cfg = Config()
    cfg.geometry.export_dir = str(tmp_path)

    # Betik kaynakla aynı adı döndürse bile üzerine yazılmamalı.
    final = analyzer._final_export_path(str(source), str(tmp_path / "Multicyclone.scdoc"), cfg)
    assert os.path.abspath(final) != os.path.abspath(str(source))
    assert "_automesh" in os.path.basename(final)


def test_prepared_file_goes_to_the_run_directory(analyzer, tmp_path):
    cfg = Config()
    cfg.geometry.export_dir = str(tmp_path / "run")
    final = analyzer._final_export_path(
        "M:/CAD/part.scdoc", "/tmp/part_automesh.scdoc", cfg)
    assert os.path.dirname(os.path.abspath(final)) == os.path.abspath(
        str(tmp_path / "run"))


def test_orchestrator_points_the_export_at_the_run_directory(step_file, cfg, tmp_path):
    from automesh.orchestrator import AutoMeshAgent

    agent = AutoMeshAgent(step_file, cfg, str(tmp_path / "run"))
    agent._analyze()
    assert cfg.geometry.export_dir == str(tmp_path / "run")


# --------------------------------------------------------------------------
# grup çıkmadığında sebebini söyleme
# --------------------------------------------------------------------------

def _diagnostics(**kwargs):
    base = {"bodies": 1, "faces_seen": 0, "measurable": 0, "too_coarse": 0,
            "with_geometry": 0, "with_area": 0, "with_perimeter": 0,
            "with_radius": 0}
    base.update(kwargs)
    return GeometryMetrics(analyzer="spaceclaim",
                           raw={"face_group_diagnostics": base})


def test_missing_groups_explains_a_disabled_setting():
    from automesh.planning.local_sizing import explain_missing_groups

    cfg = Config()
    cfg.local_sizing.enabled = False
    lines = explain_missing_groups(_diagnostics(), cfg)
    assert any("kapalı" in line for line in lines)


def test_missing_groups_explains_a_non_spaceclaim_analyzer():
    from automesh.planning.local_sizing import explain_missing_groups

    metrics = GeometryMetrics(analyzer="step")
    lines = explain_missing_groups(metrics, Config())
    assert any("SpaceClaim" in line for line in lines)
    assert any("step" in line for line in lines)


def test_missing_groups_reports_unreadable_faces():
    """Asıl teşhis: yüzeyler okundu ama ölçüm alınamadı."""
    from automesh.planning.local_sizing import explain_missing_groups

    metrics = _diagnostics(
        reason="hicbir yuzeyden olcum alinamadi (yariçap/cevre/kesit hepsi bos)",
        faces_seen=240, with_geometry=240, with_area=240)
    lines = explain_missing_groups(metrics, Config())
    text = " ".join(lines)
    assert "ölçüm alınamadı" in text
    assert "240 yüzey görüldü" in text
    assert "çevre 0" in text          # hangi alanın okunamadığı görünmeli


def test_missing_groups_reports_everything_being_coarse():
    from automesh.planning.local_sizing import explain_missing_groups

    metrics = _diagnostics(
        reason="tum olcumler global boyutla zaten cozuluyor (hepsi ust sinirin uzerinde)",
        faces_seen=50, measurable=50, too_coarse=50)
    assert any("zaten çözülüyor" in line
               for line in explain_missing_groups(metrics, Config()))


def test_missing_groups_never_says_analyse_first():
    """Analiz yapıldı; kullanıcıya tekrar analiz etmesini söylemek yanıltıcı."""
    from automesh.planning.local_sizing import explain_missing_groups

    for metrics in (_diagnostics(reason="aday bant olusmadi"),
                    GeometryMetrics(analyzer="spaceclaim")):
        text = " ".join(explain_missing_groups(metrics, Config())).lower()
        assert "analiz edin" not in text
        assert text.strip()


def test_simple_mode_sends_the_scdoc_itself_not_step(tmp_path):
    """Gruplama kapalıyken .scdoc STEP'e çevrilmeden Fluent'e gitmeli."""
    from automesh.config import Config
    from automesh.geometry.spaceclaim import SpaceClaimAnalyzer

    cfg = Config()
    cfg.local_sizing.enabled = False
    cfg.geometry.export_format = "auto"
    analyzer = SpaceClaimAnalyzer()
    assert analyzer._export_target("M:/CAD/Multicyclone.scdoc",
                                   str(tmp_path), cfg) is None
    assert analyzer._export_target("M:/CAD/part.scdocx",
                                   str(tmp_path), cfg) is None
    # SpaceClaim dışı girdilerde davranış değişmedi
    assert analyzer._export_target("C:/cad/part.x_t", str(tmp_path),
                                   cfg).endswith(".stp")
