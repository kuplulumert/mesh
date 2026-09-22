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
            "  automesh gui                      (masaüstü arayüzü)\n"
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
    run.add_argument("--gui", action="store_true", help="Fluent arayüzünü göster")
    run.add_argument("--version-ansys", dest="ansys_version",
                     help="ANSYS sürümü, örn. 24.2.0")
    run.add_argument("--workflow", choices=("auto", "watertight", "fault-tolerant"),
                     help="Meshing akışını zorla")
    run.add_argument("--fill", choices=("poly-hexcore", "polyhedra", "hexcore",
                                        "tetrahedral"),
                     help="Hacim doldurma tipi")
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
    run.add_argument("--unit", help="Geometri uzunluk birimi (m, mm, in ...)")
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

    # ---- diagnose --------------------------------------------------------
    diagnose = sub.add_parser("diagnose", parents=[common],
                              help="Bir Fluent çıktısını kural tabanıyla teşhis et")
    diagnose.add_argument("logfile", help="Transcript dosyası ('-' ile stdin)")
    diagnose.add_argument("--stage", default="any",
                          choices=("any", "import", "surface", "boundary-layer", "volume"))

    # ---- gui -------------------------------------------------------------
    sub.add_parser("gui", help="Masaüstü arayüzünü aç")

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
    if args.ansys_version:
        cfg.fluent.product_version = args.ansys_version
    if args.workflow:
        cfg.planning.workflow = args.workflow
    if args.fill:
        cfg.planning.volume_fill = args.fill
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
    _print_metrics(metrics)
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
    _print_metrics(metrics)
    _print_plan(plan)
    return 0


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

def _print_metrics(metrics) -> None:
    from .units import format_length

    unit = metrics.length_unit_hint or "m"
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


def _print_plan(plan) -> None:
    from .units import format_length

    unit = plan.length_unit
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
