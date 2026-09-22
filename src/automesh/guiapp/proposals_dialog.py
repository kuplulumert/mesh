"""Ölçüm ve mesh kademesi seçme penceresi.

"Uzunluklar bunlar, burada şu boyutu seçeceğim" akışının arayüz karşılığı:
solda ölçülen uzunluklar, sağda seçilebilir kademeler, altta seçilen
kademenin gerekçesi ve uyarıları.
"""

from __future__ import annotations

from typing import List, Optional

import tkinter as tk
from tkinter import ttk

from ..models import GeometryMetrics
from ..planning.proposals import Proposal, measurements

PAD = 8


class ProposalDialog:
    """Modal seçim penceresi. ``result`` seçilen kademeyi tutar (yoksa None)."""

    def __init__(self, parent: tk.Misc, metrics: GeometryMetrics,
                 proposals: List[Proposal]) -> None:
        self.metrics = metrics
        self.proposals = proposals
        self.result: Optional[Proposal] = None

        self.top = tk.Toplevel(parent)
        self.top.title("Ölçümler ve mesh kademeleri")
        self.top.geometry("1080x640")
        self.top.minsize(900, 520)
        self.top.transient(parent)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)

        self._build()
        self._select_default()

        self.top.grab_set()
        self.top.focus_set()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = ttk.Frame(self.top, padding=PAD)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=2)
        outer.columnconfigure(1, weight=3)
        outer.rowconfigure(0, weight=1)

        self._build_measurements(outer)
        self._build_proposals(outer)
        self._build_details(outer)
        self._build_buttons(outer)

    # -- ölçümler --------------------------------------------------------
    def _build_measurements(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Ölçülen geometri", padding=PAD // 2)
        box.grid(row=0, column=0, sticky="nsew", padx=(0, PAD // 2))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        tree = ttk.Treeview(box, columns=("value", "note"), show="tree headings",
                            height=16)
        tree.heading("#0", text="Ölçüm")
        tree.heading("value", text="Değer")
        tree.heading("note", text="Ne işe yarar")
        tree.column("#0", width=190, anchor="w")
        tree.column("value", width=150, anchor="w")
        tree.column("note", width=230, anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)

        for row in measurements(self.metrics):
            tree.insert("", "end", text=row.label, values=(row.value, row.note))

    # -- kademeler -------------------------------------------------------
    def _build_proposals(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Mesh kademeleri - birini seçin",
                             padding=PAD // 2)
        box.grid(row=0, column=1, sticky="nsew", padx=(PAD // 2, 0))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)

        columns = ("sizes", "cells", "ram", "feature")
        self.tree = ttk.Treeview(box, columns=columns, show="tree headings",
                                 selectmode="browse", height=16)
        self.tree.heading("#0", text="Kademe")
        self.tree.heading("sizes", text="min - max hücre boyutu")
        self.tree.heading("cells", text="Tahmini hücre")
        self.tree.heading("ram", text="Bellek")
        self.tree.heading("feature", text="En küçük özellikte hücre")
        self.tree.column("#0", width=150, anchor="w")
        self.tree.column("sizes", width=210, anchor="w")
        self.tree.column("cells", width=100, anchor="e")
        self.tree.column("ram", width=80, anchor="e")
        self.tree.column("feature", width=160, anchor="e")
        self.tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.tag_configure("recommended", background="#e8f5e9")
        self.tree.tag_configure("warned", foreground="#8a6d00")

        for index, proposal in enumerate(self.proposals):
            tags = []
            if proposal.recommended:
                tags.append("recommended")
            if proposal.warnings:
                tags.append("warned")
            label = proposal.label + ("  (önerilen)" if proposal.recommended else "")
            self.tree.insert(
                "", "end", iid=str(index), text=label,
                values=(proposal.size_text(), proposal.cells_text(),
                        proposal.ram_text(),
                        "{0:.1f}".format(proposal.cells_across_feature)),
                tags=tuple(tags))

        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._on_select())
        self.tree.bind("<Double-1>", lambda _event: self._accept())

    # -- gerekçe ---------------------------------------------------------
    def _build_details(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Bu kademe ne anlama geliyor",
                             padding=PAD // 2)
        box.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(PAD, 0))
        box.columnconfigure(0, weight=1)

        self.details = tk.Text(box, height=5, wrap="word", state="disabled",
                               background="#f7f7f7", relief="flat")
        self.details.grid(row=0, column=0, sticky="ew")
        self.details.tag_configure("warn", foreground="#b00020")

    # -- düğmeler --------------------------------------------------------
    def _build_buttons(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(PAD, 0))

        ttk.Label(bar, text="Bellek tahmini kabadır (~{0:.1f} GB / milyon hücre); "
                            "makinenize göre değişir.".format(1.2),
                  foreground="#666").pack(side="left")

        ttk.Button(bar, text="Vazgeç", command=self._cancel).pack(side="right")
        self.btn_ok = ttk.Button(bar, text="Bu kademeyi seç", command=self._accept)
        self.btn_ok.pack(side="right", padx=(0, PAD))
        ttk.Button(bar, text="Otomatiğe bırak",
                   command=self._use_auto).pack(side="right", padx=(0, PAD))

    # ------------------------------------------------------------------
    def _select_default(self) -> None:
        from ..planning.proposals import recommended

        preferred = recommended(self.proposals)
        index = self.proposals.index(preferred) if preferred in self.proposals else 0
        if self.proposals:
            self.tree.selection_set(str(index))
            self.tree.focus(str(index))
        self._on_select()

    def _current(self) -> Optional[Proposal]:
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return self.proposals[int(selection[0])]
        except (ValueError, IndexError):
            return None

    def _on_select(self) -> None:
        proposal = self._current()
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        if proposal is not None:
            self.details.insert("end", proposal.rationale + "\n")
            for warning in proposal.warnings:
                self.details.insert("end", "! " + warning + "\n", "warn")
        self.details.configure(state="disabled")

    # ------------------------------------------------------------------
    def _accept(self) -> None:
        self.result = self._current()
        self._close()

    def _use_auto(self) -> None:
        self.result = None
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

    # ------------------------------------------------------------------
    def show(self) -> Optional[Proposal]:
        """Pencereyi kapanana kadar bekle ve seçimi döndür."""
        self.top.wait_window()
        return self.result
