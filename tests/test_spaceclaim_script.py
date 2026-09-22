"""SpaceClaim betiğinin saf mantığının testleri.

Betik SpaceClaim'in IronPython'unda koşar ve burada çalıştırılamaz; ama
yüzey sınıflandırma, bantlama ve isimlendirme mantığı SpaceClaim API'sine
dokunmadan doğrulanabilir - asıl hata yapılacak yer de orası.
"""

import os

import pytest

from automesh.geometry.spaceclaim import script_path


@pytest.fixture(scope="module")
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


class FakeFace:
    def __init__(self, kind, radius=0.0, area=1e-6):
        self.Shape = type("S", (), {"Geometry": FakeGeometry(kind, radius),
                                    "Area": area})()
        self.Area = area


class FakeBody:
    def __init__(self, faces):
        self.Faces = faces


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


def test_planes_are_not_grouped(script):
    """Düz duvar global boyutta kalmalı, kontrol israfı olmasın."""
    body = FakeBody([FakeFace("Plane") for _ in range(20)])
    assert script["build_face_groups"]([body], {}, 0.2) == []


def test_cylinders_are_banded_by_radius(script):
    faces = ([FakeFace("Cylinder", radius=0.0008) for _ in range(6)]
             + [FakeFace("Cylinder", radius=0.004) for _ in range(4)])
    groups = script["build_face_groups"]([FakeBody(faces)], {}, 0.2)

    assert len(groups) == 2
    names = [g["name"] for g in groups]
    assert any("0p80mm" in n for n in names)
    assert any("4p00mm" in n for n in names)
    assert groups[0]["representative_radius"] < groups[1]["representative_radius"]
    assert all(g["kind"] == "cylinder" for g in groups)
    assert sum(g["face_count"] for g in groups) == 10


def test_representative_radius_is_the_smallest_in_the_band(script):
    """Bandın en ince özelliği çözülmeli, ortalaması değil."""
    faces = [FakeFace("Cylinder", radius=r) for r in (0.0010, 0.0012, 0.0015)]
    groups = script["build_face_groups"]([FakeBody(faces)], {}, 0.2)
    assert len(groups) == 1
    assert groups[0]["representative_radius"] == pytest.approx(0.0010)
    assert groups[0]["min_radius"] == pytest.approx(0.0010)
    assert groups[0]["max_radius"] == pytest.approx(0.0015)


def test_large_radii_are_left_to_the_global_size(script):
    """Gövdenin %8'inden büyük yarıçaplar zaten global boyutla çözülür."""
    faces = [FakeFace("Cylinder", radius=0.05) for _ in range(5)]
    assert script["build_face_groups"]([FakeBody(faces)], {}, 0.2) == []


def test_single_face_bands_are_skipped(script):
    faces = [FakeFace("Cylinder", radius=0.0008)]
    assert script["build_face_groups"]([FakeBody(faces)], {}, 0.2) == []
    groups = script["build_face_groups"](
        [FakeBody(faces)], {"min_faces_per_group": 1}, 0.2)
    assert len(groups) == 1


def test_group_count_is_capped_keeping_the_finest(script):
    faces = []
    for radius in (0.0002, 0.0004, 0.0008, 0.0016, 0.0032, 0.0064):
        faces.extend(FakeFace("Cylinder", radius=radius) for _ in range(3))
    groups = script["build_face_groups"]([FakeBody(faces)], {"max_groups": 3}, 0.5)
    assert len(groups) == 3
    radii = [g["representative_radius"] for g in groups]
    assert radii == sorted(radii)
    assert radii[0] == pytest.approx(0.0002)


def test_mixed_kinds_stay_in_separate_groups(script):
    faces = ([FakeFace("Cylinder", radius=0.0008) for _ in range(3)]
             + [FakeFace("Sphere", radius=0.0008) for _ in range(3)])
    groups = script["build_face_groups"]([FakeBody(faces)], {}, 0.2)
    assert sorted(g["kind"] for g in groups) == ["cylinder", "sphere"]


def test_grouping_can_be_disabled(script):
    faces = [FakeFace("Cylinder", radius=0.0008) for _ in range(5)]
    assert script["build_face_groups"](
        [FakeBody(faces)], {"group_faces": False}, 0.2) == []


def test_named_selection_failure_is_recorded_not_fatal(script):
    """SpaceClaim API burada yok; grup yine raporlanmalı, created=False ile."""
    faces = [FakeFace("Cylinder", radius=0.0008) for _ in range(4)]
    groups = script["build_face_groups"]([FakeBody(faces)], {}, 0.2)
    assert len(groups) == 1
    assert groups[0]["created"] is False
    assert groups[0]["note"]
