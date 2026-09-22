"""Arayüzün Tkinter'dan bağımsız kısımlarının testleri.

Pencerenin kendisi ekran gerektirdiği için burada açılmıyor; bütün mantık
zaten :mod:`state` ve :mod:`runner` içinde, Tk'ya dokunmadan test edilebilir
biçimde duruyor.
"""

import logging
import os
import time

import pytest

from automesh.guiapp.runner import DONE, ERROR, LOG, BackgroundRun
from automesh.guiapp.state import FILLS, SCENARIOS, WORKFLOWS, GuiSettings


def _drain_until_done(run, timeout=30.0):
    """İş bitene kadar kuyruğu boşalt; (loglar, sonuç) döndür."""
    logs, result, done = [], None, False
    deadline = time.time() + timeout
    while time.time() < deadline:
        for message in run.drain():
            if message.kind == LOG:
                logs.append(message.text)
            elif message.kind == ERROR:
                logs.append("ERROR: " + message.text)
            elif message.kind == DONE:
                result, done = message.result, True
        if done and not run.running:
            break
        time.sleep(0.01)
    run.join(5.0)
    return logs, result


# --------------------------------------------------------------------------
# ayarlar
# --------------------------------------------------------------------------

def test_settings_roundtrip_through_disk(tmp_path):
    path = str(tmp_path / "gui-settings.json")
    settings = GuiSettings(geometry_path="C:/x/part.scdoc", cores=12, y_plus="30",
                           velocity="15", dry_run=True, scenario="prism")
    settings.save(path)
    loaded = GuiSettings.load(path)
    assert loaded.cores == 12
    assert loaded.y_plus == "30"
    assert loaded.scenario == "prism"
    assert loaded.to_dict() == settings.to_dict()


def test_missing_settings_file_gives_defaults(tmp_path):
    loaded = GuiSettings.load(str(tmp_path / "yok.json"))
    assert loaded.cores == 4
    assert loaded.workflow == "auto"


def test_unwritable_settings_path_does_not_raise(tmp_path):
    # Kaydetme bir kolaylık; başarısız olursa arayüz çalışmaya devam etmeli.
    GuiSettings().save(str(tmp_path / "olmayan-klasor" / "a" / "b" / "c.json"))


def test_validation_catches_user_mistakes(step_file):
    assert GuiSettings().validate() == ["Geometri dosyası seçilmedi."]
    assert "bulunamadı" in GuiSettings(geometry_path="C:/yok.stp").validate()[0]
    assert GuiSettings(geometry_path=step_file).validate() == []

    bad_numbers = GuiSettings(geometry_path=step_file, y_plus="abc", velocity="10")
    assert any("y+ sayı olmalı" in p for p in bad_numbers.validate())

    lonely_yplus = GuiSettings(geometry_path=step_file, y_plus="30")
    assert any("hız da gerekli" in p for p in lonely_yplus.validate())

    assert any("Çekirdek" in p for p in
               GuiSettings(geometry_path=step_file, cores=0).validate())
    assert any("Konfigürasyon" in p for p in
               GuiSettings(geometry_path=step_file,
                           config_file="C:/yok.yaml").validate())


def test_comma_decimals_are_accepted(step_file):
    """Türkçe klavyede 1,5 yazmak yaygın - bunu hata saymayalım."""
    settings = GuiSettings(geometry_path=step_file, y_plus="1,5", velocity="12,5")
    assert settings.validate() == []
    cfg = settings.to_config()
    assert cfg.geometry.y_plus_target == pytest.approx(1.5)
    assert cfg.geometry.velocity == pytest.approx(12.5)


def test_settings_become_a_valid_config(step_file):
    settings = GuiSettings(
        geometry_path=step_file, cores=16, workflow="fault-tolerant",
        volume_fill="polyhedra", max_cells=5_000_000, attempts=3,
        boundary_layers=False, show_fluent_gui=True, use_advisor=True,
        y_plus="30", velocity="27.8", characteristic_length="4.5",
        length_unit="mm", ansys_version="24.2.0")
    cfg = settings.to_config()
    assert cfg.fluent.processor_count == 16
    assert cfg.fluent.ui_mode == "gui"
    assert cfg.fluent.product_version == "24.2.0"
    assert cfg.planning.workflow == "fault-tolerant"
    assert cfg.planning.volume_fill == "polyhedra"
    assert cfg.planning.max_cell_count == 5_000_000
    assert cfg.planning.boundary_layers is False
    assert cfg.autonomy.max_attempts == 3
    assert cfg.advisor.enabled is True
    assert cfg.geometry.length_unit == "mm"
    assert cfg.geometry.y_plus_target == pytest.approx(30.0)


def test_dry_run_selects_the_mock_driver(step_file):
    cfg = GuiSettings(geometry_path=step_file, dry_run=True,
                      scenario="prism").to_config()
    assert cfg.fluent.use_mock is True
    assert cfg.fluent.mock_scenario == "prism"
    assert GuiSettings(geometry_path=step_file).to_config().fluent.use_mock is False


def test_config_file_is_used_as_the_base(tmp_path, step_file):
    config = tmp_path / "base.json"
    config.write_text('{"quality": {"max_skewness_accept": 0.93}}', encoding="utf-8")
    cfg = GuiSettings(geometry_path=step_file, config_file=str(config),
                      cores=9).to_config()
    assert cfg.quality.max_skewness_accept == pytest.approx(0.93)
    assert cfg.fluent.processor_count == 9      # arayüz dosyanın üstüne yazar


def test_equivalent_command_is_copy_pasteable(step_file):
    settings = GuiSettings(geometry_path="C:/CAD/bir parca.stp", cores=8,
                           show_fluent_gui=True, y_plus="1", velocity="12",
                           output_dir="E:/mesh cikti")
    command = settings.equivalent_command()
    assert command.startswith("automesh run ")
    assert '"C:/CAD/bir parca.stp"' in command      # boşluklu yol tırnaklanmalı
    assert '"E:/mesh cikti"' in command
    assert "--cores 8" in command and "--gui" in command
    assert "--y-plus 1" in command and "--velocity 12" in command
    # Varsayılan değerler komutu şişirmemeli
    assert "--fill" not in command and "--attempts" not in command


def test_dropdown_values_match_the_engine():
    from automesh.fluent.mock_driver import SCENARIOS as ENGINE_SCENARIOS
    from automesh.models import VolumeFill

    assert set(SCENARIOS) == set(ENGINE_SCENARIOS)
    assert set(FILLS) == {f.value for f in VolumeFill}
    assert set(WORKFLOWS) == {"auto", "watertight", "fault-tolerant"}


def test_run_directory_defaults_to_none(step_file):
    assert GuiSettings(geometry_path=step_file).run_directory() is None
    assert GuiSettings(geometry_path=step_file,
                       output_dir="E:/x").run_directory() == "E:/x"


# --------------------------------------------------------------------------
# arka plan çalıştırıcı
# --------------------------------------------------------------------------

def test_background_run_drives_a_real_meshing_run(step_file, tmp_path):
    settings = GuiSettings(geometry_path=step_file, dry_run=True, scenario="realistic",
                           output_dir=str(tmp_path / "run"), attempts=4)
    run = BackgroundRun(settings, "run")
    run.start()
    logs, result = _drain_until_done(run)

    assert result is not None and result.success
    assert os.path.isfile(os.path.join(result.run_dir, "report.md"))
    # Günlük gerçekten arayüze akmış olmalı
    joined = "\n".join(logs)
    assert "Deneme 1" in joined
    assert "Teşhis" in joined
    assert not run.running


def test_analyze_mode_does_not_touch_fluent(step_file, tmp_path):
    settings = GuiSettings(geometry_path=step_file, output_dir=str(tmp_path))
    run = BackgroundRun(settings, "analyze")
    run.start()
    logs, result = _drain_until_done(run)
    joined = "\n".join(logs)
    assert "--- Geometri ---" in joined
    assert "Sınır kutusu" in joined
    assert "Deneme" not in joined          # meshing başlamadı
    assert result is None


def test_plan_mode_reports_the_recipe(step_file, tmp_path):
    settings = GuiSettings(geometry_path=step_file, output_dir=str(tmp_path),
                           y_plus="1", velocity="12")
    run = BackgroundRun(settings, "plan")
    run.start()
    logs, _ = _drain_until_done(run)
    joined = "\n".join(logs)
    assert "--- Mesh planı ---" in joined
    assert "Min / max boyut" in joined
    assert "İlk katman" in joined          # y+ verildi


def test_cancel_stops_the_run_cleanly(step_file, tmp_path):
    settings = GuiSettings(geometry_path=step_file, dry_run=True, scenario="stubborn",
                           output_dir=str(tmp_path / "run"), attempts=30)
    run = BackgroundRun(settings, "run")
    run.start()
    run.cancel()
    logs, result = _drain_until_done(run)
    assert not run.running
    assert result is not None
    assert result.success is False
    assert "durduruldu" in result.message.lower()
    # Yarıda kesilse bile rapor yazılmış olmalı
    assert os.path.isfile(os.path.join(result.run_dir, "report.md"))


def test_failures_surface_as_an_error_message(tmp_path):
    settings = GuiSettings(geometry_path=str(tmp_path / "yok.step"))
    run = BackgroundRun(settings, "analyze")
    run.start()
    logs, _ = _drain_until_done(run)
    assert any("ERROR:" in line for line in logs)


def test_log_handler_is_detached_after_the_run(step_file, tmp_path):
    from automesh.logging_utils import get_logger

    before = len(get_logger().handlers)
    settings = GuiSettings(geometry_path=step_file, dry_run=True, scenario="clean",
                           output_dir=str(tmp_path / "run"))
    run = BackgroundRun(settings, "run")
    run.start()
    _drain_until_done(run)
    # Handler sızdırırsak ikinci çalıştırmada satırlar iki kez görünürdü.
    assert len(get_logger().handlers) <= before + 1


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        BackgroundRun(GuiSettings(), "format-c")


def test_cannot_start_twice(step_file, tmp_path):
    settings = GuiSettings(geometry_path=step_file, dry_run=True, scenario="stubborn",
                           output_dir=str(tmp_path / "run"), attempts=30)
    run = BackgroundRun(settings, "run")
    run.start()
    try:
        with pytest.raises(RuntimeError):
            run.start()
    finally:
        run.cancel()
        _drain_until_done(run)
