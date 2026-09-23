"""Command line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import __version__
from .config import Config, apply_overrides, default_config_path
from .logging_utils import setup_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="automesh",
        description=(
            "SpaceClaim geometrilerini analiz edip ANSYS Fluent Meshing'de "
            "otonom olarak mesh üreten agent."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Örnekler:\n"
            "  automesh run manifold.scdoc\n"
            "  automesh run manifold.stp --cores 8 --y-plus 1 --velocity 12\n"
            "  automesh run manifold.stp --dry-run --scenario prism\n"
            "  automesh plan manifold.stp\n"
            "  automesh diagnose fluent-transcript.trn --stage volume\n"
            "  automesh doctor                   (ortam kontrolü)\n"
            "  automesh gui                      (masaüstü arayüzü)\n"
            "  automesh propose manifold.stp     (ölçümler + mesh kademeleri)\n"
            "  automesh run manifold.stp --level fine\n"
            "  automesh run manifold.scdoc --divisions inlet=24\n"
            "  automesh run manifold.scdoc --simple   (yüzey isimlendirme yok)\n"
        ),
    )
    parser.add_argument("--version", action="version",
                        version="automesh {0}".format(__version__))
    sub = parser.add_subparsers(dest="command", required=True)

    # ---- shared options ------------------------------------------------
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", help="YAML/JSON konfigürasyon dosyası")
    common.add_argument("--set", dest="overrides", action="append", default=[],
                        metavar="BÖLÜM.ANAHTAR=DEĞER",
                        help="Tek bir ayarı geçersiz kıl (birden çok kez verilebilir)")
    common.add_argument("--log-level", default="info",
                        choices=("debug", "info", "warning", "error"))
    common.add_argument("--open-cad", action="store_true",
                        help="Analiz bitince hazırlanan dosyayı SpaceClaim'de "
                             "aç (grupları Groups panelinden görmek için)")
    common.add_argument("--show-spaceclaim", action="store_true",
                        help="Analiz koşusunu görünür çalıştır (pencere açılır "
                             "ve betik bitince kapanır)")

    # ---- run -----------------------------------------------------------
    run = sub.add_parser("run", parents=[common],
                         help="Geometriyi otonom olarak meshle")
    run.add_argument("geometry", help="CAD dosyası (.scdoc/.stp/.x_t/.stl ...)")
    run.add_argument("-o", "--out", help="Çalışma dizini (varsayılan: runs/<zaman>-<ad>)")
    run.add_argument("--dry-run", action="store_true",
                     help="Fluent'i başlatmadan simülasyon sürücüsüyle çalış")
    run.add_argument("--scenario", choices=(
        "clean", "realistic", "dirty", "prism", "memory", "stubborn"),
        help="--dry-run için simülasyon senaryosu")
    run.add_argument("--cores", type=int, help="Fluent çekirdek sayısı")
    run.add_argument("--gui", action="store_true",
                     help="Fluent arayüzünü göster (meshlemeyi canlı izle)")
    run.add_argument("--keep-open", action="store_true",
                     help="Bitince Fluent'i açık bırak (ağı incelemek için)")
    run.add_argument("--version-ansys", dest="ansys_version",
                     help="ANSYS sürümü, örn. 24.2.0")
    run.add_argument("--workflow", choices=("auto", "watertight", "fault-tolerant"),
                     help="Meshing akışını zorla")
    run.add_argument("--fill", choices=("poly-hexcore", "polyhedra", "hexcore",
                                        "tetrahedral"),
                     help="Hacim doldurma tipi")
    run.add_argument("--level", choices=("preview", "coarse", "balanced", "fine",
                                        "very_fine"),
                     help="Hazır mesh kademesi (automesh propose ile listelenir)")
    run.add_argument("--min-size", help="Minimum hücre boyutu, örn. 0.4mm veya 0.0004")
    run.add_argument("--max-size", help="Maksimum hücre boyutu, örn. 3mm")
    run.add_argument("--growth", type=float, help="Büyüme oranını zorla, örn. 1.15")
    run.add_argument("--divisions", action="append", default=[], metavar="GRUP=N",
                     help="Bir yüzey grubunun bölme sayısı, örn. inlet=24 "
                          "(birden çok kez verilebilir)")
    run.add_argument("--simple", action="store_true",
                     help="Basit mod: yüzey isimlendirme/gruplama hiç "
                          "çalışmaz, yalnızca global boyutlandırma")
    run.add_argument("--no-local-sizing", action="store_true",
                     help="Yüzey gruplarına özel boyut verme")
    run.add_argument("--min-local-size",
                     help="Hiçbir yerel boyut bundan ince olmasın, örn. 0.2mm")
    run.add_argument("--layers", type=int, help="Prizma katman sayısını zorla (0 = kapalı)")
    run.add_argument("--max-cells", type=int, help="Hücre sayısı üst sınırı")
    run.add_argument("--target-cells", type=int, help="Hedeflenen hücre sayısı")
    run.add_argument("--attempts", type=int, help="Maksimum yeniden mesh denemesi")
    run.add_argument("--no-boundary-layers", action="store_true",
                     help="Prizma katmanlarını hiç kurma")
    run.add_argument("--y-plus", type=float, help="Hedef y+ değeri")
    run.add_argument("--velocity", type=float, help="Karakteristik hız [m/s]")
    run.add_argument("--density", type=float, help="Yoğunluk [kg/m3]")
    run.add_argument("--viscosity", type=float, help="Dinamik viskozite [Pa.s]")
    run.add_argument("--length", type=float, help="Karakteristik uzunluk [m]")
    run.add_argument("--unit", help="Geometrinin gerçek birimi (Fluent'e bildirilir)")
    run.add_argument("--show-unit", dest="display_unit",
                     help="Ekranda/raporda gösterim birimi (varsayılan mm, 'auto' da olur)")
    run.add_argument("--advisor", action="store_true",
                     help="Bilinmeyen hatalarda Claude danışmanını kullan")

    # ---- analyze / plan --------------------------------------------------
    analyze = sub.add_parser("analyze", parents=[common],
                             help="Sadece geometriyi analiz et")
    analyze.add_argument("geometry")
    analyze.add_argument("--json", action="store_true", help="Ham JSON bas")

    plan = sub.add_parser("plan", parents=[common],
                          help="Geometriyi analiz et ve mesh planını göster")
    plan.add_argument("geometry")
    plan.add_argument("--json", action="store_true")
    plan.add_argument("--y-plus", type=float)
    plan.add_argument("--velocity", type=float)
    plan.add_argument("--max-cells", type=int)

    propose = sub.add_parser("propose", parents=[common],
                             help="Ölçümleri ve seçilebilir mesh kademelerini göster")
    propose.add_argument("geometry")
    propose.add_argument("--json", action="store_true")
    propose.add_argument("--max-cells", type=int, help="Hücre bütçesi (uyarı için)")
    propose.add_argument("--divisions", action="append", default=[],
                         metavar="GRUP=N",
                         help="Bir grubun bölme sayısını deneyerek gör")

    # ---- diagnose --------------------------------------------------------
    diagnose = sub.add_parser("diagnose", parents=[common],
                              help="Bir Fluent çıktısını kural tabanıyla teşhis et")
    diagnose.add_argument("logfile", help="Transcript dosyası ('-' ile stdin)")
    diagnose.add_argument("--stage", default="any",
                          choices=("any", "import", "surface", "boundary-layer", "volume"))

    # ---- gui -------------------------------------------------------------
    sub.add_parser("gui", help="Masaüstü arayüzünü aç")

    # ---- doctor ----------------------------------------------------------
    doctor = sub.add_parser("doctor", parents=[common],
                            help="Ortamı kontrol et (Python, PyFluent, ANSYS, SpaceClaim)")
    doctor.add_argument("--add-path", metavar="KLASÖR",
                        help="Bu klasörü kalıcı olarak Python yoluna ekle "
                             "(her CMD'de set PYTHONPATH yazmaya son)")
    doctor.add_argument("--remove-path", metavar="KLASÖR",
                        help="Daha önce eklenmiş bir klasörü yoldan çıkar")

    # ---- rules / config --------------------------------------------------
    sub.add_parser("rules", help="Teşhis kural tabanını listele")

    config_cmd = sub.add_parser("config", help="Örnek konfigürasyon dosyası üret")
    config_cmd.add_argument("-o", "--out", default="automesh.json")
    return parser


# --------------------------------------------------------------------------

def _load_config(args: argparse.Namespace) -> Config:
    path = getattr(args, "config", None) or default_config_path()
    cfg = Config.load(path)
    apply_overrides(cfg, getattr(args, "overrides", []) or [])
    if getattr(args, "open_cad", False):
        cfg.geometry.open_in_spaceclaim = True
    if getattr(args, "show_spaceclaim", False):
        cfg.geometry.spaceclaim_headless = False
    return cfg


def _apply_run_flags(cfg: Config, args: argparse.Namespace) -> Config:
    if args.dry_run:
        cfg.fluent.use_mock = True
    if args.scenario:
        cfg.fluent.mock_scenario = args.scenario
        cfg.fluent.use_mock = True
    if args.cores:
        cfg.fluent.processor_count = args.cores
    if args.gui:
        cfg.fluent.show_gui = True
        cfg.fluent.ui_mode = "gui"
    if args.keep_open:
        cfg.fluent.keep_open_after_run = True
        cfg.fluent.show_gui = True
        cfg.fluent.ui_mode = "gui"
    if args.ansys_version:
        cfg.fluent.product_version = args.ansys_version
    if args.workflow:
        cfg.planning.workflow = args.workflow
    if args.fill:
        cfg.planning.volume_fill = args.fill
    if args.level:
        cfg.planning.level = args.level
    if args.min_size:
        cfg.planning.override_min_size = parse_length(args.min_size)
    if args.max_size:
        cfg.planning.override_max_size = parse_length(args.max_size)
    if args.growth:
        cfg.planning.override_growth_rate = args.growth
    if args.layers is not None:
        cfg.planning.override_layer_count = args.layers
    if args.no_local_sizing or getattr(args, "simple", False):
        cfg.local_sizing.enabled = False
    if getattr(args, "simple", False):
        # Basit modda SpaceClaim dokümanına hiç dokunulmaz.
        cfg.geometry.open_in_spaceclaim = False
    if args.min_local_size:
        cfg.local_sizing.absolute_floor = parse_length(args.min_local_size)
    for item in args.divisions or []:
        if "=" not in item:
            raise ValueError(
                "--divisions GRUP=N biçiminde olmalı, alınan: {0!r}".format(item))
        name, value = item.split("=", 1)
        cfg.local_sizing.divisions[name.strip()] = float(value)
    if args.max_cells:
        cfg.planning.max_cell_count = args.max_cells
    if args.target_cells:
        cfg.planning.target_cell_count = args.target_cells
    if args.attempts:
        cfg.autonomy.max_attempts = args.attempts
    if args.no_boundary_layers:
        cfg.planning.boundary_layers = False
    if args.y_plus:
        cfg.geometry.y_plus_target = args.y_plus
    if args.velocity:
        cfg.geometry.velocity = args.velocity
    if args.density:
        cfg.geometry.density = args.density
    if args.viscosity:
        cfg.geometry.viscosity = args.viscosity
    if args.length:
        cfg.geometry.characteristic_length = args.length
    if args.unit:
        cfg.geometry.length_unit = args.unit
    if args.display_unit:
        cfg.output.display_unit = args.display_unit
    if args.advisor:
        cfg.advisor.enabled = True
    return cfg


# --------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    from .orchestrator import run_agent

    cfg = _apply_run_flags(_load_config(args), args)
    result = run_agent(args.geometry, cfg, args.out)

    print()
    print("Rapor : {0}".format(os.path.join(result.run_dir, "report.md")))
    if result.mesh_file:
        print("Mesh  : {0}".format(result.mesh_file))
    print("Sonuç : {0}".format(result.message))
    return 0 if result.success else 1


def cmd_analyze(args: argparse.Namespace) -> int:
    from .geometry import analyze_geometry

    cfg = _load_config(args)
    metrics = analyze_geometry(args.geometry, cfg)
    if args.json:
        print(json.dumps(metrics.to_dict(), indent=2, ensure_ascii=False))
        return 0
    _print_metrics(metrics, cfg)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    from .geometry import analyze_geometry
    from .planning.sizing import plan_mesh

    cfg = _load_config(args)
    if getattr(args, "y_plus", None):
        cfg.geometry.y_plus_target = args.y_plus
    if getattr(args, "velocity", None):
        cfg.geometry.velocity = args.velocity
    if getattr(args, "max_cells", None):
        cfg.planning.max_cell_count = args.max_cells

    metrics = analyze_geometry(args.geometry, cfg)
    plan = plan_mesh(metrics, cfg)
    if args.json:
        print(json.dumps({"geometry": metrics.to_dict(), "plan": plan.to_dict()},
                         indent=2, ensure_ascii=False))
        return 0
    _print_metrics(metrics, cfg)
    _print_plan(plan, cfg, metrics.diagonal)
    return 0


def cmd_propose(args: argparse.Namespace) -> int:
    from .geometry import analyze_geometry
    from .planning import build_proposals, format_table, measurements

    cfg = _load_config(args)
    if getattr(args, "max_cells", None):
        cfg.planning.max_cell_count = args.max_cells

    for item in getattr(args, "divisions", []) or []:
        if "=" in item:
            name, value = item.split("=", 1)
            cfg.local_sizing.divisions[name.strip()] = float(value)

    metrics = analyze_geometry(args.geometry, cfg)
    proposals = build_proposals(metrics, cfg)
    if args.json:
        print(json.dumps({
            "geometry": metrics.to_dict(),
            "measurements": [m.__dict__ for m in measurements(metrics, cfg=cfg)],
            "proposals": [p.to_dict() for p in proposals],
            "local_sizing": _local_sizing_payload(metrics, cfg),
        }, indent=2, ensure_ascii=False))
        return 0
    print()
    print(format_table(metrics, proposals))
    print()

    if metrics.face_groups:
        from .planning.local_sizing import format_review_table, review_groups
        from .planning.sizing import plan_mesh
        from .units import resolve_display_unit

        plan = plan_mesh(metrics, cfg)
        unit = resolve_display_unit(cfg.output.display_unit, metrics.diagonal)
        print(format_review_table(review_groups(metrics.face_groups, plan, cfg),
                                  plan, unit))
        print()

    print("Seçtiğiniz kademeyle çalıştırmak için:")
    print('  automesh run "{0}" --level <kademe>'.format(args.geometry))
    print()
    return 0


def _local_sizing_payload(metrics, cfg) -> list:
    if not metrics.face_groups:
        return []
    from .planning.local_sizing import review_groups
    from .planning.sizing import plan_mesh

    plan = plan_mesh(metrics, cfg)
    return [r.to_dict() for r in review_groups(metrics.face_groups, plan, cfg)]


def cmd_diagnose(args: argparse.Namespace) -> int:
    from .diagnostics.knowledge_base import diagnose

    if args.logfile == "-":
        text = sys.stdin.read()
    else:
        with open(args.logfile, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()

    diagnoses = diagnose(text, args.stage, max_results=10)
    if not diagnoses:
        print("Bilinen bir hata örüntüsü bulunamadı.")
        return 1
    for item in diagnoses:
        print("[{0}] {1}  ({2}, {3})".format(
            item.rule_id, item.title, item.stage, item.severity))
        print("  Açıklama : {0}".format(item.explanation.replace("\n", "\n             ")))
        if item.evidence:
            print("  Kanıt    : {0}".format(item.evidence))
        print("  Çözüm    :")
        for action in item.actions:
            print("    - {0}  [{1}:{2}]".format(action.label(), action.kind,
                                                action.operation or "-"))
        print()
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from .guiapp import main as gui_main

    return gui_main()


def cmd_doctor(args: argparse.Namespace) -> int:
    from . import doctor as doctor_mod

    if args.remove_path:
        ok, message = doctor_mod.remove_path(args.remove_path)
        print(("[+] " if ok else "[x] ") + message)
        print()
        if not ok:
            return 2

    if args.add_path:
        ok, message = doctor_mod.add_path(args.add_path)
        print(("[+] " if ok else "[x] ") + message)
        print()
        if ok:
            print("Yeni bir komut istemi açıp doğrulayın:")
            print("  py -c \"import ansys.fluent.core as p; print(p.__version__)\"")
            print()
        else:
            return 2

    ready, lines = doctor_mod.summary()
    print()
    for line in lines:
        print(line)
    print()
    if ready:
        print("Her şey hazır: automesh gui ile başlayabilirsiniz.")
        return 0
    print("Eksikler var - yukarıdaki [x] satırlarına bakın.")
    return 1


def cmd_rules(args: argparse.Namespace) -> int:
    from .diagnostics.knowledge_base import RULES

    print("{0} teşhis kuralı tanımlı:\n".format(len(RULES)))
    for rule in RULES:
        print("- {0:<28} {1:<45} [{2}, {3}, {4} adım]".format(
            rule.id, rule.title, rule.stage, rule.severity, len(rule.steps)))
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = Config()
    cfg.save(args.out)
    print("Örnek konfigürasyon yazıldı: {0}".format(args.out))
    return 0


# --------------------------------------------------------------------------

_LENGTH_SUFFIXES = (
    ("mm", 1.0e-3), ("cm", 1.0e-2), ("um", 1.0e-6), ("nm", 1.0e-9),
    ("in", 0.0254), ("ft", 0.3048), ("m", 1.0),
)


def parse_length(text: str) -> float:
    """'0.4mm', '2 mm', '0.0004' -> metre.

    Birimsiz değerler metre sayılır; 'mm' yazmak isteyenin de canı yanmasın
    diye son ek kabul edilir.
    """
    raw = (text or "").strip().lower().replace(",", ".")
    if not raw:
        raise ValueError("boş uzunluk değeri")
    for suffix, factor in _LENGTH_SUFFIXES:
        if raw.endswith(suffix):
            number = raw[: -len(suffix)].strip()
            if not number:
                raise ValueError("uzunluk değeri eksik: {0!r}".format(text))
            return float(number) * factor
    return float(raw)


def _print_metrics(metrics, cfg=None) -> None:
    from .units import format_length, resolve_display_unit

    setting = cfg.output.display_unit if cfg is not None else "mm"
    unit = resolve_display_unit(setting, metrics.diagonal)
    dx, dy, dz = metrics.bbox.sizes
    print("Geometri analizi")
    print("  Kaynak            : {0}".format(os.path.basename(metrics.source_path)))
    print("  Analiz yöntemi    : {0}".format(metrics.analyzer))
    print("  Sınır kutusu      : {0} x {1} x {2}".format(
        format_length(dx, unit), format_length(dy, unit), format_length(dz, unit)))
    print("  Köşegen           : {0}".format(format_length(metrics.diagonal, unit)))
    if metrics.volume:
        print("  Hacim             : {0:.6g} m3".format(metrics.volume))
    if metrics.area:
        print("  Yüzey alanı       : {0:.6g} m2".format(metrics.area))
    print("  Gövde/yüzey/kenar : {0} / {1} / {2}".format(
        metrics.body_count, metrics.face_count, metrics.edge_count))
    print("  En küçük özellik  : {0}".format(format_length(metrics.min_feature_size, unit)))
    print("  Özellik aralığı   : 1:{0:.0f}".format(metrics.feature_span))
    print("  Eğrisel yüzey     : {0:.0%}".format(metrics.curved_face_ratio))
    print("  Su geçirmez       : {0}".format(
        "bilinmiyor" if metrics.watertight is None
        else ("evet" if metrics.watertight else "hayır")))
    print("  Karmaşıklık       : {0:.2f}".format(metrics.complexity()))
    for warning in metrics.warnings:
        print("  ! {0}".format(warning))
    print()


def _print_plan(plan, cfg=None, diagonal=0.0) -> None:
    from .units import format_length, resolve_display_unit

    setting = cfg.output.display_unit if cfg is not None else "mm"
    unit = resolve_display_unit(setting, diagonal or plan.max_size * 100)
    print("Mesh planı")
    print("  Akış              : {0}".format(plan.workflow.value))
    print("  Min / max boyut   : {0} / {1}".format(
        format_length(plan.min_size, unit), format_length(plan.max_size, unit)))
    print("  Büyüme oranı      : {0:.3f}".format(plan.growth_rate))
    print("  Boyut fonksiyonu  : {0}".format(plan.size_function.value))
    print("  Eğrilik açısı     : {0:.1f} derece".format(plan.curvature_normal_angle))
    print("  Boşluk/hücre      : {0:.1f}".format(plan.cells_per_gap))
    print("  Hacim doldurma    : {0}".format(plan.volume_fill.value))
    bl = plan.boundary_layer
    if bl.enabled and bl.layer_count:
        print("  Sınır tabakası    : {0} katman, {1}, büyüme {2:.2f}".format(
            bl.layer_count, bl.offset_method.value, bl.growth_rate))
        if bl.first_height:
            print("  İlk katman        : {0}".format(format_length(bl.first_height, unit)))
    else:
        print("  Sınır tabakası    : kapalı")
    if plan.estimated_cell_count:
        print("  Tahmini hücre     : {0:,}".format(plan.estimated_cell_count))
    for note in plan.notes:
        print("  * {0}".format(note))
    print()


COMMANDS = {
    "run": cmd_run,
    "gui": cmd_gui,
    "doctor": cmd_doctor,
    "propose": cmd_propose,
    "analyze": cmd_analyze,
    "plan": cmd_plan,
    "diagnose": cmd_diagnose,
    "rules": cmd_rules,
    "config": cmd_config,
}


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setup_logging(getattr(args, "log_level", "info"))
    try:
        return COMMANDS[args.command](args)
    except (ValueError, OSError) as exc:
        print("Hata: {0}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
