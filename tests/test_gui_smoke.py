"""Pencereyi sahte bir Tkinter ile kurup çalıştıran duman testi.

Bu ortamda gerçek Tkinter yok ve olsa da ekran yok. Ama arayüzdeki
isim/öznitelik hatalarının çoğu widget'lara hiç dokunmadan yakalanabilir:
Tkinter geri çağrımlarındaki istisnalar sessizce yutulduğu için (pencere
"hiçbir şey yapmıyor" gibi görünür) bu testin değeri yüksek.
"""

import sys
import types
from unittest import mock

import pytest


class _Var:
    def __init__(self, master=None, value=None, **_kw):
        self._value = value
        self._callbacks = []

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        for callback in self._callbacks:
            callback()

    def trace_add(self, _mode, callback):
        self._callbacks.append(lambda *_a: callback())


class _StringVar(_Var):
    def __init__(self, master=None, value="", **kw):
        super().__init__(master, "" if value is None else value, **kw)


class _BooleanVar(_Var):
    def __init__(self, master=None, value=False, **kw):
        super().__init__(master, bool(value), **kw)


class _IntVar(_Var):
    def __init__(self, master=None, value=0, **kw):
        super().__init__(master, int(value or 0), **kw)


@pytest.fixture
def fake_tk(monkeypatch):
    """Sahte tkinter modüllerini kur ve app modülünü yeniden yükle."""
    tk = types.ModuleType("tkinter")
    tk.StringVar = _StringVar
    tk.BooleanVar = _BooleanVar
    tk.IntVar = _IntVar
    tk.TclError = type("TclError", (Exception,), {})
    for name in ("Tk", "Toplevel", "Frame", "Text", "Canvas", "Misc"):
        setattr(tk, name, mock.MagicMock())

    ttk = types.ModuleType("tkinter.ttk")
    for name in ("Frame", "LabelFrame", "Label", "Button", "Entry", "Combobox",
                 "Checkbutton", "Spinbox", "Scrollbar", "Progressbar",
                 "Treeview", "Notebook"):
        setattr(ttk, name, mock.MagicMock())
    tk.ttk = ttk

    filedialog = types.ModuleType("tkinter.filedialog")
    filedialog.askopenfilename = mock.MagicMock(return_value="")
    filedialog.askdirectory = mock.MagicMock(return_value="")

    messagebox = types.ModuleType("tkinter.messagebox")
    messagebox.showerror = mock.MagicMock()
    messagebox.showinfo = mock.MagicMock()
    messagebox.showwarning = mock.MagicMock()
    messagebox.askyesno = mock.MagicMock(return_value=True)

    monkeypatch.setitem(sys.modules, "tkinter", tk)
    monkeypatch.setitem(sys.modules, "tkinter.ttk", ttk)
    monkeypatch.setitem(sys.modules, "tkinter.filedialog", filedialog)
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", messagebox)
    for name in list(sys.modules):
        if name.startswith("automesh.guiapp.app"):
            monkeypatch.delitem(sys.modules, name, raising=False)

    import importlib

    module = importlib.import_module("automesh.guiapp.app")
    module = importlib.reload(module)
    return module, messagebox


def _logged_text(app) -> str:
    """Günlük penceresine yazılan metinler (Text.insert ikinci argüman)."""
    return " ".join(str(call.args[1]) for call in app.text.insert.call_args_list
                    if len(call.args) > 1)


def _app(module, tmp_path, monkeypatch):
    """Ayarları test klasörüne yönlendirip pencereyi kur."""
    from automesh.guiapp import state

    monkeypatch.setattr(state, "SETTINGS_PATH", str(tmp_path / "gui.json"))
    monkeypatch.setattr(module.GuiSettings, "load",
                        classmethod(lambda cls, path=None: cls()))
    return module.AutoMeshApp(mock.MagicMock())


# --------------------------------------------------------------------------

def test_window_builds_without_errors(fake_tk, tmp_path, monkeypatch):
    """Pencere kurulumu istisna atmamalı - atarsa pencere hiç açılmaz."""
    module, _ = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    assert app.settings is not None
    assert app.run is None


def test_collect_reads_every_widget(fake_tk, tmp_path, monkeypatch):
    """_collect tüm alanları okur; eksik bir alan TypeError verirdi."""
    module, _ = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    app.var_geometry.set("C:/cad/part.scdoc")
    app.var_cores.set(12)
    settings = app._collect()
    assert settings.geometry_path == "C:/cad/part.scdoc"
    assert settings.cores == 12
    assert settings.display_unit == "mm"


def test_analyze_button_starts_a_background_run(fake_tk, tmp_path, monkeypatch,
                                                step_file):
    """'1. Geometriyi analiz et' ölçüm + öneri işini başlatmalı."""
    module, messagebox = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    app.var_geometry.set(step_file)
    app.var_output.set(str(tmp_path / "run"))

    app._guard("Geometri analizi", lambda: app._start("propose"))

    assert messagebox.showerror.call_count == 0, (
        "analiz başlarken hata kutusu çıktı: "
        + str(messagebox.showerror.call_args))
    assert app.run is not None
    assert app.run.mode == "propose"
    app.run.cancel()
    app.run.join(10)


def test_pump_survives_a_full_run(fake_tk, tmp_path, monkeypatch, step_file):
    """Analiz bitene kadar kuyruk pompası çalışmalı ve kırılmamalı."""
    import time

    module, messagebox = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    app.var_geometry.set(step_file)
    app.var_output.set(str(tmp_path / "run"))
    app._start("propose")

    deadline = time.time() + 30
    while app.run.running and time.time() < deadline:
        app._pump()
        time.sleep(0.01)
    app.run.join(10)
    app._pump()

    assert messagebox.showerror.call_count == 0
    logged = _logged_text(app)
    assert "Geometri analizi" in logged or "Sınır kutusu" in logged
    # Öneriler hazır olmalı: "mesh boyut ekranı" buradan açılıyor
    assert app.run.proposals


def test_guard_surfaces_errors_instead_of_swallowing_them(fake_tk, tmp_path,
                                                          monkeypatch):
    """Bir eylem patlarsa kullanıcı görmeli, pencere sessiz kalmamalı."""
    module, messagebox = fake_tk
    app = _app(module, tmp_path, monkeypatch)

    def boom():
        raise RuntimeError("bilerek patlatıldı")

    app._guard("Deneme", boom)
    assert messagebox.showerror.call_count == 1
    assert "bilerek patlatıldı" in _logged_text(app)


def test_run_without_a_geometry_is_rejected_clearly(fake_tk, tmp_path,
                                                    monkeypatch):
    module, messagebox = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    app._start("propose")
    assert messagebox.showerror.call_count == 1
    assert app.run is None


def test_every_button_command_is_callable(fake_tk, tmp_path, monkeypatch):
    """Düğmelerin bağlandığı metodlar var mı (yanlış ad = sessiz ölüm)."""
    module, _ = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    for name in ("_start", "_stop", "_open_report", "_open_folder",
                 "_copy_command", "_edit_sizing", "_open_in_spaceclaim",
                 "_clear_choice", "_pick_geometry", "_pick_output",
                 "_pick_config", "_remember_prepared_file", "_guard",
                 "_reopen_proposals", "_choose_proposal", "_show_proposals"):
        assert callable(getattr(app, name)), name
