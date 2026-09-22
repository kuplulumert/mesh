"""Ölçüm özeti ve seçilebilir mesh önerileri.

Agent tek bir plan üretir, ama kullanıcı çoğu zaman "şu uzunluklar bunlar,
bu boyutta ne olur, bir kademe kaba olsa ne kaybederim" diye bakmak ister.
Bu modül bunu sağlar: ölçülen uzunlukları ve otomatik planın etrafında
birkaç kademeyi, her birinin ne çözdüğünü söyleyerek listeler.

Tkinter'a bağlı değildir; hem arayüz hem `automesh propose` komutu kullanır.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..models import GeometryMetrics, MeshPlan
from ..units import format_length, resolve_display_unit
from .sizing import effective_area, effective_volume, estimate_cell_count, plan_mesh

#: Fluent Meshing'in kabaca milyon hücre başına istediği bellek.
#: Sürüme, doldurma tipine ve prizma sayısına göre değişir; kaba bir pusula.
GB_PER_MILLION_CELLS = 1.2

#: Otomatik planın etrafındaki kademeler: (anahtar, etiket, ölçek, açıklama)
LEVELS: Tuple[Tuple[str, str, float, str], ...] = (
    ("preview", "Hızlı önizleme", 2.0,
     "Topolojiyi ve bölgeleri görmek için; sonuç CFD'ye uygun değildir."),
    ("coarse", "Kaba", 1.4,
     "İlk yakınsama denemesi ve akış yapısını görmek için."),
    ("balanced", "Dengeli", 1.0,
     "Agent'ın geometriden hesapladığı denge noktası."),
    ("fine", "İnce", 0.7,
     "Gradyanların keskin olduğu bölgeler için; hücre sayısı ~3 kat artar."),
    ("very_fine", "Çok ince", 0.5,
     "Ağ bağımsızlık çalışmasının üst basamağı; maliyeti yüksektir."),
)


# --------------------------------------------------------------------------
# ölçümler
# --------------------------------------------------------------------------

@dataclass
class Measurement:
    """Tek bir ölçüm satırı - arayüzde ve raporda aynı şekilde gösterilir."""

    label: str
    value: str
    note: str = ""


def measurements(metrics: GeometryMetrics, unit: Optional[str] = None,
                 cfg: Optional[Config] = None) -> List[Measurement]:
    """Kullanıcıya gösterilecek "uzunluklar bunlar" tablosu."""
    if unit is None:
        setting = cfg.output.display_unit if cfg is not None else "mm"
        unit = resolve_display_unit(setting, metrics.diagonal)
    dx, dy, dz = metrics.bbox.sizes
    rows: List[Measurement] = [
        Measurement("Sınır kutusu", "{0} x {1} x {2}".format(
            format_length(dx, unit), format_length(dy, unit), format_length(dz, unit))),
        Measurement("Köşegen", format_length(metrics.diagonal, unit),
                    "genel hücre boyutu buna oranla seçilir"),
    ]
    if metrics.volume > 0:
        rows.append(Measurement("Hacim", "{0:.6g} m³".format(metrics.volume),
                                "hücre sayısı tahmini buradan çıkar"))
    else:
        rows.append(Measurement("Hacim", "bilinmiyor",
                                "CAD çekirdeği yok - sınır kutusunun yarısı varsayıldı"))
    if metrics.area > 0:
        rows.append(Measurement("Yüzey alanı", "{0:.6g} m²".format(metrics.area),
                                "prizma hücre sayısını belirler"))
    rows.append(Measurement("En küçük özellik", format_length(metrics.min_feature_size, unit),
                            "minimum hücre boyutu bunun üzerine oturur"))
    if metrics.min_edge_length > 0:
        rows.append(Measurement("En kısa kenar", format_length(metrics.min_edge_length, unit),
                                "%2'lik yüzdelik - sliver'lar sayılmaz"))
    if metrics.min_face_size > 0:
        rows.append(Measurement("En küçük yüzey", format_length(metrics.min_face_size, unit),
                                "√alan olarak"))
    if metrics.min_curvature_radius > 0:
        rows.append(Measurement("En küçük eğrilik yarıçapı",
                                format_length(metrics.min_curvature_radius, unit),
                                "delik / fileto yarıçapı"))
    if metrics.thinnest_section > 0:
        rows.append(Measurement("En ince kesit", format_length(metrics.thinnest_section, unit),
                                "2·Hacim/Alan - prizma yığınını sınırlar"))
    rows.extend([
        Measurement("Özellik aralığı", "1 : {0:.0f}".format(metrics.feature_span),
                    "gövde boyutu / en küçük detay"),
        Measurement("Gövde / yüzey / kenar", "{0} / {1} / {2}".format(
            metrics.body_count, metrics.face_count, metrics.edge_count)),
        Measurement("Eğrisel yüzey oranı", "{0:.0%}".format(metrics.curved_face_ratio)),
        Measurement("Su geçirmez", _yesno(metrics.watertight),
                    "hayır ise fault-tolerant akış seçilir"),
        Measurement("Karmaşıklık skoru", "{0:.2f} / 1.00".format(metrics.complexity()),
                    "boyutlandırmayı bu sürüyor"),
    ])
    return rows


# --------------------------------------------------------------------------
# öneriler
# --------------------------------------------------------------------------

@dataclass
class Proposal:
    """Seçilebilir bir mesh kademesi."""

    key: str
    label: str
    scale: float
    plan: MeshPlan
    recommended: bool = False
    cells: int = 0
    ram_gb: float = 0.0
    cells_across_feature: float = 0.0
    cells_across_thin_section: float = 0.0
    diagonal_divisor: float = 0.0
    rationale: str = ""
    warnings: List[str] = field(default_factory=list)
    #: Ekranda gösterim birimi (Fluent'e giden birimden bağımsız).
    display_unit: str = "mm"

    # ------------------------------------------------------------------
    @property
    def usable(self) -> bool:
        """Bu kademe CFD için savunulabilir mi?"""
        return self.cells_across_feature >= 2.0 and not any(
            "bütçe" in w for w in self.warnings)

    def size_text(self) -> str:
        unit = self.display_unit or self.plan.length_unit
        return "{0} - {1}".format(format_length(self.plan.min_size, unit),
                                  format_length(self.plan.max_size, unit))

    def cells_text(self) -> str:
        if self.cells >= 1_000_000:
            return "{0:.1f} M".format(self.cells / 1_000_000.0)
        if self.cells >= 1000:
            return "{0:.0f} B".format(self.cells / 1000.0)
        return str(self.cells)

    def ram_text(self) -> str:
        return "~{0:.1f} GB".format(self.ram_gb) if self.ram_gb >= 0.1 else "<0.1 GB"

    def to_dict(self) -> Dict[str, Any]:
        data = {k: v for k, v in dataclasses.asdict(self).items() if k != "plan"}
        data["plan"] = self.plan.to_dict()
        return data


def build_proposals(metrics: GeometryMetrics, cfg: Config,
                    levels=LEVELS) -> List[Proposal]:
    """Otomatik planı hesapla ve etrafında seçilebilir kademeler üret."""
    # Kademeleri üretirken bütçe kabalaştırması devrede olmamalı: kullanıcı
    # zaten bilinçli olarak bir kademe seçiyor, bütçe uyarısını ayrıca
    # gösteriyoruz.
    base_cfg = Config.from_dict(cfg.to_dict())
    base_cfg.planning.target_cell_count = 0
    base_cfg.planning.max_cell_count = 0
    base = plan_mesh(metrics, base_cfg)

    limit = cfg.planning.max_cell_count or 0
    display_unit = resolve_display_unit(cfg.output.display_unit, metrics.diagonal)
    proposals: List[Proposal] = []
    for key, label, scale, description in levels:
        plan = base.copy()
        plan.min_size = base.min_size * scale
        plan.max_size = base.max_size * scale
        plan.max_cell_length = plan.max_size
        plan.clamp()
        plan.estimated_cell_count = estimate_cell_count(plan, metrics)

        proposal = Proposal(
            key=key, label=label, scale=scale, plan=plan,
            display_unit=display_unit,
            recommended=(key == "balanced"),
            cells=plan.estimated_cell_count,
            ram_gb=plan.estimated_cell_count / 1_000_000.0 * GB_PER_MILLION_CELLS,
            cells_across_feature=(metrics.min_feature_size / plan.min_size
                                  if plan.min_size > 0 else 0.0),
            cells_across_thin_section=(metrics.thinnest_section / plan.max_size
                                       if plan.max_size > 0 and metrics.thinnest_section > 0
                                       else 0.0),
            diagonal_divisor=(metrics.diagonal / plan.max_size
                              if plan.max_size > 0 else 0.0),
        )
        proposal.rationale = _rationale(proposal, description)
        proposal.warnings = _warnings(proposal, metrics, limit)
        proposals.append(proposal)
    return proposals


def recommended(proposals: List[Proposal]) -> Optional[Proposal]:
    """Önerilen kademe; bütçeye sığmıyorsa sığan en ince kademe."""
    marked = [p for p in proposals if p.recommended]
    preferred = marked[0] if marked else None
    if preferred is not None and preferred.usable:
        return preferred
    for proposal in sorted(proposals, key=lambda p: p.cells, reverse=True):
        if proposal.usable:
            return proposal
    return preferred or (proposals[0] if proposals else None)


# --------------------------------------------------------------------------

def _rationale(proposal: Proposal, description: str) -> str:
    bits = [description]
    if proposal.diagonal_divisor > 0:
        bits.append("Genel boyut köşegenin 1/{0:.0f}'i.".format(proposal.diagonal_divisor))
    if proposal.cells_across_feature > 0:
        bits.append("En küçük özellik {0:.1f} hücreyle çözülüyor.".format(
            proposal.cells_across_feature))
    if proposal.cells_across_thin_section > 0:
        bits.append("En ince kesitte {0:.1f} hücre var.".format(
            proposal.cells_across_thin_section))
    return " ".join(bits)


def _warnings(proposal: Proposal, metrics: GeometryMetrics, limit: int) -> List[str]:
    warnings: List[str] = []
    if proposal.cells_across_feature and proposal.cells_across_feature < 2.0:
        warnings.append(
            "En küçük özellik 2 hücreden az ile çözülüyor - o detaylar ağda kaybolur.")
    if proposal.cells_across_thin_section and proposal.cells_across_thin_section < 3.0:
        warnings.append(
            "En ince kesitte 3'ten az hücre var - prizma katmanları sığmayabilir.")
    if limit and proposal.cells > limit:
        warnings.append(
            "Tahmini hücre sayısı bütçeyi ({0:,}) aşıyor; agent çalışırken "
            "kabalaştıracak.".format(limit))
    if proposal.ram_gb > 32:
        warnings.append(
            "Kabaca {0:.0f} GB bellek ister - makinenizde yeterli RAM var mı "
            "kontrol edin.".format(proposal.ram_gb))
    return warnings


def _yesno(value: Optional[bool]) -> str:
    if value is None:
        return "bilinmiyor"
    return "evet" if value else "hayır"


# --------------------------------------------------------------------------

def format_table(metrics: GeometryMetrics, proposals: List[Proposal],
                 unit: Optional[str] = None) -> str:
    """Konsol için ölçüm + öneri tablosu."""
    if unit is None and proposals:
        unit = proposals[0].display_unit
    lines: List[str] = ["Ölçümler", "-" * 72]
    for row in measurements(metrics, unit):
        note = "   ({0})".format(row.note) if row.note else ""
        lines.append("  {0:<26} {1}{2}".format(row.label, row.value, note))

    lines += ["", "Mesh seçenekleri", "-" * 72,
              "  {0:<16} {1:<26} {2:>9} {3:>9}  {4}".format(
                  "Kademe", "min - max", "hücre", "bellek", "özellik/hücre")]
    for proposal in proposals:
        mark = " <-- önerilen" if proposal.recommended else ""
        lines.append("  {0:<16} {1:<26} {2:>9} {3:>9}  {4:>6.1f}{5}".format(
            proposal.label, proposal.size_text(), proposal.cells_text(),
            proposal.ram_text(), proposal.cells_across_feature, mark))
    lines += ["", "Gerekçeler", "-" * 72]
    for proposal in proposals:
        lines.append("  {0}: {1}".format(proposal.label, proposal.rationale))
        for warning in proposal.warnings:
            lines.append("      ! {0}".format(warning))
    return "\n".join(lines)


def apply_proposal(cfg: Config, proposal: Proposal) -> Config:
    """Seçilen kademeyi konfigürasyona dayatma olarak yaz."""
    cfg.planning.override_min_size = proposal.plan.min_size
    cfg.planning.override_max_size = proposal.plan.max_size
    return cfg
