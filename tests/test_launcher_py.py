"""Python başlatıcısının testleri (`.exe`/`.bat` yasaklı makineler için)."""

import ast
import io
import os
import sys
import types

import pytest

from automesh import launcher

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYW = os.path.join(ROOT, "AutoMesh.pyw")
CONSOLE = os.path.join(ROOT, "AutoMesh-konsol.py")


# -- depodaki dosyalar -----------------------------------------------------

@pytest.mark.parametrize("path", [PYW, CONSOLE])
def test_launcher_files_exist_and_parse(path):
    """Sözdizimi hatası = çift tıklayınca hiçbir şey olmaz."""
    assert os.path.isfile(path), path
    ast.parse(io.open(path, encoding="utf-8").read())


@pytest.mark.parametrize("path", [PYW, CONSOLE])
def test_launcher_files_bootstrap_src(path):
    """Kurulum yapılmamış olabilir: src kendi eliyle yola eklenmeli."""
    text = io.open(path, encoding="utf-8").read()
    assert 'os.path.join(HERE, "src")' in text
    assert "sys.path.insert(0, SRC)" in text


def test_windowless_launcher_has_the_pyw_extension():
    """Konsol penceresi çıkmasın diye uzantı .pyw olmalı."""
    assert PYW.endswith(".pyw")


def test_windowless_launcher_reports_a_missing_package():
    """automesh hiç bulunamazsa bile kullanıcı sebebini görmeli."""
    text = io.open(PYW, encoding="utf-8").read()
    assert "automesh-hata.txt" in text
    assert "showerror" in text


# -- modül yolları ---------------------------------------------------------

def test_extra_paths_lists_src_first(tmp_path):
    os.makedirs(str(tmp_path / "src"))
    (tmp_path / launcher.PATHS_FILE).write_text(
        "# yorum\nD:\\Work\\plm\n\n  D:\\ortak  \n", encoding="utf-8")
    found = launcher.extra_paths(str(tmp_path))
    assert found == [str(tmp_path / "src"), "D:\\Work\\plm", "D:\\ortak"]


def test_extra_paths_without_a_list_file(tmp_path):
    os.makedirs(str(tmp_path / "src"))
    assert launcher.extra_paths(str(tmp_path)) == [str(tmp_path / "src")]


def test_prepare_path_keeps_the_written_order(tmp_path, monkeypatch):
    os.makedirs(str(tmp_path / "src"))
    (tmp_path / launcher.PATHS_FILE).write_text("A:\\bir\nB:\\iki\n",
                                                encoding="utf-8")
    monkeypatch.setattr(sys, "path", ["mevcut"])
    added = launcher.prepare_path(str(tmp_path))
    assert added == [str(tmp_path / "src"), "A:\\bir", "B:\\iki"]
    assert sys.path == added + ["mevcut"]


def test_prepare_path_does_not_duplicate(tmp_path, monkeypatch):
    os.makedirs(str(tmp_path / "src"))
    monkeypatch.setattr(sys, "path", [str(tmp_path / "src")])
    assert launcher.prepare_path(str(tmp_path)) == []


# -- arayüzü başlatma ------------------------------------------------------

def test_launch_gui_forwards_a_dropped_geometry(tmp_path, monkeypatch):
    seen = {}
    fake = types.ModuleType("automesh.guiapp")

    def gui_main(geometry=None):
        seen["g"] = geometry
        return 0

    fake.main = gui_main
    monkeypatch.setitem(sys.modules, "automesh.guiapp", fake)

    script = str(tmp_path / "AutoMesh.pyw")
    io.open(script, "w").write("")
    code = launcher.launch_gui(script, [script, "M:\\parca.scdoc"])
    assert code == 0
    assert seen["g"] == "M:\\parca.scdoc"


def test_launch_gui_ignores_option_like_arguments(tmp_path, monkeypatch):
    seen = {}
    fake = types.ModuleType("automesh.guiapp")

    def gui_main(geometry=None):
        seen["g"] = geometry
        return 0

    fake.main = gui_main
    monkeypatch.setitem(sys.modules, "automesh.guiapp", fake)

    script = str(tmp_path / "AutoMesh.pyw")
    io.open(script, "w").write("")
    launcher.launch_gui(script, [script, "--debug"])
    assert seen["g"] is None


def test_report_writes_the_crash_file(tmp_path, monkeypatch):
    """Konsolsuz başlatmada ekrana hiçbir şey düşmez; dosya tek kanıt."""
    crash = tmp_path / "automesh-hata.txt"
    monkeypatch.setattr(launcher, "CRASH_LOG", str(crash))
    assert launcher.report("bilerek patlatıldı") == 1
    assert "bilerek patlatıldı" in crash.read_text(encoding="utf-8")


# -- kısayol ---------------------------------------------------------------

def test_interpreter_prefers_pythonw(tmp_path, monkeypatch):
    folder = tmp_path / "Python311"
    os.makedirs(str(folder))
    (folder / "python.exe").write_text("", encoding="utf-8")
    (folder / "pythonw.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(folder / "python.exe"))
    assert launcher.interpreter() == str(folder / "pythonw.exe")


def test_interpreter_falls_back_when_pythonw_is_missing(tmp_path, monkeypatch):
    folder = tmp_path / "Python311"
    os.makedirs(str(folder))
    (folder / "python.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", str(folder / "python.exe"))
    assert launcher.interpreter() == str(folder / "python.exe")


def test_url_shortcut_is_plain_text_and_points_at_the_launcher(tmp_path):
    target = tmp_path / "AutoMesh.pyw"
    target.write_text("", encoding="utf-8")
    path = tmp_path / "AutoMesh.url"
    launcher.write_url(str(path), str(target))
    text = path.read_text(encoding="utf-8")
    assert text.startswith("[InternetShortcut]")
    assert "AutoMesh.pyw" in text


def test_shortcut_falls_back_to_url_when_lnk_fails(tmp_path, monkeypatch):
    """COM engellenirse kısayol yine de oluşmalı."""
    (tmp_path / launcher.LAUNCHER_NAME).write_text("", encoding="utf-8")
    desktop = tmp_path / "Masaustu"
    os.makedirs(str(desktop))

    def boom(*_a, **_kw):
        raise OSError("COM kapali")

    monkeypatch.setattr(launcher, "write_lnk", boom)
    monkeypatch.setattr(launcher, "shell_folder", lambda kind: str(desktop))

    results = launcher.create_shortcuts(str(tmp_path), ("desktop",))
    assert results[0][0] is True
    assert os.path.isfile(str(desktop / "AutoMesh.url"))


def test_shortcut_prefers_a_real_lnk(tmp_path, monkeypatch):
    (tmp_path / launcher.LAUNCHER_NAME).write_text("", encoding="utf-8")
    desktop = tmp_path / "Masaustu"
    os.makedirs(str(desktop))
    calls = {}

    def fake_lnk(path, target, arguments, workdir, description=""):
        calls.update(path=path, target=target, arguments=arguments,
                     workdir=workdir)

    monkeypatch.setattr(launcher, "write_lnk", fake_lnk)
    monkeypatch.setattr(launcher, "shell_folder", lambda kind: str(desktop))
    monkeypatch.setattr(launcher, "interpreter", lambda: "C:\\Py\\pythonw.exe")

    results = launcher.create_shortcuts(str(tmp_path), ("desktop",))
    assert results[0][0] is True
    assert calls["path"].endswith("AutoMesh.lnk")
    assert calls["target"] == "C:\\Py\\pythonw.exe"
    assert launcher.LAUNCHER_NAME in calls["arguments"]
    assert calls["workdir"] == str(tmp_path)


def test_shortcut_reports_a_missing_launcher(tmp_path):
    ok, detail = launcher.create_shortcuts(str(tmp_path), ("desktop",))[0]
    assert ok is False
    assert launcher.LAUNCHER_NAME in detail


def test_cli_exposes_the_shortcut_command(monkeypatch, capsys):
    from automesh import cli

    monkeypatch.setattr(launcher, "create_shortcuts",
                        lambda root, kinds: [(True, "C:\\Users\\x\\AutoMesh.lnk")])
    assert cli.main(["kisayol"]) == 0
    assert "AutoMesh.lnk" in capsys.readouterr().out


def test_cli_shortcut_explains_the_manual_way_on_failure(monkeypatch, capsys):
    from automesh import cli

    monkeypatch.setattr(launcher, "create_shortcuts",
                        lambda root, kinds: [(False, "olmadi")])
    assert cli.main(["kisayol"]) == 2
    out = capsys.readouterr().out
    assert "SAĞ tıklayın" in out and "AutoMesh.pyw" in out


# -- yorumlayıcıdan bağımsız yol kaydı -------------------------------------

def test_user_paths_file_is_read_too(tmp_path, monkeypatch):
    """`py` ile bulunan PyFluent, `.pyw`'yi açan Python'da da bulunmalı."""
    os.makedirs(str(tmp_path / "src"))
    user = tmp_path / "yollar.txt"
    user.write_text("# yorum\nD:\\Work\\plm\n", encoding="utf-8")
    monkeypatch.setattr(launcher, "USER_PATHS_FILE", str(user))
    assert launcher.extra_paths(str(tmp_path)) == [str(tmp_path / "src"),
                                                   "D:\\Work\\plm"]


def test_user_and_repo_lists_do_not_duplicate(tmp_path, monkeypatch):
    os.makedirs(str(tmp_path / "src"))
    (tmp_path / launcher.PATHS_FILE).write_text("D:\\Work\\plm\n",
                                                encoding="utf-8")
    user = tmp_path / "yollar.txt"
    user.write_text("D:\\Work\\plm\n", encoding="utf-8")
    monkeypatch.setattr(launcher, "USER_PATHS_FILE", str(user))
    assert launcher.extra_paths(str(tmp_path)).count("D:\\Work\\plm") == 1


def test_remember_path_writes_and_is_idempotent(tmp_path, monkeypatch):
    user = tmp_path / "kayit" / "yollar.txt"
    monkeypatch.setattr(launcher, "USER_PATHS_FILE", str(user))
    folder = tmp_path / "plm"
    os.makedirs(str(folder))

    ok, _ = launcher.remember_path(str(folder))
    assert ok and user.is_file()
    ok, note = launcher.remember_path(str(folder))
    assert ok and "Zaten" in note
    assert user.read_text(encoding="utf-8").count(str(folder)) == 1


def test_doctor_add_path_also_records_it_for_other_interpreters(
        tmp_path, monkeypatch):
    """.pth yalnızca onu yazan Python'da geçerli; kayıt her yerde geçerli."""
    from automesh import doctor

    user = tmp_path / "yollar.txt"
    monkeypatch.setattr(launcher, "USER_PATHS_FILE", str(user))
    monkeypatch.setattr(doctor, "pth_path",
                        lambda: str(tmp_path / "automesh-extra-paths.pth"))
    folder = tmp_path / "plm"
    os.makedirs(str(folder))

    ok, message = doctor.add_path(str(folder))
    assert ok, message
    assert str(folder) in user.read_text(encoding="utf-8")
