"""The autonomous loop.

    analyse -> plan -> mesh -> check -> (diagnose -> remedy) -> repeat

Two nested loops do the work.  The inner one repairs the mesh that exists
(Improve Surface Mesh, auto node move, the TUI ladders); the outer one
changes the recipe and meshes again.  A remedy is never repeated blindly:
every rule and every ladder escalates, so each pass is strictly more drastic
than the one before, and the run ends as soon as escalation is exhausted.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from .config import Config
from .diagnostics import advisor as advisor_mod
from .diagnostics.actions import (
    ABORT,
    PLAN,
    PLAN_OPERATIONS,
    RETRY,
    SURFACE_REPAIR,
    VOLUME_REPAIR,
    WORKFLOW,
    Action,
    Diagnosis,
)
from .diagnostics.knowledge_base import diagnose
from .diagnostics.repair import is_plan_change, repair_actions
from .fluent import build_driver, tui
from .fluent.driver import CommandResult, FluentDriver, FluentError, looks_fatal
from .fluent.workflows import WorkflowRunner
from .geometry import analyze_geometry
from .logging_utils import get_logger, setup_logging, timed
from .models import (
    AttemptRecord,
    AttemptStatus,
    BLOffsetMethod,
    GeometryMetrics,
    MeshPlan,
    QualityReport,
    QualityVerdict,
    RunResult,
    SizeFunction,
    StepRecord,
    VolumeFill,
    WorkflowType,
)
from .planning import adjust as adjust_mod
from .planning.sizing import estimate_cell_count, plan_mesh
from .quality import evaluate

# Stage identifiers shared with the rule base.
S_IMPORT = "import"
S_SURFACE = "surface"
S_BL = "boundary-layer"
S_VOLUME = "volume"


class _Remesh(Exception):
    """The plan changed - abandon this attempt and mesh again."""


class _Abort(Exception):
    """Nothing further can be tried."""


class AutoMeshAgent:
    """Meshes one geometry, autonomously."""

    def __init__(self, geometry_path: str, cfg: Optional[Config] = None,
                 run_dir: Optional[str] = None) -> None:
        self.geometry_path = os.path.abspath(geometry_path)
        self.cfg = cfg or Config()
        self.run_dir = run_dir or self._make_run_dir()
        self.log = get_logger()

        self.metrics: GeometryMetrics = GeometryMetrics()
        self.plan: MeshPlan = MeshPlan()
        self.driver: Optional[FluentDriver] = None
        self.attempts: List[AttemptRecord] = []
        self.rule_occurrences: Dict[str, int] = {}
        # In-place repairs restart for every freshly generated mesh...
        self.quality_rung: Dict[str, int] = {"surface": 0, "volume": 0}
        # ...but plan-level escalation is monotonic across attempts, so the
        # agent never proposes the same parameter change twice.
        self.quality_plan_rung: Dict[str, int] = {"surface": 0, "volume": 0}
        self.polished: Dict[str, bool] = {}
        self.applied_history: List[str] = []
        self.mesh_path = ""
        self.started = 0.0
        self.advisor = advisor_mod.Advisor(self.cfg.advisor)
        self._import_geometry_path = self.geometry_path

    # ------------------------------------------------------------------
    def _make_run_dir(self) -> str:
        stem = os.path.splitext(os.path.basename(self.geometry_path))[0]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(self.cfg.output.run_root, "{0}-{1}".format(stamp, stem))
        os.makedirs(path, exist_ok=True)
        return os.path.abspath(path)

    def _write(self, name: str, data: Any) -> str:
        path = os.path.join(self.run_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if isinstance(data, (dict, list)):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(str(data))
        return path

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------
    def run(self) -> RunResult:
        self.started = time.time()
        os.makedirs(self.run_dir, exist_ok=True)
        setup_logging("info", os.path.join(self.run_dir, "automesh.log"))
        self.log = get_logger()
        self.log.info("Çalışma dizini: %s", self.run_dir)

        result = RunResult(run_dir=self.run_dir)
        try:
            self._analyze()
            self._plan()
            self._prepare_import_file()
            self._launch()
            success, message = self._attempt_loop()
            result.success = success
            result.message = message
        except _Abort as exc:
            result.success = False
            result.message = str(exc)
            self.log.error("Çalışma durduruldu: %s", exc)
        except FluentError as exc:
            result.success = False
            result.message = "Fluent hatası: {0}".format(exc)
            self.log.error("%s", result.message)
        except KeyboardInterrupt:  # pragma: no cover - interactive
            result.success = False
            result.message = "Kullanıcı tarafından durduruldu."
        finally:
            self._shutdown(result)
        return result

    # ------------------------------------------------------------------
    # stages before Fluent
    # ------------------------------------------------------------------
    def _analyze(self) -> None:
        with timed("Geometri analizi", self.log):
            self.metrics = analyze_geometry(self.geometry_path, self.cfg)
        self._write("analysis.json", self.metrics.to_dict())
        for warning in self.metrics.warnings:
            self.log.warning("Geometri: %s", warning)
        self.log.info(
            "Geometri: %d gövde, %d yüzey, köşegen %.4g m, en küçük özellik %.4g m",
            self.metrics.body_count, self.metrics.face_count,
            self.metrics.diagonal, self.metrics.min_feature_size,
        )

    def _plan(self) -> None:
        self.plan = plan_mesh(self.metrics, self.cfg)

    def _prepare_import_file(self) -> None:
        """Prefer the SpaceClaim export when the analyzer produced one."""
        exported = (self.metrics.raw or {}).get("exported_path")
        if exported and os.path.isfile(exported):
            self._import_geometry_path = os.path.abspath(exported)
            self.log.info("Fluent'e verilecek dosya: %s",
                          os.path.basename(self._import_geometry_path))

    def _launch(self) -> None:
        self.driver = build_driver(self.cfg, self.run_dir)
        with timed("Fluent başlatma", self.log):
            self.driver.launch()

    # ------------------------------------------------------------------
    # the outer loop
    # ------------------------------------------------------------------
    def _attempt_loop(self) -> Tuple[bool, str]:
        deadline = self.started + self.cfg.autonomy.hard_timeout_s
        last_message = "Hiçbir deneme tamamlanamadı."

        for index in range(1, self.cfg.autonomy.max_attempts + 1):
            if time.time() > deadline:
                return False, "Zaman sınırı ({0} s) aşıldı.".format(
                    self.cfg.autonomy.hard_timeout_s)

            record = AttemptRecord(index=index, plan=self.plan.to_dict())
            self.attempts.append(record)
            self._write("plan-attempt-{0}.json".format(index), self.plan.to_dict())
            self.log.info("=" * 62)
            self.log.info("Deneme %d/%d başlıyor", index, self.cfg.autonomy.max_attempts)
            self.log.info("  %s akışı | min=%.4g m | max=%.4g m | büyüme=%.3f | "
                          "%d prizma katmanı | %s",
                          self.plan.workflow.value, self.plan.min_size, self.plan.max_size,
                          self.plan.growth_rate, self.plan.boundary_layer.layer_count,
                          self.plan.volume_fill.value)
            started = time.time()
            try:
                message = self._single_attempt(record)
                record.status = AttemptStatus.SUCCESS
                record.duration_s = time.time() - started
                return True, message
            except _Remesh as exc:
                record.status = AttemptStatus.FAILED
                record.error = str(exc)
                record.duration_s = time.time() - started
                last_message = str(exc)
                self.log.warning("Deneme %d başarısız: %s", index, exc)
                self.plan.estimated_cell_count = estimate_cell_count(self.plan, self.metrics)
            except _Abort:
                record.status = AttemptStatus.ABORTED
                record.duration_s = time.time() - started
                raise
            finally:
                self._write("transcript.log", self.driver.transcript.text()
                            if self.driver else "")

        return False, (
            "{0} denemenin tümü tükendi. Son durum: {1}".format(
                self.cfg.autonomy.max_attempts, last_message))

    # ------------------------------------------------------------------
    # one attempt
    # ------------------------------------------------------------------
    def _single_attempt(self, record: AttemptRecord) -> str:
        assert self.driver is not None
        # A new attempt means a new mesh: in-place repairs start from scratch.
        self.quality_rung = {"surface": 0, "volume": 0}
        self.polished = {}
        runner = WorkflowRunner(self.driver, self.plan, self._import_geometry_path)

        self._stage(record, "Workflow başlatma", S_IMPORT, runner,
                    lambda: runner.initialize())
        self._stage(record, "Geometri içe aktarma", S_IMPORT, runner,
                    lambda: runner.import_geometry())
        self._refresh_metrics_from_fluent()

        self._stage(record, "Yerel boyutlandırma", S_SURFACE, runner,
                    lambda: _first(runner.local_sizing()), optional=True)
        surface_result = self._stage(record, "Yüzey ağı", S_SURFACE, runner,
                                     lambda: runner.surface_mesh())
        self._ensure_quality(S_SURFACE, runner, record, surface_result.text)

        self._stage(record, "Geometri tanımı", S_SURFACE, runner,
                    lambda: _first(runner.describe_geometry()))
        self._stage(record, "Sınır ve bölge güncelleme", S_SURFACE, runner,
                    lambda: _first(runner.update_topology()))
        self._stage(record, "Sınır tabakası", S_BL, runner,
                    lambda: runner.boundary_layers())
        volume_result = self._stage(record, "Hacim ağı", S_VOLUME, runner,
                                    lambda: runner.volume_mesh())
        quality = self._ensure_quality(S_VOLUME, runner, record, volume_result.text)

        self.mesh_path = self._write_mesh(runner, record)
        return "Mesh hazır: {0} ({1})".format(
            os.path.basename(self.mesh_path) or "-", quality.summary())

    def _stage(self, record: AttemptRecord, label: str, stage: str,
               runner: WorkflowRunner, call, optional: bool = False) -> CommandResult:
        """Run one workflow stage, diagnosing and retrying on failure."""
        assert self.driver is not None
        max_inplace = max(1, self.cfg.autonomy.max_repairs_per_attempt)

        for attempt in range(max_inplace + 1):
            with timed(label, self.log) as box:
                result = call()
            record.steps.append(StepRecord(
                name=label, ok=result.ok, duration_s=box["duration_s"],
                message=(result.error or result.output or "")[:400],
            ).to_dict())
            if result.ok or (result.unsupported and optional):
                return result
            if optional and result.unsupported:
                return result

            text = "\n".join(p for p in (result.output, result.error) if p)
            self.log.warning("%s başarısız oldu.", label)
            if attempt >= max_inplace:
                raise _Remesh("{0}: yerinde onarım tükendi".format(label))
            # Diagnose and remedy; a plan-level remedy raises _Remesh.
            self._handle_failure(text, stage, record, runner, label)
        raise _Remesh(label)

    # ------------------------------------------------------------------
    # failure handling
    # ------------------------------------------------------------------
    def _handle_failure(self, text: str, stage: str, record: AttemptRecord,
                        runner: WorkflowRunner, label: str) -> None:
        diagnoses = diagnose(text, stage, self.rule_occurrences)
        if not diagnoses:
            advice = self.advisor.advise(
                self.driver.transcript.tail(self.cfg.advisor.transcript_chars)
                if self.driver else text,
                self.plan, self.metrics, stage, self.applied_history)
            if advice is not None:
                diagnoses = [advice]
                self.log.info("Claude danışmanı devreye girdi: %s", advice.title)

        if not diagnoses:
            if looks_fatal(text):
                raise _Abort(
                    "{0} aşamasında kurtarılamaz hata:\n{1}".format(label, text[-800:]))
            self.log.warning(
                "Bilinen bir kural eşleşmedi; genel çare olarak ağ kabalaştırılıyor.")
            diagnoses = [Diagnosis(
                rule_id="generic-coarsen", title="Tanınmayan hata",
                stage=stage, severity="error",
                explanation=("Hata bilinen kurallara uymuyor. Genel çare olarak "
                             "ağ kabalaştırılıp prizma katmanları azaltılıyor."),
                evidence=text[-400:],
                occurrence=self.rule_occurrences.get("generic-coarsen", 0) + 1,
                actions=[
                    Action(kind=PLAN, operation="scale_sizes",
                           params={"factor": 1.3}, description="Ağı kabalaştır"),
                    Action(kind=PLAN, operation="reduce_layers",
                           params={"count": 1}, description="Bir prizma katmanı azalt"),
                ],
            )]

        needs_remesh = False
        for diagnosis in diagnoses:
            self.rule_occurrences[diagnosis.rule_id] = diagnosis.occurrence
            record.diagnoses.append(diagnosis.to_dict())
            self.log.warning("Teşhis [%s] %s", diagnosis.rule_id, diagnosis.title)
            self.log.info("  %s", diagnosis.explanation.replace("\n", " "))
            if diagnosis.evidence:
                self.log.info("  Kanıt: %s", diagnosis.evidence)
            for action in diagnosis.actions:
                if self._apply_action(action, runner, record):
                    needs_remesh = True
        if needs_remesh:
            raise _Remesh("{0}: plan güncellendi, yeniden meshleniyor".format(label))

    def _apply_action(self, action: Action, runner: WorkflowRunner,
                      record: AttemptRecord) -> bool:
        """Apply one action. Returns True when the plan changed."""
        if action.kind == ABORT:
            raise _Abort(action.description or "Otomatik olarak çözülemeyen hata.")

        if action.kind == RETRY:
            self._note(record, action.description or "Aynı planla tekrar denendi")
            if self.driver is not None and not self.driver.is_alive():
                self.log.info("Fluent yeniden başlatılıyor...")
                self.driver.close()
                self.driver.launch()
            return False

        if action.kind in (PLAN, WORKFLOW):
            description = self._apply_plan_action(action)
            self._note(record, description)
            return True

        if action.kind == SURFACE_REPAIR:
            self._note(record, action.label())
            self._repair_surface(action, runner)
            return False

        if action.kind == VOLUME_REPAIR:
            self._note(record, action.label())
            self._repair_volume(action, runner)
            return False
        return False

    def _apply_plan_action(self, action: Action) -> str:
        name = PLAN_OPERATIONS.get(action.operation)
        if name is None:
            return "Bilinmeyen plan işlemi atlandı: {0}".format(action.operation)
        func = getattr(adjust_mod, name, None)
        if func is None:  # pragma: no cover - defensive
            return "Plan işlemi bulunamadı: {0}".format(name)
        params = _coerce_plan_params(action.operation, dict(action.params))
        try:
            description = func(self.plan, **params)
        except TypeError as exc:
            return "Plan işlemi uygulanamadı ({0}): {1}".format(action.operation, exc)
        self.plan.estimated_cell_count = estimate_cell_count(self.plan, self.metrics)
        return description

    def _note(self, record: AttemptRecord, text: str) -> None:
        record.actions_applied.append(text)
        self.applied_history.append(text)
        self.log.info("  -> %s", text)

    # ------------------------------------------------------------------
    # in-place repairs
    # ------------------------------------------------------------------
    def _repair_surface(self, action: Action, runner: WorkflowRunner) -> None:
        assert self.driver is not None
        operation = action.operation
        params = action.params
        if operation == "improve_surface_mesh":
            runner.improve_surface_mesh(
                face_quality_limit=float(params.get("face_quality_limit", 0.80)),
                iterations=int(params.get("iterations", 5)))
            return
        if operation == "surface_ladder":
            limit = float(params.get("quality_limit", 0.85))
            iterations = int(params.get("iterations", 5))
            for candidates in (
                tui.delete_unused(),
                tui.repair_face_handedness(),
                tui.boundary_improve_quality(limit, iterations),
                tui.boundary_smooth(iterations),
                tui.boundary_swap(limit, iterations),
                tui.boundary_collapse(min(limit + 0.1, 0.95), max(iterations // 2, 1)),
            ):
                self.driver.execute_tui_any(candidates)
            return
        if operation == "delete_unused":
            self.driver.execute_tui_any(tui.delete_unused())
            return
        if operation == "merge_nodes":
            self.driver.execute_tui_any(
                tui.merge_nodes(float(params.get("tolerance", 0.01))))
            return
        if operation == "repair_face_handedness":
            self.driver.execute_tui_any(tui.repair_face_handedness())
            return
        if operation == "remesh_faces":
            self.driver.execute_tui_any(tui.remesh_face_zones())
            return

    def _repair_volume(self, action: Action, runner: WorkflowRunner) -> None:
        assert self.driver is not None
        operation = action.operation
        params = action.params
        if operation == "improve_volume_mesh":
            runner.improve_volume_mesh(
                cell_quality_limit=float(params.get("cell_quality_limit", 0.05)),
                iterations=int(params.get("iterations", 5)))
            return
        if operation == "auto_node_move":
            self.driver.execute_tui_any(tui.auto_node_move(
                quality_limit=float(params.get("quality_limit", 0.15)),
                iterations=int(params.get("iterations", 10))))
            return
        if operation == "repair_improve":
            self.driver.execute_tui_any(tui.repair_mesh())
            self.driver.execute_tui_any(tui.repair_improve_quality())
            return
        if operation == "volume_ladder":
            limit = float(params.get("quality_limit", 0.1))
            iterations = int(params.get("iterations", 10))
            self.driver.execute_tui_any(tui.repair_mesh())
            self.driver.execute_tui_any(tui.improve_quality_iterations(limit, iterations))
            self.driver.execute_tui_any(tui.auto_node_move(limit, 120.0, iterations))
            return

    # ------------------------------------------------------------------
    # quality
    # ------------------------------------------------------------------
    def _check_quality(self, stage: str, task_output: str = "") -> QualityReport:
        assert self.driver is not None
        if stage == S_SURFACE:
            probe = self.driver.execute_tui_any(tui.check_boundary_mesh())
        else:
            self.driver.execute_tui_any(tui.quality_method("orthoskew"))
            probe = self.driver.execute_tui_any(tui.check_volume_quality())
        text = "\n".join(part for part in (task_output, probe.text) if part)
        kind = "surface" if stage == S_SURFACE else "volume"
        return evaluate(text, self.cfg.quality, stage=kind)

    def _ensure_quality(self, stage: str, runner: WorkflowRunner,
                        record: AttemptRecord, task_output: str) -> QualityReport:
        """Repair in place while that helps, then demand a new plan.

        Two counters do the bookkeeping.  ``quality_rung`` walks the in-place
        ladder and is reset for every newly generated mesh - a cheap repair
        deserves another chance on a mesh it has not seen.  ``quality_plan_rung``
        only ever moves forward, so each attempt's parameter change is strictly
        different from the last one and the outer loop cannot become a
        treadmill.
        """
        key = "surface" if stage == S_SURFACE else "volume"
        report = self._check_quality(stage, task_output)
        self._store_quality(record, stage, report)
        self._log_quality(stage, report)

        budget = max(0, self.cfg.autonomy.max_repairs_per_attempt)
        used = 0
        stalled = False

        for _ in range(budget + 4):        # hard bound: never spin forever
            if report.verdict is QualityVerdict.GOOD:
                return report
            if report.verdict is QualityVerdict.ACCEPTABLE:
                if not self.cfg.autonomy.improve_on_acceptable or self.polished.get(key):
                    return report
                self.polished[key] = True   # one cheap polish pass per mesh

            rung = self.quality_rung[key]
            actions = repair_actions(report, rung)
            if used >= budget or stalled or is_plan_change(actions):
                self._escalate_plan(report, key, record, stage)
            self.quality_rung[key] = rung + 1

            for action in actions:
                self._note(record, action.label())
                if stage == S_SURFACE:
                    self._repair_surface(action, runner)
                else:
                    self._repair_volume(action, runner)
            used += 1

            previous = report
            report = self._check_quality(stage, "")
            self._store_quality(record, stage, report)
            self._log_quality(stage, report)
            if not _improved(previous, report):
                self.log.info("Yerinde onarım kaliteyi iyileştirmedi.")
                stalled = True

        if report.verdict.is_success:
            return report
        self._escalate_plan(report, key, record, stage)
        raise _Remesh("{0} kalitesi yetersiz: {1}".format(
            stage, ", ".join(report.failed_metrics) or report.verdict.value))

    def _escalate_plan(self, report: QualityReport, key: str,
                       record: AttemptRecord, stage: str) -> None:
        """Apply the next unused plan-level remedy and ask for a remesh."""
        rung = self._first_plan_rung(report, self.quality_plan_rung[key])
        self.quality_plan_rung[key] = rung + 1
        for action in repair_actions(report, rung):
            self._note(record, self._apply_plan_action(action))
        raise _Remesh(
            "{0} kalitesi ({1}) yerinde düzeltilemedi; plan güncellendi"
            .format(stage, report.verdict.value))

    @staticmethod
    def _first_plan_rung(report: QualityReport, start: int) -> int:
        """The first ladder rung at or after ``start`` that edits the plan."""
        rung = max(start, 0)
        for _ in range(12):
            if is_plan_change(repair_actions(report, rung)):
                return rung
            rung += 1
        return rung

    def _store_quality(self, record: AttemptRecord, stage: str,
                       report: QualityReport) -> None:
        if stage == S_SURFACE:
            record.surface_quality = report.to_dict()
        else:
            record.volume_quality = report.to_dict()

    def _log_quality(self, stage: str, report: QualityReport) -> None:
        level = self.log.info if report.verdict.is_success else self.log.warning
        level("%s kalitesi: %s -> %s", stage, report.summary(), report.verdict.value)
        for failure in report.failed_metrics:
            self.log.info("   ! %s", failure)

    # ------------------------------------------------------------------
    def _refresh_metrics_from_fluent(self) -> None:
        """Recover sizing when the up-front analysis knew nothing."""
        if self.metrics.diagonal > 0 and self.metrics.analyzer != "fallback":
            return
        assert self.driver is not None
        box = self.driver.bounding_box()
        if not box:
            return
        from .geometry.fallback import metrics_from_bounding_box

        self.log.info("Sınır kutusu Fluent'ten alındı, plan yeniden hesaplanıyor.")
        refreshed = metrics_from_bounding_box(*box, length_unit=self.plan.length_unit)
        refreshed.warnings.extend(self.metrics.warnings)
        self.metrics = refreshed
        self._write("analysis.json", self.metrics.to_dict())
        self.plan = plan_mesh(self.metrics, self.cfg)

    def _write_mesh(self, runner: WorkflowRunner, record: AttemptRecord) -> str:
        stem = os.path.splitext(os.path.basename(self.geometry_path))[0]
        target = os.path.join(self.run_dir, "mesh",
                              "{0}.{1}".format(stem, self.cfg.output.mesh_format))
        with timed("Mesh dosyası yazılıyor", self.log):
            result = runner.write_mesh(target)
        record.steps.append(StepRecord(
            name="Mesh yazma", ok=result.ok, message=result.text[:200]).to_dict())
        if not result.ok:
            self.log.warning("Mesh dosyası yazılamadı: %s", result.error or result.output)
            return ""
        return target

    # ------------------------------------------------------------------
    def _shutdown(self, result: RunResult) -> None:
        if self.driver is not None:
            self._write("transcript.log", self.driver.transcript.text())
            self._write("journal.py", self.driver.journal_text())
            try:
                self.driver.close()
            except Exception as exc:  # pragma: no cover
                self.log.debug("Fluent kapatılamadı: %s", exc)

        result.run_dir = self.run_dir
        result.geometry = self.metrics.to_dict()
        result.final_plan = self.plan.to_dict()
        result.mesh_file = self.mesh_path
        result.attempts = [a.to_dict() for a in self.attempts]
        result.duration_s = time.time() - self.started
        last = self.attempts[-1] if self.attempts else None
        if last is not None:
            result.final_quality = last.volume_quality or last.surface_quality

        from .reporting import write_reports

        write_reports(self, result)
        if result.success:
            self.log.info("BAŞARILI: %s", result.message)
        else:
            self.log.error("BAŞARISIZ: %s", result.message)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _first(results: List[CommandResult]) -> CommandResult:
    """Collapse a list of task results into the first failure, or the last ok."""
    if not results:
        return CommandResult(command="", ok=True)
    for result in results:
        if not result.ok and not result.unsupported:
            return result
    return results[-1]


def _improved(before: QualityReport, after: QualityReport) -> bool:
    if before.max_skewness is not None and after.max_skewness is not None:
        return after.max_skewness < before.max_skewness - 1e-6
    if (before.min_orthogonal_quality is not None
            and after.min_orthogonal_quality is not None):
        return after.min_orthogonal_quality > before.min_orthogonal_quality + 1e-6
    return False


_ENUM_PARAMS = {
    "set_bl_method": ("method", BLOffsetMethod),
    "set_volume_fill": ("fill", VolumeFill),
    "set_size_function": ("function", SizeFunction),
}


def _coerce_plan_params(operation: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Turn advisor/KB strings into the enums ``adjust`` expects."""
    mapping = _ENUM_PARAMS.get(operation)
    if mapping:
        key, enum_cls = mapping
        if key in params and not isinstance(params[key], enum_cls):
            try:
                params[key] = enum_cls(str(params[key]))
            except ValueError:
                params.pop(key)
    for key in ("count", "iterations"):
        if key in params:
            params[key] = int(float(params[key]))
    for key in ("factor", "delta", "tolerance", "quality_limit",
                "face_quality_limit", "cell_quality_limit"):
        if key in params:
            params[key] = float(params[key])
    return params


def run_agent(geometry_path: str, cfg: Optional[Config] = None,
              run_dir: Optional[str] = None) -> RunResult:
    return AutoMeshAgent(geometry_path, cfg, run_dir).run()
