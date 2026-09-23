"""Yüzey gruplarından hücre boyutu türetme - bölme sayısı üzerinden.

Bir CFD mühendisi "bu yüzeye 0.31 mm ver" diye düşünmez; "20 mm çapındaki
girişi çevresinde kaça böleyim" diye düşünür. Bu modül de öyle çalışır:

    hücre boyutu = bölünen uzunluk / bölme sayısı

Bölünen uzunluk ölçüte göre değişir:

``curv``   deliğin/filetonun **çevresi** (2·pi·r) - kaç hücreyle dönülecek
``width``  dar bandın **genişliği** (2·alan/çevre) - enine kaç hücre
``gap``    **ince kesit** (2·Hacim/Alan) - içine kaç hücre

Her ölçütün bir rule-of-thumb varsayılanı var (çevrede 16, enine 3), ama
son söz kullanıcınındır: gözden geçirme ekranında bölme sayısını değiştirir,
boyut anında yeniden hesaplanır.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..models import FaceGroup, LocalSizing, MeshPlan
from ..units import format_length

# Risk seviyeleri
OK = "ok"
WARN = "warn"
HIGH = "high"

RISK_LABELS = {OK: "uygun", WARN: "dikkat", HIGH: "riskli"}

#: Ölçüt -> (bölünen uzunluğun adı, rule-of-thumb gerekçesi)
DRIVER_INFO = {
    "curv": ("çevre", "Bir deliği/filetoyu düzgün çözmek için çevresinde "
                      "12-20 hücre istenir; 16 yaygın kabuldür."),
    "width": ("dar bant genişliği", "İnce bir bandın enine en az 3 hücre "
                                    "gerekir, yoksa bant tek hücreye ezilir."),
    "gap": ("ince kesit", "Bir kanalın/cidarın içinde en az 3 hücre olmalı ki "
                          "akış profili çözülebilsin."),
}


@dataclass
class SizingReview:
    """Bir yerel boyut kontrolünün gözden geçirme satırı.

    Kullanıcı ``divisions`` değerini değiştirir; ``size`` ondan hesaplanır.
    """

    name: str
    source: str = "auto"               # auto | existing
    driver: str = ""
    face_count: int = 0

    measured: float = 0.0              # m, ölçülen ham büyüklük (yarıçap/genişlik/kesit)
    diameter: float = 0.0              # m, eğrilik ölçütünde çap (kullanıcı dili)
    base_length: float = 0.0           # m, bölünen uzunluk
    base_label: str = ""               # "çevre" / "dar bant genişliği" / "ince kesit"

    divisions: float = 0.0             # kaça bölünecek (uygulanan)
    recommended_divisions: float = 0.0  # rule-of-thumb
    rationale: str = ""                # bölme sayısının gerekçesi

    size: float = 0.0                  # m, uygulanacak hücre boyutu
    total_area: float = 0.0            # m^2, gruptaki yüzeylerin toplam alanı
    enabled: bool = True
    ratio: float = 0.0                 # global max / boyut
    face_cells: int = 0                # bu gruptaki tahmini yüzey hücresi
    risk: str = OK
    messages: List[str] = field(default_factory=list)
    clamped: bool = False

    @property
    def is_existing(self) -> bool:
        return self.source == "existing"

    @property
    def user_set(self) -> bool:
        """Bölme sayısı kullanıcı tarafından mı verildi?"""
        return abs(self.divisions - self.recommended_divisions) > 1e-9

    def size_for(self, divisions: float) -> float:
        """Verilen bölme sayısında hücre boyutu."""
        if divisions <= 0 or self.base_length <= 0:
            return 0.0
        return self.base_length / divisions

    def measured_text(self, unit: str) -> str:
        """Kullanıcıya gösterilecek ölçüm ("çap 20 mm" gibi)."""
        if self.driver == "curv" and self.diameter > 0:
            return "çap {0}".format(format_length(self.diameter, unit))
        if self.measured > 0:
            return format_length(self.measured, unit)
        return "-"

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


# --------------------------------------------------------------------------

def size_for_group(group: FaceGroup, cells_per_circle: float) -> float:
    """Geriye dönük uyumluluk: grubun önerilen hücre boyutu."""
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


def _base_for(group: FaceGroup, settings) -> Tuple[float, str, float, str]:
    """``(bölünen uzunluk, etiket, önerilen bölme, gerekçe)``."""
    label, rationale = DRIVER_INFO.get(group.driver, ("ölçülen uzunluk", ""))

    if group.driver == "curv" and group.representative_radius > 0:
        return (2.0 * math.pi * group.representative_radius, label,
                settings.cells_per_circle, rationale)
    if group.driver == "width" and group.min_width > 0:
        return group.min_width, label, settings.cells_across_width, rationale
    if group.driver == "gap" and group.min_gap > 0:
        return group.min_gap, label, settings.cells_across_gap, rationale

    # Ölçüt bilinmiyorsa betiğin hesapladığı boyuttan geriye çalış:
    # tek bölmeyle o boyutu veren bir taban kur, kullanıcı yine bölebilsin.
    size = size_for_group(group, settings.cells_per_circle)
    if size > 0:
        return size * settings.cells_across_width, label, \
            settings.cells_across_width, rationale
    return 0.0, label, 0.0, rationale


def review_groups(groups: List[FaceGroup], plan: MeshPlan,
                  cfg: Config) -> List[SizingReview]:
    """Her grup için bölme sayısını, boyutu ve riskini hesapla.

    Gözden geçirme ekranı, rapor ve ``build_local_sizings`` aynı sonucu
    kullanır - yani kullanıcının ekranda gördüğü sayı ile Fluent'e giden
    sayı tek yerden çıkar.
    """
    settings = cfg.local_sizing
    reviews: List[SizingReview] = []
    if not groups:
        return reviews

    floor = plan.max_size / max(settings.min_size_ratio, 2.0)
    if settings.absolute_floor > 0:
        floor = max(floor, settings.absolute_floor)
    useful_ceiling = plan.max_size * settings.skip_above_ratio
    unit = plan.length_unit

    ordered = (
        [g for g in groups if g.is_existing]
        + sorted((g for g in groups if not g.is_existing),
                 key=lambda g: size_for_group(g, settings.cells_per_circle)
                 or float("inf"))
    )

    for group in ordered:
        base, label, recommended, rationale = _base_for(group, settings)
        review = SizingReview(
            name=group.name, source=group.source, driver=group.driver,
            face_count=group.face_count, measured=_measured_value(group),
            diameter=(group.representative_radius * 2.0
                      if group.driver == "curv" else 0.0),
            base_length=base, base_label=label, total_area=group.total_area,
            recommended_divisions=recommended, divisions=recommended,
            rationale=rationale,
        )
        reviews.append(review)

        if group.is_existing and not settings.size_existing_groups:
            review.enabled = False
            review.messages.append("Mevcut gruplara boyut verme kapalı.")
            continue
        if not group.created:
            review.enabled = False
            review.messages.append("SpaceClaim'de oluşturulamadı: {0}".format(
                group.note or "sebep bilinmiyor"))
            continue
        if base <= 0 or recommended <= 0:
            review.enabled = False
            review.messages.append("Bölünecek bir uzunluk ölçülemedi.")
            continue

        # Kullanıcının verdiği bölme sayısı her şeyin üstünde.
        chosen = _lookup(settings.divisions, group.name)
        if chosen is not None and chosen > 0:
            review.divisions = float(chosen)
            review.messages.append(
                "Bölme sayısı sizin tarafınızdan {0:g} olarak verildi "
                "(öneri {1:g}).".format(chosen, recommended))

        size = review.size_for(review.divisions)
        if size <= 0:
            review.enabled = False
            review.messages.append("Boyut hesaplanamadı.")
            continue

        if size < floor:
            if chosen is not None:
                review.messages.append(
                    "Seçtiğiniz bölme {0} veriyor; taban {1}.".format(
                        format_length(size, unit), format_length(floor, unit)))
            size = floor
            review.clamped = True
            review.messages.append(
                "Boyut tabana ({0}) çekildi - bundan incesi Fluent'i "
                "zorlar.".format(format_length(floor, unit)))

        if size >= useful_ceiling:
            review.enabled = False
            review.size = size
            review.messages.append(
                "global boyuta ({0}) çok yakın; kontrol eklemenin faydası yok."
                .format(format_length(plan.max_size, unit)))
            continue

        review.size = size
        review.ratio = plan.max_size / size if size > 0 else 0.0
        review.face_cells = _face_cells(group, size)
        _assess_risk(review, settings)

        if _in_list(settings.disabled, group.name):
            review.enabled = False
            review.messages.append("Bu kontrolü siz kapattınız.")

    return reviews


def build_local_sizings(groups: List[FaceGroup], plan: MeshPlan,
                        cfg: Config) -> Tuple[List[LocalSizing], List[str]]:
    """Grupları Fluent kontrollerine çevir.

    ``(kontroller, notlar)`` döndürür; notlar rapora girer.
    """
    settings = cfg.local_sizing
    notes: List[str] = []
    sizings: List[LocalSizing] = []
    if not settings.enabled or not groups:
        return sizings, notes

    unit = plan.length_unit
    for review in review_groups(groups, plan, cfg):
        if not review.enabled:
            if review.messages:
                notes.append("'{0}' atlandı: {1}".format(
                    review.name, " ".join(review.messages)))
            continue

        sizings.append(LocalSizing(
            name=review.name, target=review.name, size=review.size,
            size_control_type="Face Size", growth_rate=plan.growth_rate,
        ))
        notes.append(
            "'{0}'{1}: {2} yüzey, {3} {4} -> {5:g} bölme -> hücre {6}{7}.".format(
                review.name,
                " (sizin grubunuz, değiştirilmedi)" if review.is_existing else "",
                review.face_count, review.base_label,
                format_length(review.base_length, unit),
                review.divisions, format_length(review.size, unit),
                "  [{0}]".format(RISK_LABELS[review.risk])
                if review.risk != OK else ""))
        for message in review.messages:
            notes.append("    {0}".format(message))

        if len(sizings) >= settings.max_controls:
            notes.append(
                "Kontrol sayısı sınırına ({0}) ulaşıldı; kalan gruplar global "
                "boyutta bırakıldı.".format(settings.max_controls))
            break

    return sizings, notes


# --------------------------------------------------------------------------

def evaluate(review: SizingReview, divisions: float, max_size: float,
             settings) -> Tuple[float, float, int, str]:
    """Bir bölme sayısının sonucunu hesapla - ``(boyut, oran, hücre, risk)``.

    Arayüz kullanıcı sayıyı değiştirdikçe bunu çağırır; nihai karar yine
    :func:`review_groups` tarafından yeniden üretilir, yani ekrandaki sayı
    ile Fluent'e giden sayı aynı kuraldan çıkar.
    """
    size = review.size_for(divisions)
    if size <= 0:
        return 0.0, 0.0, 0, OK
    floor = max_size / max(settings.min_size_ratio, 2.0)
    if settings.absolute_floor > 0:
        floor = max(floor, settings.absolute_floor)
    size = max(size, floor)

    ratio = max_size / size if size > 0 else 0.0
    cells = int(review.total_area / (size * size)) if review.total_area > 0 else 0
    risk = OK
    if ratio >= settings.high_risk_ratio:
        risk = HIGH
    elif ratio >= settings.warn_ratio:
        risk = WARN
    if cells >= settings.warn_face_cells and risk != HIGH:
        risk = WARN
    return size, ratio, cells, risk


def _measured_value(group: FaceGroup) -> float:
    return {
        "curv": group.representative_radius,
        "width": group.min_width,
        "gap": group.min_gap,
    }.get(group.driver, group.representative_radius)


def _face_cells(group: FaceGroup, size: float) -> int:
    """Bu gruptaki yüzeylerin üreteceği yaklaşık hücre sayısı."""
    if size <= 0 or group.total_area <= 0:
        return 0
    return int(group.total_area / (size * size))


def _assess_risk(review: SizingReview, settings) -> None:
    """Boyutun ne kadar riskli olduğunu işaretle."""
    review.risk = OK
    if review.ratio >= settings.high_risk_ratio:
        review.risk = HIGH
        review.messages.append(
            "Global boyuttan {0:.0f} kat ince - Fluent zorlanabilir, süre "
            "uzar. Bölme sayısını düşürmeyi düşünün.".format(review.ratio))
    elif review.ratio >= settings.warn_ratio:
        review.risk = WARN
        review.messages.append(
            "Global boyuttan {0:.0f} kat ince.".format(review.ratio))
    if review.face_cells >= settings.warn_face_cells:
        if review.risk != HIGH:
            review.risk = WARN
        review.messages.append(
            "Yalnızca bu grup ~{0:,} yüzey hücresi üretir.".format(
                review.face_cells))


def _lookup(mapping: Dict[str, float], name: str) -> Optional[float]:
    for key, value in (mapping or {}).items():
        if str(key).strip().lower() == name.strip().lower():
            return float(value)
    return None


def _in_list(names: List[str], name: str) -> bool:
    return any(str(n).strip().lower() == name.strip().lower()
               for n in (names or []))


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


#: Betiğin teşhis anahtarları -> insan diline çeviri.
_DIAG_REASONS = {
    "gruplama kapali": "Yüzey gruplama ayarı kapalı.",
    "sinir kutusu olculemedi": "Geometrinin sınır kutusu ölçülemedi.",
    "hic yuzey okunamadi (DesignFace listesi bos)":
        "SpaceClaim'den hiç yüzey okunamadı - bu sürümde yüzey listesi "
        "beklenenden farklı olabilir.",
    "hicbir yuzeyden olcum alinamadi (yariçap/cevre/kesit hepsi bos)":
        "Yüzeyler okundu ama hiçbirinden ölçüm alınamadı (yarıçap, çevre ve "
        "kesit boş) - SpaceClaim API'si bu sürümde farklı olabilir.",
    "tum olcumler global boyutla zaten cozuluyor (hepsi ust sinirin uzerinde)":
        "Ölçülen her şey global hücre boyutuyla zaten çözülüyor; ayrı kontrol "
        "açmanın faydası yok.",
    "bantlarda yeterli yuzey yok (min_faces_per_group)":
        "Bantlara düşen yüzey sayısı eşiğin altında "
        "(local_sizing.min_faces_per_group).",
    "aday bant olusmadi": "Gruplanacak bir bant oluşmadı.",
}


def explain_missing_groups(metrics, cfg: Config) -> List[str]:
    """Neden hiç yüzey grubu çıkmadığını insan diliyle anlat.

    Kullanıcıya "önce analiz edin" demek yanıltıcı: analiz yapıldı, sonuç
    boş çıktı. Sebebi söylemek gerekiyor.
    """
    lines: List[str] = []
    if not cfg.local_sizing.enabled:
        lines.append("Yüzey gruplarına özel boyut verme kapalı.")
        return lines

    analyzer = getattr(metrics, "analyzer", "") or "bilinmiyor"
    if not analyzer.startswith("spaceclaim"):
        lines.append(
            "Geometri SpaceClaim ile okunmadı (kullanılan: {0}). Yüzey "
            "gruplama yalnızca SpaceClaim backend'iyle çalışır.".format(analyzer))
        return lines

    diagnostics = (getattr(metrics, "raw", None) or {}).get(
        "face_group_diagnostics") or {}
    reason = diagnostics.get("reason")
    if reason:
        lines.append(_DIAG_REASONS.get(reason, reason))
    if diagnostics:
        lines.append(
            "Sayılar: {0} gövde, {1} yüzey görüldü, {2} ölçülebildi, "
            "{3} tanesi global boyutla zaten çözülüyordu.".format(
                diagnostics.get("bodies", 0), diagnostics.get("faces_seen", 0),
                diagnostics.get("measurable", 0),
                diagnostics.get("too_coarse", 0)))
        lines.append(
            "Okunabilen alanlar: geometri {0}, alan {1}, çevre {2}, "
            "yarıçap {3}.".format(
                diagnostics.get("with_geometry", 0),
                diagnostics.get("with_area", 0),
                diagnostics.get("with_perimeter", 0),
                diagnostics.get("with_radius", 0)))
    for warning in getattr(metrics, "warnings", None) or []:
        if "grup" in warning.lower() or "selection" in warning.lower():
            lines.append(warning)
    if not lines:
        lines.append("Gruplanacak yüzey bulunamadı.")
    return lines


def format_review_table(reviews: List[SizingReview], plan: MeshPlan,
                        unit: Optional[str] = None) -> str:
    """Konsol için gözden geçirme tablosu."""
    unit = unit or plan.length_unit
    lines = [
        "Yüzey grupları - hücre boyutu = ölçülen uzunluk / bölme sayısı",
        "-" * 92,
        "  {0:<26} {1:<8} {2:<18} {3:>7} {4:>12} {5:>9}".format(
            "Grup", "Kaynak", "Ölçüm", "Bölme", "Hücre", "Durum"),
    ]
    for review in reviews:
        lines.append("  {0:<26} {1:<8} {2:<18} {3:>7g} {4:>12} {5:>9}".format(
            review.name[:26],
            "sizin" if review.is_existing else "agent",
            review.measured_text(unit)[:18],
            review.divisions,
            format_length(review.size, unit) if review.size else "-",
            RISK_LABELS[review.risk] if review.enabled else "kapalı"))

    lines += ["", "Gerekçeler ve uyarılar", "-" * 92]
    for review in reviews:
        if not review.messages and review.risk == OK:
            continue
        lines.append("  {0}:".format(review.name))
        if review.rationale:
            lines.append("      {0}".format(review.rationale))
        for message in review.messages:
            lines.append("      ! {0}".format(message))
    lines += ["", "Bölme sayısını değiştirmek için:",
              "  automesh run <geometri> --divisions <grup>=<sayı>",
              "  (birden çok kez verilebilir; --no-local-sizing hepsini kapatır)"]
    return "\n".join(lines)


def summarise(sizings: List[LocalSizing], unit: str) -> List[str]:
    """Arayüz ve rapor için tek satırlık özetler."""
    return ["{0}: {1}".format(s.name, format_length(s.size, unit))
            for s in sizings]
