import pytest

from automesh.fluent import build_driver, tui
from automesh.fluent.driver import (
    CommandResult,
    Transcript,
    looks_failed,
    looks_fatal,
    looks_unsupported,
    parse_bounding_box,
)
from automesh.fluent.mock_driver import MockFluentDriver
from automesh.fluent.workflows import WorkflowRunner
from automesh.models import BLOffsetMethod, MeshPlan, VolumeFill, WorkflowType


@pytest.fixture
def driver(cfg, tmp_path):
    d = build_driver(cfg, str(tmp_path))
    d.launch()
    return d


def test_build_driver_respects_mock_flag(cfg, tmp_path):
    assert isinstance(build_driver(cfg, str(tmp_path)), MockFluentDriver)


def test_output_classification():
    assert looks_unsupported("Error: eval: unbound variable")
    assert looks_failed("Error: meshing failed")
    assert not looks_failed("Mesh generated: 100 cells.")
    assert looks_fatal("could not check out license")
    assert not looks_fatal("Maximum cell skewness = 0.8")


def test_transcript_keeps_a_bounded_tail():
    transcript = Transcript(limit_chars=20)
    for _ in range(10):
        transcript.append("0123456789")
    assert len(transcript) <= 30
    assert transcript.tail(5) == "56789"


def test_bounding_box_parsing():
    text = ("  x-coordinate: min = -1.0e-01, max = 1.0e-01\n"
            "  y-coordinate: min = -5.0e-02, max = 5.0e-02\n"
            "  z-coordinate: min = 0.0e+00, max = 2.0e-02\n")
    assert parse_bounding_box(text) == [-0.1, -0.05, 0.0, 0.1, 0.05, 0.02]
    assert parse_bounding_box("nothing") is None


def test_execute_tui_any_falls_through_unsupported_spellings(driver):
    result = driver.execute_tui_any(["/not/a/command", "/mesh/check-quality"])
    assert result.ok
    assert "Orthogonal Quality" in result.output


def test_execute_tui_any_reports_when_nothing_is_supported(driver):
    result = driver.execute_tui_any(["/nope/one", "/nope/two"])
    assert not result.ok
    assert "tanımıyor" in result.error


def test_sizes_are_converted_into_the_import_unit(driver):
    plan = MeshPlan(min_size=0.0005, max_size=0.005, length_unit="mm")
    runner = WorkflowRunner(driver, plan, "C:/geo/part.step")
    runner.initialize()
    runner.surface_mesh()
    controls = driver.arguments["Generate the Surface Mesh"]["CFDSurfaceMeshControls"]
    # 0.0005 m is 0.5 mm - Fluent must see millimetres, not metres.
    assert controls["MinSize"] == pytest.approx(0.5)
    assert controls["MaxSize"] == pytest.approx(5.0)


def test_boundary_layer_arguments_match_the_offset_method(driver):
    plan = MeshPlan(length_unit="mm")
    plan.boundary_layer.offset_method = BLOffsetMethod.LAST_RATIO
    plan.boundary_layer.first_height = 2.0e-5
    plan.boundary_layer.layer_count = 8
    runner = WorkflowRunner(driver, plan, "part.step")
    runner.initialize()
    runner.boundary_layers()
    args = driver.arguments["Add Boundary Layers"]
    assert args["OffsetMethodType"] == "last-ratio"
    assert args["FirstHeight"] == pytest.approx(0.02)   # mm
    assert args["NumberOfLayers"] == 8
    assert "TransitionRatio" not in args

    plan.boundary_layer.offset_method = BLOffsetMethod.SMOOTH_TRANSITION
    runner.boundary_layers()
    args = driver.arguments["Add Boundary Layers"]
    assert "TransitionRatio" in args
    assert "FirstHeight" not in args


def test_disabled_boundary_layers_skip_the_task(driver):
    plan = MeshPlan()
    plan.boundary_layer.enabled = False
    runner = WorkflowRunner(driver, plan, "part.step")
    runner.initialize()
    result = runner.boundary_layers()
    assert result.ok
    assert "Add Boundary Layers" not in driver.executed


def test_hexcore_controls_only_for_hexcore_fills(driver):
    plan = MeshPlan(volume_fill=VolumeFill.POLYHEDRA, length_unit="mm")
    runner = WorkflowRunner(driver, plan, "part.step")
    runner.initialize()
    runner.volume_mesh()
    assert "VolumeFillControls" not in driver.arguments["Generate the Volume Mesh"]

    plan.volume_fill = VolumeFill.POLY_HEXCORE
    runner.volume_mesh()
    assert "VolumeFillControls" in driver.arguments["Generate the Volume Mesh"]


def test_improve_surface_mesh_is_inserted_once(driver):
    plan = MeshPlan()
    runner = WorkflowRunner(driver, plan, "part.step")
    runner.initialize()
    runner.surface_mesh()
    runner.improve_surface_mesh(0.8)
    runner.improve_surface_mesh(0.75)
    assert driver.tasks.count("Improve Surface Mesh") == 1
    assert driver.executed.count("Improve Surface Mesh") == 2


def test_fault_tolerant_workflow_uses_its_own_task_list(driver):
    plan = MeshPlan(workflow=WorkflowType.FAULT_TOLERANT)
    runner = WorkflowRunner(driver, plan, "part.step")
    runner.initialize()
    assert "Enclose Fluid Regions (Capping)" in driver.tasks
    runner.describe_geometry()
    assert "Enclose Fluid Regions (Capping)" in driver.executed


def test_missing_tasks_are_skipped_not_fatal(driver):
    plan = MeshPlan(workflow=WorkflowType.FAULT_TOLERANT)
    runner = WorkflowRunner(driver, plan, "part.step")
    runner.initialize()
    results = runner.update_topology()
    assert all(r.ok for r in results)


def test_tui_catalog_builds_valid_commands():
    assert tui.auto_node_move(0.15, 120, 10)[0].startswith("/mesh/modify/auto-node-move")
    assert '"C:/tmp/a.msh.h5"' in tui.write_mesh(r"C:\tmp\a.msh.h5")[0]
    assert len(tui.check_volume_quality()) >= 2
    assert [name for name, _ in tui.SURFACE_REPAIR_LADDER]


def test_journal_is_a_replayable_pyfluent_script(driver):
    """A dry run must produce a journal that works against real Fluent."""
    plan = MeshPlan(length_unit="mm")
    runner = WorkflowRunner(driver, plan, "part.step")
    runner.initialize()
    runner.import_geometry()
    runner.surface_mesh()
    driver.execute_tui("/mesh/check-quality")
    text = driver.journal_text()
    assert "import ansys.fluent.core as pyfluent" in text
    assert 'launch_fluent(mode="meshing"' in text
    assert 'InitializeWorkflow(WorkflowType="Watertight Geometry")' in text
    assert 'TaskObject["Generate the Surface Mesh"].Execute()' in text
    assert "/mesh/check-quality" in text
    compile(text, "journal.py", "exec")   # syntactically valid Python
