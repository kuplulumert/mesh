"""AutoMesh masaüstü arayüzü (Tkinter).

Tkinter Python ile birlikte gelir, ek kurulum istemez ve Windows'un kendi
dosya seçme penceresini kullanır - yani Fluent'in kurulu olduğu makinede
ekstra hiçbir şey gerektirmeden çalışır.

Arayüz bilerek ince tutuldu: doğrulama, konfigürasyona çevirme ve işin
yürütülmesi :mod:`state` ve :mod:`runner` modüllerinde, Tkinter'dan bağımsız
biçimde duruyor.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import traceback
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import __version__
from ..units import format_length
from .runner import DONE, ERROR, LOG, BackgroundRun
from .state import (
    DISPLAY_UNITS,
    FILE_TYPES,
    FILLS,
    SCENARIOS,
    WORKFLOWS,
    GuiSettings,
)

PAD = 8


class AutoMeshApp:
    def __init__(self, root: tk.Tk, geometry: Optional[str] = None) -> None:
        self.root = root
        self.settings = GuiSettings.load()
        self.run: Optional[BackgroundRun] = None
        self.last_result = None
        self._mode = self.settings.mode if self.settings.mode in ("simple",
                                                                  "advanced") \
            else "simple"
        self._action_buttons: list = []

        root.title("AutoMesh {0} - Otonom Fluent Meshing".format(__version__))
        root.geometry("1040x760")
        root.minsize(900, 620)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_variables()
        self._build_layout()
        self._on_dry_run_toggled()
        if geometry:
            self.set_geometry(geometry)
        self._log_environment()
        self._pump()

    def _log_environment(self) -> None:
        """Hangi Python ve PyFluent kullanılıyor - açılışta bir kez.

        Arayüz komut satırından ve kısayoldan farklı yorumlayıcılarla
        açılabiliyor; PyFluent birinde bulunup diğerinde bulunmayınca
        "bazen çalışıyor" gibi görünüyordu. Bu satır farkı görünür kılar.
        """
        self._append("Python : {0}".format(sys.executable or "?"), "INFO")
        if _pyfluent_available():
            self._append("PyFluent: bulundu", "OK")
        else:
            self._append("PyFluent: BULUNAMADI - gerçek mesh çalışmaz "
                         "(prova modu çalışır).", "WARNING")
            self._append("  Çözüm:  py -m automesh doctor --add-path "
                         "<PyFluent klasörü>", "INFO")
        self._append("", "INFO")

    def set_geometry(self, path: str) -> None:
        """Dışarıdan gelen geometriyi alana yaz (kısayola sürükle-bırak)."""
        self.var_geometry.set(path)
        self.var_status.set("Geometri hazır: {0}".format(os.path.basename(path)))

    # ------------------------------------------------------------------
    # değişkenler
    # ------------------------------------------------------------------
    def _build_variables(self) -> None:
        s = self.settings
        self.var_geometry = tk.StringVar(value=s.geometry_path)
        self.var_output = tk.StringVar(value=s.output_dir)
        self.var_config = tk.StringVar(value=s.config_file)
        self.var_cores = tk.IntVar(value=s.cores)
        self.var_workflow = tk.StringVar(value=s.workflow)
        self.var_fill = tk.StringVar(value=s.volume_fill)
        self.var_max_cells = tk.IntVar(value=s.max_cells)
        self.var_attempts = tk.IntVar(value=s.attempts)
        self.var_unit = tk.StringVar(value=s.length_unit)
        self.var_display_unit = tk.StringVar(value=s.display_unit or "mm")
        self.var_ansys = tk.StringVar(value=s.ansys_version)
        self.var_bl = tk.BooleanVar(value=s.boundary_layers)
        self.var_fluent_gui = tk.BooleanVar(value=s.show_fluent_gui)
        self.var_keep_open = tk.BooleanVar(value=s.keep_fluent_open)
        self.var_dry_run = tk.BooleanVar(value=s.dry_run)
        self.var_scenario = tk.StringVar(value=s.scenario)
        self.var_advisor = tk.BooleanVar(value=s.use_advisor)
        self.var_yplus = tk.StringVar(value=s.y_plus)
        self.var_velocity = tk.StringVar(value=s.velocity)
        self.var_density = tk.StringVar(value=s.density)
        self.var_viscosity = tk.StringVar(value=s.viscosity)
        self.var_length = tk.StringVar(value=s.characteristic_length)
        self.var_status = tk.StringVar(value="Hazır. Bir geometri dosyası seçin.")
        self.var_choice = tk.StringVar(value=s.chosen_summary())
        self.var_sizing = tk.StringVar(value=s.sizing_summary())
        self.var_local_sizing = tk.BooleanVar(value=s.local_sizing_enabled)
        self.var_open_cad = tk.BooleanVar(value=s.open_in_spaceclaim)
        # temizlik sekmesi
        self.var_clean_fillet = tk.StringVar(value=s.cleanup_fillet_mm)
        self.var_clean_hole = tk.StringVar(value=s.cleanup_hole_mm)
        self.var_clean_bump = tk.StringVar(value=s.cleanup_protrusion_mm)
        self.var_clean_fillets = tk.BooleanVar(value=s.cleanup_fillets)
        self.var_clean_screws = tk.BooleanVar(value=s.cleanup_screws)
        self.var_clean_bumps = tk.BooleanVar(value=s.cleanup_protrusions)
        self.var_clean_open = tk.BooleanVar(value=s.cleanup_open_spaceclaim)
        self.var_clean_summary = tk.StringVar(
            value=("Son işaretli kopya: {0}".format(s.last_cleaned_path)
                   if s.last_cleaned_path else
                   "Henüz tarama yapılmadı."))
        self.var_dry_run.trace_add("write", lambda *_: self._on_dry_run_toggled())

    # ------------------------------------------------------------------
    # yerleşim
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=PAD)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        # İki çalışma biçimi ayrı sekmelerde: basit yol her zaman elinizin
        # altında dursun, gelişmiş akışın sorunları onu bloklamasın.
        self.notebook = ttk.Notebook(outer)
        self.notebook.grid(row=0, column=0, sticky="ew")

        simple = ttk.Frame(self.notebook, padding=PAD)
        advanced = ttk.Frame(self.notebook, padding=PAD)
        cleaning = ttk.Frame(self.notebook, padding=PAD)
        self.notebook.add(simple, text="   Basit   ")
        self.notebook.add(advanced, text="   Gelişmiş   ")
        self.notebook.add(cleaning, text="   Temizlik   ")
        self._build_simple_tab(simple)
        self._build_advanced_tab(advanced)
        self._build_cleanup_tab(cleaning)

        try:
            self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
            if self._mode == "advanced":
                self.notebook.select(1)
        except Exception:      # pragma: no cover - widget yokluğuna dayanıklı
            pass

        self._build_log(outer, row=1)
        self._build_status(outer, row=2)

    # -- basit sekme -----------------------------------------------------
    def _build_simple_tab(self, parent: ttk.Frame) -> None:
        """Yüzey isimlendirmesi olmayan, tek düğmelik yol."""
        parent.columnconfigure(0, weight=1)

        ttk.Label(
            parent,
            text=("1) Geometriyi seçip analiz edin: ölçülen uzunluklar ve "
                  "seçilebilir mesh kademeleri bir ekranda çıkar. "
                  "2) Kademeyi seçip 'Mesh oluştur'a basın. SpaceClaim "
                  "dokümanına dokunulmaz, yüzey grubu oluşturulmaz."),
            foreground="#555", wraplength=900, justify="left").grid(
            row=0, column=0, sticky="w", pady=(0, PAD))

        files = ttk.LabelFrame(parent, text="Dosyalar", padding=PAD)
        files.grid(row=1, column=0, sticky="ew")
        files.columnconfigure(1, weight=1)

        ttk.Label(files, text="Geometri").grid(row=0, column=0, sticky="w")
        ttk.Entry(files, textvariable=self.var_geometry).grid(
            row=0, column=1, sticky="ew", padx=PAD)
        ttk.Button(files, text="Seç...", command=self._pick_geometry).grid(
            row=0, column=2)

        ttk.Label(files, text="Çıktı klasörü").grid(row=1, column=0, sticky="w",
                                                    pady=(6, 0))
        ttk.Entry(files, textvariable=self.var_output).grid(
            row=1, column=1, sticky="ew", padx=PAD, pady=(6, 0))
        ttk.Button(files, text="Seç...", command=self._pick_output).grid(
            row=1, column=2, pady=(6, 0))

        options = ttk.LabelFrame(parent, text="Ayarlar", padding=PAD)
        options.grid(row=2, column=0, sticky="ew", pady=(PAD, 0))

        ttk.Label(options, text="Fluent çekirdek").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(options, from_=1, to=256, textvariable=self.var_cores,
                    width=8).grid(row=0, column=1, sticky="w", padx=(4, PAD * 2))

        ttk.Label(options, text="Hücre sınırı").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(options, from_=10000, to=500_000_000, increment=1_000_000,
                    textvariable=self.var_max_cells, width=12).grid(
            row=0, column=3, sticky="w", padx=(4, PAD * 2))

        ttk.Checkbutton(options, text="Fluent penceresini göster",
                        variable=self.var_fluent_gui).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(options, text="Prova (ANSYS açılmaz)",
                        variable=self.var_dry_run).grid(
            row=1, column=2, columnspan=2, sticky="w", pady=(6, 0))

        bar = ttk.Frame(parent)
        bar.grid(row=3, column=0, sticky="ew", pady=(PAD, 0))

        self.btn_simple_analyze = ttk.Button(
            bar, text="1. Geometriyi analiz et ve kademe seç",
            command=lambda: self._guard("Geometri analizi",
                                        lambda: self._start("propose",
                                                            mode="simple")))
        self.btn_simple_analyze.pack(side="left")

        self.btn_simple_run = ttk.Button(
            bar, text="2. Mesh oluştur",
            command=lambda: self._guard("Mesh oluşturma",
                                        lambda: self._start("run",
                                                            mode="simple")))
        self.btn_simple_run.pack(side="left", padx=(PAD, 0))

        self._action_buttons.extend([self.btn_simple_analyze, self.btn_simple_run])

        # Seçilen kademe basit sekmede de görünsün; gelişmiş sekmedekiyle
        # aynı değişkeni paylaşır, yani iki sekme aynı seçimi gösterir.
        choice = ttk.Frame(parent)
        choice.grid(row=4, column=0, sticky="ew", pady=(PAD, 0))
        choice.columnconfigure(0, weight=1)
        ttk.Label(choice, textvariable=self.var_choice,
                  foreground="#1b5e20").grid(row=0, column=0, sticky="w")
        self.btn_simple_reopen = ttk.Button(
            choice, text="Kademeyi yeniden seç",
            command=lambda: self._guard("Kademe seçimi",
                                        self._reopen_proposals),
            state="disabled")
        self.btn_simple_reopen.grid(row=0, column=1, sticky="e", padx=(PAD, 0))
        ttk.Button(choice, text="Seçimi temizle",
                   command=self._clear_choice).grid(row=0, column=2, sticky="e")

    # -- temizlik sekmesi ------------------------------------------------
    def _build_cleanup_tab(self, parent: ttk.Frame) -> None:
        """Küçük detayları bul, SpaceClaim'de grup olarak işaretle."""
        parent.columnconfigure(0, weight=1)

        ttk.Label(
            parent,
            text=("Hazır akış hacmini tarar; küçük fileto/round'ları, vida "
                  "noktalarını ve akışa etkisi olmayan küçük çıkıntıları bulur. "
                  "Hiçbir şey SİLMEZ: her bulgu SpaceClaim'de bir temizle_* "
                  "grubu olur. Groups panelinde gruba tıklayıp Delete'e "
                  "basarak siz silersiniz."),
            foreground="#555", wraplength=900, justify="left").grid(
            row=0, column=0, sticky="w", pady=(0, PAD))

        files = ttk.LabelFrame(parent, text="Geometri", padding=PAD)
        files.grid(row=1, column=0, sticky="ew")
        files.columnconfigure(1, weight=1)
        ttk.Label(files, text="Akış hacmi").grid(row=0, column=0, sticky="w")
        ttk.Entry(files, textvariable=self.var_geometry).grid(
            row=0, column=1, sticky="ew", padx=PAD)
        ttk.Button(files, text="Seç...", command=self._pick_geometry).grid(
            row=0, column=2)

        search = ttk.LabelFrame(parent, text="Ne aransın (boş = otomatik)",
                                padding=PAD)
        search.grid(row=2, column=0, sticky="ew", pady=(PAD, 0))
        rows = (
            (self.var_clean_fillets, "Küçük fileto / round", "yarıçap ≤",
             self.var_clean_fillet, "otomatik: 2 mm (küçük parçada daha az)"),
            (self.var_clean_screws, "Vida noktası / küçük delik", "çap ≤",
             self.var_clean_hole, "otomatik: 12 mm - pah ve dip fileto dahil"),
            (self.var_clean_bumps, "Küçük çıkıntı / cep", "boyut ≤",
             self.var_clean_bump, "otomatik: 10 mm - kaburga, tırnak, yazı"),
        )
        for row, (flag, title, measure, value, hint) in enumerate(rows):
            ttk.Checkbutton(search, text=title, variable=flag).grid(
                row=row, column=0, sticky="w", pady=2)
            ttk.Label(search, text=measure).grid(row=row, column=1, sticky="e",
                                                 padx=(PAD * 2, 4))
            ttk.Entry(search, textvariable=value, width=8).grid(
                row=row, column=2, sticky="w")
            ttk.Label(search, text="mm").grid(row=row, column=3, sticky="w",
                                              padx=(4, PAD * 2))
            ttk.Label(search, text=hint, foreground="#777").grid(
                row=row, column=4, sticky="w")
        ttk.Checkbutton(search, text="Bitince işaretli kopyayı SpaceClaim'de aç",
                        variable=self.var_clean_open).grid(
            row=len(rows), column=0, columnspan=5, sticky="w", pady=(6, 0))

        bar = ttk.Frame(parent)
        bar.grid(row=3, column=0, sticky="ew", pady=(PAD, 0))
        self.btn_inventory = ttk.Button(
            bar, text="0. Envanter çıkar (her şeyi sınıflandır)",
            command=lambda: self._guard("Geometri envanteri",
                                        lambda: self._start("inventory")))
        self.btn_inventory.pack(side="left", padx=(0, PAD))
        self._action_buttons.append(self.btn_inventory)
        self.btn_clean_scan = ttk.Button(
            bar, text="1. Tara ve SpaceClaim'de işaretle",
            command=lambda: self._guard("Temizlik taraması",
                                        lambda: self._start("cleanup")))
        self.btn_clean_scan.pack(side="left")
        self.btn_clean_open = ttk.Button(
            bar, text="2. SpaceClaim'de aç",
            command=lambda: self._guard("SpaceClaim'de açma", self._open_cleaned),
            state="normal" if self.settings.last_cleaned_path else "disabled")
        self.btn_clean_open.pack(side="left", padx=(PAD, 0))
        self.btn_clean_use = ttk.Button(
            bar, text="3. Temizlenmiş dosyayla mesh'e geç",
            command=lambda: self._guard("Temizlenmiş dosya", self._use_cleaned),
            state="normal" if self.settings.last_cleaned_path else "disabled")
        self.btn_clean_use.pack(side="left", padx=(PAD, 0))
        self._action_buttons.append(self.btn_clean_scan)

        ttk.Label(parent, textvariable=self.var_clean_summary,
                  foreground="#1b5e20", wraplength=900, justify="left").grid(
            row=4, column=0, sticky="w", pady=(PAD, 0))

    def _cleanup_done(self) -> None:
        """Tarama bitti: özeti göster, düğmeleri aç."""
        report = getattr(self.run, "cleanup_report", None) if self.run else None
        if report is None:
            return
        inventory = getattr(self.run, "mode", "") == "inventory"
        names = (("fileto", "fileto"), ("vida", "vida noktası"),
                 ("cikinti", "çıkıntı"))
        if inventory:
            names = (("fileto", "fileto"), ("vida", "tam silindir"),
                     ("cikinti", "çıkıntı"), ("yuzey", "yüzey"))
        counts = ", ".join("{0} {1}".format(report.summary.get(key, 0), name)
                           for key, name in names)
        if inventory:
            counts = "envanter ({0} grup): {1}".format(len(report.inventory),
                                                      counts)
        if report.saved_path:
            self.settings.last_cleaned_path = report.saved_path
            self.settings.save()
            self.btn_clean_open.configure(state="normal")
            self.btn_clean_use.configure(state="normal")
            self.var_clean_summary.set(
                "Son tarama: {0}\nİşaretli kopya: {1}".format(
                    counts, report.saved_path))
        else:
            self.var_clean_summary.set(
                "Son tarama: {0} (kopya kaydedilemedi - günlüğe bakın)".format(
                    counts))
        self.var_status.set("Temizlik taraması bitti: {0}".format(counts))

    def _open_cleaned(self) -> None:
        """İşaretli kopyayı SpaceClaim'de aç (grupları görmek/silmek için)."""
        path = self.settings.last_cleaned_path
        if not path or not os.path.isfile(path):
            messagebox.showinfo("AutoMesh",
                                "Önce '1. Tara ve SpaceClaim'de işaretle'.")
            return
        from ..geometry.spaceclaim import SpaceClaimAnalyzer

        if SpaceClaimAnalyzer().open_document(path, self.settings.to_config()):
            self.var_status.set("SpaceClaim açılıyor: {0}".format(
                os.path.basename(path)))
        else:
            messagebox.showwarning("AutoMesh", "SpaceClaim açılamadı:\n{0}".format(
                path))

    def _use_cleaned(self) -> None:
        """Temizlenmiş kopyayı geometri yap ve Basit sekmeye geç."""
        path = self.settings.last_cleaned_path
        if not path or not os.path.isfile(path):
            messagebox.showinfo("AutoMesh",
                                "Önce '1. Tara ve SpaceClaim'de işaretle'.")
            return
        self.var_geometry.set(path)
        self._mode = "simple"
        try:
            self.notebook.select(0)
        except Exception:      # pragma: no cover - widget yokluğuna dayanıklı
            pass
        self._append("", "INFO")
        self._append("Geometri: {0}".format(path), "OK")
        self._append("SpaceClaim'deki silmeleri Ctrl+S ile kaydettiğinizden emin "
                     "olun; kaydedilmeyen değişiklik mesh'e yansımaz.", "WARNING")
        self.var_status.set("Temizlenmiş geometri seçildi - 'Mesh oluştur' ile "
                            "devam edebilirsiniz.")

    # -- gelişmiş sekme --------------------------------------------------
    def _build_advanced_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        self._build_files(parent, row=0)
        self._build_options(parent, row=1)
        self._build_buttons(parent, row=2)
        self._build_choice_bar(parent, row=3)

    def _on_tab_changed(self, _event=None) -> None:
        """Seçili sekme çalışma biçimini belirler."""
        try:
            index = int(self.notebook.index("current"))
        except Exception:
            return
        if index == 2:
            # Temizlik bir mesh modu değil: seçili mod (basit/gelişmiş) aynen
            # kalır, mesh'e dönüldüğünde oradan devam edilir.
            self.var_status.set(
                "Temizlik: küçük fileto, vida noktası ve çıkıntılar bulunur, "
                "SpaceClaim'de grup olarak işaretlenir - hiçbir şey silinmez.")
            return
        self._mode = "advanced" if index == 1 else "simple"
        self.var_status.set(
            "Gelişmiş mod: ölçüm, kademe ve yüzey boyutu ekranları devrede."
            if self._mode == "advanced"
            else "Basit mod: yüzey isimlendirme yok, tek düğmeyle mesh.")

    # -- dosyalar --------------------------------------------------------
    def _build_files(self, parent: ttk.Frame, row: int) -> None:
        box = ttk.LabelFrame(parent, text="Dosyalar", padding=PAD)
        box.grid(row=row, column=0, sticky="ew", pady=(0, PAD))
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="Geometri").grid(row=0, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.var_geometry).grid(
            row=0, column=1, sticky="ew", padx=PAD)
        ttk.Button(box, text="Seç...", command=self._pick_geometry).grid(row=0, column=2)

        ttk.Label(box, text="Çıktı klasörü").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(box, textvariable=self.var_output).grid(
            row=1, column=1, sticky="ew", padx=PAD, pady=(6, 0))
        ttk.Button(box, text="Seç...", command=self._pick_output).grid(
            row=1, column=2, pady=(6, 0))

        ttk.Label(box, text="Konfigürasyon").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(box, textvariable=self.var_config).grid(
            row=2, column=1, sticky="ew", padx=PAD, pady=(6, 0))
        ttk.Button(box, text="Seç...", command=self._pick_config).grid(
            row=2, column=2, pady=(6, 0))

        ttk.Label(box, text="(boş bırakılabilir - varsayılan ayarlar kullanılır)",
                  foreground="#666").grid(row=3, column=1, sticky="w", padx=PAD)

    # -- seçenekler ------------------------------------------------------
    def _build_options(self, parent: ttk.Frame, row: int) -> None:
        wrapper = ttk.Frame(parent)
        wrapper.grid(row=row, column=0, sticky="ew", pady=(0, PAD))
        wrapper.columnconfigure(0, weight=1)
        wrapper.columnconfigure(1, weight=1)

        mesh = ttk.LabelFrame(wrapper, text="Mesh ayarları", padding=PAD)
        mesh.grid(row=0, column=0, sticky="nsew", padx=(0, PAD // 2))
        mesh.columnconfigure(1, weight=1)

        ttk.Label(mesh, text="Fluent çekirdek").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(mesh, from_=1, to=256, textvariable=self.var_cores,
                    width=10).grid(row=0, column=1, sticky="w", padx=PAD)

        ttk.Label(mesh, text="Akış").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Combobox(mesh, textvariable=self.var_workflow, values=list(WORKFLOWS),
                     state="readonly", width=16).grid(
            row=1, column=1, sticky="w", padx=PAD, pady=(4, 0))

        ttk.Label(mesh, text="Hacim doldurma").grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Combobox(mesh, textvariable=self.var_fill, values=list(FILLS),
                     state="readonly", width=16).grid(
            row=2, column=1, sticky="w", padx=PAD, pady=(4, 0))

        ttk.Label(mesh, text="Hücre sınırı").grid(row=3, column=0, sticky="w", pady=(4, 0))
        ttk.Spinbox(mesh, from_=10000, to=500_000_000, increment=1_000_000,
                    textvariable=self.var_max_cells, width=14).grid(
            row=3, column=1, sticky="w", padx=PAD, pady=(4, 0))

        ttk.Label(mesh, text="Deneme sayısı").grid(row=4, column=0, sticky="w", pady=(4, 0))
        ttk.Spinbox(mesh, from_=1, to=30, textvariable=self.var_attempts,
                    width=10).grid(row=4, column=1, sticky="w", padx=PAD, pady=(4, 0))

        ttk.Label(mesh, text="Geometri birimi").grid(
            row=5, column=0, sticky="w", pady=(4, 0))
        unit_row = ttk.Frame(mesh)
        unit_row.grid(row=5, column=1, sticky="w", padx=PAD, pady=(4, 0))
        ttk.Combobox(unit_row, textvariable=self.var_unit,
                     values=[""] + list(DISPLAY_UNITS), width=7).pack(side="left")
        ttk.Label(unit_row, text="boş = dosyadan tespit",
                  foreground="#666").pack(side="left", padx=(6, 0))

        ttk.Label(mesh, text="Gösterim birimi").grid(
            row=6, column=0, sticky="w", pady=(4, 0))
        show_row = ttk.Frame(mesh)
        show_row.grid(row=6, column=1, sticky="w", padx=PAD, pady=(4, 0))
        ttk.Combobox(show_row, textvariable=self.var_display_unit,
                     values=list(DISPLAY_UNITS) + ["auto"], state="readonly",
                     width=7).pack(side="left")
        ttk.Label(show_row, text="ekranda ve raporda",
                  foreground="#666").pack(side="left", padx=(6, 0))

        ttk.Checkbutton(mesh, text="Sınır tabakası (prizma) kur",
                        variable=self.var_bl).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(mesh, text="Yüzey gruplarına özel boyut ver",
                        variable=self.var_local_sizing).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(2, 0))
        ttk.Checkbutton(mesh, text="Analiz sonrası SpaceClaim'de aç (grupları gör)",
                        variable=self.var_open_cad).grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(2, 0))

        flow = ttk.LabelFrame(wrapper, text="Akış bilgisi (opsiyonel - y+ için)",
                              padding=PAD)
        flow.grid(row=0, column=1, sticky="nsew", padx=(PAD // 2, 0))
        flow.columnconfigure(1, weight=1)

        for index, (label, variable, hint) in enumerate((
            ("Hedef y+", self.var_yplus, "1 = duvara kadar, 30-300 = duvar fonksiyonu"),
            ("Hız [m/s]", self.var_velocity, "y+ için gerekli"),
            ("Yoğunluk [kg/m³]", self.var_density, "hava: 1.225"),
            ("Viskozite [Pa·s]", self.var_viscosity, "hava: 1.7894e-05"),
            ("Karakt. uzunluk [m]", self.var_length, "boş = gövde köşegeni"),
        )):
            ttk.Label(flow, text=label).grid(row=index, column=0, sticky="w", pady=(0, 2))
            ttk.Entry(flow, textvariable=variable, width=14).grid(
                row=index, column=1, sticky="w", padx=PAD, pady=(0, 2))
            ttk.Label(flow, text=hint, foreground="#666").grid(
                row=index, column=2, sticky="w")

        run_box = ttk.LabelFrame(wrapper, text="Çalıştırma", padding=PAD)
        run_box.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(PAD, 0))

        ttk.Checkbutton(run_box,
                        text="Fluent penceresini göster (meshlemeyi canlı izle)",
                        variable=self.var_fluent_gui).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(run_box, text="Bitince Fluent açık kalsın",
                        variable=self.var_keep_open).grid(
            row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Checkbutton(run_box, text="Prova (ANSYS açılmaz, lisans harcanmaz)",
                        variable=self.var_dry_run).grid(row=0, column=1, sticky="w",
                                                        padx=(PAD * 2, 0))
        ttk.Label(run_box, text="Senaryo").grid(row=0, column=2, sticky="w",
                                                padx=(PAD * 2, 0))
        self.combo_scenario = ttk.Combobox(
            run_box, textvariable=self.var_scenario, values=list(SCENARIOS),
            state="readonly", width=12)
        self.combo_scenario.grid(row=0, column=3, sticky="w", padx=PAD)

        ttk.Checkbutton(run_box, text="Bilinmeyen hatalarda Claude'a danış",
                        variable=self.var_advisor).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(run_box, text="ANSYS sürümü").grid(row=1, column=2, sticky="w",
                                                     padx=(PAD * 2, 0), pady=(4, 0))
        ttk.Entry(run_box, textvariable=self.var_ansys, width=12).grid(
            row=1, column=3, sticky="w", padx=PAD, pady=(4, 0))

    # -- düğmeler --------------------------------------------------------
    def _build_buttons(self, parent: ttk.Frame, row: int) -> None:
        bar = ttk.Frame(parent)
        bar.grid(row=row, column=0, sticky="ew", pady=(0, PAD))

        self.btn_analyze = ttk.Button(
            bar, text="1. Geometriyi analiz et",
            command=lambda: self._guard("Geometri analizi",
                                        lambda: self._start("propose")))
        self.btn_analyze.pack(side="left")

        self.btn_plan = ttk.Button(
            bar, text="2. Yüzey boyutları",
            command=lambda: self._guard("Yüzey boyutları", self._edit_sizing))
        self.btn_plan.pack(side="left", padx=(PAD, 0))

        self.btn_run = ttk.Button(
            bar, text="3. Mesh oluştur",
            command=lambda: self._guard("Mesh oluşturma",
                                        lambda: self._start("run")))
        self.btn_run.pack(side="left", padx=(PAD, 0))

        self.btn_stop = ttk.Button(bar, text="Durdur", command=self._stop,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=(PAD * 2, 0))

        self.btn_report = ttk.Button(bar, text="Raporu aç", command=self._open_report,
                                     state="disabled")
        self.btn_report.pack(side="right")
        self._action_buttons.extend([self.btn_analyze, self.btn_plan, self.btn_run])

        self.btn_folder = ttk.Button(bar, text="Klasörü aç", command=self._open_folder,
                                     state="disabled")
        self.btn_folder.pack(side="right", padx=(0, PAD))

        ttk.Button(bar, text="Komutu kopyala",
                   command=self._copy_command).pack(side="right", padx=(0, PAD))

    def _build_choice_bar(self, parent: ttk.Frame, row: int) -> None:
        bar = ttk.Frame(parent)
        bar.grid(row=row, column=0, sticky="ew", pady=(0, PAD // 2))
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, textvariable=self.var_choice,
                  foreground="#1b5e20").grid(row=0, column=0, sticky="w")
        ttk.Button(bar, text="Seçimi temizle",
                   command=self._clear_choice).grid(row=0, column=1, sticky="e")
        ttk.Label(bar, textvariable=self.var_sizing,
                  foreground="#1b5e20").grid(row=1, column=0, sticky="w")
        buttons = ttk.Frame(bar)
        buttons.grid(row=1, column=1, sticky="e")
        self.btn_open_cad = ttk.Button(
            buttons, text="SpaceClaim'de aç",
            command=lambda: self._guard("SpaceClaim'de açma",
                                        self._open_in_spaceclaim),
            state="disabled")
        self.btn_open_cad.pack(side="left", padx=(0, PAD))
        self.btn_sizing = ttk.Button(
            buttons, text="Kademeyi yeniden seç",
            command=lambda: self._guard("Kademe seçimi", self._reopen_proposals),
            state="disabled")
        self.btn_sizing.pack(side="left")

    # -- günlük ----------------------------------------------------------
    def _build_log(self, parent: ttk.Frame, row: int) -> None:
        box = ttk.LabelFrame(parent, text="Günlük", padding=PAD // 2)
        box.grid(row=row, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        self.text = tk.Text(box, wrap="none", height=18, state="disabled",
                            background="#1e1e1e", foreground="#d4d4d4",
                            insertbackground="#d4d4d4", font=("Consolas", 9))
        self.text.grid(row=0, column=0, sticky="nsew")

        scroll_y = ttk.Scrollbar(box, orient="vertical", command=self.text.yview)
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x = ttk.Scrollbar(box, orient="horizontal", command=self.text.xview)
        scroll_x.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        self.text.tag_configure("INFO", foreground="#d4d4d4")
        self.text.tag_configure("WARNING", foreground="#dcdcaa")
        self.text.tag_configure("ERROR", foreground="#f48771")
        self.text.tag_configure("OK", foreground="#6a9955")

    def _build_status(self, parent: ttk.Frame, row: int) -> None:
        bar = ttk.Frame(parent)
        bar.grid(row=row, column=0, sticky="ew", pady=(PAD // 2, 0))
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, textvariable=self.var_status).grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=180)
        self.progress.grid(row=0, column=1, sticky="e")

    # ------------------------------------------------------------------
    # hata görünürlüğü
    # ------------------------------------------------------------------
    def _guard(self, label: str, action):
        """Bir düğme eylemini çalıştır; hatayı yut değil, göster.

        Tkinter geri çağrımlarındaki istisnalar stderr'e gider ve pythonw ile
        başlatıldığında hiçbir yere görünmez: pencere "hiçbir şey yapmamış"
        gibi durur.  Hatayı hem günlüğe hem kullanıcıya taşıyoruz.
        """
        try:
            return action()
        except Exception:
            detail = traceback.format_exc()
            self._append("{0} sırasında hata:".format(label), "ERROR")
            for line in detail.strip().splitlines():
                self._append("  " + line, "ERROR")
            self.var_status.set("Hata: {0}".format(label))
            try:
                self._set_busy(False)
            except Exception:
                pass
            messagebox.showerror(
                "AutoMesh - {0}".format(label),
                "{0}\n\nAyrıntı günlük penceresinde.".format(
                    detail.strip().splitlines()[-1]))
            return None

    # ------------------------------------------------------------------
    # dosya seçiciler
    # ------------------------------------------------------------------
    def _pick_geometry(self) -> None:
        current = self.var_geometry.get()
        initial = os.path.dirname(current) if current else os.path.expanduser("~")
        path = filedialog.askopenfilename(
            title="Geometri dosyası seçin",
            initialdir=initial if os.path.isdir(initial) else os.path.expanduser("~"),
            filetypes=list(FILE_TYPES))
        if path:
            self.var_geometry.set(os.path.normpath(path))
            self.var_status.set("Geometri seçildi: {0}".format(os.path.basename(path)))

    def _pick_output(self) -> None:
        path = filedialog.askdirectory(title="Çıktı klasörü seçin")
        if path:
            self.var_output.set(os.path.normpath(path))

    def _pick_config(self) -> None:
        path = filedialog.askopenfilename(
            title="Konfigürasyon dosyası seçin",
            filetypes=[("Konfigürasyon", "*.yaml *.yml *.json"), ("Tüm dosyalar", "*.*")])
        if path:
            self.var_config.set(os.path.normpath(path))

    # ------------------------------------------------------------------
    # çalıştırma
    # ------------------------------------------------------------------
    def _collect(self) -> GuiSettings:
        previous = self.settings
        self.settings = GuiSettings(
            mode=self._mode,
            geometry_path=self.var_geometry.get().strip(),
            output_dir=self.var_output.get().strip(),
            config_file=self.var_config.get().strip(),
            cores=_safe_int(self.var_cores, 4),
            workflow=self.var_workflow.get(),
            volume_fill=self.var_fill.get(),
            max_cells=_safe_int(self.var_max_cells, 20_000_000),
            attempts=_safe_int(self.var_attempts, 6),
            length_unit=self.var_unit.get().strip(),
            display_unit=self.var_display_unit.get().strip() or "mm",
            ansys_version=self.var_ansys.get().strip(),
            boundary_layers=bool(self.var_bl.get()),
            show_fluent_gui=bool(self.var_fluent_gui.get()),
            keep_fluent_open=bool(self.var_keep_open.get()),
            dry_run=bool(self.var_dry_run.get()),
            scenario=self.var_scenario.get(),
            use_advisor=bool(self.var_advisor.get()),
            y_plus=self.var_yplus.get().strip(),
            velocity=self.var_velocity.get().strip(),
            density=self.var_density.get().strip(),
            viscosity=self.var_viscosity.get().strip(),
            characteristic_length=self.var_length.get().strip(),
            # Kademe seçimi widget'larda tutulmuyor; önceki durumdan taşınır.
            chosen_label=previous.chosen_label,
            chosen_min_size=previous.chosen_min_size,
            chosen_max_size=previous.chosen_max_size,
            chosen_cells=previous.chosen_cells,
            local_sizing_enabled=bool(self.var_local_sizing.get()),
            open_in_spaceclaim=bool(self.var_open_cad.get()),
            sizing_divisions=dict(previous.sizing_divisions),
            sizing_disabled=list(previous.sizing_disabled),
            local_floor=previous.local_floor,
            cleanup_fillet_mm=self.var_clean_fillet.get().strip(),
            cleanup_hole_mm=self.var_clean_hole.get().strip(),
            cleanup_protrusion_mm=self.var_clean_bump.get().strip(),
            cleanup_fillets=bool(self.var_clean_fillets.get()),
            cleanup_screws=bool(self.var_clean_screws.get()),
            cleanup_protrusions=bool(self.var_clean_bumps.get()),
            cleanup_open_spaceclaim=bool(self.var_clean_open.get()),
            last_cleaned_path=previous.last_cleaned_path,
        )
        return self.settings

    def _start(self, kind: str, mode: Optional[str] = None) -> None:
        """``kind``: analyze | propose | plan | run.  ``mode``: simple | advanced."""
        if mode in ("simple", "advanced"):
            self._mode = mode
        if self.run is not None and self.run.running:
            messagebox.showinfo("AutoMesh", "Zaten çalışan bir iş var.")
            return
        settings = self._collect()
        problems = (settings.validate_cleanup() if kind in ("cleanup", "inventory")
                    else settings.validate())
        if problems:
            messagebox.showerror("Eksik veya hatalı bilgi", "\n".join(problems))
            return
        # Gerçek mesh için PyFluent şart. Eksikse iş thread'inde yığın izi
        # olarak patlıyordu; kullanıcı açısından "düğme çalışmıyor" demekti.
        if kind == "run" and not settings.dry_run and not self._check_fluent():
            return
        settings.save()

        self._clear_log()
        label = {"analyze": "Geometri analizi", "plan": "Plan hesaplama",
                 "propose": "Geometri analizi", "run": "Mesh oluşturma",
                 "cleanup": "Temizlik taraması",
                 "inventory": "Geometri envanteri"}[kind]
        self._append("=== {0} başlıyor ===".format(label), "OK")
        if settings.is_simple and kind == "run":
            self._append(
                "Basit mod: yüzey isimlendirme yapılmaz, boyutlar geometriden "
                "otomatik hesaplanır.", "INFO")
        if kind == "run" and settings.dry_run:
            self._append(
                "PROVA modu: Fluent açılmayacak, lisans harcanmayacak "
                "(senaryo: {0}).".format(settings.scenario), "WARNING")
        if kind == "inventory":
            self._append("Eşik yok: her fileto, delik, çıkıntı ve yüzey "
                         "benzerliğe göre envanter_* gruplarında toplanır. "
                         "Hiçbir şey silinmez.", "INFO")
            command = settings.cleanup_command() + " --envanter"
        elif kind == "cleanup":
            self._append("Hiçbir şey silinmez: bulunan detaylar SpaceClaim'de "
                         "temizle_* grubu olarak işaretlenir, kaynak dosyaya "
                         "dokunulmaz.", "INFO")
            command = settings.cleanup_command()
        else:
            command = settings.equivalent_command()
        self._append("Eşdeğer komut:  {0}".format(command), "INFO")
        self._append("", "INFO")

        self.last_result = None
        self.btn_report.configure(state="disabled")
        self.btn_folder.configure(state="disabled")
        self._set_busy(True)
        self.var_status.set("{0} sürüyor...".format(label))

        self.run = BackgroundRun(settings, kind)
        self.run.start()

    def _check_fluent(self) -> bool:
        """PyFluent yoksa sebebini ve çözümünü göster, işi hiç başlatma."""
        if _pyfluent_available():
            return True

        self._clear_log()
        self._append("=== Mesh oluşturma başlatılamadı ===", "ERROR")
        self._append("PyFluent (ansys-fluent-core) bu Python'da bulunamadı; "
                     "Fluent'i sürecek kütüphane o.", "ERROR")
        self._append("", "INFO")
        try:
            from ..doctor import inspect_pyfluent

            for line in inspect_pyfluent():
                self._append("  " + line, "INFO")
        except Exception:
            for line in traceback.format_exc().strip().splitlines():
                self._append("  " + line, "ERROR")
        self._append("", "INFO")
        self._append("Çözüm:", "OK")
        self._append("  1) Kuruluysa klasörünü bir kez kaydedin:", "INFO")
        self._append("       py -m automesh doctor --add-path "
                     "D:\\Work\\plm", "INFO")
        self._append("     (bu kayıt arayüzü hangi Python açarsa açsın "
                     "okunur)", "INFO")
        self._append("  2) Ayrıntılı tanı:  py -m automesh doctor", "INFO")
        self._append("  3) Fluent'i hiç açmadan denemek için 'Prova' "
                     "kutusunu işaretleyin.", "INFO")
        self.var_status.set("PyFluent bulunamadı - günlüğe bakın.")
        messagebox.showerror(
            "PyFluent bulunamadı",
            "Mesh oluşturmak için PyFluent (ansys-fluent-core) gerekli ama "
            "bu Python'da bulunamadı.\n\n"
            "Kuruluysa klasörünü automesh-yollar.txt dosyasına yazın, "
            "ayrıntı için 'py -m automesh doctor' çalıştırın.\n\n"
            "Ayrıntılar günlük penceresinde.")
        return False

    def _stop(self) -> None:
        if self.run is not None and self.run.running:
            self.run.cancel()
            self.var_status.set("Durduruluyor...")

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in self._action_buttons:
            button.configure(state=state)
        self.btn_stop.configure(state="normal" if busy else "disabled")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    # ------------------------------------------------------------------
    # kuyruk pompası
    # ------------------------------------------------------------------
    def _pump(self) -> None:
        """Kuyruğu boşalt. Bu döngü asla kırılmamalı: kırılırsa günlük akışı
        durur ve pencere donmuş gibi görünür."""
        try:
            if self.run is not None:
                for message in self.run.drain():
                    if message.kind == LOG:
                        self._append(message.text, _tag_for(message.level))
                    elif message.kind == ERROR:
                        self._append(message.text, "ERROR")
                    elif message.kind == DONE:
                        self._finish(message.result)
                        self._remember_prepared_file()
                        if self.run is not None and self.run.mode == "propose":
                            self._show_proposals()
                        if self.run is not None and \
                                self.run.mode in ("cleanup", "inventory"):
                            self._cleanup_done()
        except Exception:
            for line in traceback.format_exc().strip().splitlines():
                self._append("  " + line, "ERROR")
            self._set_busy(False)
        finally:
            self.root.after(150, self._pump)

    def _finish(self, result) -> None:
        self._set_busy(False)
        self.last_result = result
        if result is None:
            if self.run is not None and self.run.error:
                self.var_status.set("Hata ile sonuçlandı.")
            elif self.run is not None and self.run.mode == "analyze":
                self.var_status.set("Analiz tamam.")
                self._append("", "INFO")
                self._append(
                    "Sıradaki adım: '2. Ölçüm ve öneriler' - mesh kademesini "
                    "ve yüzey boyutlarını orada seçersiniz.", "OK")
            else:
                self.var_status.set("Tamamlandı.")
            return

        if result.success:
            self._append("", "INFO")
            self._append("BAŞARILI: {0}".format(result.message), "OK")
            self.var_status.set("Başarılı - {0}".format(result.message))
        else:
            self._append("", "INFO")
            self._append("BAŞARISIZ: {0}".format(result.message), "ERROR")
            self.var_status.set("Başarısız - {0}".format(result.message))

        if result.run_dir and os.path.isdir(result.run_dir):
            self.btn_folder.configure(state="normal")
            if os.path.isfile(os.path.join(result.run_dir, "report.md")):
                self.btn_report.configure(state="normal")
                self._append("Rapor: {0}".format(
                    os.path.join(result.run_dir, "report.md")), "INFO")

    # ------------------------------------------------------------------
    # günlük penceresi
    # ------------------------------------------------------------------
    def _remember_prepared_file(self) -> None:
        """Analizin ürettiği gruplanmış dosyayı not et ve düğmeyi aç."""
        metrics = None
        if self.run is not None and getattr(self.run, "metrics", None) is not None:
            metrics = self.run.metrics
        elif self.last_result and self.last_result.geometry:
            metrics = None
            prepared = (self.last_result.geometry.get("raw") or {}).get(
                "exported_path", "")
            if prepared:
                self._prepared_path = prepared
                self.btn_open_cad.configure(state="normal")
            return
        if metrics is None:
            return
        self._last_metrics = metrics
        prepared = (getattr(metrics, "raw", None) or {}).get("exported_path", "")
        if prepared and os.path.isfile(prepared):
            self._prepared_path = prepared
            self.btn_open_cad.configure(state="normal")
            self._append("Gruplanmış dosya: {0}".format(prepared), "INFO")
        if getattr(metrics, "face_groups", None):
            self.btn_sizing.configure(state="normal")

    def _show_proposals(self) -> None:
        """Ölçüm/öneri penceresini aç ve seçimi kaydet."""
        run = self.run
        if run is None or not run.proposals:
            return
        self._last_metrics = run.metrics
        self._last_proposals = run.proposals
        self.btn_sizing.configure(state="normal")
        self.btn_simple_reopen.configure(state="normal")
        self._choose_proposal(run.metrics, run.proposals)

    def _choose_proposal(self, metrics, proposals) -> None:
        from .proposals_dialog import ProposalDialog

        chosen = ProposalDialog(self.root, metrics, proposals).show()
        if chosen is None:
            self._append("Kademe seçilmedi - boyutlandırma otomatik kalıyor.", "INFO")
            self._clear_choice()
            return
        self._collect()      # widget'lardaki güncel değerleri al
        self.settings.chosen_label = chosen.label
        self.settings.chosen_min_size = chosen.plan.min_size
        self.settings.chosen_max_size = chosen.plan.max_size
        self.settings.chosen_cells = chosen.cells
        self.settings.save()
        self.var_choice.set(self.settings.chosen_summary())
        self._append("Seçilen kademe: {0} ({1}, ~{2} hücre)".format(
            chosen.label, chosen.size_text(), chosen.cells_text()), "OK")
        for warning in chosen.warnings:
            self._append("! " + warning, "WARNING")
        self.var_status.set("Kademe seçildi: {0}".format(chosen.label))
        # Kademe seçildikten sonra yüzey boyutlarını gözden geçirme sırası.
        # Basit modda yüzey grubu hiç üretilmez, o ekran da açılmaz.
        if (self._mode == "advanced" and getattr(metrics, "face_groups", None)
                and self.var_local_sizing.get()):
            self._edit_sizing()
        self._append(
            "Şimdi '2. Mesh oluştur' ile devam edebilirsiniz."
            if self._mode == "simple"
            else "Şimdi '3. Mesh oluştur' ile devam edebilirsiniz.", "INFO")

    def _reopen_proposals(self) -> None:
        """Kademe seçim ekranını yeniden aç (yeni analiz yapmadan)."""
        metrics = getattr(self, "_last_metrics", None)
        proposals = getattr(self, "_last_proposals", None)
        if metrics is None or not proposals:
            messagebox.showinfo(
                "AutoMesh",
                "Önce '1. Geometriyi analiz et' düğmesiyle geometriyi "
                "okutun; kademeler ondan sonra listelenir.")
            return
        self._choose_proposal(metrics, proposals)

    def _open_in_spaceclaim(self) -> None:
        """Hazırlanan (gruplanmış) dosyayı SpaceClaim'de aç."""
        path = getattr(self, "_prepared_path", "")
        if not path or not os.path.isfile(path):
            messagebox.showinfo(
                "AutoMesh",
                "Henüz hazırlanmış bir dosya yok.\n\n"
                "Önce '1. Geometriyi analiz et' ya da '2. Ölçüm ve öneriler' "
                "ile SpaceClaim analizini çalıştırın.")
            return
        from ..geometry.spaceclaim import SpaceClaimAnalyzer

        cfg = self._collect().to_config()
        if SpaceClaimAnalyzer().open_document(path, cfg):
            self._append("SpaceClaim açılıyor: {0}".format(path), "OK")
            self._append("Grupları sol taraftaki 'Groups' panelinde "
                         "göreceksiniz.", "INFO")
            self.var_status.set("SpaceClaim açılıyor...")
        else:
            messagebox.showwarning(
                "AutoMesh", "SpaceClaim açılamadı. Kurulum yolunu kontrol edin.")

    def _edit_sizing(self) -> None:
        """Yüzey gruplarının bölme sayılarını seçtir."""
        metrics = getattr(self, "_last_metrics", None)
        if metrics is None:
            messagebox.showinfo(
                "AutoMesh",
                "Önce '1. Geometriyi analiz et' düğmesiyle geometriyi "
                "okutun; kademeler ondan sonra listelenir.")
            return
        if not getattr(metrics, "face_groups", None):
            # "Analiz edin" demek yanıltıcı olurdu: analiz yapıldı, sonuç boş.
            from ..planning.local_sizing import explain_missing_groups

            reasons = explain_missing_groups(metrics, self._collect().to_config())
            self._append("Yüzey grubu bulunamadı:", "WARNING")
            for reason in reasons:
                self._append("  " + reason, "WARNING")
            self._append(
                "Mesh yine de çalışır; tüm yüzeyler global boyutu kullanır.",
                "INFO")
            messagebox.showinfo(
                "AutoMesh - yüzey grubu yok",
                "Geometri analiz edildi ama boyut verilecek yüzey grubu "
                "çıkmadı.\n\n" + "\n".join(reasons)
                + "\n\nMesh yine de çalışır: tüm yüzeyler global boyutu "
                  "kullanır.")
            return
        from ..planning.local_sizing import review_groups
        from ..planning.sizing import plan_mesh
        from ..units import resolve_display_unit
        from .sizing_dialog import SizingDialog

        settings = self._collect()
        cfg = settings.to_config()
        plan = plan_mesh(metrics, cfg)
        reviews = review_groups(metrics.face_groups, plan, cfg)
        if not reviews:
            messagebox.showinfo("AutoMesh", "Boyut verilecek yüzey grubu yok.")
            return

        unit = resolve_display_unit(cfg.output.display_unit, metrics.diagonal)
        result = SizingDialog(self.root, reviews, plan,
                              cfg.local_sizing, unit).show()
        if result is None:
            self._append("Yüzey boyutları değiştirilmedi.", "INFO")
            return
        divisions, disabled, floor = result
        self.settings.sizing_divisions = divisions
        self.settings.sizing_disabled = disabled
        self.settings.local_floor = floor
        self.settings.save()
        self.var_sizing.set(self.settings.sizing_summary())
        self._append("Yüzey boyutları güncellendi:", "OK")
        for review in review_groups(metrics.face_groups, plan,
                                    self.settings.to_config()):
            if review.enabled:
                self._append("  {0}: {1:g} bölme -> {2}".format(
                    review.name, review.divisions,
                    format_length(review.size, unit)), "INFO")
            else:
                self._append("  {0}: kapalı".format(review.name), "WARNING")

    def _clear_choice(self) -> None:
        self.settings.clear_choice()
        self.settings.clear_sizing_choices()
        self.var_choice.set(self.settings.chosen_summary())
        self.var_sizing.set(self.settings.sizing_summary())

    def _append(self, text: str, tag: str = "INFO") -> None:
        self.text.configure(state="normal")
        self.text.insert("end", text + "\n", tag)
        self.text.see("end")
        self.text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    # ------------------------------------------------------------------
    # yardımcı eylemler
    # ------------------------------------------------------------------
    def _copy_command(self) -> None:
        command = self._collect().equivalent_command()
        self.root.clipboard_clear()
        self.root.clipboard_append(command)
        self.var_status.set("Komut panoya kopyalandı.")

    def _open_report(self) -> None:
        if self.last_result and self.last_result.run_dir:
            _open_path(os.path.join(self.last_result.run_dir, "report.md"))

    def _open_folder(self) -> None:
        if self.last_result and self.last_result.run_dir:
            _open_path(self.last_result.run_dir)

    def _on_dry_run_toggled(self) -> None:
        # trace, widget'lar kurulmadan önce de tetiklenebilir.
        combo = getattr(self, "combo_scenario", None)
        if combo is not None:
            combo.configure(
                state="readonly" if self.var_dry_run.get() else "disabled")

    def _on_close(self) -> None:
        if self.run is not None and self.run.running:
            if not messagebox.askyesno(
                    "AutoMesh",
                    "Bir iş sürüyor. Durdurup çıkmak istediğinize emin misiniz?"):
                return
            self.run.cancel()
        try:
            self._collect().save()
        except Exception:
            pass
        self.root.destroy()


# --------------------------------------------------------------------------

def _pyfluent_available() -> bool:
    """PyFluent import edilebiliyor mu (testlerde değiştirilebilsin diye ayrı)."""
    import importlib

    try:
        importlib.import_module("ansys.fluent.core")
        return True
    except Exception:
        return False


def _tag_for(level: int) -> str:
    if level >= logging.ERROR:
        return "ERROR"
    if level >= logging.WARNING:
        return "WARNING"
    return "INFO"


def _safe_int(variable, default: int) -> int:
    try:
        return int(variable.get())
    except (tk.TclError, ValueError):
        return default


def _open_path(path: str) -> None:
    if not path or not os.path.exists(path):
        messagebox.showwarning("AutoMesh", "Dosya bulunamadı:\n{0}".format(path))
        return
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)            # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError as exc:
        messagebox.showwarning("AutoMesh", "Açılamadı:\n{0}".format(exc))


def main(geometry: Optional[str] = None) -> int:
    # Yüksek DPI ekranlarda bulanıklığı önler; pencere oluşturulmadan ÖNCE
    # çağrılmalı, sonrasında etkisiz kalır.
    try:
        from ctypes import windll  # type: ignore

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    AutoMeshApp(root, geometry=geometry)
    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys

    raise SystemExit(main(_sys.argv[1] if len(_sys.argv) > 1 else None))
