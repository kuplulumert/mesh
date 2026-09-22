"""Yüzey gruplarından hücre boyutu türetme.

SpaceClaim tarafında yüzeyler tipine ve eğrilik yarıçapına göre gruplandı
(bkz. ``geometry/scripts/spaceclaim_analyze.py``).  Burada her grup için
kendi hücre boyutu hesaplanıyor ve Fluent'in **Add Local Sizing** görevine
verilecek kontrollere dönüştürülüyor.

Temel kural, bir deliği ya da fileto yüzeyini çözmek için çevresi boyunca
belirli sayıda hücre istemektir:

    boyut = 2·pi·r / (çevre başına hücre)

16 hücre/çevre ile bu ``0.39·r`` eder; yani 2 mm çapında bir delik yaklaşık
0.39 mm hücre alır - global boyut 3 mm olsa bile.  Düz duvarlar gruplanmaz,
onlar global boyutta kalır; kontrol sayısını şişirmenin bir faydası yok.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from ..config import Config
from ..models import FaceGroup, LocalSizing, MeshPlan
from ..units import format_length


def size_for_group(group: FaceGroup, cells_per_circle: float) -> float:
    """Bir grubun çözülebilmesi için gereken hücre boyutu (metre).

    SpaceClaim betiği eğrilik, dar bant ve ince kesit ölçütlerini yüzey yüzey
    hesaplayıp en zorlayıcısını seçtiği için normalde o değeri kullanırız.
    Eski kayıtlarda (ya da betik hesaplayamadıysa) yarıçaptan türetilir.
    """
    if group.recommended_size > 0:
        return group.recommended_size
    radius = group.representative_radius
    if radius > 0 and cells_per_circle > 0:
        return 2.0 * math.pi * radius / cells_per_circle
    if group.min_width > 0:
        return group.min_width / 3.0
    if group.min_face_size > 0:
        return group.min_face_size / 2.0
    return 0.0


def build_local_sizings(
    groups: List[FaceGroup],
    plan: MeshPlan,
    cfg: Config,
) -> Tuple[List[LocalSizing], List[str]]:
    """Grupları Fluent kontrollerine çevir.

    ``(kontroller, notlar)`` döndürür; notlar rapora girer, böylece hangi
    grubun neden hangi boyutu aldığı görünür.
    """
    settings = cfg.local_sizing
    notes: List[str] = []
    sizings: List[LocalSizing] = []

    if not settings.enabled or not groups:
        return sizings, notes

    unit = plan.length_unit
    floor = plan.max_size / max(settings.min_size_ratio, 2.0)
    # Global boyuta yakın bir kontrolün hiçbir etkisi olmaz; onları eleriz.
    useful_ceiling = plan.max_size * settings.skip_above_ratio

    # Kullanıcının kendi grupları önce gelir: onlar açık bir niyet ifadesi,
    # kontrol bütçesi dolarsa elenmemeliler.  Sonra otomatik gruplar, en
    # zorlayıcıdan başlayarak.
    ordered = (
        [g for g in groups if g.is_existing]
        + sorted((g for g in groups if not g.is_existing),
                 key=lambda g: size_for_group(g, settings.cells_per_circle)
                 or float("inf"))
    )

    for group in ordered:
        if group.is_existing and not settings.size_existing_groups:
            continue
        if not group.created:
            notes.append(
                "'{0}' grubu SpaceClaim'de oluşturulamadı, atlandı ({1}).".format(
                    group.name, group.note or "sebep bilinmiyor"))
            continue

        size = size_for_group(group, settings.cells_per_circle)
        if size <= 0:
            notes.append("'{0}' için boyut hesaplanamadı, atlandı.".format(group.name))
            continue

        clamped = size
        if clamped < floor:
            clamped = floor
            notes.append(
                "'{0}' için hesaplanan {1} çok ince bulundu, tabana ({2}) "
                "çekildi.".format(group.name, format_length(size, unit),
                                  format_length(floor, unit)))
        if clamped >= useful_ceiling:
            notes.append(
                "'{0}' global boyuta ({1}) yakın olduğu için kontrol "
                "eklenmedi.".format(group.name, format_length(plan.max_size, unit)))
            continue

        sizings.append(LocalSizing(
            name=group.name,
            target=group.name,
            size=clamped,
            size_control_type="Face Size",
            growth_rate=plan.growth_rate,
        ))
        notes.append("'{0}'{1}: {2} yüzey, {3} -> hücre {4}.".format(
            group.name,
            " (sizin grubunuz, değiştirilmedi)" if group.is_existing else "",
            group.face_count,
            _driver_text(group, unit),
            format_length(clamped, unit)))

        if len(sizings) >= settings.max_controls:
            notes.append(
                "Kontrol sayısı sınırına ({0}) ulaşıldı; kalan gruplar global "
                "boyutta bırakıldı.".format(settings.max_controls))
            break

    return sizings, notes


def spaceclaim_params(cfg: Config) -> dict:
    """SpaceClaim betiğine geçirilecek gruplama parametreleri."""
    settings = cfg.local_sizing
    return {
        "group_faces": bool(settings.enabled),
        "group_prefix": settings.name_prefix,
        "band_factor": float(settings.band_factor),
        "band_anchor": float(settings.band_anchor),
        "max_groups": int(settings.max_controls),
        "min_faces_per_group": int(settings.min_faces_per_group),
        "radius_ceiling_ratio": float(settings.radius_ceiling_ratio),
        "cells_per_circle": float(settings.cells_per_circle),
        "cells_across_width": float(settings.cells_across_width),
        "cells_across_gap": float(settings.cells_across_gap),
        "max_useful_ratio": float(settings.max_useful_ratio),
        "read_existing_groups": bool(settings.read_existing_groups),
    }


_DRIVER_TEXT = {
    "curv": "eğrilik yarıçapı {0}",
    "width": "dar bant genişliği {0}",
    "gap": "ince kesit {0}",
}


def _driver_text(group: FaceGroup, unit: str) -> str:
    """Boyutu neyin belirlediğini insan diliyle yaz."""
    value = {
        "curv": group.representative_radius,
        "width": group.min_width,
        "gap": group.min_gap,
    }.get(group.driver, 0.0)
    template = _DRIVER_TEXT.get(group.driver)
    if template and value > 0:
        return template.format(format_length(value, unit))
    if group.representative_radius > 0:
        return "eğrilik yarıçapı {0}".format(
            format_length(group.representative_radius, unit))
    return "ölçülen boyut"


def summarise(sizings: List[LocalSizing], unit: str) -> List[str]:
    """Arayüz ve rapor için tek satırlık özetler."""
    return [
        "{0}: {1}".format(sizing.name, format_length(sizing.size, unit))
        for sizing in sizings
    ]
