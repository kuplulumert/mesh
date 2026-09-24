"""Temizlik taramasının Python tarafı: betik birleştirme, çalıştırma, rapor.

SpaceClaim burada yok; çalıştırıcı sahte bir süreçle sınanır. Sahte süreç,
gerçek betiğin yazacağı JSON'u yazar.
"""

import json
import os

import pytest

from automesh.config import Config
from automesh.geometry import cleanup
from automesh.geometry.base import GeometryAnalyzerError

RAW = {
    "ok": True,
    "analyzer": "spaceclaim-cleanup",
    "source_path": "M:/CAD/Multicyclone.scdoc",
    "diagonal": 1.2,
    "body_count": 1,
    "face_count": 842,
    "thresholds": {"fillet_max_radius": 0.002, "hole_max_diameter": 0.012,
                   "protrusion_max_size": 0.01,
                   "auto": {"fillet_max_radius": True,
                            "hole_max_diameter": False,
                            "protrusion_max_size": True}},
    "features": [
        {"category": "fileto", "name": "temizle_fileto_R0p50mm_01",
         "number": 1, "size": 0.0005, "extent": 0.02, "center": [0, 0, 0],
         "face_count": 3, "created": True, "note": ""},
        {"category": "vida", "name": "temizle_vida_D6p00mm_01", "number": 1,
         "size": 0.006, "extent": 0.01, "center": [0.1, 0, 0],
         "face_count": 4, "created": False, "note": "ad verilemedi"},
        {"category": "cikinti", "name": "", "number": 81, "size": 0.004,
         "extent": 0.004, "center": [0, 0.1, 0], "face_count": 5,
         "created": False, "note": "sadece HEPSI grubunda"},
    ],
    "skipped": [{"category": "fileto", "size": 0.001, "center": [0, 0, 0],
                 "face_count": 1,
                 "reason": "kullanici grubuna ait yuz iceriyor: inlet"}],
    "summary": {"fileto": 1, "vida": 1, "cikinti": 1},
    "diagnostics": {"komsuluk": "kimlik", "komsuluk_sayisi": 1200,
                    "yuz_tipleri": {"Plane": 400, "Cylinder": 300}},
    "warnings": [],
}


# -- betik birleştirme -----------------------------------------------------

def test_built_script_is_one_valid_file(tmp_path):
    text = cleanup.build_script(str(tmp_path / "params.json"))
    compile(text, "run.py", "exec")
    assert text.count("# -*- coding") == 1
    assert "AUTOMESH_SC_LIBRARY = True" in text
    assert "def cleanup_scan" in text and "def create_named_selection" in text


def test_built_script_runs_the_scan_not_the_analysis(tmp_path, monkeypatch):
    """Analiz betiği kütüphane olarak eklenir; onun main()'i çalışmamalı."""
    params = tmp_path / "params.json"
    output = tmp_path / "out.json"
    params.write_text(json.dumps({"input": "x.scdoc", "output": str(output)}),
                      encoding="utf-8")
    monkeypatch.delenv("AUTOMESH_SC_NO_RUN", raising=False)
    opened = []
    namespace = {"DocumentOpen": type("D", (), {"Execute": staticmethod(
        lambda path: opened.append(path))})}
    exec(compile(cleanup.build_script(str(params)), "run.py", "exec"), namespace)
    # Taramada GetRootPart yok -> hata JSON'a yazılır; önemli olan DocumentOpen'ın
    # yalnızca bir kez (temizlik tarafından) çağrılması.
    assert opened == ["x.scdoc"]
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert result["analyzer"] == "spaceclaim-cleanup"


# -- yollar ve parametreler -------------------------------------------------

def test_output_path_never_overwrites_the_source(tmp_path):
    source = tmp_path / "parca_temizlik.scdoc"
    target = cleanup.cleanup_output_path(str(source), str(tmp_path))
    assert os.path.abspath(target) != os.path.abspath(str(source))
    assert target.endswith(".scdoc")


def test_output_path_uses_the_suffix(tmp_path):
    target = cleanup.cleanup_output_path("M:/CAD/Multicyclone.scdoc", str(tmp_path))
    assert os.path.basename(target) == "Multicyclone_temizlik.scdoc"


def test_params_follow_the_settings():
    cfg = Config()
    cfg.cleanup.fillet_max_radius = 0.0015
    cfg.cleanup.categories = ["fileto", "bilinmeyen"]
    params = cleanup.cleanup_params(cfg)
    assert params["fillet_max_radius"] == pytest.approx(0.0015)
    assert params["hole_max_diameter"] == 0.0            # otomatik
    assert params["categories"] == ["fileto"]
    assert params["create_groups"] is True


def test_cleanup_section_loads_from_config():
    cfg = Config.from_dict({"cleanup": {"hole_max_diameter": 0.008,
                                        "open_in_spaceclaim": False}})
    assert cfg.cleanup.hole_max_diameter == pytest.approx(0.008)
    assert cfg.cleanup.open_in_spaceclaim is False


# -- rapor -------------------------------------------------------------------

def test_report_maps_the_raw_json():
    report = cleanup.report_from_raw(RAW)
    assert report.total == 3
    assert report.summary == {"fileto": 1, "vida": 1, "cikinti": 1}
    assert report.thresholds_auto["hole_max_diameter"] is False
    assert report.by_category("vida")[0].size == pytest.approx(0.006)
    assert report.skipped[0].reason.endswith("inlet")


def test_report_text_lists_groups_thresholds_and_skips():
    text = "\n".join(cleanup.format_report(cleanup.report_from_raw(RAW)))
    assert "temizle_fileto_R0p50mm_01" in text
    assert "temizle_vida_HEPSI" in text
    assert "2 mm  (otomatik)" in text
    assert "12 mm" in text and "12 mm  (otomatik)" not in text
    assert "grup oluşturulamadı: ad verilemedi" in text
    assert "HEPSI grubunda" in text
    assert "inlet" in text


def test_empty_report_says_so():
    raw = dict(RAW, features=[], skipped=[], summary={})
    text = "\n".join(cleanup.format_report(cleanup.report_from_raw(raw)))
    assert "bulunamadı" in text


def test_usage_hint_explains_the_spaceclaim_steps():
    text = "\n".join(cleanup.usage_hint("C:/runs/x_temizlik.scdoc"))
    assert "Groups" in text and "Delete" in text and "Ctrl+S" in text
    assert "C:/runs/x_temizlik.scdoc" in text


# -- çalıştırma ----------------------------------------------------------------

class FakeAnalyzer:
    def __init__(self, available=True):
        self._available = available
        self._probed = False
        self.opened = []

    def _probe(self, cfg=None):
        pass

    def available(self):
        return self._available

    def script_command(self, run_script, cfg):
        return ["SpaceClaim.exe", "/RunScript=" + run_script]

    def open_document(self, path, cfg=None):
        self.opened.append(path)
        return True


def _fake_runner(raw, write_output=True, save=True):
    """Gerçek betik gibi davran: parametreleri oku, JSON'u ve kopyayı yaz."""
    calls = {}

    def run(cmd, env, timeout):
        calls["cmd"] = cmd
        with open(env["AUTOMESH_SC_PARAMS"], encoding="utf-8") as handle:
            params = json.load(handle)
        calls["params"] = params
        calls["script"] = open(cmd[-1].split("=", 1)[1], encoding="utf-8").read()
        result = dict(raw)
        if save:
            open(params["export"], "w").write("scdoc")
            result["saved_path"] = params["export"]
        if write_output:
            with open(params["output"], "w", encoding="utf-8") as handle:
                json.dump(result, handle)
        return type("P", (), {"returncode": 0, "stdout": b"log"})()

    return run, calls


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "cad" / "Multicyclone.scdoc"
    path.parent.mkdir()
    path.write_text("orijinal", encoding="utf-8")
    return path


def test_scan_writes_a_marked_copy_and_opens_it(tmp_path, source):
    analyzer = FakeAnalyzer()
    runner, calls = _fake_runner(RAW)
    out = tmp_path / "out"
    report = cleanup.run_cleanup_scan(str(source), Config(), str(out),
                                      analyzer=analyzer, runner=runner)

    assert report.total == 3
    assert report.saved_path == str(out / "Multicyclone_temizlik.scdoc")
    assert analyzer.opened == [report.saved_path]
    assert source.read_text(encoding="utf-8") == "orijinal"     # dokunulmadı
    assert calls["params"]["input"] == str(source)
    assert "AUTOMESH_SC_LIBRARY = True" in calls["script"]


def test_scan_can_skip_opening_spaceclaim(tmp_path, source):
    cfg = Config()
    cfg.cleanup.open_in_spaceclaim = False
    analyzer = FakeAnalyzer()
    runner, _ = _fake_runner(RAW)
    cleanup.run_cleanup_scan(str(source), cfg, str(tmp_path / "o"),
                             analyzer=analyzer, runner=runner)
    assert analyzer.opened == []


def test_scan_passes_thresholds_in_metres(tmp_path, source):
    cfg = Config()
    cfg.cleanup.fillet_max_radius = 0.001
    cfg.cleanup.categories = ["vida"]
    runner, calls = _fake_runner(RAW)
    cleanup.run_cleanup_scan(str(source), cfg, str(tmp_path / "o"),
                             analyzer=FakeAnalyzer(), runner=runner)
    assert calls["params"]["fillet_max_radius"] == pytest.approx(0.001)
    assert calls["params"]["categories"] == ["vida"]


def test_scan_without_spaceclaim_explains(tmp_path, source):
    with pytest.raises(GeometryAnalyzerError, match="SpaceClaim"):
        cleanup.run_cleanup_scan(str(source), Config(), str(tmp_path),
                                 analyzer=FakeAnalyzer(available=False))


def test_scan_rejects_mesh_files(tmp_path):
    stl = tmp_path / "x.stl"
    stl.write_text("solid", encoding="utf-8")
    with pytest.raises(GeometryAnalyzerError, match="CAD"):
        cleanup.run_cleanup_scan(str(stl), Config(), str(tmp_path),
                                 analyzer=FakeAnalyzer())


def test_scan_reports_a_missing_output(tmp_path, source):
    runner, _ = _fake_runner(RAW, write_output=False)
    with pytest.raises(GeometryAnalyzerError, match="çıktısı üretmedi"):
        cleanup.run_cleanup_scan(str(source), Config(), str(tmp_path / "o"),
                                 analyzer=FakeAnalyzer(), runner=runner)


def test_scan_surfaces_a_script_error(tmp_path, source):
    runner, _ = _fake_runner({"ok": False, "error": "Traceback: GetRootPart"},
                             save=False)
    with pytest.raises(GeometryAnalyzerError, match="GetRootPart"):
        cleanup.run_cleanup_scan(str(source), Config(), str(tmp_path / "o"),
                                 analyzer=FakeAnalyzer(), runner=runner)


# -- komut satırı --------------------------------------------------------------

def test_cli_converts_millimetres(monkeypatch, capsys, source):
    from automesh import cli

    seen = {}

    def fake_scan(path, cfg, out):
        seen["cfg"] = cfg
        return cleanup.report_from_raw(RAW)

    monkeypatch.setattr(cleanup, "run_cleanup_scan", fake_scan)
    code = cli.main(["temizle", str(source), "--fileto", "1.5", "--vida", "8mm",
                     "--kategoriler", "fileto,vida", "--acma"])
    assert code == 0
    settings = seen["cfg"].cleanup
    assert settings.fillet_max_radius == pytest.approx(0.0015)
    assert settings.hole_max_diameter == pytest.approx(0.008)
    assert settings.categories == ["fileto", "vida"]
    assert settings.open_in_spaceclaim is False
    assert "3 detay" in capsys.readouterr().out


def test_cli_rejects_unknown_categories(capsys, source):
    from automesh import cli

    assert cli.main(["temizle", str(source), "--kategoriler", "vidalar"]) == 2
    assert "Bilinmeyen kategori" in capsys.readouterr().err


def test_cli_reports_scan_errors_cleanly(monkeypatch, capsys, source):
    from automesh import cli

    def boom(*_a):
        raise GeometryAnalyzerError("SpaceClaim.exe bulunamadı")

    monkeypatch.setattr(cleanup, "run_cleanup_scan", boom)
    assert cli.main(["temizle", str(source)]) == 2
    assert "SpaceClaim.exe bulunamadı" in capsys.readouterr().err
