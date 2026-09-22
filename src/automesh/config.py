"""Configuration objects and the YAML/JSON loader.

The defaults below are meant to produce a usable CFD mesh on a "normal"
internal-flow part without any user input at all.  Everything can be
overridden from a config file or from the command line.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import VolumeFill, WorkflowType


# --------------------------------------------------------------------------

@dataclass
class QualityThresholds:
    """Accept/repair/remesh limits.

    ``*_hard`` values mark the point where the mesh is not worth repairing
    and the geometry has to be remeshed with different parameters.
    """

    # Volume mesh
    max_skewness_good: float = 0.80
    max_skewness_accept: float = 0.90
    max_skewness_hard: float = 0.98
    min_orthogonal_good: float = 0.20
    min_orthogonal_accept: float = 0.10
    min_orthogonal_hard: float = 0.01
    max_aspect_ratio_accept: float = 100.0
    max_aspect_ratio_hard: float = 1000.0

    # Surface mesh (triangles are judged more strictly - a bad surface mesh
    # poisons every volume mesh built on top of it)
    surface_max_skewness_good: float = 0.60
    surface_max_skewness_accept: float = 0.80
    surface_max_skewness_hard: float = 0.95

    # Hard stops that are never acceptable
    allow_negative_volume: bool = False
    allow_left_handed_faces: bool = False


@dataclass
class FluentSettings:
    """How to launch and drive Fluent."""

    product_version: Optional[str] = None      # e.g. "24.2.0"; None -> newest found
    processor_count: int = 4
    precision: str = "double"                  # single | double
    ui_mode: str = "no_gui"                    # no_gui | hidden_gui | gui
    show_gui: bool = False
    dimension: int = 3
    launch_timeout_s: int = 600
    cleanup_on_exit: bool = True
    #: Çalışma bitince Fluent'i açık bırak - ağı orada incelemek için.
    #: Fluent'i sonra elle kapatmanız gerekir.
    keep_open_after_run: bool = False
    env: Dict[str, str] = field(default_factory=dict)
    additional_arguments: str = ""
    awp_root: Optional[str] = None             # AWP_ROOTxxx override
    use_mock: bool = False                     # dry-run without ANSYS
    mock_scenario: str = "realistic"
    journal_only: bool = False                 # emit a .py journal, do not launch


@dataclass
class GeometrySettings:
    """Geometry analysis and CAD preparation."""

    analyzer: str = "auto"                     # auto | spaceclaim | discrete | fallback
    spaceclaim_exe: Optional[str] = None       # full path to SpaceClaim.exe
    spaceclaim_script_api: str = "251"
    spaceclaim_timeout_s: int = 900
    #: SpaceClaim'in Fluent için yazacağı format.
    #: "auto" -> yüzey grupları açıksa .scdoc (grupları taşır), yoksa STEP.
    export_format: str = "auto"
    length_unit: Optional[str] = None          # override the detected unit
    # Flow hints - purely optional, they sharpen the boundary layer sizing.
    velocity: Optional[float] = None           # m/s
    density: float = 1.225                     # kg/m^3
    viscosity: float = 1.7894e-5               # Pa.s
    characteristic_length: Optional[float] = None  # m, defaults to bbox diagonal
    y_plus_target: Optional[float] = None      # e.g. 1 for resolved, 30-300 for wall functions
    internal_flow: Optional[bool] = None       # None -> auto-detect


@dataclass
class PlanningSettings:
    """Knobs of the sizing heuristics."""

    workflow: str = "auto"                     # auto | watertight | fault-tolerant
    volume_fill: str = "poly-hexcore"
    target_cell_count: int = 0                 # 0 -> unconstrained
    max_cell_count: int = 20_000_000
    min_cells_across_feature: float = 3.0
    max_size_divisor: float = 40.0             # coarse end: diagonal / this
    max_size_divisor_complex: float = 200.0    # fine end for complex models
    growth_rate_range: List[float] = field(default_factory=lambda: [1.15, 1.20])
    curvature_angle_range: List[float] = field(default_factory=lambda: [12.0, 18.0])
    cells_per_gap_range: List[float] = field(default_factory=lambda: [2.0, 3.0])
    boundary_layers: bool = True
    layer_count_range: List[int] = field(default_factory=lambda: [5, 12])
    bl_growth_rate: float = 1.2
    size_min_max_ratio_cap: float = 500.0      # min_size >= max_size / this

    #: Hazır kademe: preview | coarse | balanced | fine | very_fine
    #: (boş -> agent'ın hesapladığı denge noktası kullanılır)
    level: str = ""

    # --- kullanıcı dayatmaları (0 / None -> otomatik) --------------------
    # Arayüzdeki "öneri seç" ekranı ve CLI'daki --min-size/--max-size bunları
    # doldurur; heuristikler hesaplanır, sonra bunlar üstüne yazılır.
    override_min_size: float = 0.0             # m
    override_max_size: float = 0.0             # m
    override_growth_rate: float = 0.0
    override_layer_count: int = -1             # -1 -> dokunma


@dataclass
class LocalSizingSettings:
    """Yüzey gruplarına özel hücre boyutu.

    SpaceClaim'de yüzeyler tipine ve yarıçapına göre gruplanır (named
    selection), sonra her grup Fluent'te kendi Face Size kontrolünü alır.
    """

    enabled: bool = True
    name_prefix: str = "automesh"
    #: Bir deliğin/filetonun çevresinde istenen hücre sayısı (curvature ölçütü).
    cells_per_circle: float = 16.0
    #: Dar bir bandın (2·alan/çevre) enine istenen hücre sayısı (width ölçütü).
    cells_across_width: float = 3.0
    #: İnce kesitte (2·Hacim/Alan) istenen hücre sayısı (gap ölçütü).
    cells_across_gap: float = 3.0
    #: Gövde köşegeninin bu oranından kaba boyutlar gruplanmaz.
    max_useful_ratio: float = 0.03
    #: Kullanıcının kendi named selection'larını oku ve onlara da boyut öner.
    #: Bu gruplar asla değiştirilmez; yalnızca ölçülüp boyut önerilir.
    read_existing_groups: bool = True
    #: Mevcut gruplar için de Face Size kontrolü üretilsin mi.
    size_existing_groups: bool = True
    #: En fazla kaç kontrol üretilsin (her biri mesh süresini uzatır).
    max_controls: int = 8
    #: Yarıçap bantlarının oranı (2.0 -> her bant bir öncekinin iki katı).
    band_factor: float = 2.0
    #: Bantlamanın başlangıç yarıçapı (m).
    band_anchor: float = 1.0e-4
    #: Bir grubun oluşması için gereken en az yüzey sayısı.
    min_faces_per_group: int = 2
    #: Gövde köşegeninin bu oranından büyük yarıçaplar gruplanmaz;
    #: onlar zaten global boyutla çözülür.
    radius_ceiling_ratio: float = 0.08
    #: Yerel boyut global maksimumun 1/bu değerinden ince olamaz.
    min_size_ratio: float = 200.0
    #: Global boyutun bu oranına yakın kontroller eklenmez (faydasız).
    skip_above_ratio: float = 0.9

    # --- güvenlik ve kullanıcı kararı ------------------------------------
    #: Mutlak taban (m). Hiçbir yerel boyut bunun altına inmez.
    #: 0 -> yalnızca ``min_size_ratio`` tabanı geçerli.
    absolute_floor: float = 0.0
    #: Global boyuttan bu kadar ince kontroller "riskli" işaretlenir.
    warn_ratio: float = 60.0
    #: Bu orandan ince kontroller "yüksek risk": Fluent zorlanabilir.
    high_risk_ratio: float = 150.0
    #: Bir kontrolün yüzeyinde tahmini hücre sayısı bunu aşarsa uyarılır.
    warn_face_cells: int = 2_000_000
    #: Yüksek riskli kontroller otomatik olarak tabana çekilsin mi?
    #: false -> uyarılır ama olduğu gibi bırakılır (kullanıcı karar verir).
    clamp_high_risk: bool = True

    #: Kullanıcının gözden geçirme ekranında verdiği bölme sayıları
    #: (grup adı -> kaça bölünecek). Hücre boyutu buradan hesaplanır.
    divisions: Dict[str, float] = field(default_factory=dict)
    #: Kullanıcının kapattığı kontroller.
    disabled: List[str] = field(default_factory=list)


@dataclass
class AutonomySettings:
    """How stubborn the agent is allowed to be."""

    max_attempts: int = 6                      # full remesh attempts
    max_repairs_per_attempt: int = 3           # in-place repair passes
    allow_workflow_switch: bool = True         # watertight -> fault-tolerant
    allow_boundary_layer_drop: bool = True     # last resort: mesh without prisms
    allow_volume_fill_downgrade: bool = True   # poly-hexcore -> poly -> tet
    stop_on_first_acceptable: bool = True
    improve_on_acceptable: bool = True         # one cheap polish pass when merely OK
    hard_timeout_s: int = 7200                 # whole run


@dataclass
class AdvisorSettings:
    """Optional Claude-powered advisor for errors the rule base misses."""

    enabled: bool = False
    model: str = "claude-opus-5"
    max_tokens: int = 8000
    effort: str = "high"
    api_key_env: str = "ANTHROPIC_API_KEY"
    transcript_chars: int = 20000
    timeout_s: float = 180.0


@dataclass
class OutputSettings:
    run_root: str = "runs"
    mesh_format: str = "msh.h5"                # msh.h5 | msh | cas.h5
    write_intermediate: bool = True            # save after surface mesh too
    keep_transcript: bool = True
    report_language: str = "tr"                # tr | en
    #: Ekranda ve raporda uzunlukların gösterileceği birim.
    #: Fluent'e bildirilen içe aktarma biriminden bağımsızdır.
    #: "auto" -> model boyutuna göre seçilir.
    display_unit: str = "mm"


@dataclass
class Config:
    fluent: FluentSettings = field(default_factory=FluentSettings)
    geometry: GeometrySettings = field(default_factory=GeometrySettings)
    planning: PlanningSettings = field(default_factory=PlanningSettings)
    local_sizing: LocalSizingSettings = field(default_factory=LocalSizingSettings)
    quality: QualityThresholds = field(default_factory=QualityThresholds)
    autonomy: AutonomySettings = field(default_factory=AutonomySettings)
    advisor: AdvisorSettings = field(default_factory=AdvisorSettings)
    output: OutputSettings = field(default_factory=OutputSettings)

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        cfg = cls()
        for section, value in (data or {}).items():
            if not hasattr(cfg, section):
                raise ValueError("unknown configuration section: {0!r}".format(section))
            target = getattr(cfg, section)
            known = {f.name for f in dataclasses.fields(target)}
            for key, val in (value or {}).items():
                if key not in known:
                    raise ValueError(
                        "unknown option {0!r} in section {1!r}".format(key, section)
                    )
                setattr(target, key, val)
        return cfg

    @classmethod
    def load(cls, path: Optional[str]) -> "Config":
        if not path:
            return cls()
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        return cls.from_dict(parse_config_text(text, path))

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    def resolved_workflow(self) -> Optional[WorkflowType]:
        if self.planning.workflow in ("auto", "", None):
            return None
        return WorkflowType(self.planning.workflow)

    def resolved_volume_fill(self) -> VolumeFill:
        return VolumeFill(self.planning.volume_fill)


# --------------------------------------------------------------------------

def parse_config_text(text: str, path: str = "<config>") -> Dict[str, Any]:
    """Parse YAML when PyYAML is around, JSON otherwise."""
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return json.loads(text)
    try:
        import yaml  # type: ignore
    except ImportError:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            "{0} looks like YAML but PyYAML is not installed; "
            "run `pip install PyYAML` or use a JSON config".format(path)
        )
    return yaml.safe_load(text) or {}


def apply_overrides(cfg: Config, overrides: List[str]) -> Config:
    """Apply ``section.key=value`` strings coming from ``--set``."""
    for item in overrides or []:
        if "=" not in item:
            raise ValueError("override must look like section.key=value, got {0!r}".format(item))
        dotted, raw = item.split("=", 1)
        if "." not in dotted:
            raise ValueError("override must look like section.key=value, got {0!r}".format(item))
        section, key = dotted.split(".", 1)
        if not hasattr(cfg, section):
            raise ValueError("unknown configuration section: {0!r}".format(section))
        target = getattr(cfg, section)
        if not hasattr(target, key):
            raise ValueError("unknown option {0!r} in section {1!r}".format(key, section))
        current = getattr(target, key)
        setattr(target, key, _coerce(raw, current))
    return cfg


def _coerce(raw: str, current: Any) -> Any:
    if isinstance(current, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on", "evet")
    if isinstance(current, int) and not isinstance(current, bool):
        return int(float(raw))
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, list):
        return [_coerce(p.strip(), current[0] if current else "") for p in raw.split(",")]
    if isinstance(current, dict):
        return json.loads(raw)
    if current is None:
        # Best effort: keep numbers numeric, everything else a string.
        low = raw.strip().lower()
        if low in ("none", "null", ""):
            return None
        if low in ("true", "false"):
            return low == "true"
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            return float(raw)
        except ValueError:
            return raw
    return raw


def default_config_path() -> Optional[str]:
    """Look for ``automesh.yaml``/``automesh.json`` next to the working dir."""
    for name in ("automesh.yaml", "automesh.yml", "automesh.json"):
        if os.path.isfile(name):
            return name
    return None
