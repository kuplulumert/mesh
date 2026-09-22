"""Tessellated-geometry backend (STL / OBJ / PLY).

``trimesh`` is used when it is installed because it handles every format and
does the watertightness bookkeeping properly.  Without it, a small built-in
STL reader keeps the tool usable on a bare Python install - which matters,
because STL is what most people export when a CAD licence is not at hand.
"""

from __future__ import annotations

import math
import struct
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..config import Config
from ..logging_utils import get_logger
from ..models import BodyInfo, BoundingBox, GeometryMetrics
from .base import (
    DISCRETE_EXTENSIONS,
    GeometryAnalyzer,
    GeometryAnalyzerError,
    extension,
    percentile,
)

MAX_TRIANGLES = 4_000_000
Vec = Tuple[float, float, float]
Tri = Tuple[Vec, Vec, Vec]


class DiscreteAnalyzer(GeometryAnalyzer):
    name = "discrete"

    def available(self) -> bool:
        return True

    def can_handle(self, path: str) -> bool:
        return extension(path) in DISCRETE_EXTENSIONS

    def analyze(self, path: str, cfg: Config) -> GeometryMetrics:
        ext = extension(path)
        triangles: Optional[List[Tri]] = None
        trimesh_metrics: Optional[Dict] = None

        try:
            trimesh_metrics = _analyze_with_trimesh(path)
        except ImportError:
            trimesh_metrics = None
        except Exception as exc:  # corrupt file, unsupported variant, ...
            get_logger().debug("trimesh analizi başarısız: %s", exc)
            trimesh_metrics = None

        if trimesh_metrics is not None:
            return _metrics_from_dict(trimesh_metrics, analyzer="discrete(trimesh)")

        if ext != ".stl":
            raise GeometryAnalyzerError(
                "{0} dosyalarını okumak için trimesh gerekli: pip install trimesh"
                .format(ext or "bu")
            )
        triangles = read_stl(path)
        return _metrics_from_dict(analyze_triangles(triangles), analyzer="discrete(stl)")


# --------------------------------------------------------------------------
# readers
# --------------------------------------------------------------------------

def read_stl(path: str) -> List[Tri]:
    """Read a binary or ASCII STL into a triangle list."""
    with open(path, "rb") as fh:
        head = fh.read(84)
        if len(head) < 84:
            raise GeometryAnalyzerError("STL dosyası çok kısa: {0}".format(path))
        count = struct.unpack("<I", head[80:84])[0]
        fh.seek(0, 2)
        size = fh.tell()
        binary_size = 84 + count * 50
        if count > 0 and size == binary_size:
            fh.seek(84)
            return _read_binary_stl(fh, count)
    return _read_ascii_stl(path)


def _read_binary_stl(fh, count: int) -> List[Tri]:
    if count > MAX_TRIANGLES:
        raise GeometryAnalyzerError(
            "STL {0} üçgen içeriyor (sınır {1}); trimesh kurun veya modeli sadeleştirin."
            .format(count, MAX_TRIANGLES)
        )
    data = fh.read(count * 50)
    triangles: List[Tri] = []
    unpack = struct.Struct("<12fH").unpack_from
    for i in range(count):
        values = unpack(data, i * 50)
        triangles.append((
            (values[3], values[4], values[5]),
            (values[6], values[7], values[8]),
            (values[9], values[10], values[11]),
        ))
    return triangles


def _read_ascii_stl(path: str) -> List[Tri]:
    triangles: List[Tri] = []
    current: List[Vec] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped.startswith("vertex"):
                continue
            parts = stripped.split()
            if len(parts) < 4:
                continue
            current.append((float(parts[1]), float(parts[2]), float(parts[3])))
            if len(current) == 3:
                triangles.append((current[0], current[1], current[2]))
                current = []
                if len(triangles) > MAX_TRIANGLES:
                    raise GeometryAnalyzerError("STL çok büyük, trimesh kurun.")
    if not triangles:
        raise GeometryAnalyzerError("STL içinde üçgen bulunamadı: {0}".format(path))
    return triangles


# --------------------------------------------------------------------------
# maths
# --------------------------------------------------------------------------

def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: Vec) -> float:
    return math.sqrt(_dot(a, a))


def _dist(a: Vec, b: Vec) -> float:
    return _norm(_sub(a, b))


def _key(v: Vec, scale: float) -> Tuple[int, int, int]:
    """Quantised vertex key for edge matching (welds numerical noise)."""
    q = scale if scale > 0 else 1.0
    return (int(round(v[0] / q)), int(round(v[1] / q)), int(round(v[2] / q)))


def analyze_triangles(triangles: Sequence[Tri]) -> Dict:
    """Derive geometry metrics from a raw triangle soup."""
    if not triangles:
        raise GeometryAnalyzerError("Boş tessellated geometri.")

    xs = [v[0] for t in triangles for v in t]
    ys = [v[1] for t in triangles for v in t]
    zs = [v[2] for t in triangles for v in t]
    bbox = (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))
    diag = math.sqrt(sum((bbox[i + 3] - bbox[i]) ** 2 for i in range(3)))
    weld = max(diag * 1.0e-7, 1.0e-12)

    area = 0.0
    volume6 = 0.0
    edge_lengths: List[float] = []
    normals: List[Vec] = []
    edge_map: Dict[Tuple[Tuple[int, int, int], Tuple[int, int, int]], List[int]] = {}

    for index, (a, b, c) in enumerate(triangles):
        ab, ac = _sub(b, a), _sub(c, a)
        n = _cross(ab, ac)
        tri_area = 0.5 * _norm(n)
        area += tri_area
        volume6 += _dot(a, _cross(b, c))
        length = _norm(n)
        normals.append((n[0] / length, n[1] / length, n[2] / length) if length > 0 else (0.0, 0.0, 0.0))
        for p, q in ((a, b), (b, c), (c, a)):
            edge_lengths.append(_dist(p, q))
            ka, kb = _key(p, weld), _key(q, weld)
            ekey = (ka, kb) if ka <= kb else (kb, ka)
            edge_map.setdefault(ekey, []).append(index)

    volume = abs(volume6) / 6.0

    boundary_edges = sum(1 for owners in edge_map.values() if len(owners) == 1)
    non_manifold = sum(1 for owners in edge_map.values() if len(owners) > 2)
    watertight = boundary_edges == 0 and non_manifold == 0

    # Curvature estimate: how many shared edges are "soft" (small dihedral
    # angle) rather than sharp corners.
    curved_edges = 0
    shared_edges = 0
    for owners in edge_map.values():
        if len(owners) != 2:
            continue
        shared_edges += 1
        n1, n2 = normals[owners[0]], normals[owners[1]]
        cos_angle = max(-1.0, min(1.0, _dot(n1, n2)))
        angle = math.degrees(math.acos(cos_angle))
        if 0.5 < angle < 60.0:
            curved_edges += 1

    mean_edge = sum(edge_lengths) / len(edge_lengths) if edge_lengths else 0.0
    small_edges = sum(1 for e in edge_lengths if 0 < e < mean_edge * 0.1)

    return {
        "bbox": bbox,
        "volume": volume,
        "area": area,
        "triangle_count": len(triangles),
        "face_count": len(triangles),
        "edge_count": len(edge_map),
        "min_edge_length": percentile([e for e in edge_lengths if e > 0], 0.02),
        "min_face_size": math.sqrt(2.0 * area / len(triangles)) if area > 0 else 0.0,
        "thinnest_section": (2.0 * volume / area) if area > 0 and volume > 0 else 0.0,
        "curved_face_ratio": (curved_edges / shared_edges) if shared_edges else 0.0,
        "small_feature_ratio": (small_edges / len(edge_lengths)) if edge_lengths else 0.0,
        "watertight": watertight,
        "has_free_edges": boundary_edges > 0,
        "free_edge_count": boundary_edges,
        "non_manifold_edge_count": non_manifold,
        "body_count": 1,
    }


def _analyze_with_trimesh(path: str) -> Dict:
    import trimesh  # type: ignore

    loaded = trimesh.load(path, force="mesh", process=False)
    if loaded is None or not hasattr(loaded, "triangles"):
        raise GeometryAnalyzerError("trimesh geometriyi okuyamadı: {0}".format(path))
    mesh = loaded
    bounds = mesh.bounds
    bbox = (
        float(bounds[0][0]), float(bounds[0][1]), float(bounds[0][2]),
        float(bounds[1][0]), float(bounds[1][1]), float(bounds[1][2]),
    )
    area = float(mesh.area)
    volume = float(abs(mesh.volume)) if mesh.is_volume else 0.0
    edges = mesh.edges_unique_length
    edge_list = [float(e) for e in edges]
    mean_edge = (sum(edge_list) / len(edge_list)) if edge_list else 0.0
    small_edges = sum(1 for e in edge_list if 0 < e < mean_edge * 0.1)

    curved_ratio = 0.0
    try:
        angles = mesh.face_adjacency_angles
        if len(angles):
            curved = sum(1 for a in angles if 0.0087 < float(a) < 1.047)  # 0.5deg..60deg
            curved_ratio = curved / float(len(angles))
    except Exception:
        pass

    try:
        free_edges = int(len(mesh.edges_sorted) - len(mesh.edges_unique) * 2 + len(mesh.edges_unique))
    except Exception:
        free_edges = 0

    return {
        "bbox": bbox,
        "volume": volume,
        "area": area,
        "triangle_count": int(len(mesh.faces)),
        "face_count": int(len(mesh.faces)),
        "edge_count": int(len(mesh.edges_unique)),
        "min_edge_length": percentile(edge_list, 0.02),
        "min_face_size": math.sqrt(2.0 * area / len(mesh.faces)) if area > 0 and len(mesh.faces) else 0.0,
        "thinnest_section": (2.0 * volume / area) if area > 0 and volume > 0 else 0.0,
        "curved_face_ratio": curved_ratio,
        "small_feature_ratio": (small_edges / len(edge_list)) if edge_list else 0.0,
        "watertight": bool(mesh.is_watertight),
        "has_free_edges": not bool(mesh.is_watertight),
        "free_edge_count": max(free_edges, 0),
        "body_count": int(mesh.body_count) if hasattr(mesh, "body_count") else 1,
    }


def _metrics_from_dict(data: Dict, analyzer: str) -> GeometryMetrics:
    bbox = data["bbox"]
    metrics = GeometryMetrics(analyzer=analyzer)
    metrics.bbox = BoundingBox(*[float(v) for v in bbox])
    for key in (
        "volume", "area", "min_edge_length", "min_face_size",
        "thinnest_section", "curved_face_ratio", "small_feature_ratio",
    ):
        if data.get(key) is not None:
            setattr(metrics, key, float(data[key]))
    for key in ("body_count", "face_count", "edge_count", "free_edge_count"):
        if data.get(key) is not None:
            setattr(metrics, key, int(data[key]))
    metrics.watertight = data.get("watertight")
    metrics.has_free_edges = data.get("has_free_edges")
    metrics.bodies = [
        BodyInfo(
            name="tessellated",
            volume=metrics.volume,
            area=metrics.area,
            face_count=metrics.face_count,
            is_solid=bool(metrics.watertight),
        )
    ]
    metrics.raw = {k: v for k, v in data.items() if k != "bbox"}
    if data.get("non_manifold_edge_count"):
        metrics.warnings.append(
            "{0} adet manifold olmayan kenar var; fault-tolerant akış önerilir."
            .format(data["non_manifold_edge_count"])
        )
    if metrics.has_free_edges:
        metrics.warnings.append(
            "Yüzey ağı su geçirmez değil ({0} serbest kenar)."
            .format(metrics.free_edge_count)
        )
    return metrics
