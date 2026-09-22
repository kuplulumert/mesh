"""Abstract Fluent driver.

Everything above this layer (workflows, quality checks, the orchestrator)
talks to Fluent only through :class:`FluentDriver`.  That is what makes the
whole pipeline testable without an ANSYS licence: :class:`MockFluentDriver`
implements the same five calls.
"""

from __future__ import annotations

import abc
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

#: Substrings Fluent uses when it does not recognise a TUI command.
_UNKNOWN_COMMAND_MARKERS = (
    "invalid command",
    "unbound variable",
    "is not a valid",
    "no such command",
    "unknown command",
)

#: Substrings that mean the command ran but the operation failed.
_FAILURE_MARKERS = (
    "error:",
    "error ",
    "failed",
    "could not",
    "cannot ",
    "unable to",
    "exception",
    "aborted",
)

#: Failures that no amount of retrying will fix.
FATAL_MARKERS = (
    "license",
    "licence",
    "flexlm",
    "no such file or directory",
    "permission denied",
)


@dataclass
class CommandResult:
    """What came back from one TUI command or workflow task."""

    command: str
    output: str = ""
    ok: bool = True
    error: str = ""
    duration_s: float = 0.0
    unsupported: bool = False

    @property
    def text(self) -> str:
        return "\n".join(part for part in (self.output, self.error) if part)


def looks_unsupported(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _UNKNOWN_COMMAND_MARKERS)


def looks_failed(text: str) -> bool:
    low = (text or "").lower()
    if looks_unsupported(low):
        return True
    return any(marker in low for marker in _FAILURE_MARKERS)


def looks_fatal(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in FATAL_MARKERS)


class Transcript:
    """Append-only log of everything Fluent said, with a bounded tail."""

    def __init__(self, limit_chars: int = 4_000_000) -> None:
        self._parts: List[str] = []
        self._size = 0
        self._limit = limit_chars

    def append(self, text: str) -> None:
        if not text:
            return
        self._parts.append(text)
        self._size += len(text)
        while self._size > self._limit and len(self._parts) > 1:
            self._size -= len(self._parts.pop(0))

    def text(self) -> str:
        return "\n".join(self._parts)

    def tail(self, chars: int = 20000) -> str:
        text = self.text()
        return text[-chars:] if len(text) > chars else text

    def __len__(self) -> int:
        return self._size


class FluentError(RuntimeError):
    """Raised when Fluent cannot continue at all (crash, licence, launch)."""


class FluentDriver(abc.ABC):
    """The five things AutoMesh needs from Fluent."""

    name = "abstract"

    def __init__(self) -> None:
        self.transcript = Transcript()
        self.launched = False
        #: Runnable PyFluent journal mirroring everything the driver did.
        self.journal: List[str] = [
            "# AutoMesh tarafından üretilen PyFluent günlüğü",
            "import ansys.fluent.core as pyfluent",
        ]

    def journal_add(self, line: str) -> None:
        self.journal.append(line)

    def journal_text(self) -> str:
        return "\n".join(self.journal) + "\n"

    # -- lifecycle ------------------------------------------------------
    @abc.abstractmethod
    def launch(self) -> None:
        ...

    @abc.abstractmethod
    def close(self) -> None:
        ...

    def __enter__(self) -> "FluentDriver":
        self.launch()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- commands -------------------------------------------------------
    @abc.abstractmethod
    def execute_tui(self, command: str) -> CommandResult:
        ...

    def execute_tui_any(self, candidates: Sequence[str]) -> CommandResult:
        """Run the first candidate this Fluent build actually understands.

        TUI paths move between releases (``/mesh/repair-improve/...`` did not
        always exist).  Rather than pinning one version, every catalog entry
        lists the spellings we know about and the first supported one wins.
        """
        last: Optional[CommandResult] = None
        for command in candidates:
            result = self.execute_tui(command)
            if not result.unsupported:
                return result
            last = result
        if last is None:
            return CommandResult(command="", ok=False, error="boş komut listesi")
        last.error = "Bu Fluent sürümü şu komutların hiçbirini tanımıyor: {0}".format(
            ", ".join(candidates))
        return last

    # -- meshing workflow ----------------------------------------------
    @abc.abstractmethod
    def workflow_initialize(self, workflow_type: str) -> CommandResult:
        ...

    @abc.abstractmethod
    def task_set_arguments(self, task: str, arguments: Dict[str, Any]) -> CommandResult:
        ...

    @abc.abstractmethod
    def task_execute(self, task: str) -> CommandResult:
        ...

    @abc.abstractmethod
    def task_insert_next(self, after: str, command_name: str) -> Optional[str]:
        """Insert a workflow task and return its display name."""

    def task_exists(self, task: str) -> bool:  # pragma: no cover - override
        return True

    def task_call(self, task: str, method: str, **kwargs: Any) -> CommandResult:
        """Call a workflow task method such as ``UpdateChildTasks``.

        Drivers that cannot do this return ``unsupported`` and the caller
        simply carries on - these calls refresh the GUI's task tree and are
        not essential to the mesh itself.
        """
        return CommandResult(
            command="{0}.{1}()".format(task, method), ok=True, unsupported=True)

    # -- results --------------------------------------------------------
    @abc.abstractmethod
    def write_mesh(self, path: str) -> CommandResult:
        ...

    def bounding_box(self) -> Optional[Sequence[float]]:
        """``(xmin, ymin, zmin, xmax, ymax, zmax)`` of the imported model."""
        return None

    def cell_count(self) -> int:
        return 0

    def is_alive(self) -> bool:
        return self.launched

    def notify_plan(self, plan: Any) -> None:
        """Told which plan the next tasks belong to (the mock reacts to it)."""
        return None

    # -- helpers --------------------------------------------------------
    def record(self, result: CommandResult) -> CommandResult:
        header = "\n>>> {0}".format(result.command) if result.command else ""
        self.transcript.append(header)
        if result.text:
            self.transcript.append(result.text)
        return result


_BBOX_RE = re.compile(
    r"([xyz])[- ]?(?:coordinate|extent)?\s*:?\s*min\s*(?:=|:)\s*(-?[\d.eE+-]+)"
    r"\s*,?\s*max\s*(?:=|:)\s*(-?[\d.eE+-]+)", re.IGNORECASE)


def parse_bounding_box(text: str) -> Optional[List[float]]:
    """Pull a bounding box out of Fluent's ``/domain`` style output."""
    found: Dict[str, Any] = {}
    for match in _BBOX_RE.finditer(text or ""):
        axis = match.group(1).lower()
        try:
            found[axis] = (float(match.group(2)), float(match.group(3)))
        except ValueError:
            continue
    if len(found) < 3:
        return None
    return [
        found["x"][0], found["y"][0], found["z"][0],
        found["x"][1], found["y"][1], found["z"][1],
    ]
