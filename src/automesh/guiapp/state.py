"""GUI durumu: kullanıcı seçimleri, kalıcılık ve Config'e çevrim.

Bu modül bilerek Tkinter'dan bağımsızdır; böylece arayüzün tüm mantığı
pencere açmadan test edilebilir.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import Config

#: Ayarların saklandığı yer (kullanıcı profili).
SETTINGS_DIR = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~/.config"), "automesh")
SETTINGS_PATH = os.path.join(SETTINGS_DIR, "gui-settings.json")

WORKFLOWS = ("auto", "watertight", "fault-tolerant")
#: Arayüzdeki birim listeleri.
DISPLAY_UNITS = ("mm", "cm", "m", "um", "in", "ft")
FILLS = ("poly-hexcore", "polyhedra", "hexcore", "tetrahedral")
SCENARIOS = ("clean", "realistic", "dirty", "prism", "memory", "stubborn")

#: Dosya seçme penceresinde gösterilecek formatlar.
FILE_TYPES = (
    ("Tüm desteklenen", "*.scdoc *.scdocx *.stp *.step *.x_t *.x_b *.igs *.iges "
                        "*.stl *.obj *.sat *.prt *.CATPart *.sldprt *.pmdb *.agdb"),
    ("SpaceClaim", "*.scdoc *.scdocx"),
    ("STEP", "*.stp *.step"),
    ("Parasolid", "*.x_t *.x_b"),
    ("IGES", "*.igs *.iges"),
    ("Tessellated", "*.stl *.obj *.ply"),
    ("Tüm dosyalar", "*.*"),
)


@dataclass
class GuiSettings:
    """Pencerede görünen her alanın karşılığı."""

    geometry_path: str = ""
    output_dir: str = ""
    cores: int = 4
    workflow: str = "auto"
    volume_fill: str = "poly-hexcore"
    max_cells: int = 20_000_000
    attempts: int = 6
    length_unit: str = ""              # geometrinin gerçek birimi; boş -> tespit
    display_unit: str = "mm"           # ekranda/raporda gösterim birimi
    boundary_layers: bool = True
    show_fluent_gui: bool = True       # meshlemeyi Fluent penceresinden izle
    keep_fluent_open: bool = False     # bitince Fluent açık kalsın
    dry_run: bool = False
    scenario: str = "realistic"
    use_advisor: bool = False
    ansys_version: str = ""

    # Akış bilgisi (opsiyonel, sınır tabakası için)
    y_plus: str = ""                   # metin: boş bırakılabilsin diye
    velocity: str = ""
    density: str = "1.225"
    viscosity: str = "1.7894e-05"
    characteristic_length: str = ""

    config_file: str = ""              # ek YAML/JSON konfigürasyon

    # "Ölçüm ve öneriler" ekranından seçilen kademe (boşsa agent kendi seçer)
    chosen_label: str = ""
    chosen_min_size: float = 0.0       # m
    chosen_max_size: float = 0.0       # m
    chosen_cells: int = 0

    # "Yüzey boyutları" ekranında verilen kararlar
    local_sizing_enabled: bool = True
    sizing_divisions: Dict[str, float] = field(default_factory=dict)
    sizing_disabled: List[str] = field(default_factory=list)
    local_floor: float = 0.0           # m, hiçbir yerel boyut bundan ince olmasın
    open_in_spaceclaim: bool = False   # analiz bitince SpaceClaim'de aç

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GuiSettings":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})

    # ------------------------------------------------------------------
    def save(self, path: str = SETTINGS_PATH) -> None:
        """Ayarları diske yaz. Yazamazsak sessizce vazgeçeriz - bu bir kolaylık,
        arayüzün çalışmasının şartı değil."""
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
        except OSError:
            pass

    @classmethod
    def load(cls, path: str = SETTINGS_PATH) -> "GuiSettings":
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return cls.from_dict(json.load(fh))
        except (OSError, ValueError):
            return cls()

    # ------------------------------------------------------------------
    def validate(self) -> List[str]:
        """Çalıştırmadan önce kullanıcıya gösterilecek hatalar."""
        problems: List[str] = []
        if not self.geometry_path:
            problems.append("Geometri dosyası seçilmedi.")
        elif not os.path.isfile(self.geometry_path):
            problems.append("Geometri dosyası bulunamadı: {0}".format(self.geometry_path))
        if self.cores < 1:
            problems.append("Çekirdek sayısı en az 1 olmalı.")
        if self.attempts < 1:
            problems.append("Deneme sayısı en az 1 olmalı.")
        if self.max_cells < 1000:
            problems.append("Hücre sınırı en az 1000 olmalı.")
        if self.config_file and not os.path.isfile(self.config_file):
            problems.append("Konfigürasyon dosyası bulunamadı: {0}".format(self.config_file))
        for label, value in (("Geometri birimi", self.length_unit),
                             ("Gösterim birimi", self.display_unit)):
            text = (value or "").strip().lower()
            if text and text != "auto" and text not in DISPLAY_UNITS:
                problems.append("{0} tanınmıyor: {1!r}".format(label, value))
        for label, value in (("y+", self.y_plus), ("Hız", self.velocity),
                             ("Yoğunluk", self.density), ("Viskozite", self.viscosity),
                             ("Karakteristik uzunluk", self.characteristic_length)):
            if value.strip() and _to_float(value) is None:
                problems.append("{0} sayı olmalı: {1!r}".format(label, value))
        if self.y_plus.strip() and not self.velocity.strip():
            problems.append("y+ hedefi için hız da gerekli (yoksa boş bırakın).")
        return problems

    # ------------------------------------------------------------------
    def to_config(self) -> Config:
        """Seçimleri AutoMesh konfigürasyonuna çevir."""
        cfg = Config.load(self.config_file) if self.config_file else Config()

        cfg.fluent.processor_count = int(self.cores)
        cfg.fluent.use_mock = bool(self.dry_run)
        if self.dry_run:
            cfg.fluent.mock_scenario = self.scenario
        if self.show_fluent_gui or self.keep_fluent_open:
            cfg.fluent.show_gui = True
            cfg.fluent.ui_mode = "gui"
        cfg.fluent.keep_open_after_run = bool(self.keep_fluent_open)
        if self.ansys_version.strip():
            cfg.fluent.product_version = self.ansys_version.strip()

        cfg.geometry.open_in_spaceclaim = bool(self.open_in_spaceclaim)
        cfg.local_sizing.enabled = bool(self.local_sizing_enabled)
        if self.sizing_divisions:
            cfg.local_sizing.divisions = dict(self.sizing_divisions)
        if self.sizing_disabled:
            cfg.local_sizing.disabled = list(self.sizing_disabled)
        if self.local_floor > 0:
            cfg.local_sizing.absolute_floor = float(self.local_floor)

        if self.chosen_min_size > 0 and self.chosen_max_size > 0:
            cfg.planning.override_min_size = self.chosen_min_size
            cfg.planning.override_max_size = self.chosen_max_size

        cfg.planning.workflow = self.workflow
        cfg.planning.volume_fill = self.volume_fill
        cfg.planning.max_cell_count = int(self.max_cells)
        cfg.planning.boundary_layers = bool(self.boundary_layers)
        cfg.autonomy.max_attempts = int(self.attempts)
        cfg.advisor.enabled = bool(self.use_advisor)

        if self.length_unit.strip():
            cfg.geometry.length_unit = self.length_unit.strip()
        cfg.output.display_unit = self.display_unit.strip() or "mm"
        for attribute, raw in (
            ("y_plus_target", self.y_plus),
            ("velocity", self.velocity),
            ("characteristic_length", self.characteristic_length),
        ):
            value = _to_float(raw)
            if value is not None:
                setattr(cfg.geometry, attribute, value)
        for attribute, raw in (("density", self.density), ("viscosity", self.viscosity)):
            value = _to_float(raw)
            if value is not None:
                setattr(cfg.geometry, attribute, value)
        return cfg

    # ------------------------------------------------------------------
    def run_directory(self) -> Optional[str]:
        """Çalışma dizini; boşsa orkestratör kendi tarih damgalı klasörünü kurar."""
        return self.output_dir.strip() or None

    def equivalent_command(self) -> str:
        """Aynı işi yapan komut satırı - kullanıcı kopyalayıp saklayabilsin."""
        parts = ["automesh", "run", _quote(self.geometry_path)]
        if self.dry_run:
            parts += ["--dry-run", "--scenario", self.scenario]
        parts += ["--cores", str(self.cores)]
        if self.keep_fluent_open:
            parts.append("--keep-open")
        elif self.show_fluent_gui:
            parts.append("--gui")
        if self.workflow != "auto":
            parts += ["--workflow", self.workflow]
        if self.volume_fill != "poly-hexcore":
            parts += ["--fill", self.volume_fill]
        if self.max_cells != 20_000_000:
            parts += ["--max-cells", str(self.max_cells)]
        if self.attempts != 6:
            parts += ["--attempts", str(self.attempts)]
        if not self.boundary_layers:
            parts.append("--no-boundary-layers")
        if self.length_unit.strip():
            parts += ["--unit", self.length_unit.strip()]
        if self.display_unit.strip() and self.display_unit.strip() != "mm":
            parts += ["--show-unit", self.display_unit.strip()]
        for flag, raw in (("--y-plus", self.y_plus), ("--velocity", self.velocity),
                          ("--length", self.characteristic_length)):
            if raw.strip():
                parts += [flag, raw.strip()]
        if self.use_advisor:
            parts.append("--advisor")
        if self.ansys_version.strip():
            parts += ["--version-ansys", self.ansys_version.strip()]
        if self.config_file.strip():
            parts += ["--config", _quote(self.config_file.strip())]
        if self.chosen_min_size > 0 and self.chosen_max_size > 0:
            parts += ["--min-size", "{0:.6g}".format(self.chosen_min_size),
                      "--max-size", "{0:.6g}".format(self.chosen_max_size)]
        if self.open_in_spaceclaim:
            parts.append("--open-cad")
        if not self.local_sizing_enabled:
            parts.append("--no-local-sizing")
        for name, value in sorted(self.sizing_divisions.items()):
            parts += ["--divisions", "{0}={1:g}".format(name, value)]
        if self.local_floor > 0:
            parts += ["--min-local-size", "{0:.6g}".format(self.local_floor)]
        if self.output_dir.strip():
            parts += ["--out", _quote(self.output_dir.strip())]
        return " ".join(parts)

    def chosen_summary(self) -> str:
        """Ana pencerede gösterilecek tek satırlık seçim özeti."""
        if not self.chosen_label:
            return "Mesh kademesi: otomatik (agent geometriden seçecek)"
        from ..units import format_length

        unit = self.display_unit.strip() or "mm"
        return "Seçili kademe: {0}  |  {1} - {2}  |  ~{3:,} hücre".format(
            self.chosen_label,
            format_length(self.chosen_min_size, unit),
            format_length(self.chosen_max_size, unit),
            self.chosen_cells)

    def clear_choice(self) -> None:
        self.chosen_label = ""
        self.chosen_min_size = 0.0
        self.chosen_max_size = 0.0
        self.chosen_cells = 0

    def sizing_summary(self) -> str:
        """Ana penceredeki yüzey boyutu özeti."""
        if not self.local_sizing_enabled:
            return "Yüzey boyutları: kapalı (her yerde global boyut)"
        if not self.sizing_divisions and not self.sizing_disabled:
            return "Yüzey boyutları: otomatik (önerilen bölme sayıları)"
        parts = []
        if self.sizing_divisions:
            parts.append("{0} grupta bölme değiştirildi".format(
                len(self.sizing_divisions)))
        if self.sizing_disabled:
            parts.append("{0} kontrol kapatıldı".format(len(self.sizing_disabled)))
        return "Yüzey boyutları: " + ", ".join(parts)

    def clear_sizing_choices(self) -> None:
        self.sizing_divisions = {}
        self.sizing_disabled = []
        self.local_floor = 0.0


# --------------------------------------------------------------------------

def _to_float(raw: str) -> Optional[float]:
    text = (raw or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _quote(path: str) -> str:
    return '"{0}"'.format(path) if " " in path else path
