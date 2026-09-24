"""Temizlik tarama betiğinin saf mantığı.

Betik SpaceClaim'in IronPython'unda koşar; burada çalıştırılamaz. Ama
asıl hata yapılacak yer olan tespit mantığı (vida noktası, fileto, küçük
çıkıntı) yalın sözlüklerle çalışıyor ve SpaceClaim olmadan doğrulanabilir.
"""

import math
import os
import re

import pytest

from automesh.geometry.spaceclaim import script_path

CLEANUP = os.path.join(os.path.dirname(script_path()), "spaceclaim_cleanup.py")
MM = 0.001
Z = (0.0, 0.0, 1.0)


@pytest.fixture
def sc():
    """Analiz betiği (kütüphane) + temizlik betiği, tek isim alanında."""
    os.environ["AUTOMESH_SC_NO_RUN"] = "1"
    namespace = {"__name__": "spaceclaim_cleanup"}
    try:
        for path in (script_path(), CLEANUP):
            with open(path, "r", encoding="utf-8") as handle:
                exec(compile(handle.read(), path, "exec"), namespace)
    finally:
        os.environ.pop("AUTOMESH_SC_NO_RUN", None)
    return namespace


# -- sentetik yüz kayıtları -----------------------------------------------

def _rec(index, kind, bbox, **extra):
    record = {"index": index, "body": 0, "kind": kind, "radius": 0.0,
              "major_radius": 0.0, "axis": None, "normal": None,
              "area": 1e-6, "bbox": bbox, "span": 0.0, "neighbors": [],
              "groups": []}
    record.update(extra)
    return record


def plane(index, bbox, normal=Z, **extra):
    return _rec(index, "Plane", bbox, normal=normal, **extra)


def cylinder(index, radius, center_xy, z0, z1, span, **extra):
    x, y = center_xy
    bbox = (x - radius, y - radius, z0, x + radius, y + radius, z1)
    return _rec(index, "Cylinder", bbox, radius=radius,
                axis=((x, y, 0.0), Z), span=span, **extra)


def torus(index, minor, major, center_xy, z0, z1, **extra):
    x, y = center_xy
    outer = major + minor
    bbox = (x - outer, y - outer, z0, x + outer, y + outer, z1)
    return _rec(index, "Torus", bbox, radius=minor, major_radius=major,
                axis=((x, y, 0.0), Z), **extra)


def link(records, a, b):
    records[a]["neighbors"].append(b)
    records[b]["neighbors"].append(a)


THRESHOLDS = {"fillet_max_radius": 2 * MM, "hole_max_diameter": 12 * MM,
              "protrusion_max_size": 10 * MM}
WALL = (-0.1, -0.1, 0.0, 0.1, 0.1, 0.0)        # büyük duvar (200 mm)


def _by_category(features, category):
    return [f for f in features if f["category"] == category]


# -- betiğin kendisi --------------------------------------------------------

def test_cleanup_script_compiles_and_is_ironpython_safe():
    source = open(CLEANUP, encoding="utf-8").read()
    compile(source, CLEANUP, "exec")
    assert re.search(r"(?<![A-Za-z0-9_])[fF][\"\']", source) is None
    assert ":=" not in source
    assert re.search(r"\)\s*->", source) is None
    assert "nonlocal" not in source


def test_cleanup_never_deletes_geometry():
    """Tarama hiçbir yüzü silmez; sadece kendi eski gruplarını kaldırır."""
    source = open(CLEANUP, encoding="utf-8").read()
    for forbidden in ("RemoveRounds", "Fill.Execute", "DeleteFaces",
                      "Combine.", "FixSmallFaces"):
        assert forbidden not in source, forbidden
    # Delete yalnızca remove_own_groups içinde, grup nesnesi üzerinde
    body = source.split("def remove_own_groups", 1)[1].split("\ndef ", 1)[0]
    assert "CLEANUP_PREFIX" in body
    calls = re.compile(r"\bDelete\s*[.(]")          # Delete.Execute / .Delete()
    assert len(calls.findall(source)) == len(calls.findall(body)) > 0


# -- yardımcılar ------------------------------------------------------------

def test_canonical_direction_fixes_the_sign(sc):
    assert sc["canonical_direction"]((0, 0, -2)) == (0, 0, 1)
    assert sc["canonical_direction"]((0, 0, 0)) is None


def test_same_axis_needs_parallel_and_coincident(sc):
    same = sc["same_axis"]
    a = ((0.0, 0.0, 0.0), Z)
    assert same(a, ((0.0, 0.0, 5.0), Z), 1e-6)
    assert not same(a, ((0.001, 0.0, 0.0), Z), 1e-6)
    assert not same(a, ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)), 1e-6)


@pytest.mark.parametrize("uv", [(math.pi, 0.01), (0.01, math.pi)])
def test_cylinder_span_picks_the_angle_parameter(sc, uv):
    """UV'nin hangi ucunun açı olduğu eksen boyunca uzunluktan anlaşılır."""
    assert sc["cylinder_span"](0.0, 0.003, list(uv), 0.01) == \
        pytest.approx(math.pi)


def test_cylinder_span_without_uv_uses_area(sc):
    r, length = 0.003, 0.01
    area = r * (math.pi / 2) * length
    assert sc["cylinder_span"](area, r, [], length) == pytest.approx(math.pi / 2)


def test_default_thresholds_scale_with_small_parts(sc):
    big = sc["default_thresholds"](1.0, {})
    assert big["fillet_max_radius"] == pytest.approx(2 * MM)
    assert big["hole_max_diameter"] == pytest.approx(12 * MM)
    small = sc["default_thresholds"](0.1, {})          # 100 mm parça
    assert small["fillet_max_radius"] == pytest.approx(1 * MM)
    assert small["auto"]["fillet_max_radius"] is True


def test_given_thresholds_are_honoured(sc):
    given = sc["default_thresholds"](1.0, {"fillet_max_radius": 0.004})
    assert given["fillet_max_radius"] == pytest.approx(0.004)
    assert given["auto"]["fillet_max_radius"] is False


# -- vida noktası ------------------------------------------------------------

def _screw_hole():
    """Duvarda Ø6 kör delik: iki yarım silindir + dip kapağı."""
    r = 3 * MM
    records = [
        plane(0, WALL),
        cylinder(1, r, (0.05, 0.05), -0.01, 0.0, math.pi),
        cylinder(2, r, (0.05, 0.05), -0.01, 0.0, math.pi),
        plane(3, (0.05 - r, 0.05 - r, -0.01, 0.05 + r, 0.05 + r, -0.01)),
    ]
    link(records, 0, 1)
    link(records, 0, 2)
    link(records, 1, 2)
    link(records, 1, 3)
    link(records, 2, 3)
    return records


def test_split_cylinder_halves_make_one_screw_feature(sc):
    features = sc["detect_features"](_screw_hole(), THRESHOLDS)
    screws = _by_category(features, "vida")
    assert len(screws) == 1
    assert screws[0]["faces"] == [1, 2, 3]          # duvar dahil değil
    assert screws[0]["size"] == pytest.approx(6 * MM)
    assert not _by_category(features, "fileto")


def test_large_holes_are_not_screw_points(sc):
    r = 10 * MM                                     # Ø20 > 12
    records = [plane(0, WALL),
               cylinder(1, r, (0.0, 0.0), -0.02, 0.0, 2 * math.pi)]
    assert sc["detect_features"](records, THRESHOLDS) == []


def test_boss_with_hole_and_base_fillet_is_one_feature(sc):
    """Vida kulesi: dış Ø8, iç Ø4, üst halka ve dipteki fileto tek parça."""
    c = (0.02, 0.02)
    records = [
        plane(0, WALL),
        cylinder(1, 4 * MM, c, 0.0, 0.01, 2 * math.pi),        # dış
        cylinder(2, 2 * MM, c, 0.002, 0.01, 2 * math.pi),      # iç delik
        plane(3, (c[0] - 4 * MM, c[1] - 4 * MM, 0.01,
                  c[0] + 4 * MM, c[1] + 4 * MM, 0.01)),        # üst halka
        torus(4, 1 * MM, 5 * MM, c, 0.0, 1 * MM),             # dip fileto
    ]
    for a, b in ((0, 4), (4, 1), (1, 3), (3, 2)):
        link(records, a, b)
    features = sc["detect_features"](records, THRESHOLDS)
    assert len(features) == 1
    feature = features[0]
    assert feature["category"] == "vida"
    assert feature["faces"] == [1, 2, 3, 4]
    assert feature["size"] == pytest.approx(8 * MM)


def test_full_small_pin_is_a_screw_not_a_fillet(sc):
    """Ø2 tam silindir fileto yarıçapının altında ama fileto değildir."""
    records = [plane(0, WALL),
               cylinder(1, 1 * MM, (0.0, 0.0), 0.0, 0.005, 2 * math.pi)]
    link(records, 0, 1)
    features = sc["detect_features"](records, THRESHOLDS)
    assert [f["category"] for f in features] == ["vida"]


# -- fileto ------------------------------------------------------------------

def test_fillet_chain_becomes_one_feature(sc):
    """Aynı yarıçaplı, birbirine bağlı fileto yüzleri tek grup."""
    records = [
        plane(0, WALL),
        cylinder(1, 1 * MM, (0.0, 0.0), 0.0, 0.05, math.pi / 2),
        torus(2, 1 * MM, 20 * MM, (0.3, 0.3), 0.0, 1 * MM),
        cylinder(3, 1.05 * MM, (0.1, 0.0), 0.0, 0.05, math.pi / 2),
    ]
    link(records, 0, 1)
    link(records, 1, 2)
    link(records, 2, 3)
    fillets = _by_category(sc["detect_features"](records, THRESHOLDS), "fileto")
    assert len(fillets) == 1
    assert fillets[0]["faces"] == [1, 2, 3]
    assert fillets[0]["size"] == pytest.approx(1.05 * MM)


def test_fillets_with_different_radii_stay_separate(sc):
    records = [plane(0, WALL),
               cylinder(1, 0.5 * MM, (0.0, 0.0), 0.0, 0.05, math.pi / 2),
               cylinder(2, 1.5 * MM, (0.1, 0.0), 0.0, 0.05, math.pi / 2)]
    link(records, 1, 2)
    fillets = _by_category(sc["detect_features"](records, THRESHOLDS), "fileto")
    assert len(fillets) == 2


def test_large_fillets_are_left_alone(sc):
    records = [plane(0, WALL),
               cylinder(1, 5 * MM, (0.0, 0.0), 0.0, 0.05, math.pi / 2)]
    assert sc["detect_features"](records, THRESHOLDS) == []


def test_fillets_found_even_without_adjacency(sc):
    """Komşuluk okunamasa da her fileto yüzü tek başına bulunur."""
    records = [plane(0, WALL),
               cylinder(1, 1 * MM, (0.0, 0.0), 0.0, 0.05, math.pi / 2),
               cylinder(2, 1 * MM, (0.1, 0.0), 0.0, 0.05, math.pi / 2)]
    fillets = _by_category(sc["detect_features"](records, THRESHOLDS), "fileto")
    assert len(fillets) == 2


# -- küçük çıkıntı -----------------------------------------------------------

def _rib(with_base_fillet=False):
    """Duvarda 6x2x3 mm'lik küçük bir çıkıntı (5 düzlem)."""
    x0, y0 = 0.03, 0.03
    x1, y1, h = x0 + 6 * MM, y0 + 2 * MM, 3 * MM
    records = [
        plane(0, WALL),
        plane(1, (x0, y0, h, x1, y1, h)),                         # üst
        plane(2, (x0, y0, 0, x1, y0, h), normal=(0, 1, 0)),
        plane(3, (x0, y1, 0, x1, y1, h), normal=(0, 1, 0)),
        plane(4, (x0, y0, 0, x0, y1, h), normal=(1, 0, 0)),
        plane(5, (x1, y0, 0, x1, y1, h), normal=(1, 0, 0)),
    ]
    for side in (2, 3, 4, 5):
        link(records, 1, side)
        link(records, 0, side)
    link(records, 2, 4)
    link(records, 2, 5)
    link(records, 3, 4)
    link(records, 3, 5)
    if with_base_fillet:
        records.append(cylinder(6, 0.5 * MM, (x0, y0 - 0.5 * MM), 0.0, 0.5 * MM,
                                math.pi / 2))
        records[6]["bbox"] = (x0, y0 - 0.5 * MM, 0.0, x1, y0, 0.5 * MM)
        link(records, 6, 2)
        link(records, 6, 0)
    return records


def test_small_rib_is_found_as_a_protrusion(sc):
    features = sc["detect_features"](_rib(), THRESHOLDS)
    bumps = _by_category(features, "cikinti")
    assert len(bumps) == 1
    assert bumps[0]["faces"] == [1, 2, 3, 4, 5]
    assert bumps[0]["size"] < 10 * MM


def test_fillet_at_the_base_goes_with_the_protrusion(sc):
    """Çıkıntının dibindeki fileto ayrı bulgu olmamalı; birlikte silinir."""
    features = sc["detect_features"](_rib(with_base_fillet=True), THRESHOLDS)
    assert [f["category"] for f in features] == ["cikinti"]
    assert 6 in features[0]["faces"]


def test_protrusion_limit_is_respected(sc):
    smaller = dict(THRESHOLDS, protrusion_max_size=3 * MM)
    assert _by_category(sc["detect_features"](_rib(), smaller), "cikinti") == []


def test_coplanar_imprints_are_not_protrusions(sc):
    """Aynı düzlemdeki bölünmüş yüzler iz (imprint) - çıkıntı değil."""
    records = [plane(0, WALL),
               plane(1, (0.0, 0.0, 0.0, 0.004, 0.004, 0.0)),
               plane(2, (0.004, 0.0, 0.0, 0.008, 0.004, 0.0))]
    link(records, 1, 2)
    link(records, 0, 1)
    diag = {}
    assert sc["detect_features"](records, THRESHOLDS, diag) == []
    assert diag.get("cikinti_iz") == 1


def test_category_selection(sc):
    records = _rib(with_base_fillet=True)
    only_fillets = sc["detect_features"](records, THRESHOLDS, None, ["fileto"])
    assert [f["category"] for f in only_fillets] == ["fileto"]


# -- kullanıcı grupları ve isimlendirme -------------------------------------

def test_features_touching_user_groups_are_skipped(sc):
    """İnlet grubundaki bir yüzü silmek o grubu bozar: işaretlenmez."""
    records = [plane(0, WALL),
               cylinder(1, 1 * MM, (0.0, 0.0), 0.0, 0.05, math.pi / 2,
                        groups=["inlet"]),
               cylinder(2, 1 * MM, (0.1, 0.0), 0.0, 0.05, math.pi / 2)]
    features = sc["detect_features"](records, THRESHOLDS)
    kept, skipped = sc["split_protected"](features, records)
    assert [f["faces"] for f in kept] == [[2]]
    assert len(skipped) == 1 and "inlet" in skipped[0]["reason"]


def test_names_are_sorted_small_first_and_capped(sc):
    features = [
        {"category": "fileto", "faces": [i], "size": s * MM, "extent": 0.0,
         "center": (0, 0, 0), "bbox": None}
        for i, s in enumerate((1.5, 0.5, 1.0))
    ]
    features.append({"category": "vida", "faces": [9], "size": 6 * MM,
                     "extent": 0.0, "center": (0, 0, 0), "bbox": None})
    named = sc["name_features"](features, max_groups=2)
    fillets = [f for f in named if f["category"] == "fileto"]
    assert [f["name"] for f in fillets] == [
        "temizle_fileto_R0p50mm_01", "temizle_fileto_R1p00mm_02", ""]
    screw = [f for f in named if f["category"] == "vida"][0]
    assert screw["name"] == "temizle_vida_D6p00mm_01"


def test_summary_counts(sc):
    named = [{"category": "fileto"}, {"category": "fileto"},
             {"category": "vida"}]
    assert sc["summary"](named) == {"fileto": 2, "vida": 1, "cikinti": 0}


# ==========================================================================
# SpaceClaim API katmanı - sahte nesnelerle uçtan uca
# ==========================================================================

class V:
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


class Named:
    def __init__(self, name):
        self.Name = name

    def GetType(self):
        return self


class Geom:
    def __init__(self, kind, **attrs):
        self._kind = Named(kind)
        for key, value in attrs.items():
            setattr(self, key, value)

    def GetType(self):
        return self._kind


class Box:
    def __init__(self, box):
        self.MinCorner = V(*box[:3])
        self.MaxCorner = V(*box[3:])


class Range:
    def __init__(self, span):
        self.Span = span


class Shape:
    def __init__(self, geometry, area, box, uv=None):
        self.Geometry = geometry
        self.Area = area
        self._box = box
        if uv:
            self.BoxUV = type("UV", (), {"RangeU": Range(uv[0]),
                                         "RangeV": Range(uv[1])})()

    def GetBoundingBox(self, _matrix):
        return Box(self._box)


class Face:
    def __init__(self, geometry, box, area=1e-6, uv=None):
        self.Shape = Shape(geometry, area, box, uv)
        self.Area = area
        self.Edges = []


class Edge:
    def __init__(self, faces):
        self.Faces = faces


def connect(a, b):
    edge = Edge([a, b])
    a.Edges.append(edge)
    b.Edges.append(edge)


class Body:
    def __init__(self, faces, box):
        self.Faces = faces
        self.Shape = Shape(None, 0.0, box)


class Group:
    def __init__(self, name, members):
        self.name = name
        self.Members = members
        self.deleted = False

    def GetName(self):
        return self.name

    def SetName(self, value):
        self.name = value

    def Delete(self):
        self.deleted = True


class FakeSpaceClaim:
    """SpaceClaim'in betiğin kullandığı küçük parçası."""

    def __init__(self, bodies, groups=(), writes=True):
        self.writes = writes
        self.bodies = bodies
        self.groups = list(groups)
        self.saved = []
        self.opened = []

    def install(self, ns):
        app = self
        ns["Matrix"] = type("M", (), {"Identity": None})
        ns["DocumentOpen"] = type("DO", (), {"Execute": staticmethod(
            lambda path: app.opened.append(path))})
        def save(path, *_options):
            app.saved.append(path)
            if app.writes:
                open(path, "w").write("scdoc")

        ns["DocumentSave"] = type("DS", (), {"Execute": staticmethod(save)})

        class Part:
            Bodies = app.bodies

            def GetAllNamedSelections(self):
                return [g for g in app.groups if not g.deleted]

        ns["GetRootPart"] = lambda: Part()

        class Selection:
            @staticmethod
            def Create(items):
                return list(items)

            @staticmethod
            def Empty():
                return []

        class NamedSelection:
            @staticmethod
            def Create(selection, _secondary=None):
                group = Group("Group1", selection)
                app.groups.append(group)
                return type("R", (), {"CreatedNamedSelection": group})()

        ns["Selection"] = Selection
        ns["NamedSelection"] = NamedSelection

    def names(self):
        return [g.name for g in self.groups if not g.deleted]


def _model():
    """Duvar + Ø6 vida deliği (iki yarım + dip) + R1 fileto."""
    r = 3 * MM
    wall = Face(Geom("Plane", Frame=type("F", (), {"DirZ": V(0, 0, 1),
                                                   "Origin": V(0, 0, 0)})()),
                WALL, area=0.04)
    axis = type("A", (), {"Origin": V(0.05, 0.05, 0), "Direction": V(0, 0, -1)})()
    hole_box = (0.05 - r, 0.05 - r, -0.01, 0.05 + r, 0.05 + r, 0.0)
    half_a = Face(Geom("Cylinder", Radius=r, Axis=axis), hole_box,
                  uv=(math.pi, 0.01))
    half_b = Face(Geom("Cylinder", Radius=r, Axis=axis), hole_box,
                  uv=(math.pi, 0.01))
    bottom = Face(Geom("Plane", Frame=type("F", (), {"DirZ": V(0, 0, -1),
                                                     "Origin": V(0, 0, 0)})()),
                  (0.05 - r, 0.05 - r, -0.01, 0.05 + r, 0.05 + r, -0.01))
    fillet_axis = type("A", (), {"Origin": V(-0.05, 0, 0.001),
                                 "Direction": V(0, 1, 0)})()
    fillet = Face(Geom("Cylinder", Radius=1 * MM, Axis=fillet_axis),
                  (-0.051, -0.05, 0.0, -0.05, 0.05, 0.001), uv=(math.pi / 2, 0.1))
    for a, b in ((wall, half_a), (wall, half_b), (half_a, half_b),
                 (half_a, bottom), (half_b, bottom), (wall, fillet)):
        connect(a, b)
    faces = [wall, half_a, half_b, bottom, fillet]
    return faces, Body(faces, (-0.1, -0.1, -0.01, 0.1, 0.1, 0.01))


def _scan(sc, fake, export=None, **params):
    import tempfile

    fake.install(sc)
    if export is None:
        export = os.path.join(tempfile.mkdtemp(), "parca_temizlik.scdoc")
    base = {"input": "M:/CAD/parca.scdoc", "export": export}
    base.update(params)
    return sc["cleanup_scan"](base)


def test_face_record_reads_axis_span_and_radius(sc):
    faces, _body = _model()
    sc["Matrix"] = type("M", (), {"Identity": None})
    record = sc["face_record"](faces[1], 1, 0)
    assert record["kind"] == "Cylinder"
    assert record["radius"] == pytest.approx(3 * MM)
    assert record["axis"][1] == (0.0, 0.0, 1.0)          # işaret sabitlendi
    assert record["span"] == pytest.approx(math.pi)


def test_scan_marks_features_and_saves_a_copy(sc, tmp_path):
    faces, body = _model()
    fake = FakeSpaceClaim([body])
    export = str(tmp_path / "parca_temizlik.scdoc")
    result = _scan(sc, fake, export)

    assert result["summary"] == {"fileto": 1, "vida": 1, "cikinti": 0}
    names = fake.names()
    assert "temizle_vida_D6p00mm_01" in names
    assert "temizle_fileto_R1p00mm_01" in names
    assert "temizle_vida_HEPSI" in names and "temizle_fileto_HEPSI" in names
    screw = [f for f in result["features"] if f["category"] == "vida"][0]
    assert screw["face_count"] == 3 and screw["created"]
    # kopyaya kaydedildi, kaynağa değil
    assert fake.saved == [export]
    assert result["saved_path"] == export
    assert result["diagnostics"]["kaydetme"] == "DocumentSave"
    assert result["diagnostics"]["komsuluk"] == "kimlik"


def test_scan_leaves_user_groups_alone(sc):
    faces, body = _model()
    inlet = Group("inlet", [faces[4]])                     # fileto inlet'te
    fake = FakeSpaceClaim([body], groups=[inlet])
    result = _scan(sc, fake)

    assert not inlet.deleted and inlet.name == "inlet"
    assert inlet.Members == [faces[4]]
    assert result["summary"]["fileto"] == 0
    assert any("inlet" in s["reason"] for s in result["skipped"])


def test_rescan_replaces_only_its_own_old_groups(sc):
    faces, body = _model()
    old = Group("temizle_fileto_R1p00mm_01", [faces[4]])
    user = Group("outlet", [faces[0]])
    fake = FakeSpaceClaim([body], groups=[old, user])
    result = _scan(sc, fake)
    assert old.deleted
    assert not user.deleted
    assert result["diagnostics"]["eski_temizlik_grubu_silindi"] == 1


def test_scan_without_group_creation_only_reports(sc):
    faces, body = _model()
    fake = FakeSpaceClaim([body])
    result = _scan(sc, fake, create_groups=False)
    assert fake.names() == []
    assert result["summary"]["vida"] == 1


def test_adjacency_falls_back_to_edge_signatures(sc):
    """Nesne kimliği eşleşmezse kenar orta noktasıyla komşuluk kurulur."""
    point = V(0.0, 0.0, 0.0)

    class SigEdge:
        def __init__(self):
            self.Faces = []                                 # kimlik yok
            self.Shape = type("S", (), {
                "EvalMid": lambda _self: type("E", (), {"Point": point})(),
                "Length": 0.01})()

    a = type("F", (), {"Edges": [SigEdge()]})()
    b = type("F", (), {"Edges": [SigEdge()]})()
    records = [{"neighbors": []}, {"neighbors": []}]
    diag = {}
    sc["build_adjacency"]([a, b], records, diag)
    assert diag["komsuluk"] == "imza"
    assert records[0]["neighbors"] == [1] and records[1]["neighbors"] == [0]


# -- dayanıklılık -----------------------------------------------------------

def test_faces_without_a_bounding_box_do_not_crash_the_scan(sc):
    """Bir yüzün kutusu okunamazsa tarama çökmemeli, o yüz atlanmalı."""
    records = _screw_hole()
    records.append(_rec(4, "Cylinder", None, radius=2 * MM,
                        axis=((0.0, 0.0, 0.0), Z), span=2 * math.pi))
    records.append(_rec(5, "Plane", None, normal=Z))
    features = sc["detect_features"](records, THRESHOLDS)
    assert [f["faces"] for f in features if f["category"] == "vida"] == [[1, 2, 3]]


def test_distant_plane_on_the_same_axis_is_not_a_cap(sc):
    """Aynı eksende ama delikten uzaktaki düzlem kapak sayılmaz."""
    records = _screw_hole()
    r = 3 * MM
    records.append(plane(4, (0.05 - r, 0.05 - r, -0.08, 0.05 + r, 0.05 + r, -0.08)))
    screws = _by_category(sc["detect_features"](records, THRESHOLDS), "vida")
    assert screws[0]["faces"] == [1, 2, 3]


def test_span_is_read_even_when_the_box_is_not(sc):
    faces, _body = _model()
    sc["Matrix"] = type("M", (), {"Identity": None})

    def no_box(_matrix):
        raise RuntimeError("kutu yok")

    faces[1].Shape.GetBoundingBox = no_box
    record = sc["face_record"](faces[1], 1, 0)
    assert record["bbox"] is None
    assert record["span"] == pytest.approx(math.pi)


def test_grid_returns_nearby_records_only(sc):
    near = plane(0, (0.0, 0.0, 0.0, 0.001, 0.001, 0.0))
    far = plane(1, (0.5, 0.5, 0.0, 0.501, 0.501, 0.0))
    grid = sc["SpatialGrid"]([near, far], 0.01)
    found = [r["index"] for r in grid.near((0.0, 0.0, 0.0), 0.02)]
    assert found == [0]


def test_coaxial_faces_with_float_noise_still_cluster(sc):
    """CAD'den gelen yarım silindirlerin ekseni birkaç nm farklı olabilir."""
    records = _screw_hole()
    records[2]["axis"] = ((0.05 + 2e-9, 0.05 - 3e-9, 0.0), Z)
    screws = _by_category(sc["detect_features"](records, THRESHOLDS), "vida")
    assert len(screws) == 1 and screws[0]["faces"] == [1, 2, 3]


def test_silent_save_failure_is_reported_not_claimed(sc, tmp_path):
    """SpaceClaim hata vermeden dosya yazmazsa kopya 'kaydedildi' denmemeli."""
    faces, body = _model()
    fake = FakeSpaceClaim([body], writes=False)
    result = _scan(sc, fake, str(tmp_path / "x_temizlik.scdoc"))
    assert "saved_path" not in result
    assert any("diske yazilamadi" in w for w in result["warnings"])
    assert result["diagnostics"]["kaydetme"] == "yok"


def test_save_found_under_another_extension(sc, tmp_path):
    """.scdoc istendi ama SpaceClaim .scdocx yazdıysa o dosya kullanılır."""
    other = tmp_path / "x_temizlik.scdocx"
    other.write_text("x")
    assert sc["_find_written"](str(tmp_path / "x_temizlik.scdoc")) == str(other)
