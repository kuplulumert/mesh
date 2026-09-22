"""A Fluent simulator.

It exists for two reasons:

1. ``automesh run --dry-run`` lets the user rehearse the whole loop - plan,
   failure, diagnosis, remediation, re-plan - without an ANSYS licence,
   which is how you find out whether the agent's decisions make sense.
2. The test suite drives the real orchestrator against it.

The simulated quality numbers are a crude but monotone model of reality:
faster growth, more prism layers and more tetrahedra all make the worst cell
worse, and every improvement pass buys a little back.  That is enough to
exercise every branch of the decision logic, and the transcript it prints is
written in Fluent's own phrasing so the parsers are tested for real.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any, Dict, List, Optional, Sequence

from ..config import Config
from ..models import MeshPlan, VolumeFill, WorkflowType
from .driver import CommandResult, FluentDriver, looks_unsupported

#: Scenarios the mock can play.
SCENARIOS = ("clean", "realistic", "dirty", "prism", "memory", "stubborn")


class MockFluentDriver(FluentDriver):
    name = "mock"

    def __init__(self, cfg: Config, work_dir: str, scenario: Optional[str] = None,
                 seed: int = 1234) -> None:
        super().__init__()
        self.cfg = cfg
        self.work_dir = work_dir
        self.scenario = (scenario or cfg.fluent.mock_scenario or "realistic").lower()
        if self.scenario not in SCENARIOS:
            raise ValueError(
                "Bilinmeyen senaryo {0!r}; seçenekler: {1}".format(
                    self.scenario, ", ".join(SCENARIOS)))
        self._rng = random.Random(seed)
        self.plan: Optional[MeshPlan] = None
        self.attempt = 0
        self.surface_improve_passes = 0
        self.volume_improve_passes = 0
        self.surface_repair_passes = 0
        self.has_surface_mesh = False
        self.has_volume_mesh = False
        self.tasks: List[str] = []
        self.executed: List[str] = []
        self.arguments: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    def launch(self) -> None:
        self.launched = True
        os.makedirs(self.work_dir, exist_ok=True)
        self.journal_add(
            'session = pyfluent.launch_fluent(mode="meshing", precision="{0}", '
            'processor_count={1})'.format(
                self.cfg.fluent.precision, self.cfg.fluent.processor_count))
        self.transcript.append(
            "Fluent Meshing (mock) {0} senaryosu ile başlatıldı.".format(self.scenario))

    def close(self) -> None:
        self.launched = False

    def notify_plan(self, plan: MeshPlan) -> None:
        self.plan = plan

    # ------------------------------------------------------------------
    def workflow_initialize(self, workflow_type: str) -> CommandResult:
        self.attempt += 1
        self.journal_add(
            'session.workflow.InitializeWorkflow(WorkflowType="{0}")'.format(workflow_type))
        self.surface_improve_passes = 0
        self.volume_improve_passes = 0
        self.surface_repair_passes = 0
        self.has_surface_mesh = False
        self.has_volume_mesh = False
        self.tasks = list(_TASKS.get(workflow_type, _TASKS["Watertight Geometry"]))
        return self.record(CommandResult(
            command="InitializeWorkflow({0})".format(workflow_type),
            output="Workflow initialized: {0}".format(workflow_type), ok=True))

    def task_set_arguments(self, task: str, arguments: Dict[str, Any]) -> CommandResult:
        self.arguments[task] = dict(arguments)
        self.journal_add(
            'session.workflow.TaskObject["{0}"].Arguments.set_state({1!r})'.format(
                task, arguments))
        return self.record(CommandResult(
            command="SetArguments({0})".format(task), ok=True,
            output="{0} arguments updated".format(task)))

    def task_exists(self, task: str) -> bool:
        return task in self.tasks

    def task_insert_next(self, after: str, command_name: str) -> Optional[str]:
        name = {
            "ImproveSurfaceMesh": "Improve Surface Mesh",
            "ImproveVolumeMesh": "Improve Volume Mesh",
        }.get(command_name, command_name)
        if after in self.tasks:
            self.tasks.insert(self.tasks.index(after) + 1, name)
        else:
            self.tasks.append(name)
        self.journal_add(
            'session.workflow.TaskObject["{0}"].InsertNextTask(CommandName="{1}")'
            .format(after, command_name))
        self.record(CommandResult(
            command="InsertNextTask({0}, {1})".format(after, command_name),
            output="Inserted task: {0}".format(name), ok=True))
        return name

    # ------------------------------------------------------------------
    def task_execute(self, task: str) -> CommandResult:
        self.executed.append(task)
        self.journal_add('session.workflow.TaskObject["{0}"].Execute()'.format(task))
        handler = {
            "Import Geometry": self._import_geometry,
            "Import CAD and Part Management": self._import_geometry,
            "Generate the Surface Mesh": self._surface_mesh,
            "Improve Surface Mesh": self._improve_surface,
            "Generate the Volume Mesh": self._volume_mesh,
            "Improve Volume Mesh": self._improve_volume,
            "Add Boundary Layers": self._boundary_layers,
        }.get(task, self._generic_task)
        result = handler(task)
        return self.record(result)

    def _generic_task(self, task: str) -> CommandResult:
        return CommandResult(command="Execute({0})".format(task), ok=True,
                             output="{0}: done.".format(task))

    def _import_geometry(self, task: str) -> CommandResult:
        return CommandResult(
            command="Execute({0})".format(task), ok=True,
            output=(
                "Reading geometry...\n"
                " Domain Extents:\n"
                "   x-coordinate: min = -1.000000e-01, max = 1.000000e-01\n"
                "   y-coordinate: min = -5.000000e-02, max = 5.000000e-02\n"
                "   z-coordinate: min = 0.000000e+00, max = 2.000000e-02\n"
                "Done."
            ))

    def _surface_mesh(self, task: str) -> CommandResult:
        plan = self.plan
        workflow = plan.workflow if plan else WorkflowType.WATERTIGHT

        if self.scenario == "dirty" and workflow is WorkflowType.WATERTIGHT:
            return CommandResult(
                command="Execute({0})".format(task), ok=False,
                output=(
                    "WARNING: 148 free faces found on the boundary mesh.\n"
                    "Error: Failed to generate the surface mesh. The geometry is "
                    "not watertight; there are free faces and multi-connected "
                    "faces in zone wall-inlet-duct."
                ),
                error="surface mesh generation failed")

        if self.scenario == "realistic" and self.attempt == 1 and plan is not None \
                and plan.min_size > _feature_limit(plan):
            return CommandResult(
                command="Execute({0})".format(task), ok=False,
                output=(
                    "Error: Failed to generate the surface mesh.\n"
                    "There are 37 intersecting faces near zone wall-manifold.\n"
                    "Try reducing the minimum size or improving the surface mesh."
                ),
                error="intersecting faces")

        self.has_surface_mesh = True
        skew = self._surface_skewness()
        return CommandResult(
            command="Execute({0})".format(task), ok=True,
            output=(
                "Generating surface mesh...\n"
                "  Surface mesh generated: {cells} faces, {nodes} nodes.\n"
                "  Maximum surface skewness = {skew:.4f}\n"
                "  Minimum surface orthogonal quality = {ortho:.4f}\n"
                "Done.".format(
                    cells=self._surface_face_count(), nodes=self._surface_face_count() // 2,
                    skew=skew, ortho=max(0.02, 1.0 - skew - 0.02))
            ))

    def _improve_surface(self, task: str) -> CommandResult:
        self.surface_improve_passes += 1
        skew = self._surface_skewness()
        return CommandResult(
            command="Execute({0})".format(task), ok=True,
            output=(
                "Improving surface mesh...\n"
                "  Quality improvement pass {0} completed.\n"
                "  Maximum surface skewness = {1:.4f}\n"
                "Done.".format(self.surface_improve_passes, skew)))

    def _boundary_layers(self, task: str) -> CommandResult:
        plan = self.plan
        layers = plan.boundary_layer.layer_count if plan else 0
        if self.scenario == "prism" and layers > 4:
            return CommandResult(
                command="Execute({0})".format(task), ok=False,
                output=(
                    "Error: prism layer generation failed.\n"
                    "Prisms collapsed at 412 nodes; only 2 of {0} layers could be "
                    "generated in zone wall-throat.".format(layers)),
                error="prism failure")
        return CommandResult(
            command="Execute({0})".format(task), ok=True,
            output="Boundary layer control created with {0} layers.".format(layers))

    def _volume_mesh(self, task: str) -> CommandResult:
        plan = self.plan
        if plan is None:
            return CommandResult(command="Execute({0})".format(task), ok=False,
                                 error="plan yok")

        if self.scenario == "memory" and plan.estimated_cell_count > 2_000_000:
            return CommandResult(
                command="Execute({0})".format(task), ok=False,
                output=(
                    "Error: Not enough memory to complete the operation.\n"
                    "Requested {0} cells exceeds the available memory."
                    .format(plan.estimated_cell_count)),
                error="out of memory")

        if self.scenario == "prism" and plan.boundary_layer.layer_count > 4:
            return CommandResult(
                command="Execute({0})".format(task), ok=False,
                output=("Error: The volume mesh could not be generated because the "
                        "prism layers are invalid (negative volume cells detected)."),
                error="prism failure")

        self.has_volume_mesh = True
        skew = self._volume_skewness()
        ortho = self._orthogonal_quality(skew)
        return CommandResult(
            command="Execute({0})".format(task), ok=True,
            output=(
                "Generating volume mesh ({fill})...\n"
                "  Mesh generated: {cells} cells, {faces} faces, {nodes} nodes.\n"
                "  Maximum cell skewness = {skew:.4f}\n"
                "  Minimum orthogonal quality = {ortho:.4f}\n"
                "  Maximum aspect ratio = {ar:.3f}\n"
                "Done.".format(
                    fill=plan.volume_fill.value,
                    cells=self.cell_count(),
                    faces=self.cell_count() * 5,
                    nodes=self.cell_count() * 2,
                    skew=skew, ortho=ortho, ar=self._aspect_ratio())))

    def _improve_volume(self, task: str) -> CommandResult:
        self.volume_improve_passes += 1
        skew = self._volume_skewness()
        return CommandResult(
            command="Execute({0})".format(task), ok=True,
            output=(
                "Improving volume mesh, pass {0}...\n"
                "  Maximum cell skewness = {1:.4f}\n"
                "  Minimum orthogonal quality = {2:.4f}\n"
                "Done.".format(self.volume_improve_passes, skew,
                               self._orthogonal_quality(skew))))

    # ------------------------------------------------------------------
    def execute_tui(self, command: str) -> CommandResult:
        low = command.lower()
        self.journal_add('session.execute_tui(r"""{0}""")'.format(command))
        if "check-quality" in low or "report-max-cell-skewness" in low:
            skew = self._volume_skewness()
            ortho = self._orthogonal_quality(skew)
            return self.record(CommandResult(
                command=command, ok=True,
                output=(
                    "\n Mesh Quality:\n\n"
                    " Minimum Orthogonal Quality = {0:.6e}\n"
                    " Maximum Ortho Skew = {1:.6e}\n"
                    " Maximum Aspect Ratio = {2:.6e}\n"
                    " Maximum cell skewness = {1:.4f}\n"
                    .format(ortho, skew, self._aspect_ratio()))))
        if "check-boundary-mesh" in low or "report-face-quality" in low:
            skew = self._surface_skewness()
            return self.record(CommandResult(
                command=command, ok=True,
                output=(
                    " Boundary mesh check:\n"
                    "   Maximum face skewness = {0:.4f}\n"
                    "   Minimum face orthogonal quality = {1:.4f}\n"
                    "   0 free faces, 0 multi-connected faces.\n"
                    .format(skew, max(0.02, 1.0 - skew - 0.02)))))
        if "/boundary/improve" in low or "repair" in low:
            self.surface_repair_passes += 1
            return self.record(CommandResult(
                command=command, ok=True,
                output="Improved 128 faces, pass {0}.".format(self.surface_repair_passes)))
        if "auto-node-move" in low or "improve-quality" in low:
            self.volume_improve_passes += 1
            skew = self._volume_skewness()
            return self.record(CommandResult(
                command=command, ok=True,
                output=("Moved 3421 nodes.\n Maximum cell skewness = {0:.4f}\n"
                        " Minimum orthogonal quality = {1:.4f}"
                        .format(skew, self._orthogonal_quality(skew)))))
        if "write-mesh" in low or "write-case" in low:
            return self.record(CommandResult(
                command=command, ok=True, output="Writing mesh file... Done."))
        if "quality-method" in low or "switch-to-solution" in low:
            return self.record(CommandResult(command=command, ok=True, output="Done."))
        return self.record(CommandResult(
            command=command, ok=False, unsupported=True,
            output="Error: eval: unbound variable (mock: komut tanımlı değil)"))

    # ------------------------------------------------------------------
    def write_mesh(self, path: str) -> CommandResult:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# mock mesh\n# cells={0}\n".format(self.cell_count()))
        return self.record(CommandResult(
            command='/file/write-mesh "{0}"'.format(path), ok=True,
            output="Writing {0}... Done.".format(path)))

    def bounding_box(self) -> Optional[Sequence[float]]:
        return [-0.1, -0.05, 0.0, 0.1, 0.05, 0.02]

    def cell_count(self) -> int:
        if not self.plan:
            return 0
        return max(int(self.plan.estimated_cell_count), 1000)

    # ------------------------------------------------------------------
    # the quality model
    # ------------------------------------------------------------------
    def _surface_face_count(self) -> int:
        return max(self.cell_count() // 8, 500)

    def _surface_skewness(self) -> float:
        if self.scenario == "clean":
            base = 0.45
        elif self.scenario == "stubborn":
            base = 0.93
        else:
            base = 0.86
        plan = self.plan
        if plan is not None:
            base += 0.10 * max(0.0, plan.growth_rate - 1.15) / 0.35
            base -= 0.04 * max(0.0, 18.0 - plan.curvature_normal_angle) / 6.0
        base -= 0.07 * self.surface_improve_passes
        base -= 0.03 * self.surface_repair_passes
        if self.scenario == "stubborn":
            base = max(base, 0.90)
        return max(0.05, min(0.999, base))

    def _volume_skewness(self) -> float:
        plan = self.plan
        if self.scenario == "clean":
            base = 0.62
        elif self.scenario == "stubborn":
            base = 0.97
        else:
            base = 0.84
        if plan is not None:
            base += 0.18 * max(0.0, plan.growth_rate - 1.15) / 0.35
            base += 0.10 * min(plan.boundary_layer.layer_count, 20) / 20.0
            base += {
                VolumeFill.TETRAHEDRAL: 0.06,
                VolumeFill.POLY_HEXCORE: 0.02,
                VolumeFill.HEXCORE: 0.03,
                VolumeFill.POLYHEDRA: 0.0,
            }.get(plan.volume_fill, 0.0)
        base -= 0.05 * self.volume_improve_passes
        base -= 0.03 * self.surface_improve_passes
        if self.scenario == "stubborn":
            base = max(base, 0.96)
        return max(0.05, min(0.999, base))

    def _orthogonal_quality(self, skew: float) -> float:
        return max(0.001, min(1.0, 1.0 - skew - 0.03))

    def _aspect_ratio(self) -> float:
        plan = self.plan
        layers = plan.boundary_layer.layer_count if plan else 0
        return 8.0 + 4.0 * layers


def _feature_limit(plan: MeshPlan) -> float:
    """The mock's stand-in for 'the minimum size is too coarse for this part'."""
    return plan.max_size / 8.0


_TASKS = {
    "Watertight Geometry": [
        "Import Geometry",
        "Add Local Sizing",
        "Generate the Surface Mesh",
        "Describe Geometry",
        "Update Boundaries",
        "Update Regions",
        "Add Boundary Layers",
        "Generate the Volume Mesh",
    ],
    "Fault-tolerant Meshing": [
        "Import CAD and Part Management",
        "Describe Geometry and Flow",
        "Enclose Fluid Regions (Capping)",
        "Extract Edge Features",
        "Identify Regions",
        "Define Leakage Threshold",
        "Update Region Settings",
        "Choose Mesh Control Options",
        "Generate the Surface Mesh",
        "Update Boundaries",
        "Add Boundary Layers",
        "Generate the Volume Mesh",
    ],
}
