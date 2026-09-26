"""Painted runway / taxiway characters as 2D polygons.

Two fonts are provided:

``RUNWAY``
    Runway designation numerals and letters after FAA AC 150/5340-1M Figure A-6
    (identical to UFC 3-260-04 Figure 5-6).  Coordinates are in **feet**; the
    nominal box is 20 x 60 ft (``1`` is 7 ft wide, ``4`` 25 ft, ``7`` 23 ft),
    vertical strokes are 5 ft, horizontal strokes 10 ft.  ``6`` and ``9`` carry
    the extra 3 ft tip above / below the 60 ft box ("Rule of 69").  Every
    dimension was taken from the dimensioned figure, so the glyphs are the
    square-cornered block characters of the standard (the only slanted
    features are the ``2``/``4``/``7`` diagonals, the ``3`` chevron, the
    ``6``/``9`` tips, the ``8`` waist notches and the ``R`` leg).

``SIGN``
    Block font for surface painted signs (taxiway location / direction / hold
    signs, "ILS", "NO ENTRY", geographic position markings) after FAA AC
    150/5340-1M Appendix B (= UFC 3-260-04 Figures 6-13 .. 6-15).  Coordinates
    are in **grid units** with the character height = 20 units (unit = height /
    20).  The characters of the standard are strongly condensed (an ``H`` is
    about 5.5 units wide, the stroke ~1.3 units): they are elongated on purpose
    so that they read correctly from a cockpit at a grazing angle.  This module
    keeps those proportions (stroke 1.5 units) and approximates the curved
    bowls with 45-degree chamfers.

Both fonts are built from a tiny polygon DSL (:func:`rect`,
:func:`chamfered_rect`, :func:`poly`, :func:`ring`, :func:`open_ring`,
:func:`slant`).  Strokes may overlap - the consumer takes the union (see
``core.geom2d.union_triangulate``).  All polygons are counter-clockwise.

The module is pure Python (no ``bpy`` / ``mathutils``) so it can be unit tested
anywhere.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

Point = tuple[float, float]
Poly = list[Point]

__all__ = [
    "Point", "Poly", "Glyph",
    "runway_glyph", "runway_standalone_one", "sign_glyph", "layout_text",
    "runway_designator_text",
    "ALL_RUNWAY_CHARS", "ALL_SIGN_CHARS", "ARROW_NAMES",
    "RUNWAY_HEIGHT", "RUNWAY_GAP", "RUNWAY_GAP_11", "SIGN_HEIGHT", "SIGN_GAP", "SIGN_STROKE",
    "rect", "chamfered_rect", "poly", "ring", "open_ring", "slant", "para",
]

# --------------------------------------------------------------------------- constants
RUNWAY_HEIGHT = 60.0      # ft, nominal character height (6/9 tip is outside the box)
RUNWAY_GAP = 15.0         # ft between adjacent characters (near edge to near edge)
RUNWAY_GAP_11 = 27.0      # ft between the two numerals of "11"
RUNWAY_ONE_TIP = 3.0      # ft, extra tip of 6 / 9

SIGN_HEIGHT = 20.0        # grid units
SIGN_GAP = 3.0            # grid units between adjacent characters
SIGN_STROKE = 1.5         # grid units
SIGN_SPACE_ADVANCE = 5.0  # grid units (word space)

ALL_RUNWAY_CHARS = "0123456789LCR"
ARROW_NAMES = ("ARROW_UP", "ARROW_UP_RIGHT", "ARROW_RIGHT", "ARROW_DOWN_RIGHT",
               "ARROW_DOWN", "ARROW_DOWN_LEFT", "ARROW_LEFT", "ARROW_UP_LEFT")
ALL_SIGN_CHARS = tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-./ ") + ARROW_NAMES

_SQRT2 = math.sqrt(2.0)
_EPS = 1e-9


# --------------------------------------------------------------------------- polygon DSL
def _signed_area(pts: Sequence[Point]) -> float:
    a = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return 0.5 * a


def _clean(pts: Iterable[Point]) -> Poly:
    """Drop consecutive duplicate points (including last == first)."""
    out: Poly = []
    for p in pts:
        q = (float(p[0]), float(p[1]))
        if out and abs(out[-1][0] - q[0]) < _EPS and abs(out[-1][1] - q[1]) < _EPS:
            continue
        out.append(q)
    while len(out) > 1 and abs(out[0][0] - out[-1][0]) < _EPS and abs(out[0][1] - out[-1][1]) < _EPS:
        out.pop()
    return out


def poly(pts: Iterable[Point]) -> Poly:
    """Polygon from points in any orientation; returned counter-clockwise and cleaned."""
    p = _clean(pts)
    if len(p) >= 3 and _signed_area(p) < 0.0:
        p.reverse()
    return p


def rect(x0: float, y0: float, x1: float, y1: float) -> Poly:
    """Axis-aligned rectangle (corners in any order), CCW."""
    xa, xb = (x0, x1) if x0 <= x1 else (x1, x0)
    ya, yb = (y0, y1) if y0 <= y1 else (y1, y0)
    return [(float(xa), float(ya)), (float(xb), float(ya)), (float(xb), float(yb)), (float(xa), float(yb))]


def chamfered_rect(x0: float, y0: float, x1: float, y1: float,
                   tl: float = 0.0, tr: float = 0.0, br: float = 0.0, bl: float = 0.0) -> Poly:
    """Rectangle with 45-degree cuts of the given size on each corner (CCW).

    Chamfers are clamped so that they never exceed half of the shorter side.
    """
    xa, xb = (x0, x1) if x0 <= x1 else (x1, x0)
    ya, yb = (y0, y1) if y0 <= y1 else (y1, y0)
    lim = 0.5 * min(xb - xa, yb - ya)
    tl, tr, br, bl = (max(0.0, min(c, lim)) for c in (tl, tr, br, bl))
    pts = [
        (xa + bl, ya), (xb - br, ya), (xb, ya + br),
        (xb, yb - tr), (xb - tr, yb), (xa + tl, yb),
        (xa, yb - tl), (xa, ya + bl),
    ]
    return poly(pts)


def _inner_chamfer(c_outer: float, t: float) -> float:
    """Chamfer of the inner (offset by t) corner that keeps the stroke thickness = t."""
    return max(0.0, c_outer - t * (2.0 - _SQRT2))


def ring(x0: float, y0: float, x1: float, y1: float, t: float,
         tl: float = 0.0, tr: float = 0.0, br: float = 0.0, bl: float = 0.0) -> tuple[Poly, Poly]:
    """Closed bowl: (outer chamfered rect, inner hole) with a constant stroke ``t``.

    Inner chamfers are derived from the outer ones so that the diagonal part of
    the stroke is never thinner than ``t`` (and never wider than the counter
    allows).
    """
    outer = chamfered_rect(x0, y0, x1, y1, tl, tr, br, bl)
    ix0, iy0, ix1, iy1 = x0 + t, y0 + t, x1 - t, y1 - t
    inner = chamfered_rect(ix0, iy0, ix1, iy1,
                           _inner_chamfer(tl, t), _inner_chamfer(tr, t),
                           _inner_chamfer(br, t), _inner_chamfer(bl, t))
    return outer, inner


def _open_ring_right(x0: float, y0: float, x1: float, y1: float, t: float, a: float, b: float,
                     tl: float, tr: float, br: float, bl: float) -> Poly:
    """Ring with the right side missing between y = a and y = b (single CCW polygon)."""
    a = max(a, y0 + t)
    b = min(b, y1 - t)
    if b <= a:
        raise ValueError("open_ring: empty opening")
    lim = 0.5 * min(x1 - x0, y1 - y0)
    tl, tr, br, bl = (max(0.0, min(c, lim)) for c in (tl, tr, br, bl))
    tr = min(tr, y1 - b)          # outer chamfer must fit on the terminal
    br = min(br, a - y0)
    ci_tl, ci_tr, ci_br, ci_bl = (_inner_chamfer(c, t) for c in (tl, tr, br, bl))
    iw, ih = (x1 - x0) - 2 * t, (y1 - y0) - 2 * t
    ilim = 0.5 * min(iw, ih)
    ci_tl, ci_tr, ci_br, ci_bl = (min(c, ilim) for c in (ci_tl, ci_tr, ci_br, ci_bl))
    ci_tr = min(ci_tr, (y1 - t) - b)
    ci_br = min(ci_br, a - (y0 + t))
    xi0, yi0, xi1, yi1 = x0 + t, y0 + t, x1 - t, y1 - t
    pts = [
        (x1, b), (x1, y1 - tr), (x1 - tr, y1), (x0 + tl, y1), (x0, y1 - tl),
        (x0, y0 + bl), (x0 + bl, y0), (x1 - br, y0), (x1, y0 + br), (x1, a),
        (xi1, a), (xi1, yi0 + ci_br), (xi1 - ci_br, yi0), (xi0 + ci_bl, yi0),
        (xi0, yi0 + ci_bl), (xi0, yi1 - ci_tl), (xi0 + ci_tl, yi1), (xi1 - ci_tr, yi1),
        (xi1, yi1 - ci_tr), (xi1, b),
    ]
    return poly(pts)


def open_ring(x0: float, y0: float, x1: float, y1: float, t: float, side: str, a: float, b: float,
              tl: float = 0.0, tr: float = 0.0, br: float = 0.0, bl: float = 0.0) -> Poly:
    """Bowl with an opening in one side: a C / U / S-part / arch as ONE polygon (no hole).

    ``side`` is 'R', 'L', 'T' or 'B'; the opening spans ``a..b`` along that side
    (y coordinates for L/R, x coordinates for T/B).  The remaining parts of the
    open side become terminals.  Chamfers are named by the corners of the
    un-rotated rectangle.
    """
    side = side.upper()
    if side == "R":
        return _open_ring_right(x0, y0, x1, y1, t, a, b, tl, tr, br, bl)
    if side == "L":
        p = _open_ring_right(x0, y0, x1, y1, t, a, b, tr, tl, bl, br)
        s = x0 + x1
        return poly([(s - x, y) for x, y in p])
    if side == "T":
        # transpose: original top side -> right side of the transposed rectangle
        p = _open_ring_right(y0, x0, y1, x1, t, a, b, br, tr, tl, bl)
        return poly([(y, x) for x, y in p])
    if side == "B":
        p = _open_ring_right(y0, x0, y1, x1, t, a, b, bl, tl, tr, br)   # transposed 'L'
        s = y0 + y1
        return poly([(y, s - x) for x, y in p])        # mirror in x', then transpose
    raise ValueError(f"open_ring: bad side {side!r}")


def slant(x_bot: float, y_bot: float, x_top: float, y_top: float, t: float, anchor: str = "L") -> Poly:
    """Slanted stroke of perpendicular thickness ``t`` with horizontal end cuts.

    The given line from (x_bot, y_bot) to (x_top, y_top) is the *left* edge of
    the stroke (``anchor='L'``) or its *right* edge (``anchor='R'``); the other
    edge is offset horizontally by t / sin(angle) so that the true thickness is
    ``t``.  Anchoring the edge that touches the glyph box keeps advances exact.
    """
    dx, dy = x_top - x_bot, y_top - y_bot
    length = math.hypot(dx, dy)
    if length < _EPS or abs(dy) < _EPS:
        raise ValueError("slant: degenerate stroke")
    w = t * length / abs(dy)
    if anchor.upper() == "R":
        w = -w
    return poly([(x_bot, y_bot), (x_bot + w, y_bot), (x_top + w, y_top), (x_top, y_top)])


def para(xb0: float, xb1: float, yb: float, xt0: float, xt1: float, yt: float) -> Poly:
    """Parallelogram-like stroke from a bottom segment (xb0..xb1 at yb) to a top segment.

    Used where both ends of a slanted stroke must land exactly on the glyph box.
    """
    return poly([(xb0, yb), (xb1, yb), (xt1, yt), (xt0, yt)])


def _rotate(pts: Sequence[Point], angle_deg: float, cx: float, cy: float) -> Poly:
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in pts]


def _bbox(polys: Sequence[Sequence[Point]]) -> tuple[float, float, float, float]:
    xs = [p[0] for poly_ in polys for p in poly_]
    ys = [p[1] for poly_ in polys for p in poly_]
    return min(xs), min(ys), max(xs), max(ys)


# --------------------------------------------------------------------------- Glyph
class Glyph:
    """A painted character: filled polygons, subtracted holes, advance and height."""

    __slots__ = ("positives", "holes", "advance", "height", "name")

    def __init__(self, positives: Sequence[Sequence[Point]], holes: Sequence[Sequence[Point]] = (),
                 advance: float = 0.0, height: float = 0.0, name: str = ""):
        self.positives: list[Poly] = [poly(p) for p in positives]
        self.holes: list[Poly] = [poly(h) for h in holes]
        self.advance = float(advance)
        self.height = float(height)
        self.name = name

    def scaled(self, s: float) -> "Glyph":
        return Glyph([[(x * s, y * s) for x, y in p] for p in self.positives],
                     [[(x * s, y * s) for x, y in h] for h in self.holes],
                     self.advance * s, self.height * s, self.name)

    def translated(self, dx: float, dy: float) -> "Glyph":
        return Glyph([[(x + dx, y + dy) for x, y in p] for p in self.positives],
                     [[(x + dx, y + dy) for x, y in h] for h in self.holes],
                     self.advance, self.height, self.name)

    def rotated(self, angle_deg: float, cx: float = 0.0, cy: float = 0.0) -> "Glyph":
        return Glyph([_rotate(p, angle_deg, cx, cy) for p in self.positives],
                     [_rotate(h, angle_deg, cx, cy) for h in self.holes],
                     self.advance, self.height, self.name)

    def bbox(self) -> tuple[float, float, float, float]:
        return _bbox(self.positives)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Glyph({self.name!r}, adv={self.advance:g}, h={self.height:g}, {len(self.positives)}+/{len(self.holes)}-)"


# --------------------------------------------------------------------------- runway font
def _runway_defs() -> dict[str, tuple[list[Poly], list[Poly], float]]:
    R = rect
    d: dict[str, tuple[list[Poly], list[Poly], float]] = {}
    # 0: plain rectangular ring, 5 ft sides, 10 ft top/bottom
    d["0"] = ([R(0, 0, 20, 60)], [R(5, 10, 15, 50)], 20)
    # 1: 5 ft stem + 2 ft flag at the top left (flag 10 ft tall, top-left corner chamfered)
    d["1"] = ([R(2, 0, 7, 60), poly([(0, 50), (2, 50), (2, 60), (0, 58)])], [], 7)
    # 2: top bar, 6 ft stub hanging at the left, right stem down to 41, diagonal (5 ft wide
    #    horizontally) from (15..20, 41) to (0..5, 17), 7 ft column above the bottom bar
    d["2"] = ([R(0, 50, 20, 60), R(0, 44, 5, 50), R(15, 41, 20, 60),
               poly([(0, 17), (5, 17), (20, 41), (15, 41)]), R(0, 10, 5, 17), R(0, 0, 20, 10)], [], 20)
    # 3: top / bottom bar, right stem, chevron arms (5 ft horizontal width) meeting 9 ft
    #    left of the stem's inner face (x = 6) at y = 36; arms reach the stem at 29 / 43
    d["3"] = ([R(0, 50, 20, 60), R(0, 0, 20, 10), R(15, 0, 20, 60),
               poly([(6, 36), (11, 36), (20, 43), (20, 46.9)]),
               poly([(6, 36), (20, 25.1), (20, 29), (11, 36)])], [], 20)
    # 4: open-top four, 25 ft wide: stem 15..20 up to 42, crossbar 13..23 over 25 ft,
    #    diagonal from (0..5, 23) to (10..15, 60)
    d["4"] = ([R(15, 0, 20, 42), R(0, 13, 25, 23), poly([(0, 23), (5, 23), (15, 60), (10, 60)])], [], 25)
    # 5
    d["5"] = ([R(0, 50, 20, 60), R(0, 40, 5, 50), R(0, 30, 20, 40), R(15, 0, 20, 40), R(0, 0, 20, 10)], [], 20)
    # 6: bowl 0..37 (hole 10..27), stem to 50, 13 ft tip rising to (15, 63)
    d["6"] = ([R(0, 0, 20, 37), R(0, 37, 5, 50), poly([(0, 50), (5, 50), (15, 58.667), (15, 63)])],
              [R(5, 10, 15, 27)], 20)
    # 7: 23 ft wide, slanted stroke 5 ft wide horizontally from (2..7, 0) to (18..23, 60);
    #    the top bar's right end follows the slant
    d["7"] = ([R(0, 50, 18, 60), poly([(2, 0), (7, 0), (23, 60), (18, 60)])], [], 23)
    # 8: holes 10..29 and 39..50, 45-degree waist notches 3 ft deep centred on y = 34
    d["8"] = ([poly([(0, 0), (20, 0), (20, 31), (17, 34), (20, 37), (20, 60),
                     (0, 60), (0, 37), (3, 34), (0, 31)])],
              [R(5, 10, 15, 29), R(5, 39, 15, 50)], 20)
    # 9: the 6 rotated by 180 degrees about the box centre (tip goes 3 ft below the baseline)
    six_p, six_h, _ = d["6"]
    d["9"] = ([[(20 - x, 60 - y) for x, y in p] for p in six_p],
              [[(20 - x, 60 - y) for x, y in h] for h in six_h], 20)
    # letters
    d["L"] = ([R(0, 0, 5, 60), R(0, 0, 20, 10)], [], 20)
    d["C"] = ([R(0, 0, 5, 60), R(0, 50, 20, 60), R(0, 0, 20, 10), R(15, 46, 20, 50), R(15, 10, 20, 14)], [], 20)
    # R: bowl 25..60 with hole 35..50; leg 5 ft wide starting 5 ft right of the stem under the
    #    bar (x 10..15 at y = 25) and landing on the bottom-right corner (x 15..20)
    d["R"] = ([R(0, 0, 5, 60), R(0, 25, 20, 60), poly([(15, 0), (20, 0), (15, 25), (10, 25)])],
              [R(5, 35, 15, 50)], 20)
    return d


_RUNWAY: dict[str, tuple[list[Poly], list[Poly], float]] = _runway_defs()


def runway_glyph(ch: str) -> Glyph:
    """Runway designator character (``0``-``9``, ``L``, ``C``, ``R``) in feet, origin bottom-left."""
    key = ch.upper()
    try:
        positives, holes, advance = _RUNWAY[key]
    except KeyError:
        raise KeyError(f"no runway glyph for {ch!r} (use one of {ALL_RUNWAY_CHARS})") from None
    return Glyph(positives, holes, advance, RUNWAY_HEIGHT, key)


def runway_standalone_one() -> Glyph:
    """The numeral ``1`` used alone (single-digit runway "1" without leading zero).

    Per FAA Fig A-6 note 5 / UFC Fig 5-6 note 3 it sits on a horizontal bar so it
    is not mistaken for the centreline: 20 x 10 ft foot, stem centred on it, the
    whole character still 60 ft tall.
    """
    positives = [rect(0, 0, 20, 10), rect(7.5, 10, 12.5, 60),
                 poly([(5.5, 50), (7.5, 50), (7.5, 60), (5.5, 58)])]
    return Glyph(positives, [], 20.0, RUNWAY_HEIGHT, "1_ALONE")


# --------------------------------------------------------------------------- sign font
def _sign_defs() -> dict[str, tuple[list[Poly], list[Poly], float]]:
    t = SIGN_STROKE
    H = SIGN_HEIGHT
    R = rect
    d: dict[str, tuple[list[Poly], list[Poly], float]] = {}

    def bowl(x0, y0, x1, y1, tl=0.0, tr=0.0, br=0.0, bl=0.0):
        return ring(x0, y0, x1, y1, t, tl, tr, br, bl)

    # ---- letters -------------------------------------------------------------------
    # A: two legs meeting in a flat apex, crossbar at 1/4 height
    d["A"] = ([slant(0, 0, 2.5, H, t), slant(6.5, 0, 4.0, H, t, "R"), R(1.0, 5.0, 5.5, 6.5)], [], 6.5)
    o, i = bowl(0, 9.25, 4.6, H, tr=1.5, br=1.5)
    o2, i2 = bowl(0, 0, 5.0, 10.75, tr=1.5, br=1.5)
    d["B"] = ([R(0, 0, t, H), o, o2], [i, i2], 5.0)
    d["C"] = ([open_ring(0, 0, 5.0, H, t, "R", 4.0, 16.0, 2, 2, 2, 2)], [], 5.0)
    o, i = bowl(0, 0, 5.0, H, tr=2.5, br=2.5)
    d["D"] = ([o], [i], 5.0)
    d["E"] = ([R(0, 0, t, H), R(0, H - t, 5.0, H), R(0, 9.25, 4.4, 10.75), R(0, 0, 5.0, t)], [], 5.0)
    d["F"] = ([R(0, 0, t, H), R(0, H - t, 5.0, H), R(0, 9.25, 4.4, 10.75)], [], 5.0)
    d["G"] = ([open_ring(0, 0, 5.5, H, t, "R", 9.5, 16.0, 2, 2, 2, 2), R(2.75, 8.0, 5.5, 9.5)], [], 5.5)
    d["H"] = ([R(0, 0, t, H), R(4.0, 0, 5.5, H), R(0, 9.25, 5.5, 10.75)], [], 5.5)
    d["I"] = ([R(0, 0, t, H)], [], t)
    d["J"] = ([R(3.0, 0, 4.5, H), open_ring(0, 0, 4.5, 4.0, t, "T", 1.5, 3.0, 0, 0, 2, 1.5)], [], 4.5)
    d["K"] = ([R(0, 0, t, H), slant(2.55, 9.0, 5.5, H, t, "R"), slant(5.5, 0, 3.05, 11.0, t, "R")], [], 5.5)
    d["L"] = ([R(0, 0, t, H), R(0, 0, 4.5, t)], [], 4.5)
    d["M"] = ([R(0, 0, t, H), R(5.5, 0, 7.0, H),
               slant(2.75, 2.5, 0, H, t), slant(4.25, 2.5, 7.0, H, t, "R")], [], 7.0)
    d["N"] = ([R(0, 0, t, H), R(4.0, 0, 5.5, H), slant(5.5, 0, 1.56, H, t, "R")], [], 5.5)
    o, i = bowl(0, 0, 5.5, H, 2, 2, 2, 2)
    d["O"] = ([o], [i], 5.5)
    o, i = bowl(0, 9.0, 5.0, H, tr=1.5, br=1.5)
    d["P"] = ([R(0, 0, t, H), o], [i], 5.0)
    o, i = bowl(0, 0, 5.5, H, 2, 2, 2, 2)
    d["Q"] = ([o, poly([(2.6, 3.0), (4.7, 0.0), (5.5, 0.0), (5.5, 1.4), (3.6, 4.1)])], [i], 5.5)
    o, i = bowl(0, 9.0, 5.0, H, tr=1.5, br=1.5)
    d["R"] = ([R(0, 0, t, H), o, slant(5.0, 0, 3.5, 10.5, t, "R")], [i], 5.0)
    d["S"] = ([open_ring(0, 9.25, 5.0, H, t, "R", 10.75, 16.0, 2, 2, 0.75, 1.5),
               open_ring(0, 0, 5.0, 10.75, t, "L", 4.0, 9.25, 1.5, 0.75, 2, 2)], [], 5.0)
    d["T"] = ([R(0, H - t, 5.0, H), R(1.75, 0, 3.25, H)], [], 5.0)
    d["U"] = ([open_ring(0, 0, 5.5, H, t, "T", 1.5, 4.0, 0, 0, 2, 2)], [], 5.5)
    d["V"] = ([slant(2.5, 0, 0, H, t), slant(4.0, 0, 6.5, H, t, "R")], [], 6.5)
    d["W"] = ([slant(1.8, 0, 0, H, t), slant(1.8, 0, 3.25, H, t),
               slant(4.7, 0, 3.25, H, t), slant(6.2, 0, 8.0, H, t, "R")], [], 8.0)
    d["X"] = ([para(0, 1.54, 0, 4.46, 6.0, H), para(4.46, 6.0, 0, 0, 1.54, H)], [], 6.0)
    d["Y"] = ([slant(2.5, 9.0, 0, H, t), slant(4.0, 9.0, 6.5, H, t, "R"), R(2.5, 0, 4.0, 9.5)], [], 6.5)
    d["Z"] = ([R(0, H - t, 5.0, H), R(0, 0, 5.0, t), para(0, 1.52, 0, 3.48, 5.0, H)], [], 5.0)

    # ---- digits --------------------------------------------------------------------
    o, i = bowl(0, 0, 5.5, H, 2, 2, 2, 2)
    d["0"] = ([o], [i], 5.5)
    d["1"] = ([R(2.5, 0, 4.0, H), poly([(0.0, 16.0), (2.5, 18.0), (2.5, H), (0.0, 17.7)])], [], 4.0)
    d["2"] = ([open_ring(0, 16.0, 5.0, H, t, "B", 1.5, 3.5, 2, 2, 0, 0), R(3.5, 11.0, 5.0, 17.0),
               slant(1.6, t, 5.0, 11.0, t, "R"), R(0, 0, 5.0, t)], [], 5.0)
    d["3"] = ([open_ring(0, 10.75, 5.0, H, t, "L", 12.25, 16.0, 2, 2, 1.5, 0.75),
               open_ring(0, 0, 5.0, 12.25, t, "L", 4.0, 10.75, 0.75, 1.5, 2, 2)], [], 5.0)
    d["4"] = ([R(3.4, 0, 4.9, H), R(0, 4.5, 6.0, 6.0), slant(0, 6.0, 3.4, H, t)], [], 6.0)
    d["5"] = ([R(0, H - t, 5.0, H), R(0, 10.0, t, H),
               open_ring(0, 0, 5.0, 11.5, t, "L", 3.5, 10.0, 1.0, 1.5, 2, 2)], [], 5.0)
    o, i = bowl(0, 0, 5.5, 11.5, 1.5, 1.5, 2, 2)
    d["6"] = ([o, R(0, 0, t, H), open_ring(0, 15.0, 5.5, H, t, "B", 1.5, 4.0, 2, 2, 0, 0)], [i], 5.5)
    d["7"] = ([R(0, H - t, 4.5, H), slant(2.2, 0, 4.5, H - t, t, "R")], [], 4.5)
    o, i = bowl(0.25, 9.25, 4.75, H, 2, 2, 2, 2)
    o2, i2 = bowl(0, 0, 5.0, 10.75, 2, 2, 2, 2)
    d["8"] = ([o, o2], [i, i2], 5.0)
    six_p, six_h, _ = d["6"]
    d["9"] = ([[(5.5 - x, H - y) for x, y in p] for p in six_p],
              [[(5.5 - x, H - y) for x, y in h] for h in six_h], 5.5)

    # ---- punctuation ---------------------------------------------------------------
    d["-"] = ([R(0, 9.0, 2.5, 11.0)], [], 2.5)
    d["."] = ([R(0, 0, 2.0, 2.0)], [], 2.0)
    d["/"] = ([para(0, 1.55, 0, 2.45, 4.0, H)], [], 4.0)
    d[" "] = ([], [], SIGN_SPACE_ADVANCE)

    # ---- arrows: shaft + swept-back head, 8 x 20, rotated about (4, 10) ---------------
    up = [R(3.0, 0, 5.0, 13.5), poly([(4, H), (0, 11.5), (3.0, 13.0), (5.0, 13.0), (8, 11.5)])]
    for name, ang in (("ARROW_UP", 0), ("ARROW_UP_RIGHT", -45), ("ARROW_RIGHT", -90),
                      ("ARROW_DOWN_RIGHT", -135), ("ARROW_DOWN", 180), ("ARROW_DOWN_LEFT", 135),
                      ("ARROW_LEFT", 90), ("ARROW_UP_LEFT", 45)):
        pts = [_rotate(p, ang, 4.0, 10.0) for p in up]
        x0, _, x1, _ = _bbox(pts)
        pts = [[(x - x0, y) for x, y in p] for p in pts]
        d[name] = (pts, [], x1 - x0)
    return d


_SIGN: dict[str, tuple[list[Poly], list[Poly], float]] = _sign_defs()


def sign_glyph(ch: str) -> Glyph:
    """Surface-painted-sign character in grid units (height 20), origin bottom-left.

    ``ch`` is a single character (A-Z, 0-9, '-', '.', '/', ' '; lower case is
    accepted) or an arrow name such as ``'ARROW_UP_RIGHT'``.
    """
    key = ch.upper() if len(ch) == 1 else ch.upper().replace("-", "_").replace(" ", "_")
    try:
        positives, holes, advance = _SIGN[key]
    except KeyError:
        raise KeyError(f"no sign glyph for {ch!r}") from None
    return Glyph(positives, holes, advance, SIGN_HEIGHT, key)


# --------------------------------------------------------------------------- layout
def _tokenize(text: str, font: str) -> list[str]:
    """Split text into glyph keys; ``{ARROW_RIGHT}`` style tokens are kept whole."""
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "{" and font == "SIGN":
            j = text.find("}", i + 1)
            if j > i:
                tokens.append(text[i + 1:j].strip().upper().replace("-", "_").replace(" ", "_"))
                i = j + 1
                continue
        tokens.append(c.upper())
        i += 1
    return tokens


def layout_text(text: str, font: str = "RUNWAY", height: float = 60.0, gap: float | None = None,
                align: str = "CENTER") -> tuple[list[Poly], list[Poly], float]:
    """Lay out ``text`` left to right at the given height (output units).

    * ``font``: 'RUNWAY' (feet-based designator font, 15 ft gap / 27 ft between two
      ``1``) or 'SIGN' (20-unit sign font, 3 unit gap). Glyphs are scaled by
      ``height / glyph.height``.
    * ``gap``: explicit gap between characters in output units; ``None`` uses
      the standard gap scaled with the height (the ``11`` rule scales along).
    * ``align``: 'CENTER' centres the block on x = 0, 'LEFT' starts at x = 0,
      'RIGHT' ends at x = 0.  Baseline is y = 0.
    * For the RUNWAY font a text consisting of a single ``1`` yields the
      standalone numeral with its bar (:func:`runway_standalone_one`).

    Returns ``(positives, holes, total_width)``.
    """
    font = font.upper()
    if font not in ("RUNWAY", "SIGN"):
        raise ValueError(f"unknown font {font!r}")
    tokens = _tokenize(text, font)
    if font == "RUNWAY":
        base_h = RUNWAY_HEIGHT
        std_gap = RUNWAY_GAP
        if tokens == ["1"]:
            glyphs = [runway_standalone_one()]
        else:
            glyphs = [runway_glyph(tk) for tk in tokens]
    else:
        base_h = SIGN_HEIGHT
        std_gap = SIGN_GAP
        glyphs = [sign_glyph(tk) for tk in tokens]
    s = height / base_h
    gap_std = std_gap * s if gap is None else float(gap)
    gap_11 = gap_std * (RUNWAY_GAP_11 / RUNWAY_GAP)

    positives: list[Poly] = []
    holes: list[Poly] = []
    x = 0.0
    prev: Glyph | None = None
    for g in glyphs:
        if prev is not None:
            if font == "RUNWAY" and prev.name == "1" and g.name == "1":
                x += gap_11
            else:
                x += gap_std
        gs = g.scaled(s).translated(x, 0.0)
        positives.extend(gs.positives)
        holes.extend(gs.holes)
        x += gs.advance
        prev = g
    total = x if glyphs else 0.0
    align = align.upper()
    if align == "CENTER":
        dx = -0.5 * total
    elif align == "LEFT":
        dx = 0.0
    elif align == "RIGHT":
        dx = -total
    else:
        raise ValueError(f"unknown align {align!r}")
    if dx:
        positives = [[(px + dx, py) for px, py in p] for p in positives]
        holes = [[(px + dx, py) for px, py in h] for h in holes]
    return positives, holes, total


# --------------------------------------------------------------------------- designators
def runway_designator_text(heading_deg: float, leading_zero: bool = True) -> tuple[str, str]:
    """(designator for this end, reciprocal) for a magnetic heading in degrees.

    ``n = round(heading / 10)`` (half up), 0 -> 36; reciprocal = n + 18 (mod 36).
    ``leading_zero`` pads single digits ("03"): UFC Class B / ICAO practice; the
    FAA (and NAVAIR) omit the zero ("3").
    """
    h = float(heading_deg) % 360.0
    n = int(math.floor(h / 10.0 + 0.5)) % 36
    if n == 0:
        n = 36
    r = (n + 18 - 1) % 36 + 1
    fmt = "{:02d}" if leading_zero else "{:d}"
    return fmt.format(n), fmt.format(r)
