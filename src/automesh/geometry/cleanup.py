"""SpaceClaim temizlik taraması.

Hazır akış hacminde CFD için genellikle gereksiz olan küçük detayları bulur
ve SpaceClaim'de **grup olarak işaretler**; hiçbir şeyi silmez:

* ``fileto``  - küçük yarıçaplı round/fileto'lar
* ``vida``    - vida deliği / vida kulesi / pim gibi küçük tam silindirik
                detaylar (pah, havşa ve dipteki fileto dahil)
* ``cikinti`` - akışa etkisi olmayan küçük çıkıntı ve cepler

Kullanıcı işaretli kopyayı SpaceClaim'de açar, Groups panelinde bir gruba
tıklar (yüzler seçilir), Delete'e basar; SpaceClaim boşluğu komşu yüzleri
uzatarak kapatır. Kaynak dosyaya dokunulmaz.

Betik (``scripts/spaceclaim_cleanup.py``) SpaceClaim'in IronPython'unda
koşar ve ölçüm/grup fonksiyonlarını analiz betiğinden alır: çalıştırmadan
önce ikisi tek dosyada birleştirilir.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from ..config import Config
from ..logging_utils import get_logger
from .base import CAD_EXTENSIONS, GeometryAnalyzerError, extension
from .spaceclaim import SpaceClaimAnalyzer, script_path

CLEANUP_SCRIPT_NAME = "spaceclaim_cleanup.py"
CATEGORIES = ("fileto", "vida", "cikinti")
GROUP_PREFIX = "temizle_"

#: Kategori -> (başlık, ölçünün adı, ölçünün harfi)
CATEGORY_INFO = {
    "fileto": ("Fileto / round", "yarıçap", "R"),
    "vida": ("Vida noktası / küçük delik", "çap", "Ø"),
    "cikinti": ("Küçük çıkıntı / cep", "boyut", "~"),
}

#: Ayar anahtarı -> kategori (eşiklerin hangi kategoriye ait olduğu)
THRESHOLD_KEYS = (
    ("fillet_max_radius", "fileto", "Fileto yarıçapı ≤"),
    ("hole_max_diameter", "vida", "Vida/delik çapı ≤"),
    ("protrusion_max_size", "cikinti", "Çıkıntı boyutu ≤"),
)


def cleanup_script_path() -> str:
    return os.path.join(os.path.dirname(script_path()), CLEANUP_SCRIPT_NAME)


# --------------------------------------------------------------------------
# rapor
# --------------------------------------------------------------------------

@dataclass
class CleanupFeature:
    category: str
    name: str                     # SpaceClaim grup adı ("" -> sadece HEPSI'nde)
    number: int
    size: float                   # m: fileto yarıçapı / vida çapı / çıkıntı boyutu
    extent: float                 # m: detayın sınır kutusu köşegeni
    center: Tuple[float, float, float]
    face_count: int
    created: bool = True
    note: str = ""


@dataclass
class SkippedFeature:
    category: str
    size: float
    center: Tuple[float, float, float]
    face_count: int
    reason: str


@dataclass
class CleanupReport:
    source_path: str = ""
    saved_path: str = ""
    diagonal: float = 0.0
    body_count: int = 0
    face_count: int = 0
    thresholds: Dict[str, float] = field(default_factory=dict)
    thresholds_auto: Dict[str, bool] = field(default_factory=dict)
    features: List[CleanupFeature] = field(default_factory=list)
    skipped: List[SkippedFeature] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=dict)
    diagnostics: Dict[str, object] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.features)

    def by_category(self, category: str) -> List[CleanupFeature]:
        return [f for f in self.features if f.category == category]


def _center(value) -> Tuple[float, float, float]:
    try:
        x, y, z = (float(v) for v in (value or (0, 0, 0)))
        return (x, y, z)
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0)


def report_from_raw(raw: Dict) -> CleanupReport:
    """Betiğin JSON çıktısını rapora çevir."""
    thresholds = raw.get("thresholds") or {}
    report = CleanupReport(
        source_path=str(raw.get("source_path", "")),
        saved_path=str(raw.get("saved_path", "") or ""),
        diagonal=float(raw.get("diagonal", 0.0) or 0.0),
        body_count=int(raw.get("body_count", 0) or 0),
        face_count=int(raw.get("face_count", 0) or 0),
        thresholds={key: float(thresholds.get(key, 0.0) or 0.0)
                    for key, _cat, _label in THRESHOLD_KEYS},
        thresholds_auto={key: bool((thresholds.get("auto") or {}).get(key, False))
                         for key, _cat, _label in THRESHOLD_KEYS},
        summary={cat: int((raw.get("summary") or {}).get(cat, 0) or 0)
                 for cat in CATEGORIES},
        diagnostics=dict(raw.get("diagnostics") or {}),
        warnings=[str(w) for w in (raw.get("warnings") or [])],
    )
    for item in raw.get("features") or []:
        report.features.append(CleanupFeature(
            category=str(item.get("category", "")),
            name=str(item.get("name", "") or ""),
            number=int(item.get("number", 0) or 0),
            size=float(item.get("size", 0.0) or 0.0),
            extent=float(item.get("extent", 0.0) or 0.0),
            center=_center(item.get("center")),
            face_count=int(item.get("face_count", 0) or 0),
            created=bool(item.get("created", True)),
            note=str(item.get("note", "") or ""),
        ))
    for item in raw.get("skipped") or []:
        report.skipped.append(SkippedFeature(
            category=str(item.get("category", "")),
            size=float(item.get("size", 0.0) or 0.0),
            center=_center(item.get("center")),
            face_count=int(item.get("face_count", 0) or 0),
            reason=str(item.get("reason", "")),
        ))
    return report


def _mm(value_m: float) -> str:
    return "{0:.3g} mm".format(value_m * 1000.0)


def format_report(report: CleanupReport) -> List[str]:
    """Günlük / komut satırı için okunur liste."""
    lines: List[str] = []
    lines.append("--- Temizlik taraması ---")
    lines.append("Gövde / yüz        : {0} / {1}".format(report.body_count,
                                                        report.face_count))
    for key, _category, label in THRESHOLD_KEYS:
        value = report.thresholds.get(key, 0.0)
        auto = "  (otomatik)" if report.thresholds_auto.get(key) else ""
        lines.append("{0:<19}: {1}{2}".format(label, _mm(value), auto))
    lines.append("")

    if not report.features:
        lines.append("Eşiklerin altında kalan detay bulunamadı.")
    for category in CATEGORIES:
        items = report.by_category(category)
        if not items:
            continue
        title, measure, letter = CATEGORY_INFO[category]
        lines.append("{0}: {1} adet".format(title, len(items)))
        for item in items:
            name = item.name or "(ayrı grup yok - HEPSI grubunda)"
            mark = "" if item.created else "  [grup oluşturulamadı: {0}]".format(
                item.note or "?")
            lines.append("  {0:<34} {1}{2:<10} {3:>2} yüz{4}".format(
                name, letter, _mm(item.size), item.face_count, mark))
        lines.append("  Hepsi birden: {0}{1}_HEPSI".format(GROUP_PREFIX, category))
        lines.append("")

    if report.skipped:
        lines.append("İşaretlenmeyenler (sizin gruplarınıza dokunuyor):")
        for item in report.skipped:
            title = CATEGORY_INFO.get(item.category, (item.category,))[0]
            lines.append("  {0} {1} - {2}".format(title, _mm(item.size),
                                                   item.reason))
        lines.append("")

    for warning in report.warnings:
        lines.append("! " + warning)
    return lines


def usage_hint(saved_path: str) -> List[str]:
    """SpaceClaim'de ne yapılacağı - her taramadan sonra gösterilir."""
    return [
        "SpaceClaim'de:",
        "  1) Sol taraftaki Groups panelinde bir temizle_* grubuna tıklayın",
        "     (yüzler seçilir, modelde görünür).",
        "  2) Silinmesini istiyorsanız Delete'e basın; SpaceClaim boşluğu",
        "     komşu yüzleri uzatarak kapatır. İstemiyorsanız geçin.",
        "  3) Bir kategorinin hepsi için temizle_<kategori>_HEPSI grubunu seçin.",
        "  4) Bitince Ctrl+S ile kaydedin:",
        "     {0}".format(saved_path or "(işaretli kopya)"),
        "Silmediğiniz temizle_* grupları Fluent'e gereksiz bölge olarak gider;",
        "işiniz bitince Groups panelinden seçip silebilirsiniz.",
    ]


# --------------------------------------------------------------------------
# çalıştırma
# --------------------------------------------------------------------------

def cleanup_params(cfg: Config) -> Dict:
    settings = cfg.cleanup
    categories = [c for c in (settings.categories or CATEGORIES) if c in CATEGORIES]
    return {
        "fillet_max_radius": float(settings.fillet_max_radius or 0.0),
        "hole_max_diameter": float(settings.hole_max_diameter or 0.0),
        "protrusion_max_size": float(settings.protrusion_max_size or 0.0),
        "categories": categories or list(CATEGORIES),
        "max_groups": int(settings.max_groups_per_category),
        "create_groups": True,
    }


def cleanup_output_path(source: str, folder: str, suffix: str = "_temizlik") -> str:
    """İşaretli kopyanın yeri. Kaynağın üzerine ASLA yazılmaz."""
    stem, ext = os.path.splitext(os.path.basename(source))
    # .scdocx girdisi .scdocx olarak kaydedilir: SpaceClaim formatı
    # uzantıdan değil belgeden alabiliyor, uyuşmazlıkta dosya yazılmıyordu.
    ext = ext.lower() if ext.lower() in (".scdoc", ".scdocx") else ".scdoc"
    target = os.path.join(os.path.abspath(folder), stem + (suffix or "_temizlik")
                          + ext)
    if os.path.normcase(os.path.abspath(target)) == \
            os.path.normcase(os.path.abspath(source)):
        target = os.path.join(os.path.abspath(folder), stem + "_temizlik2" + ext)
    return target


def default_output_dir(source: str, cfg: Config) -> str:
    """Çıktı klasörü verilmediyse: runs/<zaman>-<ad>-temizlik."""
    stem = os.path.splitext(os.path.basename(source))[0]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return os.path.abspath(os.path.join(cfg.output.run_root,
                                        "{0}-{1}-temizlik".format(stamp, stem)))


def build_script(params_file: str) -> str:
    """Analiz betiği (kütüphane olarak) + temizlik betiği, tek dosya."""
    def body(path: str) -> str:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        return text.split("\n", 1)[1] if text.startswith("# -*- coding") else text

    header = (
        "# -*- coding: utf-8 -*-\n"
        "AUTOMESH_PARAMS_FILE = r\"{0}\"\n"
        "# Analiz betigi burada yalnizca yardimci kutuphanedir.\n"
        "AUTOMESH_SC_LIBRARY = True\n".format(params_file)
    )
    return (header + body(script_path())
            + "\n\n# " + "=" * 70 + "\n# temizlik taramasi\n# " + "=" * 70 + "\n"
            + body(cleanup_script_path()))


def _run_process(cmd: List[str], env: Dict[str, str], timeout: int):
    return subprocess.run(cmd, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, timeout=timeout)


def run_cleanup_scan(path: str, cfg: Config, output_dir: Optional[str] = None,
                     analyzer: Optional[SpaceClaimAnalyzer] = None,
                     runner: Callable = _run_process) -> CleanupReport:
    """SpaceClaim'de tarat, işaretli kopyayı kaydet, istenirse aç."""
    log = get_logger()
    # Arayüzden çağrıldığında seviye ayarlanmamış olabilir; ayarlanmazsa
    # bilgi satırları (bulgu listesi) düşer, yalnızca uyarılar görünürdü.
    if log.getEffectiveLevel() > logging.INFO:
        log.setLevel(logging.INFO)
    if not path or not os.path.isfile(path):
        raise GeometryAnalyzerError("Geometri dosyası bulunamadı: {0}".format(path))
    if extension(path) not in CAD_EXTENSIONS:
        raise GeometryAnalyzerError(
            "Temizlik taraması SpaceClaim'in açabildiği bir CAD dosyası ister "
            "(.scdoc önerilir): {0}".format(os.path.basename(path)))

    analyzer = analyzer or SpaceClaimAnalyzer()
    analyzer._probed = False
    analyzer._probe(cfg)
    if not analyzer.available():
        raise GeometryAnalyzerError(
            "SpaceClaim.exe bulunamadı - temizlik taraması SpaceClaim ister. "
            "Yolunu geometry.spaceclaim_exe ayarıyla verebilirsiniz.")

    folder = output_dir or default_output_dir(path, cfg)
    os.makedirs(folder, exist_ok=True)
    target = cleanup_output_path(path, folder, cfg.cleanup.output_suffix)

    workdir = tempfile.mkdtemp(prefix="automesh-temizlik-")
    try:
        params_file = os.path.join(workdir, "params.json")
        output_file = os.path.join(workdir, "cleanup.json")
        params = {"input": os.path.abspath(path), "output": output_file,
                  "export": target}
        params.update(cleanup_params(cfg))
        with open(params_file, "w", encoding="utf-8") as handle:
            json.dump(params, handle, indent=2)

        run_script = os.path.join(workdir, CLEANUP_SCRIPT_NAME)
        with open(run_script, "w", encoding="utf-8") as handle:
            handle.write(build_script(params_file))

        env = dict(os.environ)
        env["AUTOMESH_SC_PARAMS"] = params_file
        env["AUTOMESH_SC_OUTPUT"] = output_file
        cmd = analyzer.script_command(run_script, cfg)

        log.info("SpaceClaim temizlik taraması başlıyor: %s", os.path.basename(path))
        log.info("Kaynak dosyaya dokunulmaz; işaretli kopya: %s", target)
        try:
            proc = runner(cmd, env, cfg.geometry.spaceclaim_timeout_s)
        except subprocess.TimeoutExpired:
            raise GeometryAnalyzerError(
                "SpaceClaim {0} saniyede yanıt vermedi.".format(
                    cfg.geometry.spaceclaim_timeout_s))

        if not os.path.isfile(output_file):
            tail = (getattr(proc, "stdout", b"") or b"")
            if isinstance(tail, bytes):
                tail = tail.decode("utf-8", "replace")
            raise GeometryAnalyzerError(
                "SpaceClaim tarama çıktısı üretmedi (çıkış kodu {0}).\n{1}".format(
                    getattr(proc, "returncode", "?"), tail[-2000:]))
        with open(output_file, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not raw.get("ok", False):
            raise GeometryAnalyzerError(
                "SpaceClaim temizlik betiği hata verdi:\n{0}".format(
                    raw.get("error", "?")))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    report = report_from_raw(raw)
    for line in format_report(report):
        log.info("%s", line)
    _log_diagnostics(log, report)
    log.info("Kaydetme yöntemi   : %s", report.diagnostics.get("kaydetme", "?"))

    if report.saved_path and os.path.isfile(report.saved_path):
        for line in usage_hint(report.saved_path):
            log.info("%s", line)
        if cfg.cleanup.open_in_spaceclaim:
            analyzer.open_document(report.saved_path, cfg)
    elif report.saved_path:
        log.warning("İşaretli kopya bildirildi ama bulunamadı: %s",
                    report.saved_path)
    return report


def _log_diagnostics(log, report: CleanupReport) -> None:
    """API'nin bu sürümde neyi okuyabildiği - sorun olursa sebep burada."""
    diag = report.diagnostics or {}
    if not diag:
        return
    kinds = diag.get("yuz_tipleri") or {}
    if kinds:
        log.info("Yüz tipleri        : %s", ", ".join(
            "{0} {1}".format(k, v) for k, v in sorted(kinds.items())))
    log.info("Komşuluk           : %s (%s bağlantı)", diag.get("komsuluk", "?"),
             diag.get("komsuluk_sayisi", 0))
    log.info("Ekseni okunan yüz  : %s, açısı ölçülen silindir: %s",
             diag.get("eksenli_yuz", 0), diag.get("aci_olculen_silindir", 0))
    if diag.get("komsuluk") == "yok":
        log.warning("Yüz komşulukları okunamadı: fileto zincirleri ve çıkıntılar "
                    "tek tek yüz olarak bulunur, çıkıntı taraması çalışmaz.")
