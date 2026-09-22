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
    bbox = [None, None, None, None, None, None]

    for design_body in bodies:
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
            thin_sections.append(2.0 * volume / area)

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

    # ---- disari aktarim ------------------------------------------------
    export_path = params.get("export", "")
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


main()
