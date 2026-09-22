"""Yüzey gruplarının bölme sayısını seçme ekranı.

Sorulan soru bilinçli olarak "kaç mm hücre?" değil, **"kaça böleyim?"**:
20 mm çapındaki bir girişi çevresinde 16 hücreye bölmek yaygın bir kabuldür
ve bu ekran o kabulü öneri olarak getirip değiştirilebilir bırakır.

Aşırı ince boyutlar Fluent'i zorladığı için her satırda risk göstergesi ve
alt tarafta toplam hücre tahmini var; bir kontrolü tamamen kapatmak da
mümkün.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import ttk

from ..models import MeshPlan
from ..planning.local_sizing import HIGH, OK, RISK_LABELS, WARN, SizingReview, evaluate
from ..units import format_length, from_metres, to_metres

PAD = 8

RISK_COLORS = {OK: "#1b5e20", WARN: "#8a6d00", HIGH: "#b00020"}


class SizingDialog:
    """Modal seçim penceresi.

    ``result`` seçimden sonra ``(bölme sayıları, kapatılanlar, taban)``
    içerir; kullanıcı vazgeçerse ``None`` kalır.
    """

    def __init__(self, parent: tk.Misc, reviews: List[SizingReview],
                 plan: MeshPlan, settings, unit: str = "mm") -> None:
        self.reviews = reviews
        self.plan = plan
        self.settings = settings
        self.unit = unit
        self.result: Optional[Tuple[Dict[str, float], List[str], float]] = None
        self.rows: List[dict] = []

        self.top = tk.Toplevel(parent)
        self.top.title("Yüzey grupları - hücre boyutu seçimi")
        self.top.geometry("1120x640")
        self.top.minsize(960, 520)
        self.top.transient(parent)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)

        self.var_floor = tk.StringVar(
            value="{0:.4g}".format(from_metres(settings.absolute_floor, unit))
            if settings.absolute_floor > 0 else "")
        self.var_total = tk.StringVar()

        self._build()
        self._recalculate()
        self.top.grab_set()
        self.top.focus_set()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = ttk.Frame(self.top, padding=PAD)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, sticky="ew", pady=(0, PAD))
        ttk.Label(
            header,
            text=("Hücre boyutu = ölçülen uzunluk / bölme sayısı.   "
                  "Karşılaştırma için global boyut: {0}"
                  .format(format_length(self.plan.max_size, self.unit))),
            font=("", 9, "bold")).pack(side="left")

        self._build_rows(outer)
        self._build_footer(outer)

    # -- satırlar --------------------------------------------------------
    def _build_rows(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Gruplar", padding=PAD // 2)
        box.grid(row=1, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(1, weight=1)

        headings = ("", "Grup", "Kaynak", "Ölçüm", "Bölünen",
                    "Kaça böleyim?", "Hücre boyutu", "~Hücre", "Durum")
        widths = (3, 26, 10, 18, 16, 14, 14, 12, 10)
        head = ttk.Frame(box)
        head.grid(row=0, column=0, sticky="ew")
        for index, (text, width) in enumerate(zip(headings, widths)):
            ttk.Label(head, text=text, width=width,
                      font=("", 9, "bold")).grid(row=0, column=index, sticky="w",
                                                 padx=2)

        canvas = tk.Canvas(box, highlightthickness=0, height=340)
        canvas.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(box, orient="vertical", command=canvas.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scroll.set)

        inner = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window, width=e.width))

        for index, review in enumerate(self.reviews):
            self.rows.append(self._build_row(inner, index, review, widths))

    def _build_row(self, parent: ttk.Frame, index: int, review: SizingReview,
                   widths) -> dict:
        var_enabled = tk.BooleanVar(value=review.enabled)
        var_divisions = tk.StringVar(
            value="{0:g}".format(review.divisions) if review.divisions else "")

        ttk.Checkbutton(parent, variable=var_enabled,
                        command=self._recalculate).grid(
            row=index, column=0, sticky="w", padx=2, pady=1)
        ttk.Label(parent, text=review.name, width=widths[1]).grid(
            row=index, column=1, sticky="w", padx=2)
        ttk.Label(parent, text="sizin grubunuz" if review.is_existing else "agent",
                  width=widths[2],
                  foreground="#1b5e20" if review.is_existing else "#555").grid(
            row=index, column=2, sticky="w", padx=2)
        ttk.Label(parent, text=review.measured_text(self.unit),
                  width=widths[3]).grid(row=index, column=3, sticky="w", padx=2)
        ttk.Label(parent, text="{0} {1}".format(
            review.base_label, format_length(review.base_length, self.unit)),
            width=widths[4]).grid(row=index, column=4, sticky="w", padx=2)

        spin = ttk.Spinbox(parent, from_=1, to=400, increment=1, width=8,
                           textvariable=var_divisions)
        spin.grid(row=index, column=5, sticky="w", padx=2)
        var_divisions.trace_add("write", lambda *_a: self._recalculate())

        label_size = ttk.Label(parent, text="", width=widths[6])
        label_size.grid(row=index, column=6, sticky="w", padx=2)
        label_cells = ttk.Label(parent, text="", width=widths[7])
        label_cells.grid(row=index, column=7, sticky="w", padx=2)
        label_risk = ttk.Label(parent, text="", width=widths[8])
        label_risk.grid(row=index, column=8, sticky="w", padx=2)

        if review.base_length <= 0:
            spin.state(["disabled"])

        return {
            "review": review, "enabled": var_enabled, "divisions": var_divisions,
            "size": label_size, "cells": label_cells, "risk": label_risk,
        }

    # -- alt bölüm -------------------------------------------------------
    def _build_footer(self, parent: ttk.Frame) -> None:
        details = ttk.LabelFrame(parent, text="Seçili satırın gerekçesi",
                                 padding=PAD // 2)
        details.grid(row=2, column=0, sticky="ew", pady=(PAD, 0))
        details.columnconfigure(0, weight=1)
        self.details = tk.Text(details, height=4, wrap="word", state="disabled",
                               background="#f7f7f7", relief="flat")
        self.details.grid(row=0, column=0, sticky="ew")
        self.details.tag_configure("warn", foreground="#b00020")

        bar = ttk.Frame(parent)
        bar.grid(row=3, column=0, sticky="ew", pady=(PAD, 0))

        ttk.Label(bar, text="Hiçbir boyut bundan ince olmasın:").pack(side="left")
        ttk.Entry(bar, textvariable=self.var_floor, width=10).pack(
            side="left", padx=(4, 2))
        ttk.Label(bar, text=self.unit).pack(side="left")
        self.var_floor.trace_add("write", lambda *_a: self._recalculate())

        ttk.Label(bar, textvariable=self.var_total, foreground="#555").pack(
            side="left", padx=(PAD * 2, 0))

        ttk.Button(bar, text="Vazgeç", command=self._cancel).pack(side="right")
        ttk.Button(bar, text="Uygula", command=self._accept).pack(
            side="right", padx=(0, PAD))
        ttk.Button(bar, text="Önerilere dön", command=self._reset).pack(
            side="right", padx=(0, PAD))

    # ------------------------------------------------------------------
    def _floor_metres(self) -> float:
        text = (self.var_floor.get() or "").strip().replace(",", ".")
        if not text:
            return 0.0
        try:
            return to_metres(float(text), self.unit)
        except (ValueError, KeyError):
            return 0.0

    def _divisions_of(self, row: dict) -> float:
        text = (row["divisions"].get() or "").strip().replace(",", ".")
        try:
            return float(text)
        except ValueError:
            return 0.0

    def _recalculate(self) -> None:
        """Her satırın boyutunu, hücre tahminini ve riskini tazele."""
        settings = _FloorOverride(self.settings, self._floor_metres())
        total = 0
        messages: List[str] = []
        for row in self.rows:
            review = row["review"]
            divisions = self._divisions_of(row)
            size, ratio, cells, risk = evaluate(
                review, divisions, self.plan.max_size, settings)
            enabled = bool(row["enabled"].get()) and size > 0

            row["size"].configure(
                text=format_length(size, self.unit) if size else "-")
            row["cells"].configure(text="{0:,}".format(cells) if cells else "-")
            row["risk"].configure(
                text=RISK_LABELS[risk] if enabled else "kapalı",
                foreground=RISK_COLORS[risk] if enabled else "#888")
            if enabled:
                total += cells
                if risk == HIGH:
                    messages.append(
                        "{0}: global boyuttan {1:.0f} kat ince - Fluent "
                        "zorlanabilir. Bölme sayısını düşürmeyi düşünün."
                        .format(review.name, ratio))
                elif risk == WARN:
                    messages.append("{0}: global boyuttan {1:.0f} kat ince."
                                    .format(review.name, ratio))

        self.var_total.set(
            "Yerel kontrollerin toplam yüzey hücresi tahmini: ~{0:,}".format(total))
        self._show_messages(messages)

    def _show_messages(self, messages: List[str]) -> None:
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        if messages:
            for message in messages:
                self.details.insert("end", "! " + message + "\n", "warn")
        else:
            self.details.insert(
                "end", "Seçili değerler uygun görünüyor. Bir satırın bölme "
                       "sayısını değiştirdikçe boyut ve risk anında güncellenir.\n")
        self.details.configure(state="disabled")

    # ------------------------------------------------------------------
    def _reset(self) -> None:
        for row in self.rows:
            review = row["review"]
            row["divisions"].set("{0:g}".format(review.recommended_divisions)
                                 if review.recommended_divisions else "")
            row["enabled"].set(review.enabled)
        self.var_floor.set("")
        self._recalculate()

    def _accept(self) -> None:
        divisions: Dict[str, float] = {}
        disabled: List[str] = []
        for row in self.rows:
            review = row["review"]
            value = self._divisions_of(row)
            if value > 0:
                divisions[review.name] = value
            if not row["enabled"].get():
                disabled.append(review.name)
        self.result = (divisions, disabled, self._floor_metres())
        self._close()

    def _cancel(self) -> None:
        self.result = None
        self._close()

    def _close(self) -> None:
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        self.top.destroy()

    def show(self):
        self.top.wait_window()
        return self.result


class _FloorOverride:
    """Ayarların yalnızca tabanını değiştiren ince sarmalayıcı."""

    def __init__(self, settings, floor: float) -> None:
        self._settings = settings
        self.absolute_floor = floor

    def __getattr__(self, name):
        return getattr(self._settings, name)
