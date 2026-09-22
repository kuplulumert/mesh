import json
import os

import pytest

from automesh.config import Config
from automesh.orchestrator import AutoMeshAgent, _coerce_plan_params, run_agent
from automesh.models import BLOffsetMethod, VolumeFill, WorkflowType


def _run(step_file, cfg, scenario):
    cfg.fluent.mock_scenario = scenario
    return run_agent(step_file, cfg)


def test_clean_geometry_meshes_first_time(step_file, cfg):
    result = _run(step_file, cfg, "clean")
    assert result.success
    assert len(result.attempts) == 1
    assert os.path.isfile(result.mesh_file)
    assert result.final_quality["verdict"] in ("good", "acceptable")


def test_agent_recovers_from_a_surface_failure(step_file, cfg):
    result = _run(step_file, cfg, "realistic")
    assert result.success
    assert len(result.attempts) >= 2
    first = result.attempts[0]
    assert first["status"] == "failed"
    rules = [d["rule_id"] for d in first["diagnoses"]]
    assert "surface_intersecting_faces" in rules
    # It repaired in place first, then changed the plan.
    assert any("Improve Surface Mesh" in a for a in first["actions_applied"])
    assert any("Minimum hücre boyutu" in a for a in first["actions_applied"])


def test_dirty_geometry_switches_to_fault_tolerant(step_file, cfg):
    result = _run(step_file, cfg, "dirty")
    assert result.success
    assert result.final_plan["workflow"] == WorkflowType.FAULT_TOLERANT.value
    applied = [a for attempt in result.attempts for a in attempt["actions_applied"]]
    assert any("fault-tolerant" in a for a in applied)


def test_prism_failure_reduces_the_layer_count(step_file, cfg):
    result = _run(step_file, cfg, "prism")
    assert result.success
    assert result.final_plan["boundary_layer"]["layer_count"] <= 4
    rules = [d["rule_id"] for attempt in result.attempts
             for d in attempt["diagnoses"]]
    assert "prism_failure" in rules


def test_hopeless_geometry_fails_loudly_and_explains(step_file, cfg):
    cfg.autonomy.max_attempts = 3
    result = _run(step_file, cfg, "stubborn")
    assert not result.success
    assert len(result.attempts) == 3
    assert "tüken" in result.message or "kalite" in result.message
    # Every attempt must have been genuinely different from the previous one.
    plans = [json.dumps(a["plan"], sort_keys=True) for a in result.attempts]
    assert len(set(plans)) == len(plans)


def test_out_of_memory_coarsens_the_mesh(step_file, cfg):
    cfg.planning.target_cell_count = 8_000_000
    cfg.planning.max_cell_count = 30_000_000
    result = _run(step_file, cfg, "memory")
    assert result.success
    assert result.final_plan["estimated_cell_count"] <= 2_000_000


def test_run_directory_contains_every_artifact(step_file, cfg):
    result = _run(step_file, cfg, "realistic")
    for name in ("analysis.json", "result.json", "report.md",
                 "transcript.log", "journal.py", "automesh.log"):
        assert os.path.isfile(os.path.join(result.run_dir, name)), name
    report = open(os.path.join(result.run_dir, "report.md"), encoding="utf-8").read()
    assert "# AutoMesh raporu" in report
    assert "Deneme geçmişi" in report
    with open(os.path.join(result.run_dir, "result.json"), encoding="utf-8") as fh:
        assert json.load(fh)["success"] is True


def test_licence_failure_aborts_without_burning_attempts(step_file, cfg, monkeypatch):
    from automesh.fluent.mock_driver import MockFluentDriver

    original = MockFluentDriver.task_execute

    def failing(self, task):
        if task == "Import Geometry":
            from automesh.fluent.driver import CommandResult

            return self.record(CommandResult(
                command="Execute(Import Geometry)", ok=False,
                output="Error: could not check out license for feature meshing"))
        return original(self, task)

    monkeypatch.setattr(MockFluentDriver, "task_execute", failing)
    result = _run(step_file, cfg, "clean")
    assert not result.success
    assert len(result.attempts) == 1          # no point retrying a licence error
    assert "Lisans" in result.message or "licen" in result.message.lower()


def test_unknown_error_falls_back_to_coarsening(step_file, cfg, monkeypatch):
    from automesh.fluent.driver import CommandResult
    from automesh.fluent.mock_driver import MockFluentDriver

    original = MockFluentDriver.task_execute
    state = {"failures": 0}

    def flaky(self, task):
        if task == "Generate the Volume Mesh" and state["failures"] < 1:
            state["failures"] += 1
            return self.record(CommandResult(
                command="Execute(Generate the Volume Mesh)", ok=False,
                output="Error: quantum flux inverter misaligned"))
        return original(self, task)

    monkeypatch.setattr(MockFluentDriver, "task_execute", flaky)
    result = _run(step_file, cfg, "clean")
    rules = [d["rule_id"] for attempt in result.attempts for d in attempt["diagnoses"]]
    assert "generic-coarsen" in rules


def test_plan_parameters_are_coerced_for_adjust_functions():
    params = _coerce_plan_params("set_bl_method", {"method": "aspect-ratio"})
    assert params["method"] is BLOffsetMethod.ASPECT_RATIO
    params = _coerce_plan_params("set_volume_fill", {"fill": "polyhedra"})
    assert params["fill"] is VolumeFill.POLYHEDRA
    params = _coerce_plan_params("reduce_layers", {"count": "3"})
    assert params["count"] == 3
    params = _coerce_plan_params("scale_sizes", {"factor": "1.5"})
    assert params["factor"] == pytest.approx(1.5)
    # An unknown enum value is dropped rather than crashing the run.
    assert "method" not in _coerce_plan_params("set_bl_method", {"method": "nonsense"})


def test_agent_reuses_a_spaceclaim_export(step_file, cfg, tmp_path):
    exported = tmp_path / "exported.stp"
    exported.write_text(open(step_file, encoding="utf-8").read(), encoding="utf-8")
    agent = AutoMeshAgent(step_file, cfg)
    agent._analyze()
    agent.metrics.raw["exported_path"] = str(exported)
    agent._prepare_import_file()
    assert agent._import_geometry_path == str(exported)
