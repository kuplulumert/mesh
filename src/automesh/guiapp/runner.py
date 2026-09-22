"""Arayüz için arka plan çalıştırıcı.

Tkinter tek iş parçacıklıdır: mesh üretimi doğrudan çağrılırsa pencere donar.
Burada iş ayrı bir thread'de koşar, günlük satırları bir kuyruğa yazılır,
arayüz de kuyruğu düzenli aralıklarla boşaltır.

Tkinter'a hiç bağımlı değildir; bu sayede testlerde gerçek orkestratörle
(mock Fluent sürücüsü üzerinden) doğrulanabiliyor.
"""

from __future__ import annotations

import logging
import queue
import threading
import traceback
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from ..logging_utils import add_persistent_handler, remove_persistent_handler
from ..models import RunResult
from .state import GuiSettings

# Kuyruğa yazılan mesaj tipleri
LOG = "log"
DONE = "done"
ERROR = "error"

MODES = ("run", "analyze", "plan")


@dataclass
class Message:
    kind: str
    text: str = ""
    level: int = logging.INFO
    result: Any = None


class _QueueHandler(logging.Handler):
    """Günlük kayıtlarını arayüzün kuyruğuna aktarır."""

    def __init__(self, target: "queue.Queue[Message]") -> None:
        super().__init__(logging.INFO)
        self.target = target
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.target.put(Message(kind=LOG, text=self.format(record),
                                    level=record.levelno))
        except Exception:      # kuyruk kapandıysa günlük yüzünden çökmeyelim
            pass


class BackgroundRun:
    """Tek bir işi (run/analyze/plan) arka planda yürütür."""

    def __init__(self, settings: GuiSettings, mode: str = "run") -> None:
        if mode not in MODES:
            raise ValueError("bilinmeyen mod: {0!r}".format(mode))
        self.settings = settings
        self.mode = mode
        self.messages: "queue.Queue[Message]" = queue.Queue()
        self.cancel_event = threading.Event()
        self.result: Optional[RunResult] = None
        self.error: str = ""
        self._thread: Optional[threading.Thread] = None
        self._handler: Optional[_QueueHandler] = None

    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            raise RuntimeError("Zaten çalışan bir iş var.")
        self._handler = _QueueHandler(self.messages)
        add_persistent_handler(self._handler)
        self._thread = threading.Thread(target=self._work, name="automesh-run",
                                        daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """Bir sonraki kontrol noktasında durdurur; Fluent düzgünce kapatılır."""
        self.cancel_event.set()
        self.messages.put(Message(
            kind=LOG, level=logging.WARNING,
            text="Durdurma istendi; agent bir sonraki adımda duracak..."))

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def drain(self, limit: int = 500) -> List[Message]:
        """Kuyruktaki mesajları al (arayüz bunu periyodik çağırır)."""
        out: List[Message] = []
        for _ in range(limit):
            try:
                out.append(self.messages.get_nowait())
            except queue.Empty:
                break
        return out

    # ------------------------------------------------------------------
    def _work(self) -> None:
        try:
            if self.mode == "run":
                self._do_run()
            else:
                self._do_inspect()
        except Exception:
            self.error = traceback.format_exc()
            self.messages.put(Message(kind=ERROR, text=self.error))
        finally:
            if self._handler is not None:
                remove_persistent_handler(self._handler)
            self.messages.put(Message(kind=DONE, result=self.result))

    def _do_run(self) -> None:
        from ..orchestrator import run_agent

        cfg = self.settings.to_config()
        self.result = run_agent(
            self.settings.geometry_path, cfg,
            self.settings.run_directory(), self.cancel_event)

    def _do_inspect(self) -> None:
        """Fluent açmadan geometri analizi (ve istenirse plan)."""
        from ..geometry import analyze_geometry
        from ..planning.sizing import plan_mesh
        from ..units import format_length

        cfg = self.settings.to_config()
        log = logging.getLogger("automesh")
        metrics = analyze_geometry(self.settings.geometry_path, cfg)

        unit = metrics.length_unit_hint or "m"
        dx, dy, dz = metrics.bbox.sizes
        log.info("--- Geometri ---")
        log.info("Analiz yöntemi    : %s", metrics.analyzer)
        log.info("Sınır kutusu      : %s x %s x %s",
                 format_length(dx, unit), format_length(dy, unit),
                 format_length(dz, unit))
        log.info("Köşegen           : %s", format_length(metrics.diagonal, unit))
        if metrics.volume:
            log.info("Hacim             : %.6g m3", metrics.volume)
        if metrics.area:
            log.info("Yüzey alanı       : %.6g m2", metrics.area)
        log.info("Gövde/yüzey/kenar : %d / %d / %d",
                 metrics.body_count, metrics.face_count, metrics.edge_count)
        log.info("En küçük özellik  : %s", format_length(metrics.min_feature_size, unit))
        log.info("Özellik aralığı   : 1:%.0f", metrics.feature_span)
        log.info("Eğrisel yüzey     : %.0f%%", metrics.curved_face_ratio * 100)
        log.info("Su geçirmez       : %s", _yesno(metrics.watertight))
        log.info("Karmaşıklık       : %.2f", metrics.complexity())
        for warning in metrics.warnings:
            log.warning("%s", warning)

        if self.mode != "plan":
            return

        plan = plan_mesh(metrics, cfg)
        punit = plan.length_unit
        log.info("--- Mesh planı ---")
        log.info("Akış              : %s", plan.workflow.value)
        log.info("Min / max boyut   : %s / %s",
                 format_length(plan.min_size, punit), format_length(plan.max_size, punit))
        log.info("Büyüme oranı      : %.3f", plan.growth_rate)
        log.info("Boyut fonksiyonu  : %s", plan.size_function.value)
        log.info("Eğrilik açısı     : %.1f derece", plan.curvature_normal_angle)
        log.info("Boşluk/hücre      : %.1f", plan.cells_per_gap)
        log.info("Hacim doldurma    : %s", plan.volume_fill.value)
        bl = plan.boundary_layer
        if bl.enabled and bl.layer_count:
            log.info("Sınır tabakası    : %d katman, %s, büyüme %.2f",
                     bl.layer_count, bl.offset_method.value, bl.growth_rate)
            if bl.first_height:
                log.info("İlk katman        : %s", format_length(bl.first_height, punit))
        else:
            log.info("Sınır tabakası    : kapalı")
        if plan.estimated_cell_count:
            log.info("Tahmini hücre     : {0:,}".format(plan.estimated_cell_count))
        for note in plan.notes:
            log.info("* %s", note)


def _yesno(value: Optional[bool]) -> str:
    if value is None:
        return "bilinmiyor"
    return "evet" if value else "hayır"
