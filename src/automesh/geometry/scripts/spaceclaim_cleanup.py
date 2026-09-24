# -*- coding: utf-8 -*-
"""SpaceClaim icinde calisan temizlik TARAMA betigi.

Hazir akis hacmini tarar ve CFD icin genellikle gereksiz olan kucuk
detaylari bulur:

* ``fileto``  - kucuk yaricapli yuvarlatmalar (silindir/torus/kure seridi)
* ``vida``    - kucuk capli, tam (360 derece) silindirik detaylar: vida
                deligi, vida kulesi (boss), havsa/pah dahil
* ``cikinti`` - akisa etkisi olmayan kucuk cikinti/cepler: kucuk yuzlerden
                olusan, toplam boyutu esigin altinda kalan kumeler

HICBIR SEYI SILMEZ.  Her bulgu icin SpaceClaim'de bir grup (named
selection) olusturur; kullanici Groups panelinde gruba tiklar, yuzler
secilir, Delete'e basar ve SpaceClaim boslugu komsu yuzleri uzatarak
kapatir.  Karar her zaman kullanicidadir.

Kullanicinin kendi named selection'larina ait bir yuz iceren bulgu
isaretlenmez (o grubu bozmasin diye); rapora "atlandi" olarak yazilir.

Bu dosya tek basina calismaz: AutoMesh calistirmadan once basina
``spaceclaim_analyze.py``'yi yardimci kutuphane olarak ekler (olcum,
grup olusturma ve JSON fonksiyonlari oradan gelir).  IronPython 2.7 icin
yazilmistir; uzunluklar METRE cinsindendir.
"""

import math
import os
import traceback

CLEANUP_PREFIX = "temizle_"
INVENTORY_PREFIX = "envanter_"
#: Bu onekle baslayan gruplar bizimdir; kullanici grubu sayilmaz.
OWN_PREFIXES = ("temizle_", "envanter_", "automesh")
#: Envanterde "benzer" sayilan olculerin en fazla goreli farki.
SIMILAR_TOL = 0.05
#: Envanterde bir kategoride en fazla bu kadar benzerlik grubu.
MAX_INVENTORY_GROUPS = 60

FULL_TURN = 2.0 * math.pi
#: Toplam acisi bunun ustundeki es eksenli silindirler "tam" sayilir.
FULL_SPAN_RATIO = 0.9
#: Es eksenlilik icin aci toleransi (radyan) - yaklasik 0.5 derece.
AXIS_ANGLE_TOL = math.radians(0.5)
#: Fileto zincirinde yaricaplarin en fazla bu oranda farkli olmasi.
FILLET_RADIUS_SPREAD = 0.15
#: Tek kategoride en fazla bu kadar ayri grup; fazlasi sadece HEPSI'nde.
DEFAULT_MAX_GROUPS = 80

FILLET_KINDS = ("Cylinder", "Torus", "Sphere")

LABELS = {
    "fileto": "Fileto / round",
    "vida": "Vida noktasi / kucuk delik",
    "cikinti": "Kucuk cikinti / cep",
}


# ---------------------------------------------------------------- vektorler

def v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_scale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def v_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def v_norm(a):
    return math.sqrt(v_dot(a, a))


def v_unit(a):
    n = v_norm(a)
    if n <= 0:
        return None
    return (a[0] / n, a[1] / n, a[2] / n)


def canonical_direction(direction):
    """Yonu birim yap ve isaretini sabitle (ilk sifir olmayan bilesen +)."""
    unit = v_unit(direction)
    if unit is None:
        return None
    for component in unit:
        if abs(component) > 1e-12:
            if component < 0:
                unit = v_scale(unit, -1.0)
            break
    return unit


def point_line_distance(point, origin, direction):
    """Noktanin (origin, direction) dogrusuna uzakligi."""
    return v_norm(v_cross(v_sub(point, origin), direction))


def parallel(a, b, tol=AXIS_ANGLE_TOL):
    if a is None or b is None:
        return False
    return abs(v_dot(a, b)) >= math.cos(tol)


def same_axis(axis_a, axis_b, dist_tol):
    """Iki eksen ayni dogru mu (yon paralel + dogrular cakisik)."""
    if axis_a is None or axis_b is None:
        return False
    origin_a, dir_a = axis_a
    origin_b, dir_b = axis_b
    if not parallel(dir_a, dir_b):
        return False
    return point_line_distance(origin_b, origin_a, dir_a) <= dist_tol


def bbox_corners(box):
    x0, y0, z0, x1, y1, z1 = box
    return [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]


def bbox_diagonal(box):
    if not box:
        return 0.0
    return math.sqrt((box[3] - box[0]) ** 2 + (box[4] - box[1]) ** 2 +
                     (box[5] - box[2]) ** 2)


def bbox_center(box):
    return ((box[0] + box[3]) * 0.5, (box[1] + box[4]) * 0.5,
            (box[2] + box[5]) * 0.5)


def bbox_union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min([b[0] for b in boxes]), min([b[1] for b in boxes]),
            min([b[2] for b in boxes]), max([b[3] for b in boxes]),
            max([b[4] for b in boxes]), max([b[5] for b in boxes]))


def axial_range(box, origin, direction):
    """Kutunun eksen uzerindeki izdusum araligi (min, max)."""
    values = [v_dot(v_sub(corner, origin), direction)
              for corner in bbox_corners(box)]
    return min(values), max(values)


class SpatialGrid(object):
    """Kayitlari sinir kutusu merkezine gore hucrelere koyar.

    Bir detayin yakinindaki yuzleri bulmak icin tum modeli dolasmak yerine
    yalnizca komsu hucrelere bakilir.
    """

    def __init__(self, records, cell):
        self.cell = max(cell, 1e-9)
        self.cells = {}
        for record in records:
            key = self._key(bbox_center(record["bbox"]))
            self.cells.setdefault(key, []).append(record)

    def _key(self, point):
        return (int(math.floor(point[0] / self.cell)),
                int(math.floor(point[1] / self.cell)),
                int(math.floor(point[2] / self.cell)))

    def near(self, point, radius):
        cx, cy, cz = self._key(point)
        reach = int(math.ceil(radius / self.cell))
        found = []
        for ix in range(cx - reach, cx + reach + 1):
            for iy in range(cy - reach, cy + reach + 1):
                for iz in range(cz - reach, cz + reach + 1):
                    bucket = self.cells.get((ix, iy, iz))
                    if bucket:
                        found.extend(bucket)
        return found


def axis_key(axis, cell=1e-4):
    """Eksenin kaba anahtari: yon + eksenin orijine en yakin noktasi."""
    origin, direction = axis
    along = v_dot(origin, direction)
    foot = v_sub(origin, v_scale(direction, along))
    return (round(direction[0], 3), round(direction[1], 3),
            round(direction[2], 3),
            int(math.floor(foot[0] / cell)), int(math.floor(foot[1] / cell)),
            int(math.floor(foot[2] / cell)))


def _neighbour_keys(key):
    dx, dy, dz, fx, fy, fz = key
    for ix in (fx - 1, fx, fx + 1):
        for iy in (fy - 1, fy, fy + 1):
            for iz in (fz - 1, fz, fz + 1):
                yield (dx, dy, dz, ix, iy, iz)


# ---------------------------------------------------------------- olcum

def cylinder_span(area, radius, uv_spans, axial_extent):
    """Silindirik yuzun kapsadigi aci (radyan).

    Once yuzeyin UV araligina bakilir: silindirde parametrelerden biri
    aci, digeri eksen boyunca uzunluktur.  Hangisinin aci oldugunu eksen
    boyunca olculen uzunluga yakin OLMAYAN parametre belirler.  UV yoksa
    alan / (yaricap x uzunluk) kullanilir.
    """
    spans = [float(s) for s in (uv_spans or []) if s and s > 0]
    if len(spans) == 2:
        su, sv = spans
        if axial_extent > 0:
            # uzunluga daha yakin olan uzunluktur, digeri aci
            if abs(su - axial_extent) <= abs(sv - axial_extent):
                angle = sv
            else:
                angle = su
        else:
            angle = su
        if 0 < angle <= FULL_TURN * 1.001:
            return min(angle, FULL_TURN)
    if radius > 0 and axial_extent > 0 and area > 0:
        return max(0.0, min(FULL_TURN, area / (radius * axial_extent)))
    return 0.0


def default_thresholds(diagonal, params):
    """Kullanici 0 verdiyse esikleri geometri boyutundan tut.

    Sabit mm ust sinirlari tipik mekanik parcalar icindir; kucuk bir
    parcada (kosegen 100 mm gibi) esik parca boyutuyla kuculur.
    """
    def pick(key, absolute, fraction):
        given = float(params.get(key, 0.0) or 0.0)
        if given > 0:
            return given, False
        if diagonal > 0:
            return min(absolute, fraction * diagonal), True
        return absolute, True

    fillet, fillet_auto = pick("fillet_max_radius", 0.002, 0.01)
    hole, hole_auto = pick("hole_max_diameter", 0.012, 0.05)
    bump, bump_auto = pick("protrusion_max_size", 0.010, 0.03)
    return {
        "fillet_max_radius": fillet,
        "hole_max_diameter": hole,
        "protrusion_max_size": bump,
        "auto": {"fillet_max_radius": fillet_auto,
                 "hole_max_diameter": hole_auto,
                 "protrusion_max_size": bump_auto},
    }


# ---------------------------------------------------------------- tespit

def _feature(category, faces, size, records):
    boxes = [records[i]["bbox"] for i in faces]
    box = bbox_union(boxes)
    return {
        "category": category,
        "faces": sorted(faces),
        "size": float(size),
        "bbox": box,
        "center": bbox_center(box) if box else (0.0, 0.0, 0.0),
        "extent": bbox_diagonal(box),
    }


def find_hole_features(records, thresholds, claimed):
    """Es eksenli, toplamda tam tur yapan kucuk silindir kumeleri.

    Vida deligi, vida kulesi, havsa (koni) ve dibindeki fileto (torus)
    ayni eksen etrafinda toplandigi icin tek bulgu olarak doner.
    """
    max_radius = thresholds["hole_max_diameter"] * 0.5
    cylinders = [r for r in records
                 if r["index"] not in claimed and r["kind"] == "Cylinder"
                 and r.get("axis") is not None and r.get("bbox")
                 and 0 < r["radius"] <= max_radius * 1.0000001]
    # Kapak/pah/fileto adaylari: yalnizca kucuk yuzler.  Her kume icin tum
    # modeli dolasmak buyuk modelde IronPython'da dakikalar surerdi.
    pool_limit = 6.0 * max_radius + 4.0 * thresholds["fillet_max_radius"]
    pool = [r for r in records
            if r.get("bbox") and r["kind"] in ("Plane", "Cone", "Torus", "Cylinder")
            and bbox_diagonal(r["bbox"]) <= pool_limit]
    grid = SpatialGrid(pool, pool_limit)

    clusters = []
    by_key = {}
    for record in cylinders:
        placed = False
        tol = max(1e-6, 1e-3 * record["radius"])
        key = axis_key(record["axis"])
        for near_key in _neighbour_keys(key):
            for cluster in by_key.get(near_key, []):
                if same_axis(cluster["axis"], record["axis"], tol):
                    cluster["members"].append(record)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            cluster = {"axis": record["axis"], "members": [record]}
            clusters.append(cluster)
            by_key.setdefault(key, []).append(cluster)

    features = []
    for cluster in clusters:
        # Her yaricap seviyesinde toplam aci; biri tam tursa detay tamdir.
        by_radius = []
        for record in cluster["members"]:
            for level in by_radius:
                if abs(level["radius"] - record["radius"]) <= \
                        1e-3 * record["radius"] + 1e-9:
                    level["span"] += record.get("span", 0.0)
                    level["faces"].append(record["index"])
                    break
            else:
                by_radius.append({"radius": record["radius"],
                                  "span": record.get("span", 0.0),
                                  "faces": [record["index"]]})
        full = [lvl for lvl in by_radius
                if lvl["span"] >= FULL_TURN * FULL_SPAN_RATIO]
        if not full:
            continue                          # kismi silindir: fileto/yuva

        origin, direction = cluster["axis"]
        faces = []
        for lvl in full:
            faces.extend(lvl["faces"])
        r_max = max([lvl["radius"] for lvl in full])
        lo, hi = None, None
        for index in faces:
            a, b = axial_range(records[index]["bbox"], origin, direction)
            lo = a if lo is None else min(lo, a)
            hi = b if hi is None else max(hi, b)
        margin = r_max
        cluster_box = bbox_union([records[i]["bbox"] for i in faces])
        near_center = bbox_center(cluster_box)
        near_limit = bbox_diagonal(cluster_box) + pool_limit

        # Ayni eksendeki koni (pah/havsa), torus (fileto) ve kapak duzlemleri
        dist_tol = max(1e-6, 0.05 * r_max)
        for record in grid.near(near_center, near_limit):
            index = record["index"]
            if index in claimed or index in faces:
                continue
            kind = record["kind"]
            box = record["bbox"]
            if v_norm(v_sub(bbox_center(box), near_center)) > near_limit:
                continue
            a, b = axial_range(box, origin, direction)
            if b < lo - margin or a > hi + margin:
                continue
            if kind in ("Cone", "Torus") and record.get("axis") is not None:
                if not same_axis(cluster["axis"], record["axis"], dist_tol):
                    continue
                if kind == "Torus" and record.get("major_radius", 0.0) > \
                        2.0 * r_max + 2.0 * thresholds["fillet_max_radius"]:
                    continue
                if bbox_diagonal(box) > 4.0 * r_max * 1.5:
                    continue
                faces.append(index)
            elif kind == "Cylinder" and record.get("axis") is not None and \
                    same_axis(cluster["axis"], record["axis"], dist_tol) and \
                    record["radius"] <= max_radius:
                faces.append(index)           # ayni eksende kismi parca
            elif kind == "Plane" and record.get("normal") is not None:
                if not parallel(record["normal"], direction):
                    continue
                center = bbox_center(box)
                if point_line_distance(center, origin, direction) > dist_tol:
                    continue
                if bbox_diagonal(box) > 1.5 * 2.0 * r_max * 1.5:
                    continue
                faces.append(index)

        for index in faces:
            claimed.add(index)
        features.append(_feature("vida", faces, 2.0 * r_max, records))
    return features


def _components(indices, records):
    """Verilen yuzlerin komsuluk uzerinden bagli bilesenleri."""
    pool = set(indices)
    seen = set()
    groups = []
    for start in indices:
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        group = []
        while stack:
            current = stack.pop()
            group.append(current)
            for other in records[current].get("neighbors", []):
                if other in pool and other not in seen:
                    seen.add(other)
                    stack.append(other)
        groups.append(group)
    return groups


def is_fillet_candidate(record, thresholds):
    kind = record["kind"]
    limit = thresholds["fillet_max_radius"] * 1.0000001
    if kind not in FILLET_KINDS or record["radius"] <= 0:
        return False
    if record["radius"] > limit:
        return False
    if kind == "Cylinder":
        # tam silindir fileto degildir (vida/pim); UV yoksa kabul et
        span = record.get("span", 0.0)
        return span <= 0 or span < FULL_TURN * FULL_SPAN_RATIO
    return True


def _coplanar(records, indices):
    """Hepsi ayni duzlemde duzlemsel yuz mu (iz/imprint, cikinti degil)."""
    first = None
    for index in indices:
        record = records[index]
        if record["kind"] != "Plane" or record.get("normal") is None:
            return False
        normal = record["normal"]
        offset = v_dot(bbox_center(record["bbox"]), normal)
        if first is None:
            first = (normal, offset)
            continue
        if not parallel(first[0], normal):
            return False
        sign = 1.0 if v_dot(first[0], normal) > 0 else -1.0
        if abs(first[1] - sign * offset) > 1e-6:
            return False
    return True


def find_protrusion_features(records, thresholds, claimed, diag=None):
    """Kucuk yuzlerden olusan, toplamda da kucuk kalan kumeler."""
    limit = thresholds["protrusion_max_size"]
    small = [r["index"] for r in records
             if r["index"] not in claimed and r["bbox"]
             and bbox_diagonal(r["bbox"]) <= limit]
    features = []
    for group in _components(small, records):
        if len(group) < 2:
            continue
        box = bbox_union([records[i]["bbox"] for i in group])
        extent = bbox_diagonal(box)
        if extent > limit:
            if diag is not None:
                diag["cikinti_buyuk_kume"] = diag.get("cikinti_buyuk_kume", 0) + 1
            continue
        if all([is_fillet_candidate(records[i], thresholds) for i in group]):
            continue                          # sadece fileto zinciri
        if _coplanar(records, group):
            if diag is not None:
                diag["cikinti_iz"] = diag.get("cikinti_iz", 0) + 1
            continue
        for index in group:
            claimed.add(index)
        features.append(_feature("cikinti", group, extent, records))
    return features


def find_fillet_features(records, thresholds, claimed):
    """Kucuk yaricapli fileto yuzleri; komsu ve benzer yaricaplilar zincir."""
    candidates = [r["index"] for r in records
                  if r["index"] not in claimed
                  and is_fillet_candidate(r, thresholds)]
    features = []
    remaining = set(candidates)
    for start in candidates:
        if start not in remaining:
            continue
        remaining.discard(start)
        radius = records[start]["radius"]
        chain = [start]
        stack = [start]
        while stack:
            current = stack.pop()
            for other in records[current].get("neighbors", []):
                if other not in remaining:
                    continue
                r_other = records[other]["radius"]
                if abs(r_other - radius) > FILLET_RADIUS_SPREAD * radius:
                    continue
                remaining.discard(other)
                chain.append(other)
                stack.append(other)
        for index in chain:
            claimed.add(index)
        size = max([records[i]["radius"] for i in chain])
        features.append(_feature("fileto", chain, size, records))
    return features


def detect_features(records, thresholds, diag=None, categories=None):
    """Tum kategoriler.  Sira onemli: once vida, sonra cikinti, en son fileto.

    Vida noktasinin dibindeki fileto vidaya, cikintinin dibindeki fileto
    cikintiya aittir; ayri ayri silmek yerine detay tek parca silinir.
    ``categories`` verilirse yalnizca o kategoriler aranir.
    """
    wanted = categories or ("vida", "cikinti", "fileto")
    claimed = set()
    features = []
    if "vida" in wanted:
        features.extend(find_hole_features(records, thresholds, claimed))
    if "cikinti" in wanted:
        features.extend(find_protrusion_features(records, thresholds, claimed,
                                                 diag))
    if "fileto" in wanted:
        features.extend(find_fillet_features(records, thresholds, claimed))
    return features


def split_protected(features, records):
    """Kullanici grubuna ait yuz iceren bulgulari ayir (isaretlenmez)."""
    kept = []
    skipped = []
    for feature in features:
        owners = []
        for index in feature["faces"]:
            for name in records[index].get("groups", []):
                if name not in owners:
                    owners.append(name)
        if owners:
            item = dict(feature)
            item["reason"] = "kullanici grubuna ait yuz iceriyor: %s" % \
                ", ".join(owners)
            skipped.append(item)
        else:
            kept.append(feature)
    return kept, skipped


def size_token(category, size):
    """Grup adindaki olcu: R (yaricap), D (cap) ya da S (boyut)."""
    letter = {"fileto": "R", "vida": "D", "cikinti": "S"}.get(category, "S")
    return letter + format_size(size)          # noqa: F821 - analiz betiginden


def name_features(features, max_groups=DEFAULT_MAX_GROUPS):
    """Kategori icinde kucukten buyuge sirala ve isim ver.

    Her kategoride ilk ``max_groups`` bulgu kendi grubunu alir; hepsi
    ayrica ``temizle_<kategori>_HEPSI`` grubunda toplanir.
    """
    named = []
    for category in ("fileto", "vida", "cikinti"):
        items = [f for f in features if f["category"] == category]
        items.sort(key=lambda f: (f["size"], f["extent"]))
        for number, feature in enumerate(items):
            item = dict(feature)
            item["number"] = number + 1
            if number < max_groups:
                item["name"] = "%s%s_%s_%02d" % (
                    CLEANUP_PREFIX, category, size_token(category, item["size"]),
                    number + 1)
            else:
                item["name"] = ""             # sadece HEPSI grubunda
            named.append(item)
    return named


# ---------------------------------------------------------------- envanter

def inventory_thresholds(diagonal):
    """Envanter: esik yok gibi - her sey bulunsun, siniflandirma olcuyle.

    Fileto ile kavisli duvar ayrimi icin tek sinir: yaricapi kosegenin
    %5'inden buyuk kismi silindir fileto degil, yuzeydir.
    """
    big = max(diagonal, 1e-6)
    return {"fillet_max_radius": 0.05 * big,
            "hole_max_diameter": big,
            "protrusion_max_size": 0.05 * big,
            "auto": {"fillet_max_radius": True, "hole_max_diameter": True,
                     "protrusion_max_size": True}}


SURFACE_LABELS = {"Plane": "duzlem", "Cylinder": "silindir", "Cone": "koni",
                  "Torus": "torus", "Sphere": "kure"}


def surface_features(records, claimed):
    """Hicbir detaya girmeyen yuzler: tiplerine gore."""
    features = []
    for record in records:
        if record["index"] in claimed:
            continue
        kind = SURFACE_LABELS.get(record["kind"], "serbest")
        feature = _feature("yuzey", [record["index"]], 0.0, records) \
            if record.get("bbox") else {"category": "yuzey",
                                        "faces": [record["index"]],
                                        "size": 0.0, "bbox": None,
                                        "center": (0.0, 0.0, 0.0),
                                        "extent": 0.0}
        feature["kind"] = kind
        features.append(feature)
    return features


def similarity_groups(features, tol=SIMILAR_TOL, max_groups=MAX_INVENTORY_GROUPS):
    """Ayni kategorideki detaylari olcu benzerligine gore grupla.

    Olcuye gore siralanir; bir oncekinden en fazla ``tol`` oraninda
    buyuk olan ayni gruba girer (zincir).  Yuzeyler tipe gore gruplanir.
    """
    groups = []
    order = ("fileto", "vida", "cikinti", "yuzey")
    for category in order:
        items = [f for f in features if f["category"] == category]
        if not items:
            continue
        if category == "yuzey":
            by_kind = {}
            for item in items:
                by_kind.setdefault(item.get("kind", "serbest"), []).append(item)
            for kind in sorted(by_kind):
                groups.append({"category": category, "kind": kind,
                               "members": by_kind[kind]})
            continue
        items.sort(key=lambda f: f["size"])
        current = [items[0]]
        for item in items[1:]:
            previous = current[-1]["size"]
            if previous > 0 and item["size"] <= previous * (1.0 + tol):
                current.append(item)
            else:
                groups.append({"category": category, "members": current})
                current = [item]
        groups.append({"category": category, "members": current})

    # cok fazla grup olursa en buyukler "diger"de birlesir
    limited = []
    for category in order:
        mine = [g for g in groups if g["category"] == category]
        if len(mine) > max_groups:
            rest = []
            for g in mine[max_groups - 1:]:
                rest.extend(g["members"])
            mine = mine[:max_groups - 1] + [{"category": category,
                                             "members": rest, "other": True}]
        limited.extend(mine)

    out = []
    for group in limited:
        sizes = [m["size"] for m in group["members"]]
        faces = []
        for m in group["members"]:
            faces.extend(m["faces"])
        category = group["category"]
        size = sorted(sizes)[(len(sizes) - 1) // 2] if sizes else 0.0
        if category == "yuzey":
            name = "%syuzey_%s" % (INVENTORY_PREFIX, group["kind"])
        elif group.get("other"):
            name = "%s%s_diger" % (INVENTORY_PREFIX, category)
        else:
            name = "%s%s_%s" % (INVENTORY_PREFIX, category,
                                size_token(category, size))
        out.append({"category": category, "name": name,
                    "kind": group.get("kind", ""),
                    "size": size, "size_min": min(sizes) if sizes else 0.0,
                    "size_max": max(sizes) if sizes else 0.0,
                    "count": len(group["members"]), "faces": faces})
    # ayni ada dusen gruplari ayir (yuvarlama cakismasi)
    seen = {}
    for group in out:
        n = seen.get(group["name"], 0)
        seen[group["name"]] = n + 1
        if n:
            group["name"] = "%s_%d" % (group["name"], n + 1)
    return out


def summary(features):
    counts = {"fileto": 0, "vida": 0, "cikinti": 0}
    for feature in features:
        counts[feature["category"]] = counts.get(feature["category"], 0) + 1
    return counts


# ---------------------------------------------------------------- API

def _xyz(value):
    return (float(value.X), float(value.Y), float(value.Z))


def geometry_axis(geometry):
    """(origin, yon) ya da None - silindir/koni/torus ekseni."""
    for getter in (lambda: (geometry.Axis.Origin, geometry.Axis.Direction),
                   lambda: (geometry.Frame.Origin, geometry.Frame.DirZ)):
        try:
            origin, direction = getter()
            unit = canonical_direction(_xyz(direction))
            if unit is not None:
                return (_xyz(origin), unit)
        except Exception:
            continue
    return None


def plane_normal(geometry):
    for getter in (lambda: geometry.Frame.DirZ,
                   lambda: geometry.Normal):
        try:
            unit = canonical_direction(_xyz(getter()))
            if unit is not None:
                return unit
        except Exception:
            continue
    return None


def uv_spans(design_face):
    for getter in (lambda: design_face.Shape.BoxUV,
                   lambda: design_face.BoxUV):
        try:
            box = getter()
            spans = []
            for rng in (box.RangeU, box.RangeV):
                try:
                    spans.append(float(rng.Span))
                except Exception:
                    spans.append(float(rng.End) - float(rng.Start))
            return spans
        except Exception:
            continue
    return []


def face_record(design_face, index, body_index):
    geometry = face_geometry(design_face)                 # noqa: F821
    kind = geometry_name(geometry)                        # noqa: F821
    record = {
        "index": index,
        "body": body_index,
        "kind": kind,
        "radius": 0.0,
        "major_radius": 0.0,
        "axis": None,
        "normal": None,
        "area": face_area(design_face),                   # noqa: F821
        "bbox": None,
        "span": 0.0,
        "neighbors": [],
        "groups": [],
    }
    try:
        record["bbox"] = body_bbox(design_face.Shape)     # noqa: F821
    except Exception:
        record["bbox"] = None

    if kind == "Plane":
        record["normal"] = plane_normal(geometry)
    elif kind in ("Cylinder", "Cone", "Torus", "Sphere"):
        if kind == "Torus":
            record["radius"] = float(safe(lambda: geometry.MinorRadius, 0.0))  # noqa: F821
            record["major_radius"] = float(safe(lambda: geometry.MajorRadius, 0.0))  # noqa: F821
        else:
            record["radius"] = float(safe(lambda: geometry.Radius, 0.0))  # noqa: F821
        if kind != "Sphere":
            record["axis"] = geometry_axis(geometry)
        if kind == "Cylinder":
            extent = 0.0
            if record["axis"] is not None and record["bbox"]:
                lo, hi = axial_range(record["bbox"], record["axis"][0],
                                     record["axis"][1])
                extent = hi - lo
            record["span"] = cylinder_span(record["area"], record["radius"],
                                           uv_spans(design_face), extent)
    return record


def edge_signature(design_edge):
    """Kenarin kimlikten bagimsiz imzasi: orta nokta + uzunluk (yuvarlanmis)."""
    point = None
    for getter in (lambda: design_edge.Shape.EvalMid().Point,
                   lambda: design_edge.EvalMid().Point):
        try:
            point = _xyz(getter())
            break
        except Exception:
            continue
    if point is None:
        for getter in (lambda: (design_edge.Shape.StartPoint, design_edge.Shape.EndPoint),
                       lambda: (design_edge.StartPoint, design_edge.EndPoint)):
            try:
                a, b = getter()
                point = v_scale(v_add(_xyz(a), _xyz(b)), 0.5)
                break
            except Exception:
                continue
    if point is None:
        return None
    length = float(safe(lambda: design_edge.Shape.Length,           # noqa: F821
                        safe(lambda: design_edge.Length, 0.0)))     # noqa: F821
    return (round(point[0], 7), round(point[1], 7), round(point[2], 7),
            round(length, 7))


def build_adjacency(faces, records, diag):
    """Yuz komsuluklari.  Once nesne kimligi, olmazsa kenar imzasi."""
    lookup = {}
    try:
        for index, face in enumerate(faces):
            lookup[face] = index
    except Exception:
        lookup = {}

    found = 0
    if lookup:
        for index, face in enumerate(faces):
            try:
                edges = list(face.Edges)
            except Exception:
                continue
            for edge in edges:
                try:
                    others = list(edge.Faces)
                except Exception:
                    continue
                for other in others:
                    j = lookup.get(other)
                    if j is not None and j != index and \
                            j not in records[index]["neighbors"]:
                        records[index]["neighbors"].append(j)
                        found += 1
    if found:
        diag["komsuluk"] = "kimlik"
        diag["komsuluk_sayisi"] = found
        return

    # Yedek: ayni kenari paylasan yuzlerin kenar imzasi aynidir.
    by_signature = {}
    for index, face in enumerate(faces):
        try:
            edges = list(face.Edges)
        except Exception:
            continue
        for edge in edges:
            key = edge_signature(edge)
            if key is None:
                continue
            by_signature.setdefault(key, []).append(index)
    for owners in by_signature.values():
        for a in owners:
            for b in owners:
                if a != b and b not in records[a]["neighbors"]:
                    records[a]["neighbors"].append(b)
                    found += 1
    diag["komsuluk"] = "imza" if found else "yok"
    diag["komsuluk_sayisi"] = found


def face_signature(record):
    """Yuzu geometrisiyle tani (grup uyeligi eslestirmesi icin)."""
    box = record.get("bbox")
    if not box:
        return None
    center = bbox_center(box)
    return (round(center[0], 6), round(center[1], 6), round(center[2], 6),
            round(record.get("area", 0.0), 10))


def mark_user_groups(faces, records, diag):
    """Kullanicinin named selection'larini oku (DEGISTIRMEDEN)."""
    lookup = {}
    try:
        for index, face in enumerate(faces):
            lookup[face] = index
    except Exception:
        lookup = {}
    by_signature = {}
    for record in records:
        key = face_signature(record)
        if key is not None:
            by_signature.setdefault(key, []).append(record["index"])

    names = []
    for named_selection in named_selection_list():               # noqa: F821
        name = ""
        for getter in (lambda: named_selection.GetName(),
                       lambda: named_selection.Name):
            try:
                name = str(getter())
                break
            except Exception:
                continue
        if not name or name.startswith(OWN_PREFIXES):
            continue
        names.append(name)
        for member in named_selection_faces(named_selection):     # noqa: F821
            index = lookup.get(member) if lookup else None
            targets = [index] if index is not None else []
            if not targets:
                probe = {"bbox": None, "area": face_area(member)}  # noqa: F821
                try:
                    probe["bbox"] = body_bbox(member.Shape)        # noqa: F821
                except Exception:
                    pass
                targets = by_signature.get(face_signature(probe), [])
            for target in targets:
                if name not in records[target]["groups"]:
                    records[target]["groups"].append(name)
    diag["kullanici_gruplari"] = names


def remove_own_groups(diag, prefix=CLEANUP_PREFIX):
    """Onceki taramadan kalan kendi gruplarimizi kaldir (sadece bizimkiler)."""
    removed = 0
    for named_selection in named_selection_list():               # noqa: F821
        try:
            name = str(named_selection.GetName())
        except Exception:
            try:
                name = str(named_selection.Name)
            except Exception:
                continue
        if not name.startswith(prefix):
            continue
        for action in (lambda: named_selection.Delete(),
                       lambda: Delete.Execute(Selection.Create(named_selection))):  # noqa: F821
            try:
                action()
                removed += 1
                break
            except Exception:
                continue
    diag["eski_temizlik_grubu_silindi"] = removed


def cleanup_scan(params):
    diag = {}
    result = {
        "analyzer": "spaceclaim-cleanup",
        "source_path": params.get("input", ""),
        "warnings": [],
        "features": [],
        "skipped": [],
        "diagnostics": diag,
    }
    input_path = params.get("input", "")
    if input_path:
        DocumentOpen.Execute(input_path)                          # noqa: F821

    part = GetRootPart()                                          # noqa: F821
    bodies = iter_bodies(part)                                    # noqa: F821
    faces = []
    records = []
    boxes = []
    limit_hit = False
    for body_index, design_body in enumerate(bodies):
        try:
            boxes.append(body_bbox(design_body.Shape))            # noqa: F821
        except Exception:
            pass
        for design_face in design_faces(design_body):             # noqa: F821
            if len(faces) >= MAX_FACES:                           # noqa: F821
                limit_hit = True
                break
            try:
                record = face_record(design_face, len(faces), body_index)
            except Exception:
                diag["okunamayan_yuz"] = diag.get("okunamayan_yuz", 0) + 1
                continue
            faces.append(design_face)
            records.append(record)

    if limit_hit:
        result["warnings"].append(
            "Yuzey sayisi %d sinirini asti, tarama kismi." % MAX_FACES)  # noqa: F821
    if not records:
        result["warnings"].append("Dokumanda hic yuzey okunamadi.")
    if not boxes:
        boxes = [r["bbox"] for r in records if r.get("bbox")]
    diagonal = bbox_diagonal(bbox_union(boxes))
    thresholds = default_thresholds(diagonal, params)
    result["thresholds"] = thresholds
    result["diagonal"] = diagonal
    result["body_count"] = len(bodies)
    result["face_count"] = len(records)

    kinds = {}
    for record in records:
        kinds[record["kind"]] = kinds.get(record["kind"], 0) + 1
    diag["yuz_tipleri"] = kinds
    diag["eksenli_yuz"] = len([r for r in records if r.get("axis")])
    diag["kutusu_okunamayan"] = len([r for r in records if not r.get("bbox")])
    diag["aci_olculen_silindir"] = len([r for r in records
                                        if r["kind"] == "Cylinder" and r["span"] > 0])

    build_adjacency(faces, records, diag)
    try:
        mark_user_groups(faces, records, diag)
    except Exception:
        result["warnings"].append("Mevcut gruplar okunamadi: %s"
                                  % traceback.format_exc().splitlines()[-1])

    if params.get("mode") == "inventory":
        return _inventory(params, result, faces, records, diagonal, diag)

    categories = params.get("categories") or ["fileto", "vida", "cikinti"]
    features = detect_features(records, thresholds, diag, categories)
    kept, skipped = split_protected(features, records)
    named = name_features(kept, int(params.get("max_groups", DEFAULT_MAX_GROUPS)))

    if params.get("create_groups", True):
        try:
            remove_own_groups(diag)
        except Exception:
            pass
        for category in ("fileto", "vida", "cikinti"):
            members = [f for f in named if f["category"] == category]
            if not members:
                continue
            for feature in members:
                feature["created"] = False
                feature["note"] = ""
                if not feature["name"]:
                    feature["note"] = "sadece HEPSI grubunda"
                    continue
                ok, note = create_named_selection(                 # noqa: F821
                    [faces[i] for i in feature["faces"]], feature["name"])
                feature["created"] = bool(ok)
                feature["note"] = note
            everything = []
            for feature in members:
                everything.extend([faces[i] for i in feature["faces"]])
            ok, note = create_named_selection(                     # noqa: F821
                everything, "%s%s_HEPSI" % (CLEANUP_PREFIX, category))
            if not ok:
                result["warnings"].append("%s HEPSI grubu olusturulamadi: %s"
                                          % (category, note))

    for feature in named:
        result["features"].append({
            "category": feature["category"],
            "name": feature["name"],
            "number": feature["number"],
            "size": feature["size"],
            "extent": feature["extent"],
            "center": list(feature["center"]),
            "face_count": len(feature["faces"]),
            "created": bool(feature.get("created", False)),
            "note": feature.get("note", ""),
        })
    for feature in skipped:
        result["skipped"].append({
            "category": feature["category"],
            "size": feature["size"],
            "center": list(feature["center"]),
            "face_count": len(feature["faces"]),
            "reason": feature["reason"],
        })
    result["summary"] = summary(named)

    save_path = params.get("export", "")
    if save_path:
        saved, method, errors = save_copy(save_path)
        diag["kaydetme"] = method
        if saved:
            result["saved_path"] = saved
        else:
            result["warnings"].append(
                "Isaretli kopya diske yazilamadi (%s). Denenenler: %s"
                % (save_path, " | ".join(errors) or "-"))
    return result


def _inventory(params, result, faces, records, diagonal, diag):
    """Her seyi siniflandir, benzerleri envanter_* gruplarinda topla."""
    thresholds = inventory_thresholds(diagonal)
    result["thresholds"] = thresholds
    claimed = set()
    features = []
    features.extend(find_hole_features(records, thresholds, claimed))
    features.extend(find_protrusion_features(records, thresholds, claimed, diag))
    features.extend(find_fillet_features(records, thresholds, claimed))
    features.extend(surface_features(records, claimed))
    groups = similarity_groups(features)

    if params.get("create_groups", True):
        try:
            remove_own_groups(diag, INVENTORY_PREFIX)
        except Exception:
            pass
    for group in groups:
        owners = []
        for index in group["faces"]:
            for name in records[index].get("groups", []):
                if name not in owners:
                    owners.append(name)
        created, note = False, ""
        if params.get("create_groups", True):
            ok, note = create_named_selection(                      # noqa: F821
                [faces[i] for i in group["faces"]], group["name"])
            created = bool(ok)
        result.setdefault("inventory", []).append({
            "category": group["category"], "name": group["name"],
            "kind": group["kind"], "size": group["size"],
            "size_min": group["size_min"], "size_max": group["size_max"],
            "count": group["count"], "face_count": len(group["faces"]),
            "user_groups": owners, "created": created, "note": note})
    counts = {}
    for group in groups:
        counts[group["category"]] = counts.get(group["category"], 0) + group["count"]
    result["summary"] = counts
    result["features"] = []
    save_path = params.get("export", "")
    if save_path:
        saved, method, errors = save_copy(save_path)
        diag["kaydetme"] = method
        if saved:
            result["saved_path"] = saved
        else:
            result["warnings"].append(
                "Envanter kopyasi diske yazilamadi (%s). Denenenler: %s"
                % (save_path, " | ".join(errors) or "-"))
    return result


def _find_written(path):
    """Kaydedilen dosya: tam yol, yoksa ayni adla baska uzantida."""
    if os.path.isfile(path):
        return path
    folder = os.path.dirname(path) or "."
    stem = os.path.splitext(os.path.basename(path))[0]
    try:
        for name in os.listdir(folder):
            if os.path.splitext(name)[0] == stem and \
                    name.lower().endswith((".scdoc", ".scdocx")):
                return os.path.join(folder, name)
    except Exception:
        pass
    return ""


def save_copy(path):
    """Belgeyi kopya olarak kaydet; diske gercekten yazildigini dogrula.

    SpaceClaim surumlerine gore farkli yollar calisiyor; her denemeden sonra
    dosyanin varligina bakilir.  (yazilan yol, yontem, hatalar) doner.
    """
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    attempts = (
        ("DocumentSave", lambda: DocumentSave.Execute(path)),              # noqa: F821
        ("DocumentSave+secenek",
         lambda: DocumentSave.Execute(path, ExportOptions.Create())),      # noqa: F821
        ("Document.SaveAs",
         lambda: Window.ActiveWindow.Document.SaveAs(path)),               # noqa: F821
        ("RootPart.SaveAs",
         lambda: GetRootPart().Document.SaveAs(path)),                     # noqa: F821
    )
    errors = []
    for name, action in attempts:
        try:
            action()
        except Exception:
            errors.append("%s: %s" % (name, traceback.format_exc().splitlines()[-1]))
            continue
        written = _find_written(path)
        if written:
            return written, name, errors
        errors.append("%s: hata yok ama dosya olusmadi" % name)
    return "", "yok", errors


def cleanup_main():
    output_path = ""
    try:
        params = read_params()                                    # noqa: F821
        output_path = params.get("output", "")
        result = cleanup_scan(params)
        result["ok"] = True
    except Exception:
        result = {"ok": False, "analyzer": "spaceclaim-cleanup",
                  "error": traceback.format_exc()}
        if not output_path:
            output_path = os.environ.get("AUTOMESH_SC_OUTPUT", "")
    if output_path:
        try:
            dump_json(result, output_path)                        # noqa: F821
        except Exception:
            pass


if not os.environ.get("AUTOMESH_SC_NO_RUN"):
    cleanup_main()
