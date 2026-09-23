"""Çift tıkla çalışan başlatıcıların denetimi.

Batch dosyaları bu ortamda koşturulamaz, ama sessiz ölüme yol açan
hataların çoğu metinden görülür: eksik bir etiket ("cannot find the batch
label" ile pencere kapanır), eksik ``pause`` (hata okunamadan kaybolur),
LF satır sonu (``goto`` bloklarını bozar) ya da ASCII dışı karakter
(konsol kod sayfasında bozuk çıkar).
"""

import io
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(ROOT, "AutoMesh.bat")
SHORTCUT = os.path.join(ROOT, "Kisayol-Olustur.bat")


def _raw(path: str) -> bytes:
    with io.open(path, "rb") as handle:
        return handle.read()


def _text(path: str) -> str:
    return _raw(path).decode("ascii")


@pytest.fixture(params=[LAUNCHER, SHORTCUT])
def batch_file(request):
    return request.param


# -- her iki dosya için ortak --------------------------------------------

def test_batch_files_exist(batch_file):
    assert os.path.isfile(batch_file), batch_file


def test_batch_files_are_pure_ascii(batch_file):
    """Türkçe karakter konsol kod sayfasında bozulur - mesajlar ASCII."""
    _raw(batch_file).decode("ascii")      # UnicodeDecodeError = hata


def test_batch_files_use_windows_line_endings(batch_file):
    """cmd.exe LF'li dosyalarda goto/label bloklarında şaşırır."""
    raw = _raw(batch_file)
    assert b"\r\n" in raw
    assert raw.replace(b"\r\n", b"") .count(b"\n") == 0, "yalın LF satır var"


def test_every_goto_has_a_label(batch_file):
    """Eksik etiket = pencere anında kapanır, kullanıcı sebebini göremez."""
    text = _text(batch_file)
    labels = {m.group(1).lower()
              for m in re.finditer(r"(?m)^:([A-Za-z_][\w]*)", text)}
    labels.add("eof")                     # cmd'nin yerleşik etiketi
    targets = {m.group(1).lower()
               for m in re.finditer(r"goto\s+:?([A-Za-z_][\w]*)", text)}
    assert targets <= labels, "tanımsız etiket: {0}".format(targets - labels)


def _blocks(text: str) -> dict:
    """Etiketten bir sonraki etikete kadar olan gövdeleri döndür."""
    marks = [(m.start(), m.group(1).lower())
             for m in re.finditer(r"(?m)^:([A-Za-z_][\w]*)", text)]
    out = {}
    for index, (start, label) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(text)
        out[label] = text[start:end]
    return out


def test_error_paths_pause_so_the_message_can_be_read(batch_file):
    """Hata dalları pause ile bitmeli; yoksa pencere kapanıp gider."""
    blocks = _blocks(_text(batch_file))
    interesting = [name for name in blocks
                   if "error" in name or name in ("no_python", "manual")]
    assert interesting, "hata dalı bulunamadı"
    for name in interesting:
        assert "pause" in blocks[name].lower(), \
            "{0} bloğunda pause yok".format(name)


# -- başlatıcıya özel ------------------------------------------------------

def test_launcher_starts_the_gui_module():
    assert "-m automesh.guiapp" in _text(LAUNCHER)


def test_launcher_does_not_prefer_a_blocked_venv():
    """Grup ilkesi E:'deki .venv\\Scripts\\python.exe'yi çalıştırmıyor."""
    assert ".venv" not in _text(LAUNCHER)


def test_launcher_verifies_the_interpreter_really_runs():
    """Microsoft Store'un 'python' kısayolu PATH'te var ama Python değil."""
    text = _text(LAUNCHER)
    assert "sys.exit(7)" in text
    assert "if not errorlevel 7" in text and "if errorlevel 8" in text


def test_launcher_checks_imports_before_the_windowless_start():
    """pyw ile başlatılan hata hiçbir yerde görünmez: önce sınanmalı."""
    text = _text(LAUNCHER)
    preflight = text.index("call :preflight")
    windowless = text.index('start "" "%PYWEXE%"')
    assert preflight < windowless
    assert "import automesh.guiapp.app" in text


def test_launcher_puts_the_repo_and_extra_paths_on_pythonpath():
    """PyFluent başka klasörde kurulu; kullanıcı onu dosyayla ekleyebilmeli."""
    text = _text(LAUNCHER)
    assert "%~dp0src" in text
    assert "automesh-yollar.txt" in text
    assert "PYTHONPATH" in text


def test_launcher_passes_other_arguments_to_the_cli():
    assert "-m automesh %*" in _text(LAUNCHER)


def test_example_paths_file_is_shipped():
    """Kullanıcının kopyalayacağı örnek dosya depoda olmalı."""
    path = os.path.join(ROOT, "automesh-yollar.ornek.txt")
    assert os.path.isfile(path)
    assert "#" in io.open(path, encoding="ascii").read()


def test_user_paths_file_is_ignored_by_git():
    ignore = io.open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read()
    assert "automesh-yollar.txt" in ignore


def test_gitattributes_forces_crlf_for_batch_files():
    """Aksi halde Linux'ta işlenen dosya Windows'a LF olarak iner."""
    text = io.open(os.path.join(ROOT, ".gitattributes"), encoding="utf-8").read()
    assert "*.bat text eol=crlf" in text


# -- kısayol oluşturucuya özel --------------------------------------------

def test_shortcut_targets_the_launcher():
    assert "AutoMesh.bat" in _text(SHORTCUT)


def test_shortcut_covers_desktop_and_start_menu():
    """Masaüstü OneDrive'a taşınmış olabilir; klasör Windows'tan sorulmalı."""
    text = _text(SHORTCUT)
    assert "GetFolderPath" in text
    assert "'Desktop','Programs'" in text


def test_shortcut_explains_the_manual_way_when_powershell_is_blocked():
    text = _text(SHORTCUT)
    assert ":manual" in text
    assert "Kisayol olustur" in text


def test_launcher_forwards_a_dropped_geometry_to_the_gui():
    """Kısayolun üzerine bırakılan dosya arayüzde açılmalı."""
    text = _text(LAUNCHER)
    assert 'if exist "%ACTION%" set "DROPPED=%ACTION%"' in text
    assert '-m automesh.guiapp "!DROPPED!"' in text
