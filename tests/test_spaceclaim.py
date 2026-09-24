"""The SpaceClaim backend cannot be executed here (no Windows, no licence),
so these tests pin down everything that *can* be verified off-line: the
embedded script's syntax, the parameter injection and the JSON mapping."""

import json
import os

import pytest

from automesh.geometry.spaceclaim import (
    SpaceClaimAnalyzer,
    discover_spaceclaim,
    metrics_from_raw,
    script_path,
)


def test_embedded_script_exists_and_compiles():
    path = script_path()
    assert os.path.isfile(path)
    source = open(path, encoding="utf-8").read()
    compile(source, path, "exec")


def test_embedded_script_avoids_python3_only_syntax():
    """SpaceClaim runs IronPython 2.7 - no f-strings, walrus or annotations."""
    import re

    source = open(script_path(), encoding="utf-8").read()
    assert re.search(r"(?<![A-Za-z0-9_])[fF][\"\']", source) is None
    assert ":=" not in source
    assert re.search(r"\)\s*->", source) is None          # return annotations
    assert re.search(r"^\s*def \w+\([^)]*:\s*\w+\s*[,=)]", source,
                     re.MULTILINE) is None                  # arg annotations


def test_parameters_are_injected_into_the_script_copy(tmp_path):
    analyzer = SpaceClaimAnalyzer(exe="C:/fake/SpaceClaim.exe", script_api="251")
    params = tmp_path / "params.json"
    params.write_text("{}", encoding="utf-8")
    destination = tmp_path / "run.py"
    analyzer._materialise_script(str(destination), str(params))
    text = destination.read_text(encoding="utf-8")
    assert text.startswith("# -*- coding: utf-8 -*-")
    assert 'AUTOMESH_PARAMS_FILE = r"' in text
    assert str(params) in text
    assert text.count("# -*- coding") == 1
    compile(text, str(destination), "exec")


def test_analyzer_is_unavailable_without_an_installation():
    # There is no ANSYS install in the test environment, so discovery must
    # return nothing rather than guessing a path.
    assert discover_spaceclaim() is None
    assert SpaceClaimAnalyzer().available() is False
    assert SpaceClaimAnalyzer().can_handle("part.scdoc") is False


def test_explicit_executable_is_honoured(tmp_path):
    exe = tmp_path / "SpaceClaim.exe"
    exe.write_text("")
    analyzer = SpaceClaimAnalyzer(exe=str(exe), script_api="242")
    assert analyzer.available() is True
    assert analyzer.can_handle("part.scdoc") is True
    assert analyzer.can_handle("mesh.stl") is False


def test_raw_json_maps_onto_metrics():
    raw = {
        "analyzer": "spaceclaim",
        "bbox": {"xmin": 0, "ymin": 0, "zmin": 0,
                 "xmax": 0.3, "ymax": 0.1, "zmax": 0.05},
        "volume": 3.5e-4, "area": 0.12, "body_count": 2,
        "face_count": 240, "edge_count": 600,
        "min_edge_length": 0.0008, "min_face_size": 0.0012,
        "min_curvature_radius": 0.0015, "thinnest_section": 0.004,
        "curved_face_ratio": 0.42, "small_feature_ratio": 0.08,
        "watertight": True, "length_unit_hint": "m",
        "warnings": ["dikkat"],
        "bodies": [{"name": "govde-1", "volume": 3.5e-4, "area": 0.12,
                    "face_count": 240, "edge_count": 600, "is_solid": True,
                    "min_face_area": 1e-6, "min_edge_length": 0.0008}],
        "exported_path": "C:/tmp/part.stp",
    }
    metrics = metrics_from_raw(raw)
    assert metrics.analyzer == "spaceclaim"
    assert metrics.diagonal == pytest.approx(0.3201562, rel=1e-5)
    assert metrics.volume == pytest.approx(3.5e-4)
    assert metrics.body_count == 2
    assert metrics.watertight is True
    assert metrics.bodies[0].name == "govde-1"
    assert metrics.warnings == ["dikkat"]
    assert metrics.raw["exported_path"] == "C:/tmp/part.stp"
    # Round-trips through JSON for the run report.
    assert json.loads(json.dumps(metrics.to_dict()))["body_count"] == 2


# --------------------------------------------------------------------------
# dosyayı SpaceClaim'de açma
# --------------------------------------------------------------------------

def test_open_document_launches_a_detached_process(tmp_path, monkeypatch):
    """Analiz koşusundan ayrı, beklenmeyen bir süreç başlatılmalı.

    Beklenseydi ajan, kullanıcı SpaceClaim'i kapatana kadar donardı.
    """
    import subprocess

    from automesh.config import Config

    exe = tmp_path / "SpaceClaim.exe"
    exe.write_text("")
    document = tmp_path / "part_automesh.scdoc"
    document.write_text("")

    launched = {}

    def fake_popen(cmd, **kwargs):
        launched["cmd"] = cmd
        launched["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    analyzer = SpaceClaimAnalyzer(exe=str(exe), script_api="252")
    assert analyzer.open_document(str(document), Config()) is True
    cmd = launched["cmd"]
    assert cmd[0] == str(exe)
    assert "/Headless=False" in cmd
    # SpaceClaim açık kalmalı
    assert not any("ExitAfterScript" in str(part) for part in cmd)
    # Dosyayı küçük bir betik açar
    script = [part for part in cmd if part.startswith("/RunScript=")][0]
    text = open(script.split("=", 1)[1], encoding="utf-8").read()
    assert "DocumentOpen.Execute" in text and str(document) in text
    compile(text, "ac.py", "exec")


def test_open_document_is_quiet_when_there_is_nothing_to_open(tmp_path):
    from automesh.config import Config

    exe = tmp_path / "SpaceClaim.exe"
    exe.write_text("")
    analyzer = SpaceClaimAnalyzer(exe=str(exe), script_api="252")
    assert analyzer.open_document(str(tmp_path / "yok.scdoc"), Config()) is False


def test_open_document_without_an_installation(tmp_path):
    from automesh.config import Config

    document = tmp_path / "part.scdoc"
    document.write_text("")
    assert SpaceClaimAnalyzer().open_document(str(document), Config()) is False


def test_headless_flag_follows_the_config():
    from automesh.config import Config

    cfg = Config()
    assert cfg.geometry.spaceclaim_headless is True
    assert cfg.geometry.open_in_spaceclaim is False
    cfg.geometry.spaceclaim_headless = False
    assert cfg.geometry.spaceclaim_headless is False
