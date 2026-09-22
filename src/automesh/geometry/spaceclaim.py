"""Head-less SpaceClaim backend.

SpaceClaim is asked to open the CAD file, measure it and (optionally)
re-export it in a format Fluent Meshing imports cleanly.  The measurement
itself happens inside ``scripts/spaceclaim_analyze.py``, which runs in
SpaceClaim's IronPython interpreter.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

from ..config import Config
from ..logging_utils import get_logger
from ..models import BodyInfo, BoundingBox, FaceGroup, GeometryMetrics
from .base import CAD_EXTENSIONS, GeometryAnalyzer, GeometryAnalyzerError, extension

SCRIPT_NAME = "spaceclaim_analyze.py"

#: Where ANSYS is normally installed on Windows.
_WINDOWS_ROOTS = (
    r"C:\Program Files\ANSYS Inc",
    r"C:\Program Files (x86)\ANSYS Inc",
    r"D:\Program Files\ANSYS Inc",
)

_EXPORT_EXTENSIONS = {
    "step": ".stp",
    "stp": ".stp",
    "iges": ".igs",
    "parasolid": ".x_t",
    "stl": ".stl",
    "scdoc": ".scdoc",
    "pmdb": ".pmdb",
}


def script_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", SCRIPT_NAME)


def _version_from_root(root: str) -> Optional[str]:
    match = re.search(r"v(\d{3})", root.replace("/", "\\"))
    return match.group(1) if match else None


def discover_spaceclaim() -> Optional[Tuple[str, str]]:
    """Return ``(exe_path, script_api_version)`` for the newest install."""
    candidates: List[Tuple[int, str, str]] = []

    # 1) AWP_ROOTxxx environment variables set by the ANSYS installer.
    for key, value in os.environ.items():
        match = re.fullmatch(r"AWP_ROOT(\d{3})", key)
        if not match or not value:
            continue
        exe = os.path.join(value, "scdm", "SpaceClaim.exe")
        if os.path.isfile(exe):
            candidates.append((int(match.group(1)), exe, match.group(1)))

    # 2) Well-known install directories.
    for root in _WINDOWS_ROOTS:
        if not os.path.isdir(root):
            continue
        for exe in glob.glob(os.path.join(root, "v*", "scdm", "SpaceClaim.exe")):
            version = _version_from_root(exe)
            if version:
                candidates.append((int(version), exe, version))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, exe, version = candidates[0]
    return exe, version


class SpaceClaimAnalyzer(GeometryAnalyzer):
    name = "spaceclaim"

    def __init__(self, exe: Optional[str] = None, script_api: Optional[str] = None) -> None:
        self._exe = exe
        self._script_api = script_api
        self._probed = False

    # ------------------------------------------------------------------
    def _probe(self, cfg: Optional[Config] = None) -> None:
        if self._probed:
            return
        self._probed = True
        if cfg is not None and cfg.geometry.spaceclaim_exe:
            self._exe = cfg.geometry.spaceclaim_exe
            self._script_api = cfg.geometry.spaceclaim_script_api
            return
        if self._exe:
            return
        found = discover_spaceclaim()
        if found:
            self._exe, self._script_api = found

    def available(self) -> bool:
        self._probe()
        return bool(self._exe and os.path.isfile(self._exe))

    def can_handle(self, path: str) -> bool:
        return extension(path) in CAD_EXTENSIONS and self.available()

    # ------------------------------------------------------------------
    def analyze(self, path: str, cfg: Config) -> GeometryMetrics:
        self._probed = False
        self._probe(cfg)
        if not self.available():
            raise GeometryAnalyzerError(
                "SpaceClaim.exe bulunamadı. geometry.spaceclaim_exe ayarını elle verin."
            )
        log = get_logger()
        workdir = tempfile.mkdtemp(prefix="automesh-scdm-")
        try:
            params_file = os.path.join(workdir, "params.json")
            output_file = os.path.join(workdir, "analysis.json")
            export_file = self._export_target(path, workdir, cfg)

            from ..planning.local_sizing import spaceclaim_params

            params = {
                "input": os.path.abspath(path),
                "output": output_file,
                "export": export_file or "",
            }
            params.update(spaceclaim_params(cfg))
            with open(params_file, "w", encoding="utf-8") as fh:
                json.dump(params, fh, indent=2)

            run_script = os.path.join(workdir, SCRIPT_NAME)
            self._materialise_script(run_script, params_file)

            cmd = [
                self._exe,
                "/Headless=True",
                "/Splash=False",
                "/Welcome=False",
                "/ExitAfterScript=True",
                "/ScriptAPI={0}".format(self._script_api or cfg.geometry.spaceclaim_script_api),
                "/RunScript={0}".format(run_script),
            ]
            env = dict(os.environ)
            env["AUTOMESH_SC_PARAMS"] = params_file
            env["AUTOMESH_SC_OUTPUT"] = output_file

            log.info("SpaceClaim başlatılıyor (headless): %s", os.path.basename(self._exe))
            log.debug("Komut: %s", " ".join(cmd))
            try:
                proc = subprocess.run(
                    cmd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=cfg.geometry.spaceclaim_timeout_s,
                )
            except subprocess.TimeoutExpired:
                raise GeometryAnalyzerError(
                    "SpaceClaim {0} saniyede yanıt vermedi (geometry.spaceclaim_timeout_s)."
                    .format(cfg.geometry.spaceclaim_timeout_s)
                )

            if not os.path.isfile(output_file):
                tail = (proc.stdout or b"").decode("utf-8", "replace")[-2000:]
                raise GeometryAnalyzerError(
                    "SpaceClaim analiz çıktısı üretmedi (çıkış kodu {0}).\n{1}"
                    .format(proc.returncode, tail)
                )
            with open(output_file, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if not raw.get("ok", False):
                raise GeometryAnalyzerError(
                    "SpaceClaim betiği hata verdi:\n{0}".format(raw.get("error", "?"))
                )
            metrics = metrics_from_raw(raw)
            exported = raw.get("exported_path")
            if exported and os.path.isfile(exported):
                # Keep the export alive after the temp dir goes away.
                final = os.path.join(
                    os.path.dirname(os.path.abspath(path)),
                    os.path.basename(exported),
                )
                if os.path.abspath(final) != os.path.abspath(exported):
                    shutil.copy2(exported, final)
                metrics.raw["exported_path"] = final
                log.info("SpaceClaim geometriyi dışa aktardı: %s", final)
            return metrics
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    # ------------------------------------------------------------------
    def _export_target(self, path: str, workdir: str, cfg: Config) -> Optional[str]:
        fmt = (cfg.geometry.export_format or "auto").lower()
        if fmt in ("none", "off"):
            return None
        if fmt == "auto":
            # Yüzey grupları üretilecekse STEP işe yaramaz: STEP named
            # selection taşımaz.  .scdoc taşır ve Fluent Meshing onu okur.
            fmt = "scdoc" if cfg.local_sizing.enabled else "step"
        ext = _EXPORT_EXTENSIONS.get(fmt)
        if ext is None:
            raise GeometryAnalyzerError("Desteklenmeyen dışa aktarım formatı: {0}".format(fmt))
        if extension(path) == ext:
            return None           # already in the target format
        stem = os.path.splitext(os.path.basename(path))[0]
        return os.path.join(workdir, stem + ext)

    def _materialise_script(self, destination: str, params_file: str) -> None:
        """Copy the template and bake the parameter path into it."""
        with open(script_path(), "r", encoding="utf-8") as fh:
            source = fh.read()
        header = "# -*- coding: utf-8 -*-\nAUTOMESH_PARAMS_FILE = r\"{0}\"\n".format(params_file)
        # Strip the template's own coding line so there is exactly one.
        body = source.split("\n", 1)[1] if source.startswith("# -*- coding") else source
        with open(destination, "w", encoding="utf-8") as fh:
            fh.write(header + body)


# --------------------------------------------------------------------------

def metrics_from_raw(raw: Dict) -> GeometryMetrics:
    """Convert the script's JSON into :class:`GeometryMetrics`."""
    metrics = GeometryMetrics(analyzer=raw.get("analyzer", "spaceclaim"))
    bbox = raw.get("bbox") or {}
    metrics.bbox = BoundingBox(
        xmin=float(bbox.get("xmin", 0.0) or 0.0),
        ymin=float(bbox.get("ymin", 0.0) or 0.0),
        zmin=float(bbox.get("zmin", 0.0) or 0.0),
        xmax=float(bbox.get("xmax", 0.0) or 0.0),
        ymax=float(bbox.get("ymax", 0.0) or 0.0),
        zmax=float(bbox.get("zmax", 0.0) or 0.0),
    )
    for key in (
        "volume", "area", "min_edge_length", "min_face_size",
        "min_curvature_radius", "thinnest_section", "curved_face_ratio",
        "small_feature_ratio",
    ):
        if raw.get(key) is not None:
            setattr(metrics, key, float(raw[key]))
    for key in ("body_count", "face_count", "edge_count", "free_edge_count"):
        if raw.get(key) is not None:
            setattr(metrics, key, int(raw[key]))
    if raw.get("watertight") is not None:
        metrics.watertight = bool(raw["watertight"])
    if raw.get("has_free_edges") is not None:
        metrics.has_free_edges = bool(raw["has_free_edges"])
    metrics.length_unit_hint = raw.get("length_unit_hint", "m")
    metrics.warnings = list(raw.get("warnings") or [])
    metrics.face_groups = [
        FaceGroup(
            name=str(g.get("name", "")),
            kind=str(g.get("kind", "")),
            driver=str(g.get("driver", "")),
            source=str(g.get("source", "auto")),
            recommended_size=float(g.get("recommended_size", 0.0) or 0.0),
            face_count=int(g.get("face_count", 0) or 0),
            min_radius=float(g.get("min_radius", 0.0) or 0.0),
            max_radius=float(g.get("max_radius", 0.0) or 0.0),
            representative_radius=float(g.get("representative_radius", 0.0) or 0.0),
            min_width=float(g.get("min_width", 0.0) or 0.0),
            min_gap=float(g.get("min_gap", 0.0) or 0.0),
            total_area=float(g.get("total_area", 0.0) or 0.0),
            min_face_size=float(g.get("min_face_size", 0.0) or 0.0),
            created=bool(g.get("created", True)),
            note=str(g.get("note", "")),
        )
        for g in (raw.get("face_groups") or [])
    ]
    metrics.bodies = [
        BodyInfo(
            name=str(b.get("name", "")),
            volume=float(b.get("volume", 0.0) or 0.0),
            area=float(b.get("area", 0.0) or 0.0),
            face_count=int(b.get("face_count", 0) or 0),
            edge_count=int(b.get("edge_count", 0) or 0),
            is_solid=bool(b.get("is_solid", True)),
            min_face_area=float(b.get("min_face_area", 0.0) or 0.0),
            min_edge_length=float(b.get("min_edge_length", 0.0) or 0.0),
        )
        for b in (raw.get("bodies") or [])
    ]
    metrics.raw = {k: v for k, v in raw.items()
                   if k not in ("bodies", "face_groups")}
    return metrics
