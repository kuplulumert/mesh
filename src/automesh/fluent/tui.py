"""Fluent Meshing TUI command catalog.

Fluent's text interface is stable in spirit but not in spelling: paths get
reorganised between releases, and some commands only exist once a mesh of a
certain type is present.  Every entry here therefore returns a *list* of
candidate spellings; :meth:`FluentDriver.execute_tui_any` runs the first one
the running build accepts.

If a command in your ANSYS version lives somewhere else, this is the only
file you need to touch.
"""

from __future__ import annotations

from typing import List, Optional, Sequence


def _fmt(value: float) -> str:
    return "{0:.6g}".format(value)


# --------------------------------------------------------------------------
# inspection
# --------------------------------------------------------------------------

def check_volume_quality() -> List[str]:
    return ["/mesh/check-quality", "/mesh/check-quality-level", "/report/quality"]


def check_mesh() -> List[str]:
    return ["/mesh/check-mesh"]


def check_boundary_mesh() -> List[str]:
    return ["/boundary/check-boundary-mesh"]


def check_face_quality() -> List[str]:
    return [
        "/boundary/improve/report-face-quality",
        "/report/face-quality-limits",
        "/boundary/check-boundary-mesh",
    ]


def quality_method(method: str = "orthoskew") -> List[str]:
    """``orthoskew`` reports orthogonal quality, ``skewness`` reports skew."""
    return ["/mesh/quality-method {0}".format(method)]


def mesh_size_info() -> List[str]:
    return ["/mesh/size-info", "/report/mesh-size"]


def domain_extents() -> List[str]:
    return ["/report/boundary-cell-quality", "/mesh/domain-extents", "/report/extents"]


# --------------------------------------------------------------------------
# surface mesh repair
# --------------------------------------------------------------------------

def boundary_improve_quality(quality_limit: float = 0.8, iterations: int = 5) -> List[str]:
    return [
        "/boundary/improve/improve () {0} {1}".format(_fmt(quality_limit), iterations),
        "/boundary/improve/improve",
    ]


def boundary_smooth(iterations: int = 5) -> List[str]:
    return [
        "/boundary/improve/smooth () {0} yes".format(iterations),
        "/boundary/improve/smooth",
    ]


def boundary_swap(quality_limit: float = 0.8, iterations: int = 5) -> List[str]:
    return [
        "/boundary/improve/swap () {0} {1}".format(_fmt(quality_limit), iterations),
        "/boundary/improve/swap",
    ]


def boundary_collapse(quality_limit: float = 0.9, iterations: int = 3) -> List[str]:
    return [
        "/boundary/improve/collapse () {0} {1}".format(_fmt(quality_limit), iterations),
        "/boundary/improve/collapse",
    ]


def boundary_degree_of_freedom(quality_limit: float = 0.9, iterations: int = 3) -> List[str]:
    return [
        "/boundary/improve/degree-of-freedom () {0} {1}".format(
            _fmt(quality_limit), iterations),
        "/boundary/improve/degree-of-freedom",
    ]


def delete_unused() -> List[str]:
    return ["/boundary/delete-unused-faces", "/boundary/delete-unused-nodes"]


def merge_nodes(tolerance: float = 0.01) -> List[str]:
    return [
        "/boundary/merge-nodes () {0}".format(_fmt(tolerance)),
        "/boundary/manage/merge-nodes",
    ]


def repair_face_handedness() -> List[str]:
    return [
        "/boundary/repair-face-handedness",
        "/mesh/repair-improve/repair-face-handedness",
    ]


def repair_self_intersections() -> List[str]:
    return [
        "/boundary/repair-self-intersections",
        "/boundary/repair-face-node-order",
    ]


def remesh_face_zones() -> List[str]:
    return [
        "/boundary/remesh/remesh-face-zones-conformally",
        "/boundary/remesh/remesh-face-zone",
    ]


def project_free_edges() -> List[str]:
    return ["/boundary/remesh/mark-intersecting-faces", "/boundary/mark-face-intersection"]


# --------------------------------------------------------------------------
# volume mesh repair
# --------------------------------------------------------------------------

def repair_improve_quality() -> List[str]:
    return [
        "/mesh/repair-improve/improve-quality",
        "/mesh/modify/auto-improve-quality",
    ]


def repair_mesh() -> List[str]:
    return ["/mesh/repair-improve/repair", "/mesh/modify/repair"]


def auto_node_move(
    quality_limit: float = 0.15,
    dihedral_angle: float = 120.0,
    iterations: int = 10,
    cell_zones: str = "*",
) -> List[str]:
    """Node smoothing driven by the worst cells.

    The positional form matches Fluent's prompt order:
    ``cell zones, quality limit, dihedral angle, iterations, boundary layers?``
    """
    base = "/mesh/modify/auto-node-move {0} () {1} {2} {3} yes".format(
        cell_zones, _fmt(quality_limit), _fmt(dihedral_angle), iterations)
    return [
        base,
        "/mesh/modify/auto-node-move",
        "/mesh/repair-improve/improve-quality",
    ]


def improve_quality_iterations(
    quality_limit: float = 0.1, iterations: int = 5
) -> List[str]:
    return [
        "/mesh/repair-improve/improve-quality {0} {1}".format(
            _fmt(quality_limit), iterations),
        "/mesh/repair-improve/improve-quality",
    ]


def report_max_skewness() -> List[str]:
    return [
        "/mesh/repair-improve/report-max-cell-skewness",
        "/mesh/check-quality",
    ]


# --------------------------------------------------------------------------
# prisms
# --------------------------------------------------------------------------

def prism_controls_stair_step(enable: bool = True) -> List[str]:
    flag = "yes" if enable else "no"
    return [
        "/mesh/prism/controls/improve/stair-step-options {0}".format(flag),
        "/mesh/prism/controls/post-ignore/post-ignore-method",
    ]


def prism_post_ignore(quality_limit: float = 0.1) -> List[str]:
    return [
        "/mesh/prism/controls/post-ignore/post-ignore-method quality "
        "{0}".format(_fmt(quality_limit)),
        "/mesh/prism/controls/post-ignore",
    ]


# --------------------------------------------------------------------------
# files
# --------------------------------------------------------------------------

def write_mesh(path: str) -> List[str]:
    quoted = path.replace("\\", "/")
    return [
        '/file/write-mesh "{0}"'.format(quoted),
        '/file/write-mesh {0}'.format(quoted),
    ]


def write_case(path: str) -> List[str]:
    quoted = path.replace("\\", "/")
    return ['/file/write-case "{0}"'.format(quoted)]


def read_mesh(path: str) -> List[str]:
    quoted = path.replace("\\", "/")
    return ['/file/read-mesh "{0}"'.format(quoted)]


def switch_to_solver() -> List[str]:
    return ["/mesh/switch-to-solution-mode yes", "/switch-to-solution-mode yes"]


# --------------------------------------------------------------------------
# named repair recipes used by the remediation engine
# --------------------------------------------------------------------------

#: Ordered surface-repair ladder: cheapest and safest first.
SURFACE_REPAIR_LADDER = (
    ("delete-unused", delete_unused),
    ("face-handedness", repair_face_handedness),
    ("improve-quality", boundary_improve_quality),
    ("smooth", boundary_smooth),
    ("swap", boundary_swap),
    ("collapse", boundary_collapse),
)

#: Ordered volume-repair ladder.
VOLUME_REPAIR_LADDER = (
    ("repair", repair_mesh),
    ("improve-quality", repair_improve_quality),
    ("auto-node-move", auto_node_move),
)
