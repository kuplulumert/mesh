"""Length-unit helpers.

Everything inside AutoMesh is stored in **metres**.  Fluent's meshing
workflow, on the other hand, expresses sizes in whatever unit the geometry
was imported with, so the plan is converted right before the arguments are
handed to Fluent.
"""

from __future__ import annotations

# Fluent's ``LengthUnit`` strings -> metres per unit.
_TO_METRES = {
    "m": 1.0,
    "cm": 1.0e-2,
    "mm": 1.0e-3,
    "um": 1.0e-6,
    "nm": 1.0e-9,
    "in": 0.0254,
    "ft": 0.3048,
}

#: Units Fluent's watertight workflow accepts for ``LengthUnit``.
FLUENT_LENGTH_UNITS = ("m", "cm", "mm", "um", "nm", "in", "ft")

_ALIASES = {
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "centimeter": "cm",
    "centimeters": "cm",
    "millimeter": "mm",
    "millimeters": "mm",
    "micron": "um",
    "microns": "um",
    "micrometer": "um",
    "µm": "um",
    "inch": "in",
    "inches": "in",
    "foot": "ft",
    "feet": "ft",
}


def normalise(unit: str) -> str:
    """Return the canonical Fluent spelling of ``unit``."""
    key = (unit or "").strip().lower()
    key = _ALIASES.get(key, key)
    if key not in _TO_METRES:
        raise ValueError(
            "unsupported length unit {0!r}; expected one of {1}".format(
                unit, ", ".join(FLUENT_LENGTH_UNITS)
            )
        )
    return key


def to_metres(value: float, unit: str) -> float:
    """Convert ``value`` expressed in ``unit`` into metres."""
    return value * _TO_METRES[normalise(unit)]


def from_metres(value: float, unit: str) -> float:
    """Convert ``value`` expressed in metres into ``unit``."""
    return value / _TO_METRES[normalise(unit)]


def pick_working_unit(bbox_diagonal_m: float) -> str:
    """Choose a sensible import unit for a model of the given size.

    Keeping the numbers Fluent sees in a human range makes the transcript far
    easier to read and avoids silly float formatting in the journal.
    """
    if bbox_diagonal_m <= 0:
        return "m"
    if bbox_diagonal_m < 1.0e-3:
        return "um"
    if bbox_diagonal_m < 2.0:
        return "mm"
    return "m"


def format_length(value_m: float, unit: str) -> str:
    """Human readable ``value`` (metres) rendered in ``unit``."""
    return "{0:.6g} {1}".format(from_metres(value_m, unit), normalise(unit))
