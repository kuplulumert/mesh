"""Fluent Meshing integration."""

from .driver import CommandResult, FluentDriver, FluentError  # noqa: F401
from .workflows import WorkflowRunner  # noqa: F401


def build_driver(cfg, work_dir):
    """Pick the real driver or the simulator according to the config."""
    if cfg.fluent.use_mock:
        from .mock_driver import MockFluentDriver

        return MockFluentDriver(cfg, work_dir)
    from .pyfluent_driver import PyFluentDriver

    return PyFluentDriver(cfg, work_dir)
