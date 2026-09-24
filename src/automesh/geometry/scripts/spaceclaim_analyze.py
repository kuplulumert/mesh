# -*- coding: utf-8 -*-
"""SpaceClaim icinde calisan geometri analiz betigi.

Bu dosya AutoMesh tarafindan SpaceClaim'e su sekilde verilir:

    SpaceClaim.exe /Headless=True /Splash=False /ExitAfterScript=True
                   /RunScript="...\\spaceclaim_analyze.py" /ScriptAPI=251

SpaceClaim betikleri IronPython 2.7 ile calisir; bu yuzden burada f-string,
type hint veya Python 3'e ozgu hicbir sey kullanilmaz.  SpaceClaim API'si
tum uzunluklari METRE cinsinden verir, cikti da metre cinsindendir.

Parametreler AutoMesh tarafindan dosyanin basina enjekte edilen
``AUTOMESH_PARAMS_FILE`` degiskeninden ya da ``AUTOMESH_SC_PARAMS`` ortam
degiskeninden okunur.
"""

import os
import sys
import math
import traceback

try:
    import json as _json
except ImportError:                                   # cok eski IronPython
    _json = None

MAX_FACES = 400000        # cok buyuk modellerde donmayi engelle
MAX_EDGES = 800000


# ---------------------------------------------------------------- yardimcilar

def _encode(value, indent=0):
    """json modulu yoksa devreye giren minik serilestirici."""
    pad = "  " * indent
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, long)):                # noqa: F821 (IronPython 2)
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return "null"
        return repr(value)
    if isinstance(value, dict):
        items = []
        for key in value:
            items.append('%s  "%s": %s' % (pad, key, _encode(value[key], indent + 1)))
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if isinstance(value, (list, tuple)):
        items = [pad + "  " + _encode(v, indent + 1) for v in value]
        return "[\n" + ",\n".join(items) + "\n" + pad + "]"
    text = unicode(value) if str is bytes else str(value)   # noqa: F821
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\n", "\\n").replace("\r", "")
    return '"%s"' % text


def dump_json(obj, path):
    if _json is not None:
        text = _json.dumps(obj, indent=2)
    else:
        text = _encode(obj)
    handle = open(path, "w")
    try:
        handle.write(text)
    finally:
        handle.close()


def load_json(path):
    handle = open(path, "r")
    try:
        text = handle.read()
    finally:
        handle.close()
    if _json is not None:
        return _json.loads(text)
    raise RuntimeError("IronPython'da json modulu yok, parametreler okunamadi")


def read_params():
    path = ""
    try:
        path = AUTOMESH_PARAMS_FILE                   # noqa: F821 - enjekte edilir
    except NameError:
        path = os.environ.get("AUTOMESH_SC_PARAMS", "")
    if not path or not os.path.isfile(path):
        raise RuntimeError("Parametre dosyasi bulunamadi: %r" % (path,))
    return load_json(path)


def safe(getter, default=0.0):
    try:
        value = getter()
    except Exception:
        return default
    if value is None:
        return default
    return value


def percentile(values, q):
    data = sorted([v for v in values if v is not None and v > 0])
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    pos = (len(data) - 1) * q
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return data[low]
    return data[low] + (data[high] - data[low]) * (pos - low)


# ---------------------------------------------------------------- API sarmali

def iter_bodies(part):
    """Assembly agacindaki tum design body'leri dolas."""
    bodies = []
    try:
        for body in part.GetDescendants[IDesignBody]():          # noqa: F821
            bodies.append(body)
    except Exception:
        # Eski API surumleri: sadece dogrudan cocuklar
        try:
            for body in part.Bodies:
                bodies.append(body)
        except Exception:
            pass
        try:
            for component in part.Components:
                for body in component.Content.Bodies:
                    bodies.append(body)
        except Exception:
            pass
    return bodies


def body_bbox(shape):
    """(xmin, ymin, zmin, xmax, ymax, zmax) - metre."""
    box = None
    for attempt in range(3):
        try:
            if attempt == 0:
                box = shape.GetBoundingBox(Matrix.Identity)      # noqa: F821
            elif attempt == 1:
                box = shape.GetBoundingBox(Matrix.Identity, False)  # noqa: F821
            else:
                box = shape.BoundingBox
            if box is not None:
                break
        except Exception:
            box = None
    if box is None:
        return None
    try:
        lo, hi = box.MinCorner, box.MaxCorner
        return (lo.X, lo.Y, lo.Z, hi.X, hi.Y, hi.Z)
    except Exception:
        return None


def geometry_kind(face):
    try:
        return face.Geometry.GetType().Name
    except Exception:
        return "Unknown"


def face_radius(face):
    """Silindir/kure/koni/torus yuzeyinin egrilik yaricapi (metre)."""
    geom = None
    try:
        geom = face.Geometry
    except Exception:
        return 0.0
    for attr in ("Radius", "MinorRadius"):
        try:
            value = getattr(geom, attr)
            if value and value > 0:
                return float(value)
        except Exception:
            continue
    # Koni: yaricap ekseni boyunca degisir, yuzey alanindan kabaca tahmin et
    try:
        if geom.GetType().Name == "Cone":
            return float(geom.Radius)
    except Exception:
        pass
    return 0.0


# ------------------------------------------------- yuzey siniflandirma

def design_faces(design_body):
    """Secim yapilabilir DesignFace listesi.

    ``body.Shape.Faces`` geometrik Face doner ve secime uygun degildir;
    named selection icin DesignFace gerekir.
    """
    try:
        return list(design_body.Faces)
    except Exception:
        return []


def face_area(design_face):
    for getter in (lambda: design_face.Area,
                   lambda: design_face.Shape.Area):
        try:
            value = getter()
            if value and value > 0:
                return float(value)
        except Exception:
            continue
    return 0.0


def face_geometry(design_face):
    for getter in (lambda: design_face.Shape.Geometry,
                   lambda: design_face.Geometry):
        try:
            value = getter()
            if value is not None:
                return value
        except Exception:
            continue
    return None


def geometry_name(geometry):
    try:
        return geometry.GetType().Name
    except Exception:
        return "Unknown"


def geometry_radius(geometry):
    """Silindir/kure/koni/torus yariçapi (metre); duzlemde 0."""
    if geometry is None:
        return 0.0
    for attr in ("Radius", "MinorRadius"):
        try:
            value = getattr(geometry, attr)
            if value and value > 0:
                return float(value)
        except Exception:
            continue
    return 0.0


def face_perimeter(design_face):
    """Yuzeyin cevresi (metre) - dar bant / sliver olcusu icin."""
    total = 0.0
    for getter in (lambda: design_face.Edges,
                   lambda: design_face.Shape.Edges):
        try:
            edges = list(getter())
        except Exception:
            continue
        for edge in edges:
            try:
                length = edge.Length
            except Exception:
                try:
                    length = edge.Shape.Length
                except Exception:
                    length = 0.0
            if length and length > 0:
                total += float(length)
        if total > 0:
            return total
    return 0.0


def band_key(value, anchor, factor):
    """Degeri kat-kat bantlara ayir (bant indeksi)."""
    if value <= 0 or anchor <= 0:
        return None
    ratio = value / anchor
    index = 0
    while ratio >= factor:
        ratio = ratio / factor
        index += 1
    while ratio < 1.0 and index > -20:
        ratio = ratio * factor
        index -= 1
    return index


def format_size(value_m):
    """0.0008 -> '0p80mm' (isimde nokta kullanilamaz)."""
    mm = value_m * 1000.0
    # 0.1 mm ve ustu mm olarak okunur (mesh dilinde dogal olan bu);
    # daha kucugu mikrona cevrilir ki "0p05mm" gibi okunmaz isimler cikmasin.
    if mm >= 0.1:
        text = "%.2f" % mm
        unit = "mm"
    else:
        text = "%.1f" % (mm * 1000.0)
        unit = "um"
    return text.replace(".", "p") + unit


def required_size(design_face, body_gap, params):
    """Bir yuzeyin cozulebilmesi icin gereken hucre boyutu ve sebebi.

    Uc olcut ayri ayri hesaplanir, en zorlayici olan kazanir:

    ``curvature``  cevre boyunca N hucre  -> 2*pi*r / N
    ``width``      dar bant / sliver      -> (2*alan/cevre) / N
    ``gap``        ince kesit             -> (2*Hacim/Alan) / N

    Boylece isimlendirme ve boyut, yuzeyin tipinden degil olculen
    buyukluklerden cikar.
    """
    cells_circle = float(params.get("cells_per_circle", 16.0))
    cells_width = float(params.get("cells_across_width", 3.0))
    cells_gap = float(params.get("cells_across_gap", 3.0))

    area = face_area(design_face)
    geometry = face_geometry(design_face)
    kind = geometry_name(geometry).lower()
    radius = geometry_radius(geometry)

    candidates = []
    if radius > 0 and cells_circle > 0:
        candidates.append((2.0 * math.pi * radius / cells_circle, "curv"))

    perimeter = face_perimeter(design_face)
    width = 0.0
    if area > 0 and perimeter > 0:
        width = 2.0 * area / perimeter
        if cells_width > 0:
            candidates.append((width / cells_width, "width"))

    if body_gap > 0 and cells_gap > 0:
        candidates.append((body_gap / cells_gap, "gap"))

    if not candidates:
        return 0.0, "", kind, radius, width

    candidates.sort()
    size, driver = candidates[0]
    return size, driver, kind, radius, width


def named_selection_list():
    """Dokumandaki mevcut named selection'lari bul (bozmadan)."""
    accessors = (
        lambda: GetRootPart().GetAllNamedSelections(),          # noqa: F821
        lambda: GetRootPart().GetDescendants[INamedSelection](),  # noqa: F821
        lambda: GetRootPart().Document.NamedSelections,         # noqa: F821
        lambda: Window.ActiveWindow.Document.NamedSelections,   # noqa: F821
        lambda: GetRootPart().Groups,                           # noqa: F821
    )
    for accessor in accessors:
        try:
            found = list(accessor())
            if found is not None:
                return found
        except Exception:
            continue
    return []


def named_selection_faces(named_selection):
    """Bir named selection'in icindeki DesignFace'ler."""
    accessors = (
        lambda: named_selection.Members,
        lambda: named_selection.GetMembers[IDesignFace](),      # noqa: F821
        lambda: named_selection.Members.Faces,
        lambda: named_selection.Faces,
    )
    for accessor in accessors:
        try:
            members = list(accessor())
        except Exception:
            continue
        faces = []
        for member in members:
            # Sadece yuzeyleri al; govde/kenar iceren gruplari atla.
            if face_area(member) > 0 and face_geometry(member) is not None:
                faces.append(member)
        if faces:
            return faces
    return []


def describe_existing_groups(params, body_gap_lookup, default_gap):
    """Kullanicinin kendi gruplarini oku ve onlara boyut oner.

    Bu gruplar **degistirilmez**: adi, uyeleri, hicbiri.  Yalnizca
    icerdikleri yuzeyler olculup uygun bir hucre boyutu onerilir.
    """
    prefix = params.get("group_prefix", "automesh")
    groups = []
    for named_selection in named_selection_list():
        try:
            name = named_selection.GetName()
        except Exception:
            try:
                name = named_selection.Name
            except Exception:
                continue
        name = str(name)
        if name.startswith(prefix):
            continue                      # bizim urettiklerimiz
        if name.startswith(("temizle_", "envanter_")):
            continue                      # temizlik taramasinin isaretleri
        faces = named_selection_faces(named_selection)
        if not faces:
            continue

        sizes = []
        radii = []
        widths = []
        areas = []
        drivers = {}
        kinds = {}
        for design_face in faces:
            size, driver, kind, radius, width = required_size(
                design_face, default_gap, params)
            if size > 0:
                sizes.append(size)
                drivers[driver] = drivers.get(driver, 0) + 1
            if radius > 0:
                radii.append(radius)
            if width > 0:
                widths.append(width)
            area = face_area(design_face)
            if area > 0:
                areas.append(area)
            kinds[kind] = kinds.get(kind, 0) + 1

        if not sizes:
            continue
        dominant_driver = ""
        best = 0
        for key in drivers:
            if drivers[key] > best:
                best, dominant_driver = drivers[key], key
        dominant_kind = ""
        best = 0
        for key in kinds:
            if kinds[key] > best:
                best, dominant_kind = kinds[key], key

        groups.append({
            "name": name,
            "kind": dominant_kind if len(kinds) == 1 else "mixed",
            "driver": dominant_driver,
            "source": "existing",
            "face_count": len(faces),
            "recommended_size": min(sizes),
            "min_radius": min(radii) if radii else 0.0,
            "max_radius": max(radii) if radii else 0.0,
            "representative_radius": min(radii) if radii else 0.0,
            "min_width": min(widths) if widths else 0.0,
            "min_gap": default_gap,
            "total_area": sum(areas),
            "min_face_size": math.sqrt(min(areas)) if areas else 0.0,
            "created": True,
            "note": "kullanicinin grubu - degistirilmedi",
        })
    return groups


def create_named_selection(faces, name):
    """SpaceClaim'de grup (named selection) olustur."""
    try:
        selection = Selection.Create(faces)                   # noqa: F821
    except Exception:
        try:
            selection = FaceSelection.Create(faces)           # noqa: F821
        except Exception:
            return False, "secim olusturulamadi"

    result = None
    try:
        result = NamedSelection.Create(selection, Selection.Empty())   # noqa: F821
    except Exception:
        try:
            result = NamedSelection.Create(selection)         # noqa: F821
        except Exception:
            return False, traceback.format_exc().splitlines()[-1]

    created = None
    try:
        created = result.CreatedNamedSelection
    except Exception:
        created = None
    if created is None:
        return True, "olusturuldu ama yeniden adlandirilamadi"

    for setter in (lambda: created.SetName(name),
                   lambda: setattr(created, "Name", name)):
        try:
            setter()
            return True, ""
        except Exception:
            continue
    try:
        RenameObject.Execute(created, name)                   # noqa: F821
        return True, ""
    except Exception:
        return True, "ad verilemedi"


def build_face_groups(bodies, params, diagonal, body_gaps=None, diag=None):
    """Yuzeyleri OLCULEN buyukluklere gore grupla ve named selection yaz.

    Gruplama ve isimlendirme yuzey tipine degil, yuzeyin gerektirdigi hucre
    boyutuna ve o boyutu belirleyen olcute (egrilik / dar bant / ince kesit)
    dayanir.  Boylece ad, dogrudan uygulanacak boyutu soyler:
    ``automesh_curv_0p31mm``.
    """
    # Teshis sayaclari: grup cikmadiginda nedenini soyleyebilmek icin.
    if diag is None:
        diag = {}
    diag["faces_seen"] = 0
    diag["with_geometry"] = 0
    diag["with_radius"] = 0
    diag["with_perimeter"] = 0
    diag["with_area"] = 0
    diag["measurable"] = 0
    diag["too_coarse"] = 0
    diag["bands"] = 0
    diag["small_bands"] = 0
    diag["bodies"] = len(bodies)

    if not params.get("group_faces", True):
        diag["reason"] = "gruplama kapali"
        return []
    if diagonal <= 0:
        diag["reason"] = "sinir kutusu olculemedi"
        return []

    prefix = params.get("group_prefix", "automesh")
    factor = float(params.get("band_factor", 2.0))
    anchor = float(params.get("band_anchor", 1.0e-4))
    max_groups = int(params.get("max_groups", 8))
    min_faces = int(params.get("min_faces_per_group", 2))
    # Govde boyutuna gore kaba kalan yuzeyler global boyutla zaten cozulur.
    size_ceiling = diagonal * float(params.get("max_useful_ratio", 0.03))
    body_gaps = body_gaps or {}
    default_gap = 0.0
    if body_gaps:
        values = [v for v in body_gaps.values() if v > 0]
        default_gap = min(values) if values else 0.0

    diag["size_ceiling"] = size_ceiling
    diag["default_gap"] = default_gap

    buckets = {}
    for index, design_body in enumerate(bodies):
        body_gap = body_gaps.get(index, default_gap)
        for design_face in design_faces(design_body):
            diag["faces_seen"] += 1
            if face_geometry(design_face) is not None:
                diag["with_geometry"] += 1
            if face_area(design_face) > 0:
                diag["with_area"] += 1
            if face_perimeter(design_face) > 0:
                diag["with_perimeter"] += 1

            size, driver, kind, radius, width = required_size(
                design_face, body_gap, params)
            if radius > 0:
                diag["with_radius"] += 1
            if size > 0:
                diag["measurable"] += 1
            if size > 0 and size > size_ceiling:
                diag["too_coarse"] += 1
            if size <= 0 or size > size_ceiling:
                continue
            band = band_key(size, anchor, factor)
            if band is None:
                continue
            key = (driver, band)
            bucket = buckets.get(key)
            if bucket is None:
                bucket = {"driver": driver, "faces": [], "sizes": [],
                          "radii": [], "widths": [], "areas": [], "kinds": {}}
                buckets[key] = bucket
            bucket["faces"].append(design_face)
            bucket["sizes"].append(size)
            if radius > 0:
                bucket["radii"].append(radius)
            if width > 0:
                bucket["widths"].append(width)
            area = face_area(design_face)
            if area > 0:
                bucket["areas"].append(area)
            bucket["kinds"][kind] = bucket["kinds"].get(kind, 0) + 1

    diag["bands"] = len(buckets)
    candidates = []
    for key in buckets:
        bucket = buckets[key]
        if len(bucket["faces"]) < min_faces:
            diag["small_bands"] += 1
            continue
        kinds = bucket["kinds"]
        dominant = ""
        best = 0
        for kind in kinds:
            if kinds[kind] > best:
                best, dominant = kinds[kind], kind
        candidates.append({
            "driver": bucket["driver"],
            "faces": bucket["faces"],
            # Bandin en zorlayici degeri belirleyici: ortalama alinsaydi
            # bandin en ince ozelligi cozulmeden kalirdi.
            "recommended_size": min(bucket["sizes"]),
            "kind": dominant if len(kinds) == 1 else "mixed",
            "min_radius": min(bucket["radii"]) if bucket["radii"] else 0.0,
            "max_radius": max(bucket["radii"]) if bucket["radii"] else 0.0,
            "min_width": min(bucket["widths"]) if bucket["widths"] else 0.0,
            "min_gap": body_gaps.get(0, default_gap),
            "total_area": sum(bucket["areas"]),
            "min_face_size": (math.sqrt(min(bucket["areas"]))
                              if bucket["areas"] else 0.0),
        })

    candidates.sort(key=lambda item: item["recommended_size"])
    candidates = candidates[:max_groups]
    diag["candidates"] = len(candidates)
    if not candidates:
        if diag["faces_seen"] == 0:
            diag["reason"] = "hic yuzey okunamadi (DesignFace listesi bos)"
        elif diag["measurable"] == 0:
            diag["reason"] = ("hicbir yuzeyden olcum alinamadi "
                              "(yariçap/cevre/kesit hepsi bos)")
        elif diag["too_coarse"] == diag["measurable"]:
            diag["reason"] = ("tum olcumler global boyutla zaten cozuluyor "
                              "(hepsi ust sinirin uzerinde)")
        elif diag["small_bands"]:
            diag["reason"] = ("bantlarda yeterli yuzey yok "
                              "(min_faces_per_group)")
        else:
            diag["reason"] = "aday bant olusmadi"

    groups = []
    for item in candidates:
        name = "{0}_{1}_{2}".format(prefix, item["driver"],
                                    format_size(item["recommended_size"]))
        ok, note = create_named_selection(item["faces"], name)
        groups.append({
            "name": name,
            "kind": item["kind"],
            "driver": item["driver"],
            "source": "auto",
            "face_count": len(item["faces"]),
            "recommended_size": item["recommended_size"],
            "min_radius": item["min_radius"],
            "max_radius": item["max_radius"],
            "representative_radius": item["min_radius"],
            "min_width": item["min_width"],
            "min_gap": item["min_gap"],
            "total_area": item["total_area"],
            "min_face_size": item["min_face_size"],
            "created": bool(ok),
            "note": note,
        })
    return groups


# ---------------------------------------------------------------- ana analiz

def analyze(params):
    result = {
        "analyzer": "spaceclaim",
        "source_path": params.get("input", ""),
        "bodies": [],
        "warnings": [],
    }

    input_path = params.get("input", "")
    if input_path:
        DocumentOpen.Execute(input_path)                          # noqa: F821

    part = GetRootPart()                                          # noqa: F821
    bodies = iter_bodies(part)
    if not bodies:
        result["warnings"].append("Dokumanda hic kati/yuzey gövdesi bulunamadi.")

    total_volume = 0.0
    total_area = 0.0
    face_count = 0
    edge_count = 0
    closed_count = 0
    curved_faces = 0
    all_face_areas = []
    all_edge_lengths = []
    radii = []
    thin_sections = []
    body_gaps = {}          # govde indeksi -> ince kesit tahmini (m)
    bbox = [None, None, None, None, None, None]

    for body_index, design_body in enumerate(bodies):
        try:
            shape = design_body.Shape
        except Exception:
            continue

        info = {
            "name": safe(lambda: design_body.GetName(), ""),
            "volume": 0.0,
            "area": 0.0,
            "face_count": 0,
            "edge_count": 0,
            "is_solid": True,
            "min_face_area": 0.0,
            "min_edge_length": 0.0,
        }

        volume = float(safe(lambda: shape.Volume, 0.0))
        area = float(safe(lambda: shape.SurfaceArea, 0.0))
        is_closed = bool(safe(lambda: shape.IsClosed, True))
        info["volume"] = volume
        info["area"] = area
        info["is_solid"] = is_closed
        total_volume += volume
        total_area += area
        if is_closed:
            closed_count += 1

        box = body_bbox(shape)
        if box is not None:
            for i in range(3):
                if bbox[i] is None or box[i] < bbox[i]:
                    bbox[i] = box[i]
            for i in range(3, 6):
                if bbox[i] is None or box[i] > bbox[i]:
                    bbox[i] = box[i]

        body_face_areas = []
        try:
            for face in shape.Faces:
                if face_count >= MAX_FACES:
                    result["warnings"].append(
                        "Yuzey sayisi %d sinirini asti, analiz kismi." % MAX_FACES)
                    break
                face_count += 1
                info["face_count"] += 1
                a = float(safe(lambda: face.Area, 0.0))
                if a > 0:
                    body_face_areas.append(a)
                    all_face_areas.append(a)
                kind = geometry_kind(face)
                if kind not in ("Plane", "Unknown"):
                    curved_faces += 1
                    r = face_radius(face)
                    if r > 0:
                        radii.append(r)
        except Exception:
            result["warnings"].append("Yuzey listesi okunamadi: %s" % info["name"])

        body_edge_lengths = []
        try:
            for edge in shape.Edges:
                if edge_count >= MAX_EDGES:
                    break
                edge_count += 1
                info["edge_count"] += 1
                length = float(safe(lambda: edge.Length, 0.0))
                if length > 0:
                    body_edge_lengths.append(length)
                    all_edge_lengths.append(length)
        except Exception:
            pass

        if body_face_areas:
            info["min_face_area"] = min(body_face_areas)
        if body_edge_lengths:
            info["min_edge_length"] = min(body_edge_lengths)
        if area > 0 and volume > 0:
            # ince cidar / kanal yaklasik kalinligi
            gap = 2.0 * volume / area
            thin_sections.append(gap)
            body_gaps[body_index] = gap

        result["bodies"].append(info)

    if bbox[0] is None:
        bbox = [0.0] * 6
    result["bbox"] = {
        "xmin": bbox[0], "ymin": bbox[1], "zmin": bbox[2],
        "xmax": bbox[3], "ymax": bbox[4], "zmax": bbox[5],
    }
    result["volume"] = total_volume
    result["area"] = total_area
    result["body_count"] = len(bodies)
    result["face_count"] = face_count
    result["edge_count"] = edge_count

    # Sliver yuzeylerden etkilenmemek icin dusuk yuzdelik kullanilir.
    min_edge = percentile(all_edge_lengths, 0.02)
    min_face_area = percentile(all_face_areas, 0.02)
    result["min_edge_length"] = min_edge
    result["min_face_size"] = math.sqrt(min_face_area) if min_face_area > 0 else 0.0
    result["min_curvature_radius"] = percentile(radii, 0.05) if radii else 0.0
    result["thinnest_section"] = min(thin_sections) if thin_sections else 0.0

    if face_count:
        result["curved_face_ratio"] = float(curved_faces) / float(face_count)
        mean_area = sum(all_face_areas) / float(len(all_face_areas)) if all_face_areas else 0.0
        if mean_area > 0:
            small = len([a for a in all_face_areas if a < mean_area * 0.01])
            result["small_feature_ratio"] = float(small) / float(face_count)
        else:
            result["small_feature_ratio"] = 0.0
    else:
        result["curved_face_ratio"] = 0.0
        result["small_feature_ratio"] = 0.0

    result["watertight"] = bool(bodies) and closed_count == len(bodies)
    result["has_free_edges"] = not result["watertight"]
    result["length_unit_hint"] = "m"

    # ---- yuzey gruplari (named selection) ------------------------------
    diagonal = 0.0
    try:
        diagonal = math.sqrt(sum([(bbox[i + 3] - bbox[i]) ** 2 for i in range(3)]))
    except Exception:
        diagonal = 0.0
    groups = []
    group_diag = {}
    try:
        groups = build_face_groups(bodies, params, diagonal, body_gaps,
                                   group_diag)
    except Exception:
        group_diag["reason"] = "hata: %s" % traceback.format_exc().splitlines()[-1]
        result["warnings"].append(
            "Yuzey gruplama basarisiz: %s" % traceback.format_exc().splitlines()[-1])

    # Kullanicinin kendi gruplari: okunur, boyut onerilir, ASLA degistirilmez.
    if params.get("read_existing_groups", True):
        try:
            default_gap = min(thin_sections) if thin_sections else 0.0
            existing = describe_existing_groups(params, body_gaps, default_gap)
            if existing:
                groups = list(existing) + list(groups)
        except Exception:
            result["warnings"].append(
                "Mevcut named selection'lar okunamadi: %s"
                % traceback.format_exc().splitlines()[-1])
    result["face_groups"] = groups
    result["face_group_diagnostics"] = group_diag

    # ---- disari aktarim ------------------------------------------------
    # Named selection olusturduysak STEP ise yaramaz: STEP grup tasimaz.
    # Bu durumda .scdoc kaydedilir; Fluent Meshing onu okuyup gruplari
    # yuzey etiketi olarak alir.
    export_path = params.get("export", "")
    if result.get("face_groups") and export_path:
        root, extension = os.path.splitext(export_path)
        if extension.lower() not in (".scdoc", ".scdocx", ".pmdb"):
            export_path = root + ".scdoc"
            result["warnings"].append(
                "Yuzey gruplari olusturuldugu icin disari aktarim .scdoc "
                "olarak degistirildi (STEP named selection tasimaz).")
    if export_path:
        try:
            folder = os.path.dirname(export_path)
            if folder and not os.path.isdir(folder):
                os.makedirs(folder)
            DocumentSave.Execute(export_path)                     # noqa: F821
            result["exported_path"] = export_path
        except Exception:
            result["warnings"].append(
                "Disari aktarim basarisiz: %s" % traceback.format_exc().splitlines()[-1])
    return result


def main():
    output_path = ""
    try:
        params = read_params()
        output_path = params.get("output", "")
        result = analyze(params)
        result["ok"] = True
    except Exception:
        result = {
            "ok": False,
            "analyzer": "spaceclaim",
            "error": traceback.format_exc(),
        }
        if not output_path:
            output_path = os.environ.get("AUTOMESH_SC_OUTPUT", "")
    if output_path:
        try:
            dump_json(result, output_path)
        except Exception:
            sys.stderr.write(traceback.format_exc())
    else:
        sys.stdout.write(_encode(result))


# SpaceClaim betigi dogrudan calistirir. Testler ayni dosyayi
# AUTOMESH_SC_NO_RUN=1 ile exec edip saf fonksiyonlari dogrular.
# Temizlik betigi bu dosyayi yardimci kutuphane olarak basina ekler ve
# AUTOMESH_SC_LIBRARY = True tanimlar; o zaman analiz calismaz.
if not os.environ.get("AUTOMESH_SC_NO_RUN") and \
        not globals().get("AUTOMESH_SC_LIBRARY"):
    main()
