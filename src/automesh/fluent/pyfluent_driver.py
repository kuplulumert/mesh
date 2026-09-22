"""PyFluent-backed driver - the one that talks to the real ANSYS Fluent.

PyFluent's public surface has moved a fair amount between releases
(``show_gui`` -> ``ui_mode``, ``session.workflow`` -> ``session.meshing.workflow``,
``Arguments = {...}`` -> ``Arguments.set_state({...})``).  Every access below
goes through a small "try the spellings we know" helper so one AutoMesh
version works across several ANSYS versions instead of pinning exactly one.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..config import Config
from ..logging_utils import get_logger
from .driver import (
    CommandResult,
    FluentDriver,
    FluentError,
    Transcript,
    looks_failed,
    looks_unsupported,
    parse_bounding_box,
)


def _try(*candidates: Callable[[], Any]) -> Any:
    """Return the first candidate that does not raise."""
    last: Optional[BaseException] = None
    for candidate in candidates:
        try:
            return candidate()
        except (AttributeError, TypeError, KeyError) as exc:
            last = exc
            continue
    if last is not None:
        raise last
    return None


class PyFluentDriver(FluentDriver):
    name = "pyfluent"

    def __init__(self, cfg: Config, work_dir: str) -> None:
        super().__init__()
        self.cfg = cfg
        self.work_dir = work_dir
        self.session: Any = None
        self._workflow: Any = None
        self._transcript_file = os.path.join(work_dir, "fluent-transcript.trn")
        self._transcript_pos = 0
        self._log = get_logger()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def launch(self) -> None:
        try:
            import ansys.fluent.core as pyfluent  # type: ignore
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise FluentError(
                "PyFluent kurulu değil. `pip install ansys-fluent-core` çalıştırın "
                "veya --dry-run ile mock sürücüyü kullanın."
            ) from exc

        settings = self.cfg.fluent
        os.makedirs(self.work_dir, exist_ok=True)

        kwargs: Dict[str, Any] = {
            "mode": "meshing",
            "precision": settings.precision,
            "processor_count": settings.processor_count,
            "dimension": settings.dimension,
            "cwd": self.work_dir,
            # Fluent'i açık bırakacaksak PyFluent süreç sonunda onu
            # öldürmemeli, yoksa pencere hemen kapanır.
            "cleanup_on_exit": (settings.cleanup_on_exit
                                and not settings.keep_open_after_run),
            "start_timeout": settings.launch_timeout_s,
        }
        if settings.product_version:
            kwargs["product_version"] = settings.product_version
        if settings.env:
            kwargs["env"] = dict(settings.env)
        if settings.additional_arguments:
            kwargs["additional_arguments"] = settings.additional_arguments
        ui_kwargs = (
            {"ui_mode": settings.ui_mode},
            {"show_gui": settings.show_gui},
            {},
        )

        self._log.info(
            "Fluent Meshing başlatılıyor (%s çekirdek, %s hassasiyet)...",
            settings.processor_count, settings.precision,
        )
        last_error: Optional[BaseException] = None
        for extra in ui_kwargs:
            attempt = dict(kwargs)
            attempt.update(extra)
            try:
                self.session = pyfluent.launch_fluent(**attempt)
                break
            except TypeError as exc:
                # An argument this PyFluent release does not know - drop the
                # optional ones one by one and retry.
                last_error = exc
                for optional in ("start_timeout", "dimension", "cleanup_on_exit",
                                 "additional_arguments", "env"):
                    attempt.pop(optional, None)
                try:
                    self.session = pyfluent.launch_fluent(**attempt)
                    break
                except Exception as exc2:  # pragma: no cover
                    last_error = exc2
                    continue
            except Exception as exc:  # pragma: no cover - licence, path, ...
                last_error = exc
                continue

        if self.session is None:
            raise FluentError("Fluent başlatılamadı: {0}".format(last_error))

        self.launched = True
        self.journal_add(
            'session = pyfluent.launch_fluent(mode="meshing", precision="{0}", '
            'processor_count={1})'.format(settings.precision, settings.processor_count)
        )
        self._start_transcript()
        self._log.info("Fluent hazır.")

    def _start_transcript(self) -> None:
        try:
            _try(
                lambda: self.session.transcript.start(file_name=self._transcript_file),
                lambda: self.session.transcript.start(self._transcript_file),
                lambda: self.session.start_transcript(self._transcript_file),
            )
        except Exception as exc:  # pragma: no cover
            self._log.debug("Transcript dosyası başlatılamadı: %s", exc)

    def close(self) -> None:
        if self.session is None:
            return
        try:
            _try(
                lambda: self.session.exit(),
                lambda: self.session.close(),
            )
        except Exception as exc:  # pragma: no cover
            self._log.debug("Fluent kapatılırken hata: %s", exc)
        finally:
            self.launched = False
            self.session = None

    def is_alive(self) -> bool:
        if self.session is None:
            return False
        try:
            return bool(_try(
                lambda: self.session.is_active(),
                lambda: self.session.health_check.is_serving,
                lambda: True,
            ))
        except Exception:
            return False

    # ------------------------------------------------------------------
    # transcript
    # ------------------------------------------------------------------
    def _read_transcript_delta(self) -> str:
        """Everything Fluent printed since the previous call."""
        if not os.path.isfile(self._transcript_file):
            return ""
        try:
            with open(self._transcript_file, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self._transcript_pos)
                chunk = fh.read()
                self._transcript_pos = fh.tell()
            return chunk
        except OSError:  # pragma: no cover
            return ""

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------
    def execute_tui(self, command: str) -> CommandResult:
        started = time.time()
        output = ""
        error = ""
        ok = True
        try:
            returned = _try(
                lambda: self.session.execute_tui(command),
                lambda: self.session.tui.execute(command),
                lambda: self.session.meshing.execute_tui(command),
            )
            if isinstance(returned, str):
                output = returned
        except Exception as exc:
            ok = False
            error = str(exc)
        output = "\n".join(p for p in (output, self._read_transcript_delta()) if p)
        result = CommandResult(
            command=command,
            output=output,
            ok=ok and not looks_failed(output),
            error=error,
            duration_s=time.time() - started,
            unsupported=looks_unsupported(output) or looks_unsupported(error),
        )
        self.journal_add('session.execute_tui(r"""{0}""")'.format(command))
        return self.record(result)

    # ------------------------------------------------------------------
    # workflow
    # ------------------------------------------------------------------
    def _get_workflow(self) -> Any:
        if self._workflow is None:
            self._workflow = _try(
                lambda: self.session.workflow,
                lambda: self.session.meshing.workflow,
            )
        if self._workflow is None:
            raise FluentError("PyFluent oturumunda meshing workflow bulunamadı.")
        return self._workflow

    def workflow_initialize(self, workflow_type: str) -> CommandResult:
        started = time.time()
        workflow = self._get_workflow()
        error = ""
        ok = True
        try:
            _try(
                lambda: workflow.InitializeWorkflow(WorkflowType=workflow_type),
                lambda: workflow.initialize_workflow(WorkflowType=workflow_type),
            )
        except Exception as exc:
            ok = False
            error = str(exc)
        self.journal_add(
            'session.workflow.InitializeWorkflow(WorkflowType="{0}")'.format(workflow_type))
        return self.record(CommandResult(
            command="InitializeWorkflow({0})".format(workflow_type),
            output=self._read_transcript_delta(),
            ok=ok, error=error, duration_s=time.time() - started,
        ))

    def _task(self, name: str) -> Any:
        workflow = self._get_workflow()
        return workflow.TaskObject[name]

    def task_exists(self, task: str) -> bool:
        try:
            self._task(task)
            return True
        except Exception:
            return False

    def task_set_arguments(self, task: str, arguments: Dict[str, Any]) -> CommandResult:
        started = time.time()
        error = ""
        ok = True
        try:
            obj = self._task(task)
            _try(
                lambda: obj.Arguments.set_state(arguments),
                lambda: obj.Arguments.setState(arguments),
                lambda: setattr(obj, "Arguments", arguments),
                lambda: obj.Arguments.update_dict(arguments),
            )
        except Exception as exc:
            ok = False
            error = str(exc)
        self.journal_add(
            'session.workflow.TaskObject["{0}"].Arguments.set_state({1!r})'.format(
                task, arguments))
        return self.record(CommandResult(
            command="SetArguments({0})".format(task),
            output=self._read_transcript_delta(),
            ok=ok, error=error, duration_s=time.time() - started,
        ))

    def task_execute(self, task: str) -> CommandResult:
        started = time.time()
        error = ""
        ok = True
        try:
            obj = self._task(task)
            _try(lambda: obj.Execute(), lambda: obj.execute())
        except Exception as exc:
            ok = False
            error = str(exc)
        output = self._read_transcript_delta()
        state = self._task_state(task)
        if state and state.lower() in ("failed", "out-of-date", "forced-up-to-date"):
            if state.lower() == "failed":
                ok = False
                error = error or "Görev durumu: {0}".format(state)
        if looks_failed(output):
            ok = False
        return self.record(CommandResult(
            command="Execute({0})".format(task),
            output=output, ok=ok, error=error,
            duration_s=time.time() - started,
        ))

    def task_call(self, task: str, method: str, **kwargs: Any) -> CommandResult:
        started = time.time()
        ok, error = True, ""
        try:
            obj = self._task(task)
            func = getattr(obj, method)
            func(**kwargs)
        except Exception as exc:
            ok, error = False, str(exc)
        self.journal_add(
            'session.workflow.TaskObject["{0}"].{1}(**{2!r})'.format(task, method, kwargs))
        return self.record(CommandResult(
            command="{0}.{1}()".format(task, method),
            output=self._read_transcript_delta(), ok=ok, error=error,
            unsupported=not ok and "has no attribute" in error,
            duration_s=time.time() - started))

    def _task_state(self, task: str) -> str:
        try:
            obj = self._task(task)
            value = _try(
                lambda: obj.State.get_state(),
                lambda: obj.State(),
                lambda: obj.state,
            )
            return str(value) if value is not None else ""
        except Exception:
            return ""

    def _task_names(self) -> List[str]:
        workflow = self._get_workflow()
        try:
            names = _try(
                lambda: list(workflow.TaskObject.get_object_names()),
                lambda: list(workflow.TaskObject.keys()),
                lambda: list(workflow.TaskObject()),
            )
            return [str(n) for n in (names or [])]
        except Exception:
            return []

    def task_insert_next(self, after: str, command_name: str) -> Optional[str]:
        before = set(self._task_names())
        try:
            obj = self._task(after)
            _try(
                lambda: obj.InsertNextTask(CommandName=command_name),
                lambda: obj.insert_next_task(CommandName=command_name),
            )
        except Exception as exc:
            self.record(CommandResult(
                command="InsertNextTask({0}, {1})".format(after, command_name),
                ok=False, error=str(exc)))
            return None
        self.journal_add(
            'session.workflow.TaskObject["{0}"].InsertNextTask(CommandName="{1}")'
            .format(after, command_name))
        after_names = self._task_names()
        new = [n for n in after_names if n not in before]
        name = new[0] if new else _EXPECTED_TASK_NAMES.get(command_name)
        self.record(CommandResult(
            command="InsertNextTask({0}, {1})".format(after, command_name),
            output="Yeni görev: {0}".format(name), ok=bool(name)))
        return name

    # ------------------------------------------------------------------
    # results
    # ------------------------------------------------------------------
    def write_mesh(self, path: str) -> CommandResult:
        from . import tui

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        return self.execute_tui_any(tui.write_mesh(path))

    def bounding_box(self) -> Optional[Sequence[float]]:
        from . import tui

        result = self.execute_tui_any(tui.domain_extents())
        box = parse_bounding_box(result.text)
        if box:
            return box
        return parse_bounding_box(self.transcript.tail(40000))

    def cell_count(self) -> int:
        try:
            value = _try(
                lambda: self.session.scheme_eval.scheme_eval("(cx-cell-count)"),
                lambda: self.session.scheme.eval("(cx-cell-count)"),
            )
            return int(value or 0)
        except Exception:
            return 0


#: Display names Fluent gives to inserted workflow tasks.
_EXPECTED_TASK_NAMES = {
    "ImproveSurfaceMesh": "Improve Surface Mesh",
    "ImproveVolumeMesh": "Improve Volume Mesh",
    "AddLocalSizingWTM": "Add Local Sizing",
    "RunCustomJournal": "Run Custom Journal",
}
