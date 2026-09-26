"""Unit helpers.

All internal geometry is metric (Blender units = metres). Airfield standards are
written in feet/inches, so the standards tables use these helpers to stay readable
and auditable against the source documents (UFC 3-260-01, UFC 3-535-01,
FAA AC 150/5340-1, ICAO Annex 14).
"""

FT = 0.3048          # 1 international foot in metres
IN = 0.0254          # 1 inch in metres
NM = 1852.0          # nautical mile in metres


def ft(value: float) -> float:
    """Feet -> metres."""
    return value * FT


def inch(value: float) -> float:
    """Inches -> metres."""
    return value * IN


def m_to_ft(value: float) -> float:
    return value / FT


def fmt_len(metres: float, imperial: bool = False) -> str:
    """Human readable length for UI labels."""
    if imperial:
        return f"{metres / FT:,.0f} ft"
    return f"{metres:,.1f} m"
