import json
import os

import pytest

from automesh.cli import main
from automesh.config import Config, apply_overrides, parse_config_text


def test_overrides_are_typed_correctly():
    cfg = Config()
    apply_overrides(cfg, [
        "fluent.processor_count=12",
        "fluent.use_mock=true",
        "autonomy.max_attempts=2",
        "geometry.y_plus_target=1.5",
        "geometry.spaceclaim_exe=C:/ansys/SpaceClaim.exe",
        "planning.growth_rate_range=1.1,1.25",
    ])
    assert cfg.fluent.processor_count == 12
    assert cfg.fluent.use_mock is True
    assert cfg.autonomy.max_attempts == 2
    assert cfg.geometry.y_plus_target == pytest.approx(1.5)
    assert cfg.geometry.spaceclaim_exe == "C:/ansys/SpaceClaim.exe"
    assert cfg.planning.growth_rate_range == [1.1, 1.25]


def test_unknown_options_are_rejected_loudly():
    cfg = Config()
    with pytest.raises(ValueError):
        apply_overrides(cfg, ["fluent.warp_drive=9"])
    with pytest.raises(ValueError):
        apply_overrides(cfg, ["nonexistent.key=1"])
    with pytest.raises(ValueError):
        apply_overrides(cfg, ["missing_equals"])
    with pytest.raises(ValueError):
        Config.from_dict({"fluent": {"nope": 1}})


def test_shipped_config_files_load():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("config/default.yaml",
                 "config/examples/external-aero.yaml",
                 "config/examples/dirty-cad.yaml"):
        assert Config.load(os.path.join(root, name)) is not None


def test_json_config_needs_no_yaml_dependency():
    data = parse_config_text('{"fluent": {"processor_count": 3}}')
    assert Config.from_dict(data).fluent.processor_count == 3


def test_config_roundtrips_through_disk(tmp_path):
    path = tmp_path / "cfg.json"
    cfg = Config()
    cfg.fluent.processor_count = 7
    cfg.save(str(path))
    assert Config.load(str(path)).fluent.processor_count == 7


def test_cli_plan_prints_a_recipe(step_file, capsys):
    assert main(["plan", step_file]) == 0
    out = capsys.readouterr().out
    assert "Mesh planı" in out
    assert "Min / max boyut" in out


def test_cli_plan_json_is_machine_readable(step_file, capsys):
    assert main(["plan", step_file, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["plan"]["workflow"] in ("watertight", "fault-tolerant")
    assert payload["geometry"]["analyzer"] == "step"


def test_cli_analyze(step_file, capsys):
    assert main(["analyze", step_file]) == 0
    assert "Geometri analizi" in capsys.readouterr().out


def test_cli_diagnose_reads_a_transcript(tmp_path, capsys):
    log = tmp_path / "fluent.trn"
    log.write_text("Error: prism layer generation failed\nPrisms collapsed.\n")
    assert main(["diagnose", str(log), "--stage", "boundary-layer"]) == 0
    out = capsys.readouterr().out
    assert "prism_failure" in out
    assert "Çözüm" in out


def test_cli_diagnose_reports_no_match(tmp_path, capsys):
    log = tmp_path / "clean.trn"
    log.write_text("Everything went fine.\n")
    assert main(["diagnose", str(log)]) == 1


def test_cli_rules_lists_the_knowledge_base(capsys):
    assert main(["rules"]) == 0
    assert "teşhis kuralı" in capsys.readouterr().out


def test_cli_config_writes_a_template(tmp_path, capsys):
    target = tmp_path / "automesh.json"
    assert main(["config", "-o", str(target)]) == 0
    assert Config.load(str(target)) is not None


def test_cli_run_dry_run(step_file, tmp_path, capsys):
    code = main(["run", step_file, "--dry-run", "--scenario", "clean",
                 "--out", str(tmp_path / "run"),
                 "--set", "output.run_root={0}".format(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Rapor" in out and "Mesh" in out
    assert os.path.isfile(str(tmp_path / "run" / "report.md"))


def test_cli_run_returns_nonzero_when_it_gives_up(step_file, tmp_path):
    code = main(["run", step_file, "--dry-run", "--scenario", "stubborn",
                 "--attempts", "2", "--out", str(tmp_path / "run")])
    assert code == 1


def test_cli_run_flags_reach_the_config(step_file, tmp_path, monkeypatch):
    captured = {}

    def fake_run(geometry, cfg, out):
        captured["cfg"] = cfg
        from automesh.models import RunResult
        return RunResult(success=True, run_dir=str(tmp_path), message="ok")

    monkeypatch.setattr("automesh.orchestrator.run_agent", fake_run)
    main(["run", step_file, "--dry-run", "--cores", "16", "--y-plus", "1",
          "--velocity", "25", "--workflow", "fault-tolerant",
          "--fill", "polyhedra", "--max-cells", "5000000",
          "--no-boundary-layers", "--advisor"])
    cfg = captured["cfg"]
    assert cfg.fluent.processor_count == 16
    assert cfg.geometry.y_plus_target == 1
    assert cfg.geometry.velocity == 25
    assert cfg.planning.workflow == "fault-tolerant"
    assert cfg.planning.volume_fill == "polyhedra"
    assert cfg.planning.max_cell_count == 5_000_000
    assert cfg.planning.boundary_layers is False
    assert cfg.advisor.enabled is True


def test_parse_length_accepts_units():
    from automesh.cli import parse_length

    assert parse_length("0.4mm") == pytest.approx(0.0004)
    assert parse_length("2 mm") == pytest.approx(0.002)
    assert parse_length("0.0004") == pytest.approx(0.0004)
    assert parse_length("0.1in") == pytest.approx(0.00254)
    assert parse_length("1,5mm") == pytest.approx(0.0015)     # Türkçe ondalık
    with pytest.raises(ValueError):
        parse_length("mm")
    with pytest.raises(ValueError):
        parse_length("")


def test_cli_propose_lists_measurements_and_levels(step_file, capsys):
    assert main(["propose", step_file]) == 0
    out = capsys.readouterr().out
    assert "Ölçümler" in out
    assert "Mesh seçenekleri" in out
    assert "önerilen" in out
    assert "--level" in out


def test_cli_propose_json(step_file, capsys):
    assert main(["propose", step_file, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["proposals"]) == 5
    assert payload["measurements"][0]["label"] == "Sınır kutusu"


def test_cli_run_level_and_sizes_reach_the_config(step_file, tmp_path, monkeypatch):
    captured = {}

    def fake_run(geometry, cfg, out, cancel=None):
        captured["cfg"] = cfg
        from automesh.models import RunResult
        return RunResult(success=True, run_dir=str(tmp_path), message="ok")

    monkeypatch.setattr("automesh.orchestrator.run_agent", fake_run)
    main(["run", step_file, "--dry-run", "--level", "fine",
          "--min-size", "0.3mm", "--max-size", "2mm",
          "--growth", "1.12", "--layers", "3"])
    planning = captured["cfg"].planning
    assert planning.level == "fine"
    assert planning.override_min_size == pytest.approx(0.0003)
    assert planning.override_max_size == pytest.approx(0.002)
    assert planning.override_growth_rate == pytest.approx(1.12)
    assert planning.override_layer_count == 3
