"""Translate a :class:`MeshPlan` into Fluent Meshing workflow tasks.

Two workflows are supported:

``Watertight Geometry``
    The default for clean CAD.  Import -> surface mesh -> describe geometry
    -> boundaries/regions -> prism layers -> volume mesh.

``Fault-tolerant Meshing``
    For geometry with gaps, free edges or overlapping parts.  It wraps the
    model instead of trusting it, which is slower but survives dirty CAD.

Each stage is a separate method so the orchestrator can slot a quality check
or a repair in between, which is exactly what the autonomous loop does.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..logging_utils import get_logger
from ..models import BLOffsetMethod, MeshPlan, VolumeFill, WorkflowType
from ..units import from_metres
from .driver import CommandResult, FluentDriver

# Task display names, as Fluent shows them in the workflow tree.
IMPORT_WTM = "Import Geometry"
IMPORT_FTM = "Import CAD and Part Management"
LOCAL_SIZING = "Add Local Sizing"
SURFACE_MESH = "Generate the Surface Mesh"
IMPROVE_SURFACE = "Improve Surface Mesh"
DESCRIBE_WTM = "Describe Geometry"
DESCRIBE_FTM = "Describe Geometry and Flow"
UPDATE_BOUNDARIES = "Update Boundaries"
UPDATE_REGIONS = "Update Regions"
BOUNDARY_LAYERS = "Add Boundary Layers"
VOLUME_MESH = "Generate the Volume Mesh"
IMPROVE_VOLUME = "Improve Volume Mesh"

#: Tasks of the fault-tolerant workflow that are executed with their
#: defaults, in order, before the surface mesh is generated.
FTM_PRE_TASKS = (
    DESCRIBE_FTM,
    "Enclose Fluid Regions (Capping)",
    "Extract Edge Features",
    "Identify Regions",
    "Define Leakage Threshold",
    "Update Region Settings",
    "Choose Mesh Control Options",
)

#: Tasks executed between the surface mesh and the prism layers.
FTM_POST_TASKS = (UPDATE_BOUNDARIES,)


class WorkflowRunner:
    """Drives one meshing attempt, stage by stage."""

    def __init__(self, driver: FluentDriver, plan: MeshPlan, geometry_path: str) -> None:
        self.driver = driver
        self.plan = plan
        self.geometry_path = geometry_path
        self.log = get_logger()
        self._improve_surface_task: Optional[str] = None
        self._improve_volume_task: Optional[str] = None

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _u(self, value_m: float) -> float:
        """Metres -> the unit the geometry was imported with."""
        return round(from_metres(value_m, self.plan.length_unit), 10)

    def _set_and_execute(self, task: str, arguments: Dict[str, Any]) -> CommandResult:
        if not self.driver.task_exists(task):
            return CommandResult(
                command="Execute({0})".format(task), ok=True, unsupported=True,
                output="Görev bu akışta yok, atlandı: {0}".format(task))
        if arguments:
            setter = self.driver.task_set_arguments(task, arguments)
            if not setter.ok:
                return setter
        return self.driver.task_execute(task)

    def _execute(self, task: str) -> CommandResult:
        return self._set_and_execute(task, {})

    # ------------------------------------------------------------------
    # stages
    # ------------------------------------------------------------------
    def initialize(self) -> CommandResult:
        self.driver.notify_plan(self.plan)
        return self.driver.workflow_initialize(self.plan.workflow.fluent_name)

    def import_geometry(self) -> CommandResult:
        task = IMPORT_WTM if self.plan.workflow is WorkflowType.WATERTIGHT else IMPORT_FTM
        arguments: Dict[str, Any] = {
            "FileName": self.geometry_path.replace("\\", "/"),
            "LengthUnit": self.plan.length_unit,
        }
        if self.plan.workflow is WorkflowType.FAULT_TOLERANT:
            arguments = {
                "FileLoaded": "yes",
                "LengthUnit": self.plan.length_unit,
            }
            # FTM loads files through Part Management; the file list lives in
            # a different argument depending on the release, so both are set.
            arguments["FileName"] = self.geometry_path.replace("\\", "/")
        return self._set_and_execute(task, arguments)

    def local_sizing(self) -> List[CommandResult]:
        results: List[CommandResult] = []
        if not self.plan.local_sizings:
            results.append(self._execute(LOCAL_SIZING))
            return results
        for sizing in self.plan.local_sizings:
            arguments: Dict[str, Any] = {
                "AddChild": "yes",
                "BOIControlName": sizing.name,
                "BOIExecution": sizing.size_control_type,
                "BOISize": self._u(sizing.size),
                "BOIGrowthRate": round(sizing.growth_rate, 4),
                # Gruplar SpaceClaim'den named selection olarak geldiği için
                # kapsam "label"dır; zone değil.
                "BOIZoneorLabel": "label",
                "BOIFaceLabelList": [sizing.target],
            }
            self.log.info("Yerel boyut: %s -> %.4g m", sizing.name, sizing.size)
            results.append(self._set_and_execute(LOCAL_SIZING, arguments))
        return results

    def surface_mesh(self) -> CommandResult:
        controls = {
            "MinSize": self._u(self.plan.min_size),
            "MaxSize": self._u(self.plan.max_size),
            "GrowthRate": round(self.plan.growth_rate, 4),
            "SizeFunctions": self.plan.size_function.value,
            "CurvatureNormalAngle": round(self.plan.curvature_normal_angle, 2),
            "CellsPerGap": round(self.plan.cells_per_gap, 2),
            "ScopeProximityTo": self.plan.scope_proximity_to,
        }
        return self._set_and_execute(SURFACE_MESH, {"CFDSurfaceMeshControls": controls})

    def improve_surface_mesh(self, face_quality_limit: float = 0.80,
                             iterations: int = 5) -> CommandResult:
        """Insert (once) and run Fluent's *Improve Surface Mesh* task.

        This is the task a human reaches for when the surface mesh comes out
        skewed: it collapses slivers and re-swaps edges without touching the
        sizing, so it is always tried before a full remesh.
        """
        task = self._improve_surface_task
        if task is None:
            if self.driver.task_exists(IMPROVE_SURFACE):
                task = IMPROVE_SURFACE
            else:
                task = self.driver.task_insert_next(SURFACE_MESH, "ImproveSurfaceMesh")
            self._improve_surface_task = task
        if not task:
            return CommandResult(
                command="ImproveSurfaceMesh", ok=False, unsupported=True,
                error="Improve Surface Mesh görevi eklenemedi.")
        arguments = {
            "FaceQualityLimit": round(face_quality_limit, 4),
            "SMImprovePreferences": {
                "ShowSMImprovePreferences": False,
                "SIQualityIterations": int(iterations),
                "SIQualityMaxAngle": 180,
                "SIQualityCollapseLimit": round(max(face_quality_limit + 0.05, 0.85), 4),
                "SIRemoveStep": "no",
            },
        }
        return self._set_and_execute(task, arguments)

    def describe_geometry(self) -> List[CommandResult]:
        results: List[CommandResult] = []
        if self.plan.workflow is WorkflowType.FAULT_TOLERANT:
            for task in FTM_PRE_TASKS:
                results.append(self._execute(task))
            return results

        arguments = {
            "SetupType": self.plan.geometry_setup,
            "CappingRequired": "Yes" if self.plan.capping_required else "No",
            "WallToInternal": "No",
            "InvokeShareTopology": "Yes" if self.plan.share_topology else "No",
            "NonConformal": "No",
            "Multizone": "No",
        }
        if self.driver.task_exists(DESCRIBE_WTM):
            self.driver.task_set_arguments(DESCRIBE_WTM, {"SetupType": self.plan.geometry_setup})
            self.driver.task_call(DESCRIBE_WTM, "UpdateChildTasks", SetupTypeChanged=True)
            results.append(self._set_and_execute(DESCRIBE_WTM, arguments))
        else:
            results.append(CommandResult(command=DESCRIBE_WTM, ok=True, unsupported=True))
        return results

    def update_topology(self) -> List[CommandResult]:
        tasks = (
            FTM_POST_TASKS
            if self.plan.workflow is WorkflowType.FAULT_TOLERANT
            else (UPDATE_BOUNDARIES, UPDATE_REGIONS)
        )
        return [self._execute(task) for task in tasks]

    def boundary_layers(self) -> CommandResult:
        bl = self.plan.boundary_layer
        if not bl.enabled or bl.layer_count <= 0:
            return CommandResult(
                command="Execute({0})".format(BOUNDARY_LAYERS), ok=True,
                output="Sınır tabakası planda kapalı, görev atlandı.")

        arguments: Dict[str, Any] = {
            "BLControlName": bl.control_name,
            "NumberOfLayers": int(bl.layer_count),
            "OffsetMethodType": bl.offset_method.value,
            "Rate": round(bl.growth_rate, 4),
            "BLZoneList": ["*"],
        }
        if bl.offset_method is BLOffsetMethod.SMOOTH_TRANSITION:
            arguments["TransitionRatio"] = round(bl.transition_ratio, 4)
        elif bl.offset_method in (BLOffsetMethod.LAST_RATIO, BLOffsetMethod.UNIFORM):
            arguments["FirstHeight"] = self._u(bl.first_height)
        elif bl.offset_method is BLOffsetMethod.ASPECT_RATIO:
            arguments["AspectRatio"] = round(bl.aspect_ratio, 3)
        return self._set_and_execute(BOUNDARY_LAYERS, arguments)

    def volume_mesh(self) -> CommandResult:
        arguments: Dict[str, Any] = {
            "VolumeFill": self.plan.volume_fill.value,
            "VolumeMeshPreferences": {
                "ShowVolumeMeshPreferences": False,
                "CheckSelfProximity": "yes",
            },
        }
        if self.plan.volume_fill in (VolumeFill.POLY_HEXCORE, VolumeFill.HEXCORE):
            arguments["VolumeFillControls"] = {
                "HexMaxCellLength": self._u(self.plan.max_cell_length or self.plan.max_size),
                "PeelLayers": int(self.plan.peel_layers),
                "BufferLayers": int(self.plan.buffer_layers),
            }
        return self._set_and_execute(VOLUME_MESH, arguments)

    def improve_volume_mesh(self, cell_quality_limit: float = 0.05,
                            iterations: int = 5) -> CommandResult:
        task = self._improve_volume_task
        if task is None:
            if self.driver.task_exists(IMPROVE_VOLUME):
                task = IMPROVE_VOLUME
            else:
                task = self.driver.task_insert_next(VOLUME_MESH, "ImproveVolumeMesh")
            self._improve_volume_task = task
        if not task:
            return CommandResult(
                command="ImproveVolumeMesh", ok=False, unsupported=True,
                error="Improve Volume Mesh görevi eklenemedi.")
        arguments = {
            "CellQualityLimit": round(cell_quality_limit, 4),
            "VMImprovePreferences": {
                "ShowVMImprovePreferences": False,
                "VIQualityIterations": int(iterations),
                "VIQualityMinAngle": 0,
                "VIgnoreFeature": "yes",
            },
        }
        return self._set_and_execute(task, arguments)

    def write_mesh(self, path: str) -> CommandResult:
        return self.driver.write_mesh(path)
