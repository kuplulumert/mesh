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
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.settings = GuiSettings.load()
        self.run: Optional[BackgroundRun] = None
        self.last_result = None

        root.title("AutoMesh {0} - Otonom Fluent Meshing".format(__version__))
        root.geometry("1040x760")
        root.minsize(900, 620)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_variables()
        self._build_layout()
        self._on_dry_run_toggled()
        self._pump()

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
        self.var_dry_run.trace_add("write", lambda *_: self._on_dry_run_toggled())

    # ------------------------------------------------------------------
    # yerleşim
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=PAD)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1)

        self._build_files(outer, row=0)
        self._build_options(outer, row=1)
        self._build_buttons(outer, row=2)
        self._build_choice_bar(outer, row=3)
        self._build_log(outer, row=4)
        self._build_status(outer, row=5)

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

        self.btn_analyze = ttk.Button(bar, text="1. Geometriyi analiz et",
                                      command=lambda: self._start("analyze"))
        self.btn_analyze.pack(side="left")

        self.btn_plan = ttk.Button(bar, text="2. Ölçüm ve öneriler",
                                   command=lambda: self._start("propose"))
        self.btn_plan.pack(side="left", padx=(PAD, 0))

        self.btn_run = ttk.Button(bar, text="3. Mesh oluştur",
                                  command=lambda: self._start("run"))
        self.btn_run.pack(side="left", padx=(PAD, 0))

        self.btn_stop = ttk.Button(bar, text="Durdur", command=self._stop,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=(PAD * 2, 0))

        self.btn_report = ttk.Button(bar, text="Raporu aç", command=self._open_report,
                                     state="disabled")
        self.btn_report.pack(side="right")

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
        self.btn_sizing = ttk.Button(bar, text="Yüzey boyutlarını düzenle",
                                     command=self._edit_sizing, state="disabled")
        self.btn_sizing.grid(row=1, column=1, sticky="e")

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
            sizing_divisions=dict(previous.sizing_divisions),
            sizing_disabled=list(previous.sizing_disabled),
            local_floor=previous.local_floor,
        )
        return self.settings

    def _start(self, mode: str) -> None:
        if self.run is not None and self.run.running:
            messagebox.showinfo("AutoMesh", "Zaten çalışan bir iş var.")
            return
        settings = self._collect()
        problems = settings.validate()
        if problems:
            messagebox.showerror("Eksik veya hatalı bilgi", "\n".join(problems))
            return
        settings.save()

        self._clear_log()
        label = {"analyze": "Geometri analizi", "plan": "Plan hesaplama",
                 "propose": "Ölçüm ve öneriler", "run": "Mesh oluşturma"}[mode]
        self._append("=== {0} başlıyor ===".format(label), "OK")
        if mode == "run" and settings.dry_run:
            self._append(
                "PROVA modu: Fluent açılmayacak, lisans harcanmayacak "
                "(senaryo: {0}).".format(settings.scenario), "WARNING")
        self._append("Eşdeğer komut:  {0}".format(settings.equivalent_command()), "INFO")
        self._append("", "INFO")

        self.last_result = None
        self.btn_report.configure(state="disabled")
        self.btn_folder.configure(state="disabled")
        self._set_busy(True)
        self.var_status.set("{0} sürüyor...".format(label))

        self.run = BackgroundRun(settings, mode)
        self.run.start()

    def _stop(self) -> None:
        if self.run is not None and self.run.running:
            self.run.cancel()
            self.var_status.set("Durduruluyor...")

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in (self.btn_analyze, self.btn_plan, self.btn_run):
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
        if self.run is not None:
            for message in self.run.drain():
                if message.kind == LOG:
                    self._append(message.text, _tag_for(message.level))
                elif message.kind == ERROR:
                    self._append(message.text, "ERROR")
                elif message.kind == DONE:
                    self._finish(message.result)
                    if self.run is not None and self.run.mode == "propose":
                        self._show_proposals()
        self.root.after(150, self._pump)

    def _finish(self, result) -> None:
        self._set_busy(False)
        self.last_result = result
        if result is None:
            if self.run is not None and self.run.error:
                self.var_status.set("Hata ile sonuçlandı.")
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
    def _show_proposals(self) -> None:
        """Ölçüm/öneri penceresini aç ve seçimi kaydet."""
        run = self.run
        if run is None or not run.proposals:
            return
        from .proposals_dialog import ProposalDialog

        chosen = ProposalDialog(self.root, run.metrics, run.proposals).show()
        if chosen is None:
            self._append("Kademe seçilmedi - boyutlandırma otomatik kalıyor.", "INFO")
            self._clear_choice()
            return
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
        self._last_metrics = run.metrics
        self.btn_sizing.configure(
            state="normal" if getattr(run.metrics, "face_groups", None) else "disabled")
        if getattr(run.metrics, "face_groups", None) and self.var_local_sizing.get():
            self._edit_sizing()
        self._append("Şimdi '3. Mesh oluştur' ile devam edebilirsiniz.", "INFO")

    def _edit_sizing(self) -> None:
        """Yüzey gruplarının bölme sayılarını seçtir."""
        metrics = getattr(self, "_last_metrics", None)
        if metrics is None or not getattr(metrics, "face_groups", None):
            messagebox.showinfo(
                "AutoMesh",
                "Önce '2. Ölçüm ve öneriler' ile geometriyi analiz edin.")
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


def main() -> int:
    # Yüksek DPI ekranlarda bulanıklığı önler; pencere oluşturulmadan ÖNCE
    # çağrılmalı, sonrasında etkisiz kalır.
    try:
        from ctypes import windll  # type: ignore

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    AutoMeshApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
