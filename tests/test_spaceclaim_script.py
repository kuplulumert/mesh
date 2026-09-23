"""SpaceClaim betiğinin saf mantığının testleri.

Betik SpaceClaim'in IronPython'unda koşar ve burada çalıştırılamaz; ama
yüzey ölçümü, ölçüt seçimi, bantlama ve isimlendirme SpaceClaim API'sine
dokunmadan doğrulanabilir - asıl hata yapılacak yer de orası.
"""

import math
import os

import pytest

from automesh.geometry.spaceclaim import script_path


@pytest.fixture
def script():
    """Betiği modül gibi yükle (main() çalışmasın)."""
    os.environ["AUTOMESH_SC_NO_RUN"] = "1"
    namespace = {"__name__": "spaceclaim_analyze", "__file__": script_path()}
    with open(script_path(), "r", encoding="utf-8") as handle:
        source = handle.read()
    try:
        exec(compile(source, script_path(), "exec"), namespace)
    finally:
        os.environ.pop("AUTOMESH_SC_NO_RUN", None)
    return namespace


class FakeGeometry:
    def __init__(self, name, radius=0.0):
        self._name = name
        if radius:
            self.Radius = radius

    def GetType(self):
        return type("T", (), {"Name": self._name})()


class FakeEdge:
    def __init__(self, length):
        self.Length = length


class FakeFace:
    def __init__(self, kind, radius=0.0, area=1e-6, perimeter=0.0):
        self.Shape = type("S", (), {"Geometry": FakeGeometry(kind, radius),
                                    "Area": area})()
        self.Area = area
        # Çevre verilirse dar bant (width) ölçütü devreye girer.
        self.Edges = [FakeEdge(perimeter)] if perimeter else []


class FakeBody:
    def __init__(self, faces):
        self.Faces = faces


class FakeNamedSelection:
    def __init__(self, name, faces):
        self._name = name
        self.Members = faces
        self.renamed = False

    def GetName(self):
        return self._name

    def SetName(self, value):          # çağrılırsa test yakalar
        self.renamed = True
        self._name = value


# --------------------------------------------------------------------------
# ölçüm ve ölçüt seçimi
# --------------------------------------------------------------------------

def test_band_key_groups_by_powers_of_two(script):
    band_key = script["band_key"]
    anchor, factor = 1.0e-4, 2.0
    assert band_key(1.0e-4, anchor, factor) == band_key(1.5e-4, anchor, factor)
    assert band_key(2.5e-4, anchor, factor) > band_key(1.5e-4, anchor, factor)
    assert band_key(6.0e-5, anchor, factor) < band_key(1.5e-4, anchor, factor)
    assert band_key(0.0, anchor, factor) is None


def test_format_size_is_filename_safe(script):
    fmt = script["format_size"]
    assert fmt(0.0008) == "0p80mm"
    assert fmt(0.012) == "12p00mm"
    assert fmt(0.00005) == "50p0um"
    for value in (0.0008, 0.012, 0.00005):
        assert "." not in fmt(value) and " " not in fmt(value)


def test_curvature_criterion_resolves_the_circumference(script):
    size, driver, _kind, radius, _w = script["required_size"](
        FakeFace("Cylinder", radius=0.0008), 0.0, {})
    assert driver == "curv"
    assert size == pytest.approx(2 * math.pi * 0.0008 / 16)
    assert radius == pytest.approx(0.0008)


def test_width_criterion_catches_narrow_bands(script):
    """alan 1e-6, çevre 0.02 -> genişlik 1e-4 -> 1e-4/3."""
    size, driver, _kind, _r, width = script["required_size"](
        FakeFace("Plane", area=1e-6, perimeter=0.02), 0.0, {})
    assert driver == "width"
    assert width == pytest.approx(1e-4)
    assert size == pytest.approx(1e-4 / 3.0)


def test_gap_criterion_uses_the_thin_section(script):
    size, driver, _kind, _r, _w = script["required_size"](
        FakeFace("Plane", area=1e-4), 0.0009, {})
    assert driver == "gap"
    assert size == pytest.approx(0.0009 / 3.0)


def test_the_most_demanding_criterion_wins(script):
    """Üç ölçüt de varsa en ince olan seçilmeli."""
    # curv = 3.9 mm, width = 33 um, gap = 300 um -> width kazanır
    size, driver, _kind, _r, _w = script["required_size"](
        FakeFace("Cylinder", radius=0.01, area=1e-6, perimeter=0.02),
        0.0009, {})
    assert driver == "width"
    assert size == pytest.approx(1e-4 / 3.0)


def test_cells_per_criterion_are_configurable(script):
    size, _d, _k, _r, _w = script["required_size"](
        FakeFace("Cylinder", radius=0.001), 0.0, {"cells_per_circle": 32.0})
    assert size == pytest.approx(2 * math.pi * 0.001 / 32)


def test_a_face_with_no_measurable_quantity_is_skipped(script):
    size, driver, _k, _r, _w = script["required_size"](
        FakeFace("Plane", area=0.0), 0.0, {})
    assert size == 0.0 and driver == ""


# --------------------------------------------------------------------------
# gruplama
# --------------------------------------------------------------------------

def test_faces_are_banded_by_required_cell_size(script):
    """Gruplama tipe değil, yüzeyin gerektirdiği hücre boyutuna göre."""
    faces = ([FakeFace("Cylinder", radius=0.0008) for _ in range(6)]
             + [FakeFace("Cylinder", radius=0.004) for _ in range(4)])
    groups = script["build_face_groups"]([FakeBody(faces)], {}, 0.5)

    assert len(groups) == 2
    assert groups[0]["recommended_size"] == pytest.approx(2 * math.pi * 0.0008 / 16)
    assert groups[1]["recommended_size"] == pytest.approx(2 * math.pi * 0.004 / 16)
    assert all(g["driver"] == "curv" for g in groups)
    assert sum(g["face_count"] for g in groups) == 10


def test_names_carry_the_criterion_and_the_size(script):
    """Ad, uygulanacak boyutu ve onu belirleyen ölçütü söylemeli."""
    faces = [FakeFace("Cylinder", radius=0.0008) for _ in range(4)]
    groups = script["build_face_groups"]([FakeBody(faces)], {}, 0.5)
    assert groups[0]["name"] == "automesh_curv_0p31mm"


def test_width_driven_groups_are_named_accordingly(script):
    faces = [FakeFace("Cylinder", radius=0.01, area=1e-6, perimeter=0.02)
             for _ in range(4)]
    groups = script["build_face_groups"]([FakeBody(faces)], {}, 0.5)
    assert len(groups) == 1
    assert groups[0]["driver"] == "width"
    assert groups[0]["name"].startswith("automesh_width_")
    assert groups[0]["min_width"] == pytest.approx(1e-4)


def test_gap_driven_groups_come_from_thin_bodies(script):
    faces = [FakeFace("Plane", area=1e-4) for _ in range(4)]
    groups = script["build_face_groups"]([FakeBody(faces)], {}, 0.5, {0: 0.0009})
    assert len(groups) == 1
    assert groups[0]["driver"] == "gap"
    assert groups[0]["recommended_size"] == pytest.approx(0.0009 / 3.0)


def test_planes_alone_are_not_grouped(script):
    """Ölçülebilir bir zorluk yoksa global boyutta kalmalı."""
    body = FakeBody([FakeFace("Plane") for _ in range(20)])
    assert script["build_face_groups"]([body], {}, 0.2) == []


def test_band_takes_its_most_demanding_member(script):
    """Bandın en ince özelliği çözülmeli, ortalaması değil.

    Yarıçaplar tek bantta kalacak şekilde seçildi: 2*pi*r/16 değerleri
    [0.4 mm, 0.8 mm) aralığına düşüyor.
    """
    faces = [FakeFace("Cylinder", radius=r) for r in (0.0011, 0.0013, 0.0016)]
    groups = script["build_face_groups"]([FakeBody(faces)], {}, 0.5)
    assert len(groups) == 1
    assert groups[0]["face_count"] == 3
    assert groups[0]["recommended_size"] == pytest.approx(2 * math.pi * 0.0011 / 16)
    assert groups[0]["min_radius"] == pytest.approx(0.0011)
    assert groups[0]["max_radius"] == pytest.approx(0.0016)


def test_coarse_requirements_are_left_to_the_global_size(script):
    faces = [FakeFace("Cylinder", radius=0.05) for _ in range(5)]
    assert script["build_face_groups"]([FakeBody(faces)], {}, 0.2) == []


def test_single_face_bands_are_skipped(script):
    faces = [FakeFace("Cylinder", radius=0.0008)]
    assert script["build_face_groups"]([FakeBody(faces)], {}, 0.5) == []
    assert len(script["build_face_groups"](
        [FakeBody(faces)], {"min_faces_per_group": 1}, 0.5)) == 1


def test_group_count_is_capped_keeping_the_finest(script):
    faces = []
    for radius in (0.0002, 0.0004, 0.0008, 0.0016, 0.0032, 0.0064):
        faces.extend(FakeFace("Cylinder", radius=radius) for _ in range(3))
    groups = script["build_face_groups"]([FakeBody(faces)], {"max_groups": 3}, 1.0)
    assert len(groups) == 3
    sizes = [g["recommended_size"] for g in groups]
    assert sizes == sorted(sizes)
    assert sizes[0] == pytest.approx(2 * math.pi * 0.0002 / 16)


def test_same_size_different_kinds_share_one_control(script):
    """Aynı boyutu gerektiren yüzeyler tek kontrolde toplanır."""
    faces = ([FakeFace("Cylinder", radius=0.0008) for _ in range(3)]
             + [FakeFace("Sphere", radius=0.0008) for _ in range(3)])
    groups = script["build_face_groups"]([FakeBody(faces)], {}, 0.5)
    assert len(groups) == 1
    assert groups[0]["face_count"] == 6
    assert groups[0]["kind"] == "mixed"


def test_grouping_can_be_disabled(script):
    faces = [FakeFace("Cylinder", radius=0.0008) for _ in range(5)]
    assert script["build_face_groups"](
        [FakeBody(faces)], {"group_faces": False}, 0.5) == []


def test_named_selection_failure_is_recorded_not_fatal(script):
    """SpaceClaim API burada yok; grup yine raporlanmalı, created=False ile."""
    faces = [FakeFace("Cylinder", radius=0.0008) for _ in range(4)]
    groups = script["build_face_groups"]([FakeBody(faces)], {}, 0.5)
    assert len(groups) == 1
    assert groups[0]["created"] is False
    assert groups[0]["note"]
    assert groups[0]["source"] == "auto"


# --------------------------------------------------------------------------
# kullanıcının kendi grupları
# --------------------------------------------------------------------------

def test_existing_groups_are_read_and_sized(script):
    inlet = FakeNamedSelection(
        "inlet", [FakeFace("Cylinder", radius=0.0008) for _ in range(3)])
    script["named_selection_list"] = lambda: [inlet]

    groups = script["describe_existing_groups"]({}, {}, 0.0)
    assert len(groups) == 1
    assert groups[0]["name"] == "inlet"
    assert groups[0]["source"] == "existing"
    assert groups[0]["face_count"] == 3
    assert groups[0]["recommended_size"] == pytest.approx(2 * math.pi * 0.0008 / 16)


def test_existing_groups_are_never_modified(script):
    """Kullanıcının grubuna dokunulmamalı: adı da üyeleri de aynı kalmalı."""
    wall = FakeNamedSelection(
        "wall-duct", [FakeFace("Cylinder", radius=0.001) for _ in range(2)])
    members_before = list(wall.Members)
    script["named_selection_list"] = lambda: [wall]

    script["describe_existing_groups"]({}, {}, 0.0)
    assert wall.GetName() == "wall-duct"
    assert wall.renamed is False
    assert wall.Members == members_before


def test_our_own_groups_are_not_read_back_as_existing(script):
    ours = FakeNamedSelection(
        "automesh_curv_0p31mm", [FakeFace("Cylinder", radius=0.0008)])
    theirs = FakeNamedSelection("outlet", [FakeFace("Cylinder", radius=0.002)])
    script["named_selection_list"] = lambda: [ours, theirs]
    names = [g["name"] for g in script["describe_existing_groups"]({}, {}, 0.0)]
    assert names == ["outlet"]


def test_existing_groups_without_faces_are_skipped(script):
    script["named_selection_list"] = lambda: [FakeNamedSelection("bos", [])]
    assert script["describe_existing_groups"]({}, {}, 0.0) == []


def test_existing_group_reports_its_dominant_criterion(script):
    narrow = FakeNamedSelection("fillet-band", [
        FakeFace("Cylinder", radius=0.01, area=1e-6, perimeter=0.02)
        for _ in range(3)])
    script["named_selection_list"] = lambda: [narrow]
    group = script["describe_existing_groups"]({}, {}, 0.0)[0]
    assert group["driver"] == "width"
    assert group["recommended_size"] == pytest.approx(1e-4 / 3.0)


def test_existing_group_takes_its_finest_member(script):
    mixed = FakeNamedSelection("port", [
        FakeFace("Cylinder", radius=0.004),
        FakeFace("Cylinder", radius=0.0005),
    ])
    script["named_selection_list"] = lambda: [mixed]
    group = script["describe_existing_groups"]({}, {}, 0.0)[0]
    assert group["recommended_size"] == pytest.approx(2 * math.pi * 0.0005 / 16)


def test_missing_named_selection_api_is_not_fatal(script):
    """SpaceClaim sürümü grupları vermiyorsa sessizce boş dönmeli."""
    assert script["named_selection_list"]() == []
    assert script["describe_existing_groups"]({}, {}, 0.0) == []


# --------------------------------------------------------------------------
# grup çıkmadığında teşhis
# --------------------------------------------------------------------------

def test_diagnostics_report_unreadable_faces(script):
    """Yüzeyler görülüyor ama ölçüm alınamıyorsa bu söylenmeli."""
    diag = {}
    body = FakeBody([FakeFace("Plane") for _ in range(8)])
    assert script["build_face_groups"]([body], {}, 0.2, None, diag) == []
    assert diag["faces_seen"] == 8
    assert diag["measurable"] == 0
    assert "olcum alinamadi" in diag["reason"]


def test_diagnostics_report_everything_being_coarse(script):
    diag = {}
    body = FakeBody([FakeFace("Cylinder", radius=0.05) for _ in range(5)])
    script["build_face_groups"]([body], {}, 0.2, None, diag)
    assert diag["measurable"] == 5
    assert diag["too_coarse"] == 5
    assert "global boyutla zaten" in diag["reason"]


def test_diagnostics_count_readable_fields(script):
    """Hangi API alanının okunabildiği ayrı ayrı sayılmalı."""
    diag = {}
    body = FakeBody([FakeFace("Cylinder", radius=0.0008, perimeter=0.005)
                     for _ in range(4)])
    script["build_face_groups"]([body], {}, 0.5, None, diag)
    assert diag["with_geometry"] == 4
    assert diag["with_area"] == 4
    assert diag["with_perimeter"] == 4
    assert diag["with_radius"] == 4
    assert diag["candidates"] == 1
    assert "reason" not in diag          # başarılıysa sebep yazılmamalı


def test_diagnostics_report_a_disabled_setting(script):
    diag = {}
    script["build_face_groups"]([FakeBody([])], {"group_faces": False}, 0.2,
                                None, diag)
    assert diag["reason"] == "gruplama kapali"


def test_diagnostics_report_small_bands(script):
    diag = {}
    body = FakeBody([FakeFace("Cylinder", radius=0.0008)])
    script["build_face_groups"]([body], {}, 0.5, None, diag)
    assert diag["small_bands"] == 1
    assert "yeterli yuzey yok" in diag["reason"]
