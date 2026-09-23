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


# --------------------------------------------------------------------------
# basit / gelişmiş sekmeler
# --------------------------------------------------------------------------

def test_both_tabs_build(fake_tk, tmp_path, monkeypatch):
    """İki sekme de kurulmalı; biri patlarsa pencere hiç açılmaz."""
    module, _ = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    assert app.btn_simple_run is not None      # basit sekme
    assert app.btn_analyze is not None         # gelişmiş sekme
    # Her iki sekmenin düğmeleri de meşgulken kilitlenmeli
    assert app.btn_simple_run in app._action_buttons
    assert app.btn_run in app._action_buttons


def test_simple_button_runs_without_face_naming(fake_tk, tmp_path, monkeypatch,
                                                step_file):
    """Basit düğme uçtan uca meshlemeli ve gruplamayı hiç açmamalı."""
    module, messagebox = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    app.var_geometry.set(step_file)
    app.var_output.set(str(tmp_path / "run"))
    app.var_dry_run.set(True)

    app._guard("Mesh oluşturma", lambda: app._start("run", mode="simple"))

    assert messagebox.showerror.call_count == 0
    assert app.run is not None and app.run.mode == "run"
    assert app.run.settings.is_simple
    assert app.run.settings.to_config().local_sizing.enabled is False
    app.run.cancel()
    app.run.join(15)


def test_simple_run_never_opens_a_dialog(fake_tk, tmp_path, monkeypatch,
                                         step_file):
    """Basit modda kademe/boyut ekranı açılmamalı - tek düğme, tek akış."""
    import time

    module, _ = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    opened = []
    monkeypatch.setattr(app, "_show_proposals", lambda: opened.append("kademe"))
    monkeypatch.setattr(app, "_edit_sizing", lambda: opened.append("boyut"))

    app.var_geometry.set(step_file)
    app.var_output.set(str(tmp_path / "run"))
    app.var_dry_run.set(True)
    app._start("run", mode="simple")

    deadline = time.time() + 60
    while app.run.running and time.time() < deadline:
        app._pump()
        time.sleep(0.01)
    app.run.join(15)
    app._pump()

    assert opened == []
    assert app.last_result is not None and app.last_result.success


def test_tab_change_switches_the_mode(fake_tk, tmp_path, monkeypatch):
    module, _ = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    app.notebook.index = lambda _what: 1
    app._on_tab_changed()
    assert app._mode == "advanced"
    assert app._collect().mode == "advanced"

    app.notebook.index = lambda _what: 0
    app._on_tab_changed()
    assert app._mode == "simple"
    assert app._collect().is_simple


def test_simple_analyze_opens_the_level_screen(fake_tk, tmp_path, monkeypatch,
                                               step_file):
    """Basit sekmede analiz de ölçüm + kademe işini başlatmalı."""
    module, messagebox = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    app.var_geometry.set(step_file)
    app.var_output.set(str(tmp_path / "run"))

    app._guard("Geometri analizi", lambda: app._start("propose", mode="simple"))

    assert messagebox.showerror.call_count == 0
    assert app.run is not None and app.run.mode == "propose"
    assert app._mode == "simple"
    app.run.cancel()
    app.run.join(10)


def _fake_choice():
    """ProposalDialog'un döndürdüğü nesnenin arayüze bakan yüzü."""
    plan = types.SimpleNamespace(min_size=0.001, max_size=0.01)
    return types.SimpleNamespace(
        label="Orta", plan=plan, cells=1_000_000, warnings=[],
        size_text=lambda: "1 mm / 10 mm", cells_text=lambda: "1.000.000")


def test_simple_mode_skips_the_face_size_screen(fake_tk, tmp_path, monkeypatch):
    """Kademe seçilince basit modda yüzey boyutu ekranı açılmamalı."""
    module, _ = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    from automesh.guiapp import proposals_dialog

    monkeypatch.setattr(
        proposals_dialog, "ProposalDialog",
        lambda *a, **kw: types.SimpleNamespace(show=lambda: _fake_choice()))
    opened = []
    monkeypatch.setattr(app, "_edit_sizing", lambda: opened.append("boyut"))

    app._mode = "simple"
    app.var_local_sizing.set(True)
    metrics = types.SimpleNamespace(face_groups=[object()])
    app._choose_proposal(metrics, [_fake_choice()])

    assert opened == []
    assert app.settings.chosen_label == "Orta"
    assert "2. Mesh oluştur" in _logged_text(app)


def test_advanced_mode_still_opens_the_face_size_screen(fake_tk, tmp_path,
                                                        monkeypatch):
    module, _ = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    from automesh.guiapp import proposals_dialog

    monkeypatch.setattr(
        proposals_dialog, "ProposalDialog",
        lambda *a, **kw: types.SimpleNamespace(show=lambda: _fake_choice()))
    opened = []
    monkeypatch.setattr(app, "_edit_sizing", lambda: opened.append("boyut"))

    app._mode = "advanced"
    app.var_local_sizing.set(True)
    app._choose_proposal(types.SimpleNamespace(face_groups=[object()]),
                         [_fake_choice()])

    assert opened == ["boyut"]


def test_level_screen_enables_the_simple_reopen_button(fake_tk, tmp_path,
                                                       monkeypatch):
    """Seçim ekranı açıldıktan sonra basit sekmeden yeniden açılabilmeli."""
    module, _ = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    monkeypatch.setattr(app, "_choose_proposal", lambda *a: None)
    app.run = types.SimpleNamespace(
        proposals=[_fake_choice()],
        metrics=types.SimpleNamespace(face_groups=[]))

    app._show_proposals()

    app.btn_simple_reopen.configure.assert_called_with(state="normal")


def test_geometry_argument_prefills_the_field(fake_tk, tmp_path, monkeypatch):
    """Sürükle-bırak: dosya yolu doğrudan geometri alanına yazılmalı."""
    module, _ = fake_tk
    from automesh.guiapp import state

    monkeypatch.setattr(state, "SETTINGS_PATH", str(tmp_path / "gui.json"))
    monkeypatch.setattr(module.GuiSettings, "load",
                        classmethod(lambda cls, path=None: cls()))
    app = module.AutoMeshApp(mock.MagicMock(), geometry="C:/cad/parca.scdoc")
    assert app.var_geometry.get() == "C:/cad/parca.scdoc"
    assert "parca.scdoc" in app.var_status.get()


def test_main_passes_the_geometry_through(fake_tk, tmp_path, monkeypatch):
    """main(geometry) -> AutoMeshApp(geometry): zincir kopmamalı."""
    module, _ = fake_tk
    seen = {}
    monkeypatch.setattr(module, "AutoMeshApp",
                        lambda root, geometry=None: seen.setdefault("g", geometry))
    module.main("C:/cad/parca.scdoc")
    assert seen["g"] == "C:/cad/parca.scdoc"


def test_real_run_without_pyfluent_says_why(fake_tk, tmp_path, monkeypatch,
                                            step_file):
    """PyFluent yoksa düğme sessiz kalmamalı: sebep ve çözüm görünmeli."""
    module, messagebox = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_pyfluent_available", lambda: False)

    app.var_geometry.set(step_file)
    app.var_output.set(str(tmp_path / "run"))
    app.var_dry_run.set(False)

    app._guard("Mesh oluşturma", lambda: app._start("run", mode="simple"))

    assert app.run is None, "PyFluent yokken iş başlatılmamalı"
    assert messagebox.showerror.call_count == 1
    logged = _logged_text(app)
    assert "PyFluent" in logged
    assert "automesh doctor" in logged
    assert "Prova" in logged


def test_real_run_starts_when_pyfluent_is_there(fake_tk, tmp_path, monkeypatch,
                                                step_file):
    module, messagebox = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_pyfluent_available", lambda: True)
    started = {}
    monkeypatch.setattr(module, "BackgroundRun",
                        lambda settings, kind: started.setdefault(
                            "run", types.SimpleNamespace(
                                running=False, mode=kind, start=lambda: None,
                                drain=lambda: [], proposals=None,
                                metrics=None, error="")))

    app.var_geometry.set(step_file)
    app.var_output.set(str(tmp_path / "run"))
    app.var_dry_run.set(False)
    app._start("run", mode="simple")

    assert messagebox.showerror.call_count == 0
    assert started["run"].mode == "run"


def test_dry_run_never_needs_pyfluent(fake_tk, tmp_path, monkeypatch, step_file):
    """Prova modu Fluent'e hiç dokunmaz; kontrol onu engellememeli."""
    module, messagebox = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_pyfluent_available", lambda: False)

    app.var_geometry.set(step_file)
    app.var_output.set(str(tmp_path / "run"))
    app.var_dry_run.set(True)
    app._start("run", mode="simple")

    assert messagebox.showerror.call_count == 0
    assert app.run is not None
    app.run.cancel()
    app.run.join(15)


def test_analysis_never_needs_pyfluent(fake_tk, tmp_path, monkeypatch, step_file):
    """Analiz Fluent açmaz: PyFluent yokken de çalışmalı."""
    module, messagebox = fake_tk
    app = _app(module, tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_pyfluent_available", lambda: False)

    app.var_geometry.set(step_file)
    app.var_output.set(str(tmp_path / "run"))
    app._start("propose", mode="simple")

    assert messagebox.showerror.call_count == 0
    assert app.run is not None and app.run.mode == "propose"
    app.run.cancel()
    app.run.join(10)
