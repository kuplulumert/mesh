"""AutoMesh - SpaceClaim geometrilerini ANSYS Fluent'te otonom olarak meshleyen agent."""

__version__ = "0.1.0"

from .config import Config  # noqa: F401
from .models import GeometryMetrics, MeshPlan, QualityReport, RunResult  # noqa: F401


def run(geometry_path, config=None, run_dir=None):
    """Mesh ``geometry_path`` autonomously and return a :class:`RunResult`."""
    from .orchestrator import run_agent

    return run_agent(geometry_path, config, run_dir)
