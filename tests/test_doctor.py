import os

import pytest

from automesh import doctor


def test_report_covers_every_dependency():
    text = "\n".join(doctor.report())
    for section in ("Python", "AutoMesh", "PyFluent", "Tkinter",
                    "Ek Python yolları", "PYTHONPATH", "ANSYS kurulumları",
                    "SpaceClaim"):
        assert section in text


def test_automesh_itself_is_found():
    text = "\n".join(doctor.report())
    assert "[+] 0.1.0" in text or "automesh" in text


def test_add_path_writes_a_pth_file(tmp_path, monkeypatch):
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    target = tmp_path / "vendor"
    target.mkdir()
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(site_dir))

    ok, message = doctor.add_path(str(target))
    assert ok
    assert str(target) in message

    written = (site_dir / doctor.PTH_NAME).read_text(encoding="utf-8")
    assert str(target) in written
    assert doctor.existing_extra_paths() == [str(target)]


def test_add_path_keeps_existing_entries(tmp_path, monkeypatch):
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(site_dir))

    doctor.add_path(str(first))
    doctor.add_path(str(second))
    entries = doctor.existing_extra_paths()
    assert str(first) in entries and str(second) in entries
    assert len(entries) == 2


def test_add_path_is_idempotent(tmp_path, monkeypatch):
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    target = tmp_path / "vendor"
    target.mkdir()
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(site_dir))

    doctor.add_path(str(target))
    ok, message = doctor.add_path(str(target))
    assert ok
    assert "Güncellendi" in message
    assert len(doctor.existing_extra_paths()) == 1


def test_readding_an_existing_path_still_upgrades_the_file(tmp_path, monkeypatch):
    """Eski düz biçim, yol zaten kayıtlıyken de yenilenmeli.

    Erken dönmek, öncelik düzeltmesinin hiç uygulanmaması demekti.
    """
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    vendor = tmp_path / "plm"
    vendor.mkdir()
    (site_dir / doctor.PTH_NAME).write_text(str(vendor) + "\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(site_dir))

    ok, _ = doctor.add_path(str(vendor))
    assert ok
    content = (site_dir / doctor.PTH_NAME).read_text(encoding="utf-8")
    assert "sys.path.insert(0" in content
    assert doctor.existing_extra_paths() == [str(vendor)]


def test_add_path_rejects_a_missing_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(tmp_path))
    ok, message = doctor.add_path(str(tmp_path / "yok"))
    assert not ok
    assert "bulunamadı" in message


def test_add_path_reports_when_it_cannot_write(tmp_path, monkeypatch):
    target = tmp_path / "vendor"
    target.mkdir()
    # Dosyanın yerine klasör koyarak yazmayı imkânsız kıl
    blocked = tmp_path / "site-packages"
    blocked.mkdir()
    (blocked / doctor.PTH_NAME).mkdir()
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(blocked))

    ok, message = doctor.add_path(str(target))
    assert not ok
    assert "Yazılamadı" in message


def test_existing_paths_ignore_comments(tmp_path, monkeypatch):
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    (site_dir / doctor.PTH_NAME).write_text(
        "# yorum\n\nD:\\Work\\plm\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(site_dir))
    assert doctor.existing_extra_paths() == ["D:\\Work\\plm"]


def test_summary_flags_missing_pyfluent():
    ready, lines = doctor.summary()
    # Bu ortamda PyFluent yok, dolayısıyla hazır olmamalı.
    assert ready is False
    assert any("PyFluent" in line for line in lines)


def test_cli_doctor_runs(capsys):
    from automesh.cli import main

    code = main(["doctor"])
    out = capsys.readouterr().out
    assert "PyFluent" in out
    assert code in (0, 1)


def test_cli_doctor_add_path(tmp_path, monkeypatch, capsys):
    from automesh.cli import main

    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    vendor = tmp_path / "plm"
    vendor.mkdir()
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(site_dir))

    main(["doctor", "--add-path", str(vendor)])
    out = capsys.readouterr().out
    assert "[+]" in out
    assert (site_dir / doctor.PTH_NAME).is_file()


def test_cli_doctor_add_path_failure_returns_error(tmp_path, capsys):
    from automesh.cli import main

    assert main(["doctor", "--add-path", str(tmp_path / "olmayan")]) == 2
    assert "[x]" in capsys.readouterr().out


def test_remove_path_drops_one_entry(tmp_path, monkeypatch):
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(site_dir))

    doctor.add_path(str(first))
    doctor.add_path(str(second))
    ok, message = doctor.remove_path(str(first))
    assert ok and "Çıkarıldı" in message
    assert doctor.existing_extra_paths() == [str(second)]


def test_removing_the_last_entry_deletes_the_file(tmp_path, monkeypatch):
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    only = tmp_path / "only"
    only.mkdir()
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(site_dir))

    doctor.add_path(str(only))
    doctor.remove_path(str(only))
    assert not (site_dir / doctor.PTH_NAME).exists()
    assert doctor.existing_extra_paths() == []


def test_remove_path_reports_unknown_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(tmp_path))
    ok, message = doctor.remove_path(str(tmp_path / "hic-eklenmedi"))
    assert not ok and "Listede yok" in message


def test_inspect_pyfluent_explains_a_missing_package():
    lines = doctor.inspect_pyfluent()
    assert lines
    assert any("ansys" in line for line in lines)


def test_inspect_pyfluent_flags_a_partial_install(tmp_path, monkeypatch):
    """Paket bulunuyor ama alt paketleri eksikse bunu açıkça söylemeli."""
    root = tmp_path / "plm" / "ansys" / "fluent" / "core"
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("", encoding="utf-8")

    class _Module:
        __path__ = [str(root)]

    real_import = doctor.__builtins__["__import__"] if isinstance(
        doctor.__builtins__, dict) else __import__

    def fake_import(name, *args, **kwargs):
        if name in ("ansys", "ansys.fluent", "ansys.fluent.core"):
            return _Module()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    lines = doctor.inspect_pyfluent()
    text = "\n".join(lines)
    assert "Eksik alt paketler" in text
    assert "solver" in text
    assert "pip install ansys-fluent-core" in text
    assert "--remove-path" in text


def test_cli_doctor_remove_path(tmp_path, monkeypatch, capsys):
    from automesh.cli import main

    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    vendor = tmp_path / "plm"
    vendor.mkdir()
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(site_dir))

    main(["doctor", "--add-path", str(vendor)])
    capsys.readouterr()
    main(["doctor", "--remove-path", str(vendor)])
    assert "Çıkarıldı" in capsys.readouterr().out


def test_pth_line_puts_paths_first_like_pythonpath(tmp_path, monkeypatch):
    """Düz yol listesi sys.path'in SONUNA eklenir; bu yetmiyor.

    Aynı paketin yarım bir kopyası daha önce geliyorsa düz liste
    çalışmaz. Üretilen satır yolu PYTHONPATH gibi başa almalı.
    """
    import sys

    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    vendor = tmp_path / "plm"
    vendor.mkdir()
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(site_dir))
    doctor.add_path(str(vendor))

    content = (site_dir / doctor.PTH_NAME).read_text(encoding="utf-8")
    exec_lines = [l for l in content.splitlines() if l.startswith("import ")]
    assert len(exec_lines) == 1

    original = list(sys.path)
    try:
        exec(exec_lines[0], {})          # site modülünün yaptığının aynısı
        assert sys.path[0] == str(vendor)
        exec(exec_lines[0], {})          # iki kez çalışsa da tek kayıt
        assert sys.path.count(str(vendor)) == 1
    finally:
        sys.path[:] = original


def test_pth_skips_folders_that_disappeared(tmp_path, monkeypatch):
    """Klasör sonradan silinirse satır sessizce atlamalı, patlamamalı."""
    import sys

    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    vendor = tmp_path / "gidecek"
    vendor.mkdir()
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(site_dir))
    doctor.add_path(str(vendor))
    vendor.rmdir()

    line = [l for l in (site_dir / doctor.PTH_NAME).read_text(
        encoding="utf-8").splitlines() if l.startswith("import ")][0]
    original = list(sys.path)
    try:
        exec(line, {})
        assert str(vendor) not in sys.path
    finally:
        sys.path[:] = original


def test_old_plain_format_is_still_readable(tmp_path, monkeypatch):
    """Önceki sürümün yazdığı düz listeler kaybolmamalı."""
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    (site_dir / doctor.PTH_NAME).write_text(
        "D:\\Work\\plm\nC:\\baska\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(site_dir))
    assert doctor.existing_extra_paths() == ["D:\\Work\\plm", "C:\\baska"]


def test_rewriting_upgrades_the_old_format(tmp_path, monkeypatch):
    site_dir = tmp_path / "site-packages"
    site_dir.mkdir()
    old = tmp_path / "eski"
    old.mkdir()
    new = tmp_path / "yeni"
    new.mkdir()
    (site_dir / doctor.PTH_NAME).write_text(str(old) + "\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "_site_packages", lambda: str(site_dir))

    doctor.add_path(str(new))
    content = (site_dir / doctor.PTH_NAME).read_text(encoding="utf-8")
    assert "sys.path.insert(0" in content
    assert set(doctor.existing_extra_paths()) == {str(old), str(new)}


def _make_fluent_tree(root, complete):
    """Sahte bir ansys/fluent ağacı kur."""
    fluent = root / "ansys" / "fluent"
    core = fluent / "core"
    core.mkdir(parents=True)
    if complete:
        for sub in ("solver", "meshing", "session"):
            (core / sub).mkdir()
    return str(fluent)


def test_shadowing_names_the_complete_and_broken_copies(tmp_path):
    broken = _make_fluent_tree(tmp_path / "site-packages", complete=False)
    good = _make_fluent_tree(tmp_path / "plm", complete=True)

    text = "\n".join(doctor._diagnose_shadowing([broken, good]))
    assert "eksik kurulum" in text and broken in text
    assert "tam kurulum" in text and good in text
    assert "İLK sıra kazanır" in text
    # Önerilen çözüm hiçbir şey silmemeli - iş bilgisayarında site-packages'e
    # dokunmak çoğu zaman ne mümkün ne de istenir.
    assert "--add-path" in text and str(tmp_path / "plm") in text
    assert "hiçbir şey silinmez" in text
    assert "--remove-path" in text
    assert "force-reinstall" not in text


def test_shadowing_is_quiet_when_there_is_one_good_copy(tmp_path):
    good = _make_fluent_tree(tmp_path / "plm", complete=True)
    text = "\n".join(doctor._diagnose_shadowing([good]))
    assert "tam kurulum" in text
    assert "İLK sıra kazanır" not in text
    assert "--add-path" not in text


def test_shadowing_when_every_copy_is_broken(tmp_path):
    first = _make_fluent_tree(tmp_path / "a", complete=False)
    second = _make_fluent_tree(tmp_path / "b", complete=False)
    text = "\n".join(doctor._diagnose_shadowing([first, second]))
    assert "Hiçbir kopya tam değil" in text
    # Ortak klasöre kurulum önerilir, site-packages'e dokunulmaz
    assert "--target" in text
    assert "force-reinstall" not in text


def test_complete_core_detection(tmp_path):
    good = _make_fluent_tree(tmp_path / "good", complete=True)
    bad = _make_fluent_tree(tmp_path / "bad", complete=False)
    assert doctor._has_complete_core(good) == (True, [])
    ok, missing = doctor._has_complete_core(bad)
    assert not ok and "solver" in missing
    # core klasörü hiç yoksa
    empty = tmp_path / "empty"
    empty.mkdir()
    assert doctor._has_complete_core(str(empty)) == (False, ["core"])
