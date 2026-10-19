"""Pavement markings: runway, taxiway, apron, helipad, compass pad and road paint.

Geometry follows UFC 3-260-04 *Airfield and Heliport Marking* (FAA AC 150/5340-1M
compatible) with an ICAO Annex 14 variant. Every marking is a flat polygon; the
polygons of one colour family are unioned into a single mesh (no coplanar
overlaps -> no z-fighting) floating ``MARKING_Z`` above the pavement, black
borders ``BORDER_Z`` below the paint.

Vertex colour ``Wear`` alpha carries the paint-wear mask (1 = intact paint); the
paint materials turn it into masked transparency so worn paint reveals the
pavement underneath. RGB is (0, 0, 0.5): no rubber/dirt darkening, neutral macro.
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

from ..core import constants as C
from ..core import geom2d as g
from ..core.constants import MK
from ..core.meshbuild import MeshBuilder
from ..core.units import ft, inch
from ..layout import plan as planmod
from . import glyphs

Point = g.Point
Poly = g.Poly

CATEGORY = "Markings"
FAMILIES = {
    # family -> (material spec, z offset, object suffix)
    # Different colour families that overlap (white border under orange stripes, yellow chevron over a white edge line)
    # get distinct sub-millimetre heights: coplanar duplicates make Cycles self-shadow the overlap (speckled black).
    "White": ("PaintWhite", C.MARKING_Z, "White"),
    "Yellow": ("PaintYellow", C.MARKING_Z + 0.0005, "Yellow"),
    "Red": ("PaintRed", C.MARKING_Z + 0.0003, "Red"),
    "Orange": ("PaintOrange", C.MARKING_Z + 0.001, "Orange"),
    "Black": ("PaintBlack", C.BORDER_Z, "Black"),
    "RoadWhite": ("RoadPaintWhite", C.MARKING_Z, "RoadWhite"),
}
BORDERED_FAMILIES = ("centerline", "threshold", "designator", "aiming", "tdz", "hold", "taxi_cl", "sphps", "lead_in")
TEXT_LIFT = 0.001    # text painted over a painted background sits 1 mm higher (coplanar paint z-fights)
DENSIFY_STEP = 2.0   # max edge length of crowned (runway / shoulder) paint polygons: a flat chord across a 1.5 %
                     # crown sags 4 mm per 5 m span, so wide markings (threshold bar, aiming point) would sink into
                     # the pavement; 2 m edges keep the sag under 1 mm
MAX_SEG = 1.0        # arc segmentation for curved paint (m)
SEG_STEP = 0.5       # polyline sub-segment length for interruption tests (m)


# =========================================================================== helpers
def _hash01(x: float, y: float, seed: int = 7) -> float:
    """Cheap deterministic hash noise in [0, 1)."""
    h = math.sin(x * 12.9898 + y * 78.233 + seed * 0.731) * 43758.5453
    return h - math.floor(h)


def _value_noise(x: float, y: float, period: float, seed: int = 11) -> float:
    xi, yi = math.floor(x / period), math.floor(y / period)
    fx, fy = x / period - xi, y / period - yi
    fx = fx * fx * (3 - 2 * fx)
    fy = fy * fy * (3 - 2 * fy)
    a = _hash01(xi, yi, seed)
    b = _hash01(xi + 1, yi, seed)
    c = _hash01(xi, yi + 1, seed)
    d = _hash01(xi + 1, yi + 1, seed)
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def clip_halfplane(poly: Sequence[Point], a: float, b: float, c: float) -> Poly:
    """Sutherland-Hodgman clip of a polygon to the half-plane a*x + b*y + c >= 0."""
    out: list[Point] = []
    n = len(poly)
    if n < 3:
        return []
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        fp = a * p[0] + b * p[1] + c
        fq = a * q[0] + b * q[1] + c
        if fp >= 0:
            out.append(p)
        if (fp >= 0) != (fq >= 0):
            t = fp / (fp - fq)
            out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
    return out if len(out) >= 3 else []


def clip_rect(poly: Sequence[Point], x0: float, y0: float, x1: float, y1: float) -> Poly:
    p = list(poly)
    for a, b, c in ((1, 0, -x0), (-1, 0, x1), (0, 1, -y0), (0, -1, y1)):
        p = clip_halfplane(p, a, b, c)
        if not p:
            return []
    return p


def _rect_st(s0: float, s1: float, t0: float, t1: float) -> list[tuple[float, float]]:
    return [(s0, t0), (s1, t0), (s1, t1), (s0, t1)]


def _dashes(start: float, end: float, dash: float, gap: float, centre_on: float | None = None) -> list[tuple[float, float]]:
    """Dash intervals [a, b] along a 1-D axis. ``centre_on`` centres one dash on that coordinate."""
    out = []
    period = dash + gap
    if centre_on is None:
        a = start
    else:
        a = centre_on - dash / 2
        while a > start:
            a -= period
    while a < end:
        b = a + dash
        aa, bb = max(a, start), min(b, end)
        if bb - aa > 0.05:
            out.append((aa, bb))
        a += period
    return out


def _split_polyline(points: Sequence[Point], step: float = SEG_STEP) -> list[tuple[Point, Point]]:
    """Polyline -> short segments (for interruption tests)."""
    segs = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        n = max(1, int(math.ceil(g.length(a, b) / step)))
        for k in range(n):
            segs.append((g.lerp(a, b, k / n), g.lerp(a, b, (k + 1) / n)))
    return segs


def _runs(segs: Sequence[tuple[Point, Point]], keep: Callable[[Point], bool]) -> list[list[Point]]:
    """Group consecutive kept segments into polylines."""
    runs: list[list[Point]] = []
    cur: list[Point] = []
    for a, b in segs:
        mid = g.lerp(a, b, 0.5)
        if keep(mid):
            if not cur:
                cur = [a]
            cur.append(b)
        else:
            if len(cur) > 1:
                runs.append(cur)
            cur = []
    if len(cur) > 1:
        runs.append(cur)
    return runs


def _polyline_paint(points: Sequence[Point], width: float, closed: bool = False) -> list[Poly]:
    if closed:
        outer, inner = g.polyline_strip(points, width, closed=True)
        # ring as a polygon with a hole is handled by the painter (positives + holes)
        return [outer, inner]
    if len(points) < 2:
        return []
    return [g.polyline_strip(points, width)]


# =========================================================================== painter
def _densify(poly: Sequence[Point], step: float) -> list[Point]:
    """Insert points along edges longer than ``step`` (corners kept)."""
    out: list[Point] = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        out.append(a)
        length = g.length(a, b)
        k = int(length // step)
        for j in range(1, k + 1):
            t = j * step / length
            if t < 0.999:
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


class Painter:
    """Collects polygons per colour family and builds one mesh per family."""

    def __init__(self, ctx, border_width: float):
        self.ctx = ctx
        self.border = border_width
        self.pos: dict[str, list[Poly]] = {k: [] for k in FAMILIES}
        self.holes: dict[str, list[Poly]] = {k: [] for k in FAMILIES}
        self.zone: dict[str, list[str]] = {k: [] for k in FAMILIES}   # per positive: 'RUNWAY' / 'SHOULDER' / 'OTHER' / 'ROAD'
        self.lift: dict[str, list[float]] = {k: [] for k in FAMILIES}  # per positive: extra z (text over a painted background)
        self.count: dict[str, int] = {}

    def add(self, family: str, poly: Sequence[Point], zone: str = "OTHER", bordered: bool = False,
            holes: Sequence[Sequence[Point]] = (), kind: str = "", lift: float = 0.0) -> None:
        p = g.ensure_ccw(poly)
        if len(p) < 3 or g.area(p) < 1e-4:
            return
        self.pos[family].append(p)
        self.zone[family].append(zone)
        self.lift[family].append(lift)
        for h in holes:
            hh = g.ensure_ccw(h)
            if len(hh) >= 3:
                self.holes[family].append(hh)
        if kind:
            self.count[kind] = self.count.get(kind, 0) + 1
        if bordered and self.border > 0:
            self.pos["Black"].append(g.offset_loop(p, self.border))
            self.zone["Black"].append(zone)
            self.lift["Black"].append(0.0)
            for h in holes:
                hh = g.ensure_ccw(h)
                if len(hh) >= 3 and g.area(hh) > 4 * self.border * self.border:
                    self.holes["Black"].append(g.offset_loop(hh, -self.border))

    def add_many(self, family: str, polys: Sequence[Sequence[Point]], **kw) -> None:
        for p in polys:
            self.add(family, p, **kw)

    def add_ring(self, family: str, outer: Sequence[Point], inner: Sequence[Point], **kw) -> None:
        """Closed band between two loops. Built from quads (never as a hole: holes in the union are
        global and would erase every other marking of the family inside the loop)."""
        o = g.ensure_ccw(outer)
        i = g.ensure_ccw(inner)
        if len(o) == len(i) and len(o) >= 3:
            n = len(o)
            # align the two loops (offset loops keep vertex order; guard against a rotated start)
            best, best_d = 0, float("inf")
            for k in (0, 1, n - 1):
                d = g.length(o[0], i[k % n])
                if d < best_d:
                    best, best_d = k % n, d
            for j in range(n):
                a, b = o[j], o[(j + 1) % n]
                c, d = i[(best + j + 1) % n], i[(best + j) % n]
                self.add(family, [a, b, c, d], **kw)
            return
        # fallback: resample both loops to the same count
        m = max(len(o), len(i), 8)
        oo = g.resample_polyline(o, g.polyline_length(o, True) / m, closed=True)[:m]
        ii = g.resample_polyline(i, g.polyline_length(i, True) / m, closed=True)[:m]
        if len(oo) == len(ii):
            for j in range(len(oo)):
                self.add(family, [oo[j], oo[(j + 1) % len(oo)], ii[(j + 1) % len(ii)], ii[j]], **kw)
        else:
            self.add(family, outer, holes=[inner], **kw)

    def build(self, z_fn: Callable[[float, float, str], float], wear_fn: Callable[[float, float, str], float]) -> None:
        for fam, (mat_name, z_off, suffix) in FAMILIES.items():
            if not self.pos[fam]:
                continue
            # crowned zones: split long edges so the paint follows the pavement surface
            polys = [(_densify(p, DENSIFY_STEP) if zz in ('RUNWAY', 'SHOULDER') else p)
                     for p, zz in zip(self.pos[fam], self.zone[fam])]
            pts, tris, origins = g.union_triangulate(polys, holes=self.holes[fam])
            if not tris:
                continue
            zones = self.zone[fam]
            lifts = self.lift[fam]
            zone_of_tri = [zones[min(o)] if o else "OTHER" for o in origins]
            lift_of_tri = [max(lifts[k] for k in o) if o else 0.0 for o in origins]
            # per-vertex zone = zone of the first triangle using it (markings of different zones never share verts)
            vzone = ["OTHER"] * len(pts)
            vlift = [0.0] * len(pts)
            for t, zz, lf in zip(tris, zone_of_tri, lift_of_tri):
                for i in t:
                    vzone[i] = zz
                    vlift[i] = max(vlift[i], lf)
            b = MeshBuilder(default_color=(0.0, 0.0, 0.5, 1.0))
            base = len(b.verts)
            for i, p in enumerate(pts):
                b.add_vertex((p[0], p[1], z_fn(p[0], p[1], vzone[i]) + z_off + vlift[i]))
            for t in tris:
                a_, b_, c_ = pts[t[0]], pts[t[1]], pts[t[2]]
                if abs((b_[0] - a_[0]) * (c_[1] - a_[1]) - (c_[0] - a_[0]) * (b_[1] - a_[1])) < 2e-9:
                    continue                      # zero-area sliver left by the union triangulation
                # Wear = (rubber 0, dirt 0, macro neutral 0.5, paint-wear alpha)
                cols = [(0.0, 0.0, 0.5, wear_fn(pts[i][0], pts[i][1], vzone[i])) for i in t]
                uvs = [(pts[i][0] / 2.0, pts[i][1] / 2.0) for i in t]
                b.add_face_idx((base + t[0], base + t[1], base + t[2]), 0, False, uvs=uvs, colors=cols)
            me = b.build(f"{C.PREFIX}Markings_{suffix}", materials=[self.ctx.mats(mat_name)])
            self.ctx.add_object(f"Markings_{suffix}", me, CATEGORY)


# =========================================================================== runway end frame
class EndFrame:
    """Coordinates of one runway end: s = distance from the threshold along the runway,
    t = lateral, positive to the RIGHT of a pilot on approach to this end."""

    def __init__(self, runway: planmod.RunwayPlan, low: bool):
        self.rw = runway
        self.low = low
        self.sign = 1.0 if low else -1.0          # +x direction of increasing s
        self.thr = runway.thr_low if low else runway.thr_high
        self.pave_end = -runway.half_len if low else runway.half_len
        self.displaced = runway.displaced_low if low else runway.displaced_high
        self.overrun = runway.overrun_low if low else runway.overrun_high
        self.numerals = runway.numerals_low if low else runway.numerals_high
        self.letter = runway.letter_low if low else runway.letter_high

    def to_base(self, s: float, t: float) -> Point:
        if self.low:
            return (self.thr + s, -t)
        return (self.thr - s, t)

    def poly(self, pts_st: Sequence[tuple[float, float]]) -> Poly:
        return g.ensure_ccw([self.to_base(s, t) for s, t in pts_st])

    def rect(self, s0: float, s1: float, t0: float, t1: float) -> Poly:
        return self.poly(_rect_st(s0, s1, t0, t1))

    def glyph_polys(self, positives: Sequence[Poly], holes: Sequence[Poly], s0: float) -> tuple[list[Poly], list[Poly]]:
        """Glyph coordinates (x right, y up, baseline 0) -> base frame with 'up' pointing down the runway."""
        pos = [self.poly([(s0 + y, x) for x, y in p]) for p in positives]
        hol = [self.poly([(s0 + y, x) for x, y in h]) for h in holes]
        return pos, hol


# =========================================================================== main builder
def build(ctx) -> None:
    s = ctx.settings
    ms = s.markings
    plan = ctx.plan
    rw = plan.runway
    icao = ms.standard == 'ICAO'
    faa = ms.standard == 'FAA'

    # black borders: on light (concrete) pavement
    def bordered(zone_concrete: bool) -> bool:
        if ms.black_border == 'NEVER':
            return False
        if ms.black_border == 'ALWAYS':
            return True
        return zone_concrete

    painter = Painter(ctx, MK.black_border)
    white_runway_boxes: list[tuple[float, float, float, float, float]] = []   # (x0, y0, x1, y1, clearance) for taxiway interruptions

    # ------------------------------------------------------------------ runway (both ends)
    for low in (True, False):
        e = EndFrame(rw, low)
        _runway_end(painter, e, ms, icao, faa, bordered, white_runway_boxes, plan)
    _runway_centerline(painter, rw, ms, icao, bordered, white_runway_boxes)
    _side_stripes(painter, rw, ms, bordered(rw.surface != 'ASPHALT'))
    if ms.arresting_discs:
        _arresting_discs(painter, rw)
    if ms.shoulder_stripes and rw.shoulder_type == 'PAVED' and rw.shoulder > ft(8):
        _shoulder_stripes(painter, plan)

    # ------------------------------------------------------------------ taxiways
    hold_lines = [h for tw in plan.taxiways for h in tw.hold_lines]
    for tw in plan.taxiways:
        _taxiway(painter, tw, plan, ms, white_runway_boxes, bordered)
    for tw in plan.taxiways:
        for h in tw.hold_lines:
            if ms.hold_lines:
                _hold_line(painter, h, tw, ms, bordered(False))
            if ms.enhanced_centerline and h.kind == 'A':
                _enhanced_centerline(painter, h, tw, ms)
            if ms.surface_signs and h.kind == 'A' and tw.kind != 'PARALLEL':
                _surface_signs(painter, h, tw, ms)

    # ------------------------------------------------------------------ aprons
    if ms.apron_markings:
        for ap in plan.aprons:
            _apron(painter, ap, ms, bordered(True))

    # ------------------------------------------------------------------ heliports
    for hp in plan.heliports:
        _heliport(painter, hp, ms, bordered(True))

    # ------------------------------------------------------------------ roads
    if ms.road_markings:
        for road in plan.roads:
            _road(painter, road, [o for o in plan.roads if o is not road])

    # ------------------------------------------------------------------ meshes
    footprint = None
    try:
        from . import pavement as pav
        footprint = pav.get_footprint(ctx) if hasattr(pav, "get_footprint") else None
    except Exception:
        footprint = None
    shoulder_w = rw.shoulder
    dirt = s.terrain.dirt_strip if s.terrain.enabled else 0.8

    def z_fn(x: float, y: float, zone: str) -> float:
        if zone == 'RUNWAY':
            return rw.crown(x, y)
        if zone == 'SHOULDER':
            d = abs(y) - rw.half_w
            if footprint is not None and hasattr(footprint, "surface_z"):
                try:
                    return footprint.surface_z(x, y)
                except Exception:
                    pass
            return -0.02 * max(0.0, min(1.0, d / max(shoulder_w, 1e-3)))
        if zone == 'ROAD':
            return -0.01
        return 0.0

    wear_amount = ms.paint_wear
    seed = s.materials.seed

    def wear_fn(x: float, y: float, zone: str) -> float:
        n = 0.55 + 0.45 * _value_noise(x, y, 1.7, seed)
        if zone == 'RUNWAY':
            bell = 0.0
            for thr, sign in ((rw.thr_low, 1.0), (rw.thr_high, -1.0)):
                d = (x - thr) * sign
                if d > 0:
                    bell = max(bell, math.exp(-((d - 380.0) / 230.0) ** 2))
            lat = math.exp(-(y / 6.0) ** 2) * 0.8 + 0.2
            w = wear_amount * (0.25 + 0.75 * bell * lat) * n
        elif zone == 'ROAD':
            w = wear_amount * 0.4 * n
        else:
            w = wear_amount * 0.35 * n
        return max(0.0, min(1.0, 1.0 - w))

    painter.build(z_fn, wear_fn)


# =========================================================================== runway end markings
# Threshold stripe totals by runway width: UFC 3-260-04 para 5-2.9.3 / FAA AC 150/5340-1M Table 3-1 (ft) and ICAO
# Annex 14 Vol I 5.2.4 (m) agree: 4 / 6 / 8 / (10) / 12 / 16 stripes for 60 / 75 / 100 / (125) / 150 / 200 ft
# (18 / 23 / 30 / 45 / 60 m). Non-standard widths pack the stripes outward until 4 ft (1.2 m) from the edge.
_UFC_THRESHOLD_STRIPES = ((60, 4), (75, 6), (100, 8), (125, 10), (150, 12), (200, 16))
_ICAO_THRESHOLD_STRIPES = ((18, 4), (23, 6), (30, 8), (45, 12), (60, 16))


def _standard_stripe_total(width_m: float, icao: bool) -> int | None:
    table = _ICAO_THRESHOLD_STRIPES if icao else tuple((ft(w) / 1.0, n) for w, n in _UFC_THRESHOLD_STRIPES)
    tol = 0.6 if icao else ft(0.5)
    for w, n in table:
        if abs(width_m - w) <= tol:
            return n
    return None


def _threshold_stripe_edges(half_w: float, icao: bool) -> list[tuple[float, float]]:
    """Lateral [t0, t1] (one side, positive) of the threshold stripes."""
    out: list[tuple[float, float]] = []
    if icao:
        sw, pitch, t = 1.8, 3.6, 1.8          # centre gap 3.6 m
        margin = 1.2
    else:
        sw, pitch = MK.threshold_stripe_w, MK.threshold_pitch
        t = MK.threshold_center_gap / 2
        margin = MK.threshold_edge_margin
    total = _standard_stripe_total(2.0 * half_w, icao)
    n_max = total // 2 if total is not None else (8 if icao else MK.threshold_max_stripes // 2)
    limit = half_w + 1e-6 if total is not None else half_w - margin + 1e-6      # standard widths: the table decides
    while t + sw <= limit and len(out) < n_max:
        out.append((t, t + sw))
        t += pitch
    return out


def _runway_end(painter: Painter, e: EndFrame, ms, icao: bool, faa: bool, bordered, boxes: list, plan) -> None:
    rw = e.rw
    W, hw, L = rw.width, rw.half_w, rw.length
    concrete = rw.is_concrete_at(e.thr + e.sign * ft(300))
    bd = bordered(concrete)
    edge_w = MK.edge_w if W >= ft(100) else MK.edge_w_narrow

    def box(poly: Poly, clearance: float) -> None:
        x0, y0, x1, y1 = g.bbox(poly)
        boxes.append((x0, y0, x1, y1, clearance))

    pavement_precedes = e.overrun is not None or e.displaced > 0.0
    stripes_on = ms.threshold
    # ---- transverse threshold bar
    if stripes_on and pavement_precedes:
        bar_w = 1.8 if icao else MK.threshold_bar_w
        p = e.rect(0.0, bar_w, -(hw - edge_w), hw - edge_w)
        painter.add("White", p, 'RUNWAY', bd, kind="threshold_bar")
        box(p, MK.interrupt_threshold)
    # ---- threshold stripes
    s_num = 0.0
    if stripes_on:
        s0 = 6.0 if icao else MK.threshold_start
        s1 = s0 + (30.0 if icao else MK.threshold_stripe_len)
        for t0, t1 in _threshold_stripe_edges(hw, icao):
            for sg in (1.0, -1.0):
                p = e.rect(s0, s1, sg * t0, sg * t1)
                painter.add("White", p, 'RUNWAY', bd, kind="threshold_stripe")
                box(p, MK.interrupt_threshold)
        s_num = s1 + (12.0 if icao else MK.designator_gap_after_stripes)
    else:
        s_num = 6.0 if icao else ft(20)
    # ---- designators
    height = 9.0 if icao else MK.numeral_height
    s_after = s_num
    if ms.designators:
        if e.letter:
            pos, hol, _w = glyphs.layout_text(e.letter, 'RUNWAY', height=height)
            pp, hh = e.glyph_polys(pos, hol, s_num)
            for p in pp:
                painter.add("White", p, 'RUNWAY', bd, holes=[h for h in hh if g.point_in_poly(g.centroid(h), p)], kind="designator")
                box(p, MK.interrupt_threshold)
            s_num += height + (6.0 if icao else MK.designator_letter_gap)
        numerals = e.numerals
        if icao and len(numerals) == 1:
            numerals = "0" + numerals
        pos, hol, _w = glyphs.layout_text(numerals, 'RUNWAY', height=height)
        pp, hh = e.glyph_polys(pos, hol, s_num)
        for p in pp:
            painter.add("White", p, 'RUNWAY', bd, holes=[h for h in hh if g.point_in_poly(g.centroid(h), p)], kind="designator")
            box(p, MK.interrupt_threshold)
        s_after = s_num + height
    e.centerline_start = s_after + (12.0 if icao else MK.centerline_start_gap)   # consumed by _runway_centerline
    # ---- aiming point
    if ms.aiming_point and L >= ft(4000):
        if icao:
            lda = L
            s_ap = 150.0 if lda < 800 else 250.0 if lda < 1200 else 300.0 if lda < 2400 else 400.0
            ap_len, ap_w, inner = 45.0, 6.0, 18.0
        else:
            s_ap, ap_len = MK.aiming_start, MK.aiming_len
            k = min(1.0, W / ft(150))
            ap_w, inner = MK.aiming_w * k, MK.aiming_inner * k
        for sg in (1.0, -1.0):
            p = e.rect(s_ap, s_ap + ap_len, sg * inner / 2, sg * (inner / 2 + ap_w))
            painter.add("White", p, 'RUNWAY', bd, kind="aiming_point")
            box(p, MK.interrupt_other)
    # ---- touchdown zone
    if ms.touchdown_zone and L >= ft(4000):
        omit = ft(900) if faa else MK.tdz_omit_from_midpoint
        if icao:
            pitch, bar_len, bar_w, gap, inner = 150.0, 22.5, 3.0, 1.5, 18.0
            n_pairs = 1 if L < 900 else 2 if L < 1200 else 3 if L < 1500 else 4 if L < 2400 else 6
            groups = [(150.0 * (i + 1), (3, 3, 2, 2, 1, 1)[i]) for i in range(n_pairs)]
        else:
            k = min(1.0, W / ft(150))
            bar_len, bar_w, gap, inner = MK.tdz_bar_len, MK.tdz_bar_w * k, MK.tdz_bar_gap * k, MK.tdz_inner * k
            groups = list(zip(MK.tdz_starts, MK.tdz_groups))
        for s_g, n_bars in groups:
            if s_g + bar_len > L / 2 - omit:
                continue
            if not icao and abs(s_g - MK.aiming_start) < 1.0:
                continue   # aiming point replaces this group
            for i in range(n_bars):
                t0 = inner / 2 + i * (bar_w + gap)
                for sg in (1.0, -1.0):
                    p = e.rect(s_g, s_g + bar_len, sg * t0, sg * (t0 + bar_w))
                    painter.add("White", p, 'RUNWAY', bd, kind="tdz_bar")
                    box(p, MK.interrupt_other)
    # ---- displaced threshold: arrows + arrowheads + chevron row in the displaced area
    if e.displaced > 0.0 and ms.threshold:
        shaft_w, head_len, head_w = ft(3), ft(45), ft(15)
        s_end = -ft(5)                       # last arrowhead tip 5 ft before the bar
        s_cur = s_end
        while s_cur - head_len > -e.displaced + ft(20):
            tip = s_cur
            head = e.poly([(tip, 0.0), (tip - head_len, head_w / 2), (tip - head_len, -head_w / 2)])
            painter.add("White", head, 'RUNWAY', bd, kind="displaced_arrow")
            shaft_s1 = tip - head_len
            shaft_s0 = max(shaft_s1 - ft(120), -e.displaced + ft(20))
            if shaft_s1 - shaft_s0 > ft(10):
                painter.add("White", e.rect(shaft_s0, shaft_s1, -shaft_w / 2, shaft_w / 2), 'RUNWAY', bd, kind="displaced_arrow")
            s_cur = shaft_s0 - ft(80)
        # row of arrowheads across the width 100 ft before the threshold bar
        n = 4 if W >= ft(100) else 3 if W > ft(60) else 2
        pitch = W / n
        for i in range(n):
            t = -W / 2 + pitch * (i + 0.5)
            head = e.poly([(-ft(100), t), (-ft(100) - head_len, t + head_w / 2), (-ft(100) - head_len, t - head_w / 2)])
            painter.add("White", head, 'RUNWAY', bd, kind="displaced_chevron")
    # ---- overrun chevrons (yellow, non-reflective)
    if ms.overrun_chevrons and e.overrun is not None and rw.overrun_type == 'PAVED' and rw.overrun_length > ft(60):
        ov_len = rw.overrun_length
        H = W / 2 - MK.chevron_margin
        leg_w = MK.chevron_w
        pitch = MK.chevron_pitch if ov_len >= ft(250) else MK.chevron_pitch_short
        s_pave_end = e.pave_end - e.thr if e.low else e.thr - e.pave_end   # s of the pavement end (<= 0)
        s_pave_end = -(abs(e.pave_end - e.thr))
        apex = s_pave_end + ft(50)             # first apex laid out 50 ft inside the pavement end
        s_min = s_pave_end - ov_len
        while apex > s_min:
            for sg in (1.0, -1.0):
                a = (apex, 0.0)
                b = (apex - H, sg * H)
                leg = e.poly(_strip_st(a, b, leg_w))
                # paint only the part outboard of the pavement end and inside the overrun
                if e.low:
                    leg = clip_rect(leg, e.pave_end - ov_len, -1e9, e.pave_end, 1e9)
                else:
                    leg = clip_rect(leg, e.pave_end, -1e9, e.pave_end + ov_len, 1e9)
                if leg:
                    painter.add("Yellow", leg, 'RUNWAY', False, kind="overrun_chevron")
            apex -= pitch


def _strip_st(a: tuple[float, float], b: tuple[float, float], width: float) -> list[tuple[float, float]]:
    d = g.normalize((b[0] - a[0], b[1] - a[1]))
    n = (-d[1], d[0])
    hw = width / 2
    return [(a[0] - n[0] * hw, a[1] - n[1] * hw), (b[0] - n[0] * hw, b[1] - n[1] * hw),
            (b[0] + n[0] * hw, b[1] + n[1] * hw), (a[0] + n[0] * hw, a[1] + n[1] * hw)]


def _runway_centerline(painter: Painter, rw: planmod.RunwayPlan, ms, icao: bool, bordered, boxes: list) -> None:
    if not ms.centerline:
        return
    e_low, e_high = EndFrame(rw, True), EndFrame(rw, False)
    # start distances were stored by _runway_end on fresh frames; recompute cheaply
    def start_of(e: EndFrame) -> float:
        height = 9.0 if icao else MK.numeral_height
        if ms.threshold:
            s = (6.0 + 30.0 + 12.0) if icao else (MK.threshold_start + MK.threshold_stripe_len + MK.designator_gap_after_stripes)
        else:
            s = 6.0 if icao else ft(20)
        if ms.designators:
            if e.letter:
                s += height + (6.0 if icao else MK.designator_letter_gap)
            s += height
        return s + (12.0 if icao else MK.centerline_start_gap)
    xa = rw.thr_low + start_of(e_low)
    xb = rw.thr_high - start_of(e_high)
    avail = xb - xa
    if avail < ft(200):
        return
    stripe, gap = (30.0, 20.0) if icao else (MK.centerline_stripe, MK.centerline_gap)
    period = stripe + gap
    n = max(1, int(round((avail + gap) / period)))
    k = avail / (n * period - gap)           # uniform scale so the pattern starts and ends with a stripe
    stripe_l, gap_l = stripe * k, gap * k
    w = (0.9 if icao else MK.centerline_w)
    forbidden = [(xc - MK.arresting_disc_d / 2 - MK.arresting_clearance, xc + MK.arresting_disc_d / 2 + MK.arresting_clearance)
                 for xc in rw.arresting] if ms.arresting_discs else []
    bd_cache = {}
    x = xa
    for i in range(n):
        x0, x1 = x, x + stripe_l
        for a, b in _subtract_intervals((x0, x1), forbidden):
            p = g.rect_xy(a, -w / 2, b, w / 2)
            key = round((a + b) / 2 / 100.0)
            if key not in bd_cache:
                bd_cache[key] = bordered(rw.is_concrete_at((a + b) / 2))
            painter.add("White", p, 'RUNWAY', bd_cache[key], kind="centerline_stripe")
            boxes.append((a, -w / 2, b, w / 2, MK.interrupt_other - 0.05))
        x += stripe_l + gap_l


def _subtract_intervals(iv: tuple[float, float], forbidden: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    out = [iv]
    for f0, f1 in forbidden:
        nxt = []
        for a, b in out:
            if b <= f0 or a >= f1:
                nxt.append((a, b))
            else:
                if a < f0:
                    nxt.append((a, f0))
                if b > f1:
                    nxt.append((f1, b))
        out = nxt
    return [(a, b) for a, b in out if b - a > 0.3]


def _side_stripes(painter: Painter, rw: planmod.RunwayPlan, ms, bd: bool) -> None:
    if not ms.side_stripes:
        return
    W = rw.width
    w = MK.edge_w if W >= ft(100) else MK.edge_w_narrow
    outer = rw.half_w
    if W > ft(200):
        outer = MK.edge_inner_special / 2 + w        # wide military runways: inner edges 194 ft apart
    x0 = (-rw.half_len if rw.displaced_low > 0 else rw.thr_low) + MK.edge_start
    x1 = (rw.half_len if rw.displaced_high > 0 else rw.thr_high) - MK.edge_start
    for sg in (1.0, -1.0):
        p = g.rect_xy(x0, sg * (outer - w), x1, sg * outer)
        painter.add("White", p, 'RUNWAY', False, kind="side_stripe")


def _arresting_discs(painter: Painter, rw: planmod.RunwayPlan) -> None:
    r = MK.arresting_disc_d / 2
    for xc in rw.arresting:
        t = MK.arresting_first
        while t + r <= rw.half_w - ft(5):
            for sg in (1.0, -1.0):
                painter.add("Yellow", g.circle((xc, sg * t), r, 32), 'RUNWAY', False, kind="arresting_disc")
            t += MK.arresting_pitch


def _shoulder_stripes(painter: Painter, plan: planmod.Plan) -> None:
    rw = plan.runway
    L_len = min(MK.shoulder_stripe_max_len, rw.shoulder - MK.shoulder_stripe_margin)
    if L_len <= 0.5:
        return
    # openings where taxiways join the runway (no shoulder there)
    openings: dict[int, list[tuple[float, float]]] = {1: [], -1: []}
    for tw in plan.taxiways:
        if tw.runway_tangents:
            xs = [p[0] for p in tw.runway_tangents]
            openings[tw.side].append((min(xs) - 2.0, max(xs) + 2.0))
        elif tw.runway_junction is not None:
            x = tw.runway_junction[0]
            openings[tw.side].append((x - tw.width, x + tw.width))
    for low in (True, False):
        e = EndFrame(rw, low)
        s = ft(50)
        while s < rw.length / 2:
            for sg in (1.0, -1.0):
                a = (s, sg * rw.half_w)
                b = (s - L_len, sg * (rw.half_w + L_len))
                p = e.poly(_strip_st(a, b, MK.shoulder_stripe_w))
                xm = sum(q[0] for q in p) / 4
                side = 1 if (sum(q[1] for q in p) / 4) > 0 else -1
                if any(o0 <= xm <= o1 for o0, o1 in openings[side]):
                    pass
                else:
                    painter.add("Yellow", p, 'SHOULDER', False, kind="shoulder_stripe")
            s += MK.shoulder_stripe_pitch


# =========================================================================== taxiways
def _lead_on_lines(tw: planmod.TaxiwayPlan, rw: planmod.RunwayPlan) -> list[list[Point]]:
    """Taxiway centreline on the runway: R=150 ft curve tangent to the 4.5 ft offset line + 200 ft straight."""
    side = tw.side
    out: list[list[Point]] = []
    off = MK.centerline_w / 2 + MK.taxi_lead_on_offset        # 4.5 ft from the runway CL
    R = MK.taxi_lead_on_radius
    if tw.kind == 'HIGH_SPEED':
        # the plan centreline is already an arc from the runway CL: paint it from the offset line outward
        pts = [p for p in tw.centerline if abs(p[1]) >= off * 0.999]
        if pts:
            # straight 200 ft back along the offset line toward the approach
            dir_x = 1.0 if tw.exit_dir == 'HIGH' else -1.0
            first = pts[0]
            out.append([(first[0] - dir_x * MK.taxi_lead_on_straight, side * off), (first[0], side * off)] + pts[1:])
        return out
    xs = tw.centerline[0][0]
    if tw.kind == 'END':
        return [[(xs, side * rw.half_w), tw.centerline[-1]]]
    # two 90-degree curves from the connector line to the offset line, each followed by a 200 ft straight
    for dx in (1.0, -1.0):
        cx, cy = xs + dx * R, side * (off + R)
        start_ang = math.atan2(0.0, -dx)              # centre -> curve start (xs, side*(off+R))
        end_ang = math.atan2(-side * 1.0, 0.0)        # centre -> curve end (cx, side*off)
        sweep = end_ang - start_ang
        while sweep > math.pi:
            sweep -= 2 * math.pi
        while sweep < -math.pi:
            sweep += 2 * math.pi
        arc = g.arc_points((cx, cy), R, start_ang, start_ang + sweep, max_seg_len=MAX_SEG)
        out.append([(xs, side * (off + R))] + arc[1:] + [(cx + dx * MK.taxi_lead_on_straight, side * off)])
    return out


def _taxiway(painter: Painter, tw: planmod.TaxiwayPlan, plan: planmod.Plan, ms, boxes: list, bordered) -> None:
    rw = plan.runway
    lw = MK.taxi_cl_w if ms.taxi_line_width == '6' else MK.taxi_cl_w_wide
    side = tw.side
    # ---- centreline polylines
    polylines: list[list[Point]] = []
    if tw.kind == 'PARALLEL':
        polylines.append(list(tw.centerline))
    elif tw.kind == 'ACCESS':
        polylines.append(list(tw.centerline))
    else:
        # off-runway part: from the parallel taxiway to the runway edge (plus lead-on/off on the runway)
        off_rw = [p for p in tw.centerline if abs(p[1]) >= rw.half_w]
        edge_pt = tw.runway_junction
        if edge_pt is not None:
            off_rw = [edge_pt] + off_rw
        if len(off_rw) >= 2:
            polylines.append(off_rw)
        if tw.kind != 'END':
            polylines.extend(_lead_on_lines(tw, rw))
            # connector: straight from the runway edge to the start of the curves (y = side*(off+R))
            if tw.kind == 'CONNECTOR':
                xs = tw.centerline[0][0]
                y_curve = side * (MK.centerline_w / 2 + MK.taxi_lead_on_offset + MK.taxi_lead_on_radius)
                polylines.append([(xs, side * rw.half_w), (xs, y_curve)])
    if not ms.taxiway_centerline:
        polylines = []
    # interruptions: runway markings (on-runway part), hold lines
    hold_cuts = [(h.center, h.direction) for h in tw.hold_lines]

    def keep(p: Point) -> bool:
        if abs(p[1]) < rw.half_w + 0.5:
            for x0, y0, x1, y1, c in boxes:
                if x0 - c <= p[0] <= x1 + c and y0 - c <= p[1] <= y1 + c:
                    return False
        for centre, d in hold_cuts:
            along = (p[0] - centre[0]) * d[0] + (p[1] - centre[1]) * d[1]      # + toward the runway
            lw_h = MK.hold_a_line if ms.hold_line_width == '6' else MK.hold_a_line_faa
            if -(3.5 * lw_h + inch(6)) <= along <= 3.5 * lw_h + ft(3):
                return False
        return True

    concrete_under = tw.kind == 'ACCESS'   # apron stubs are concrete; taxiways asphalt
    for pl in polylines:
        for run in _runs(_split_polyline(pl), keep):
            painter.add("Yellow", g.polyline_strip(run, lw), 'RUNWAY' if abs(run[0][1]) < rw.half_w and abs(run[-1][1]) < rw.half_w else 'OTHER',
                        bordered(concrete_under) and False, kind="taxi_centerline")
    # ---- edge lines (double 6 in with 6 in gap, outer edge on the pavement edge)
    if ms.taxiway_edges:
        _taxiway_edges(painter, tw, plan)


def _taxiway_edges(painter: Painter, tw: planmod.TaxiwayPlan, plan: planmod.Plan) -> None:
    rw = plan.runway
    ew, gap = MK.taxi_edge_w, MK.taxi_edge_gap
    hw = tw.width / 2
    offsets = (hw - ew / 2, hw - ew - gap - ew / 2)
    cl = list(tw.centerline)
    side = tw.side
    if tw.kind == 'PARALLEL':
        # openings where connectors join (runway side) and apron stubs join (far side)
        openings = {1: [], -1: []}   # key: +1 = runway side of this taxiway (toward y=0)
        for other in plan.taxiways:
            if other is tw or other.side != side:
                continue
            if other.kind in ('CONNECTOR', 'HIGH_SPEED', 'END') and other.parallel_junction is not None:
                x = other.parallel_junction[0]
                tl = other.fillet_radius * 1.2
                openings[1].append((x - other.width / 2 - tl, x + other.width / 2 + tl))
                if other.kind == 'HIGH_SPEED':
                    dir_x = 1.0 if other.exit_dir == 'HIGH' else -1.0
                    openings[1].append((x - other.width - tl * 1.5, x + other.width + tl * 1.5))
            elif other.kind == 'ACCESS' and other.parallel_junction is not None:
                x = other.parallel_junction[0]
                tl = other.fillet_radius
                openings[-1].append((x - other.width / 2 - tl, x + other.width / 2 + tl))
        for hp in plan.heliports:                       # helitaxiway junctions open the far-side edge lines
            if hp.connected and hp.side == side:
                openings[-1].append((hp.x_c + hp.junction_u[0], hp.x_c + hp.junction_u[1]))
        for sg in (1.0, -1.0):      # sg = +1 -> toward the runway (y toward 0)
            key = 1 if sg > 0 else -1
            for off in offsets:
                y = side * tw.offset - side * sg * off
                x0, x1 = cl[0][0], cl[-1][0]
                for a, b in _subtract_intervals((x0, x1), sorted(openings[key])):
                    painter.add("Yellow", g.rect_xy(a, y - ew / 2, b, y + ew / 2), 'OTHER', False, kind="taxi_edge")
        return
    # connectors / exits / access stubs: edge polylines trimmed at both junction fillets
    par = plan.parallel_taxiway(side)
    r_par = tw.fillet_radius * 1.2
    for sgn in (1.0, -1.0):
        for off in offsets:
            edge = g.offset_polyline(cl, sgn * off)
            # trim at the runway end (first point is on the runway CL): find crossing with y = side*half_w
            edge = _trim_from_line(edge, side * rw.half_w, side, tw)
            if tw.kind == 'ACCESS':
                host_y = cl[0][1]  # parallel taxiway CL or runway CL
                host_half = (par.width / 2 if (par and abs(host_y) > rw.half_w + 1) else rw.half_w)
                edge = _trim_from_line(edge, host_y + side * host_half, side, tw, tangent=tw.fillet_radius)
                edge = _trim_end(edge, tw.fillet_radius * 0.6)
            elif par is not None:
                edge = _trim_from_line(edge, side * par.offset - side * par.width / 2, -side, tw, tangent=r_par, from_end=True)
            if len(edge) >= 2:
                painter.add("Yellow", g.polyline_strip(edge, ew), 'OTHER', False, kind="taxi_edge")


def _trim_from_line(edge: list[Point], y_line: float, side: int, tw: planmod.TaxiwayPlan, tangent: float | None = None,
                    from_end: bool = False) -> list[Point]:
    """Cut the part of an edge polyline that lies beyond ``y_line`` (toward the host pavement) and
    shorten it by the fillet tangent length so the edge line stops where the fillet begins."""
    if tangent is None:
        tangent = tw.fillet_radius
        if tw.runway_tangents:
            # tangent length = distance from corner to tangent point on the runway edge
            xs = [abs(p[0] - tw.runway_junction[0]) for p in tw.runway_tangents] if tw.runway_junction else []
            if xs:
                tangent = max(min(xs) - tw.width / 2, tw.fillet_radius * 0.5)
    pts = edge[::-1] if from_end else edge
    # keep points on the far side of the line (away from the host)
    kept: list[Point] = []
    crossed = False
    for i, p in enumerate(pts):
        inside_host = (p[1] - y_line) * side <= 0 if not from_end else (p[1] - y_line) * side <= 0
        if not inside_host:
            if not crossed and i > 0:
                a = pts[i - 1]
                t = (y_line - a[1]) / (p[1] - a[1]) if abs(p[1] - a[1]) > 1e-9 else 0.0
                kept.append((a[0] + (p[0] - a[0]) * t, y_line))
            crossed = True
            kept.append(p)
        elif crossed:
            break
    if len(kept) < 2:
        return []
    # shorten from the host end by the tangent length
    acc = 0.0
    out: list[Point] = []
    for i in range(len(kept) - 1):
        a, b = kept[i], kept[i + 1]
        seg = g.length(a, b)
        if acc + seg <= tangent:
            acc += seg
            continue
        if acc < tangent:
            t = (tangent - acc) / seg
            out.append(g.lerp(a, b, t))
            acc = tangent
        out.append(b)
    if from_end:
        out = out[::-1]
    return out


def _trim_end(edge: list[Point], length: float) -> list[Point]:
    if len(edge) < 2:
        return edge
    rev = _trim_from_line_simple(edge[::-1], length)
    return rev[::-1]


def _trim_from_line_simple(pts: list[Point], tangent: float) -> list[Point]:
    acc = 0.0
    out: list[Point] = []
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        seg = g.length(a, b)
        if acc + seg <= tangent:
            acc += seg
            continue
        if acc < tangent:
            out.append(g.lerp(a, b, (tangent - acc) / seg))
            acc = tangent
        out.append(b)
    return out


def _hold_line(painter: Painter, h: planmod.HoldLine, tw: planmod.TaxiwayPlan, ms, bd: bool) -> None:
    d = h.direction                     # toward the runway
    n = g.perp_left(d)                  # along the line
    c = h.center
    half = h.length / 2

    def seg(along0: float, along1: float, lat0: float, lat1: float) -> Poly:
        pts = []
        for a, l in ((along0, lat0), (along1, lat0), (along1, lat1), (along0, lat1)):
            pts.append((c[0] + d[0] * a + n[0] * l, c[1] + d[1] * a + n[1] * l))
        return g.ensure_ccw(pts)

    if h.kind == 'A':
        lw = MK.hold_a_line if ms.hold_line_width == '6' else MK.hold_a_line_faa
        # two solid lines on the hold side (away from the runway), two dashed on the runway side
        for k in (-3.0, -1.0):
            painter.add("Yellow", seg(k * lw - lw / 2, k * lw + lw / 2, -half, half), 'OTHER', bd, kind="hold_a")
        for k in (1.0, 3.0):
            for l0, l1 in _dashes(-half, half, MK.hold_a_dash, MK.hold_a_dash_gap, centre_on=0.0):
                painter.add("Yellow", seg(k * lw - lw / 2, k * lw + lw / 2, l0, l1), 'OTHER', bd, kind="hold_a")
        if bd:
            painter.add("Black", seg(-3.5 * lw, 3.5 * lw, -half, half), 'OTHER', False)
    else:
        line_w, spacing = MK.hold_b_line, MK.hold_b_spacing
        for k in (-1.0, 1.0):
            a = k * (spacing / 2 + line_w / 2)
            painter.add("Yellow", seg(a - line_w / 2, a + line_w / 2, -half, half), 'OTHER', bd, kind="hold_b")
        rung, rung_gap, pitch = MK.hold_b_rung, MK.hold_b_rung_gap, MK.hold_b_rung_pitch
        l = pitch / 2
        while l < half - rung:
            for sg in (1.0, -1.0):
                for off in (-(rung_gap / 2 + rung / 2), rung_gap / 2 + rung / 2):
                    lat = sg * l + off
                    painter.add("Yellow", seg(-spacing / 2, spacing / 2, lat - rung / 2, lat + rung / 2), 'OTHER', bd, kind="hold_b")
            l += pitch


def _enhanced_centerline(painter: Painter, h: planmod.HoldLine, tw: planmod.TaxiwayPlan, ms) -> None:
    d = h.direction
    n = g.perp_left(d)
    lw_h = MK.hold_a_line if ms.hold_line_width == '6' else MK.hold_a_line_faa
    cl_w = MK.taxi_cl_w if ms.taxi_line_width == '6' else MK.taxi_cl_w_wide
    lat = cl_w / 2 + MK.enhanced_offset + inch(3)
    start = -(3.5 * lw_h + inch(9))           # just behind the solid lines (hold side)
    dash, gap = MK.enhanced_dash, MK.enhanced_gap
    along = start
    count = 0
    while count < 12:
        a0, a1 = along - dash, along
        for sg in (1.0, -1.0):
            pts = [(h.center[0] + d[0] * a + n[0] * sg * l, h.center[1] + d[1] * a + n[1] * sg * l)
                   for a, l in ((a0, lat - inch(3)), (a1, lat - inch(3)), (a1, lat + inch(3)), (a0, lat + inch(3)))]
            painter.add("Yellow", g.ensure_ccw(pts), 'OTHER', False, kind="enhanced_cl")
        along = a0 - gap
        count += 1
    # final 6 ft dash
    a0, a1 = along - MK.enhanced_last_dash, along
    for sg in (1.0, -1.0):
        pts = [(h.center[0] + d[0] * a + n[0] * sg * l, h.center[1] + d[1] * a + n[1] * sg * l)
               for a, l in ((a0, lat - inch(3)), (a1, lat - inch(3)), (a1, lat + inch(3)), (a0, lat + inch(3)))]
        painter.add("Yellow", g.ensure_ccw(pts), 'OTHER', False, kind="enhanced_cl")


def _surface_signs(painter: Painter, h: planmod.HoldLine, tw: planmod.TaxiwayPlan, ms) -> None:
    """Surface painted holding position sign (white on red, left) and location sign (yellow on black, right)."""
    d = h.direction
    right = (d[1], -d[0])                 # pilot's right when facing the runway
    text_h = MK.sphps_text_h if tw.width >= ft(50) else ft(9)
    margin = MK.sphps_margin
    lw_h = MK.hold_a_line if ms.hold_line_width == '6' else MK.hold_a_line_faa
    along_far = -(3.5 * lw_h + MK.sphps_before_hold)      # far edge of the sign box (toward the runway)

    def place(text: str, lateral_centre: float, bg: str, fg: str, border: str | None) -> None:
        try:
            pos, hol, width = glyphs.layout_text(text, 'SIGN', height=text_h, align='CENTER')
        except KeyError:
            return              # designator with characters outside the sign font (A-Z 0-9 - . / space): skip this sign
        box_w, box_h = width + 2 * margin, text_h + 2 * margin
        a1 = along_far
        a0 = a1 - box_h

        def to_base(gx: float, gy: float) -> Point:
            a = a0 + margin + gy
            l = lateral_centre + gx
            return (h.center[0] + d[0] * a + right[0] * l, h.center[1] + d[1] * a + right[1] * l)

        bg_poly = g.ensure_ccw([to_base(-box_w / 2, -margin), to_base(box_w / 2, -margin), to_base(box_w / 2, text_h + margin), to_base(-box_w / 2, text_h + margin)])
        if border:
            # yellow border ring; the black background sits 1 mm lower and shows through the ring's hole
            painter.add_ring(border, g.offset_loop(bg_poly, inch(6)), bg_poly, zone='OTHER', kind="sphps")
        painter.add(bg, bg_poly, 'OTHER', False, kind="sphps")
        for p in pos:
            pp = g.ensure_ccw([to_base(x, y) for x, y in p])
            hh = [g.ensure_ccw([to_base(x, y) for x, y in hole]) for hole in hol if g.point_in_poly(g.centroid(hole), p)]
            painter.add(fg, pp, 'OTHER', False, holes=hh, kind="sphps", lift=TEXT_LIFT)

    half_w = tw.width / 2
    if tw.width <= ft(50):
        place(h.runway_text, 0.0, "Red", "White", None)
    else:
        place(h.runway_text, -(MK.sphps_lateral + half_w * 0.35), "Red", "White", None)
        if tw.name:
            place(tw.name, MK.sphps_lateral + half_w * 0.35, "Black", "Yellow", "Yellow")


# =========================================================================== aprons
def _apron(painter: Painter, ap: planmod.ApronPlan, ms, bd: bool) -> None:
    lw = MK.taxi_cl_w if ms.taxi_line_width == '6' else MK.taxi_cl_w_wide
    ew, gap = MK.taxi_edge_w, MK.taxi_edge_gap
    # edge marking (double yellow) along the apron boundary
    if ap.kind not in ('HAS_LOOP',):
        loop = g.ensure_ccw(ap.edge_loop)
        for off in (ew / 2, ew + gap + ew / 2):
            inner_loop = g.offset_loop(loop, -off)
            outer_c = g.offset_loop(inner_loop, ew / 2)
            inner_c = g.offset_loop(inner_loop, -ew / 2)
            painter.add_ring("Yellow", outer_c, inner_c, zone='OTHER', kind="apron_edge")
    # taxilanes
    for tl in ap.taxilanes:
        if len(tl) >= 2:
            painter.add("Yellow", g.polyline_strip(tl, lw), 'OTHER', False, kind="taxilane")
    if ap.loop_centerline:
        outer, inner = g.polyline_strip(ap.loop_centerline, lw, closed=True)
        painter.add_ring("Yellow", outer, inner, zone='OTHER', kind="taxilane")
    # spots: lead-in, stop bar, number
    for sp in ap.spots:
        if len(sp.lead_in) >= 2:
            painter.add("Yellow", g.polyline_strip(sp.lead_in, lw), 'OTHER', False, kind="lead_in")
            a, b = sp.lead_in[-2], sp.lead_in[-1]
            dd = g.normalize((b[0] - a[0], b[1] - a[1]))
            nn = g.perp_left(dd)
            c = sp.position
            bar = [(c[0] + nn[0] * MK.parking_stop_len / 2 - dd[0] * MK.parking_stop_w / 2, c[1] + nn[1] * MK.parking_stop_len / 2 - dd[1] * MK.parking_stop_w / 2),
                   (c[0] + nn[0] * MK.parking_stop_len / 2 + dd[0] * MK.parking_stop_w / 2, c[1] + nn[1] * MK.parking_stop_len / 2 + dd[1] * MK.parking_stop_w / 2),
                   (c[0] - nn[0] * MK.parking_stop_len / 2 + dd[0] * MK.parking_stop_w / 2, c[1] - nn[1] * MK.parking_stop_len / 2 + dd[1] * MK.parking_stop_w / 2),
                   (c[0] - nn[0] * MK.parking_stop_len / 2 - dd[0] * MK.parking_stop_w / 2, c[1] - nn[1] * MK.parking_stop_len / 2 - dd[1] * MK.parking_stop_w / 2)]
            painter.add("Yellow", g.ensure_ccw(bar), 'OTHER', False, kind="stop_bar")
            # spot number beside the lead-in, 3 m before the stop bar, reading along the lead-in direction
            if sp.number and len(sp.number) <= 4:
                pos, hol, width = glyphs.layout_text(sp.number, 'SIGN', height=MK.spot_number_h, align='CENTER')
                origin = (c[0] - dd[0] * 3.0 + nn[0] * (MK.spot_number_h * 1.2 + width / 2), c[1] - dd[1] * 3.0 + nn[1] * (MK.spot_number_h * 1.2 + width / 2))
                # glyph x -> -nn (right of the taxiing pilot), glyph y -> dd
                def tb(gx: float, gy: float) -> Point:
                    return (origin[0] - nn[0] * gx + dd[0] * gy, origin[1] - nn[1] * gx + dd[1] * gy)
                m = 0.25
                box = g.ensure_ccw([tb(-width / 2 - m, -m), tb(width / 2 + m, -m), tb(width / 2 + m, MK.spot_number_h + m), tb(-width / 2 - m, MK.spot_number_h + m)])
                painter.add("Black", box, 'OTHER', False, kind="spot_number")
                for p in pos:
                    painter.add("Yellow", g.ensure_ccw([tb(x, y) for x, y in p]), 'OTHER', False,
                                holes=[g.ensure_ccw([tb(x, y) for x, y in hh]) for hh in hol if g.point_in_poly(g.centroid(hh), p)], kind="spot_number")
    # tie-downs, grounding, hydrants
    for p in ap.tie_downs:
        painter.add_ring("Yellow", g.circle(p, 0.25 + inch(3), 16), g.circle(p, 0.25 - inch(3), 16), zone='OTHER', kind="tie_down")
    for p in ap.grounding:
        painter.add("Yellow", g.regular_polygon(p, 0.35, 3, math.pi / 2), 'OTHER', False, kind="grounding")
    for p in ap.hydrants:
        painter.add("Yellow", g.rect(p[0], p[1], MK.hydrant_square, MK.hydrant_square), 'OTHER', False, kind="hydrant")
    if ap.kind == 'HOT_CARGO':
        loop = g.offset_loop(g.ensure_ccw(ap.edge_loop), -ft(3))
        painter.add_ring("Red", g.offset_loop(loop, inch(3)), g.offset_loop(loop, -inch(3)), zone='OTHER', kind="restricted_boundary")
    if 'helipad' in ap.extra_markings:
        _helipad(painter, ap.extra_markings['helipad'])
    if 'compass' in ap.extra_markings:
        _compass(painter, ap.extra_markings['compass'])


def _helipad(painter: Painter, info: dict) -> None:
    """UFC 3-260-04 Fig 7-5: H sized from the pad, broken-square TLOF perimeter."""
    cx, cy = info['center']
    size = info['size']
    heading = info.get('heading', math.pi / 2)
    A = min(0.6 * size, 20.0)
    B = 0.5 * A
    stroke = A / 6.5
    if size < ft(60):
        Cw, D, E = ft(1.3), ft(3), ft(5)
    elif size < ft(80):
        Cw, D, E = ft(2), ft(4), ft(7)
    elif size < ft(99):
        Cw, D, E = ft(2), ft(5), ft(10)
    else:
        Cw, D, E = ft(2.5), ft(6.5), ft(11.5)
    rot = heading - math.pi / 2   # H 'up' toward the approach heading

    def tr(pts: Sequence[Point]) -> Poly:
        return g.transform_poly(pts, rot, (cx, cy))

    # H: two uprights + crossbar
    painter.add("White", tr(g.rect(-B / 2 + stroke / 2, 0.0, stroke, A)), 'OTHER', False, kind="helipad")
    painter.add("White", tr(g.rect(B / 2 - stroke / 2, 0.0, stroke, A)), 'OTHER', False, kind="helipad")
    painter.add("White", tr(g.rect(0.0, 0.0, B - 2 * stroke, stroke)), 'OTHER', False, kind="helipad")
    half = size / 2
    # corners (L shapes) and mid-edge segments of the TLOF perimeter
    for sx in (1.0, -1.0):
        for sy in (1.0, -1.0):
            painter.add("White", tr(g.rect_xy(sx * half, sy * half, sx * (half - E), sy * (half - D))), 'OTHER', False, kind="helipad")
            painter.add("White", tr(g.rect_xy(sx * half, sy * half, sx * (half - D), sy * (half - E))), 'OTHER', False, kind="helipad")
    for sx in (1.0, -1.0):
        painter.add("White", tr(g.rect_xy(sx * half, -E / 2, sx * (half - D), E / 2)), 'OTHER', False, kind="helipad")
        painter.add("White", tr(g.rect_xy(-E / 2, sx * half, E / 2, sx * (half - D))), 'OTHER', False, kind="helipad")


# =========================================================================== heliports
HELI_H_RATIO = 0.35            # H height / TLOF size (clamped)
HELI_FATO_DASH, HELI_FATO_GAP = 9.0, 1.5
HELI_LINE_W = 0.30             # TLOF / FATO perimeter marking width (12 in)
HELI_TDPM_W = 0.50             # touchdown / positioning marking circle width (ICAO >= 0.5 m)


def _heliport(painter: Painter, hp, ms, bd: bool) -> None:
    """FATO / TLOF markings (UFC 3-260-04 ch. 7, ICAO Annex 14 Vol II 5.3, FAA AC 150/5390-2C): white TLOF perimeter and
    H, dashed white FATO perimeter on a paved FATO, yellow touchdown / positioning circle, helitaxiway centrelines and
    edge lines, stand circles with lead-in lines and numbers, tie-down rings."""
    B = hp.to_base
    if ms.apron_markings:
        c = hp.fato_center
        cb = B(c)
        t = hp.tlof
        # ---- TLOF perimeter (continuous white, inside the pad edge)
        if hp.tlof_shape == 'ROUND':
            n = 96
            painter.add_ring("White", g.circle(cb, t * 0.5, n), g.circle(cb, t * 0.5 - HELI_LINE_W, n),
                             zone='OTHER', bordered=bd, kind="helipad")
        else:
            h0, h1 = t * 0.5, t * 0.5 - HELI_LINE_W
            outer = [B((c[0] + sx * h0, c[1] + sy * h0)) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            inner = [B((c[0] + sx * h1, c[1] + sy * h1)) for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            painter.add_ring("White", outer, inner, zone='OTHER', bordered=bd, kind="helipad")
        # ---- H (up toward the FATO feeder, rotated by the item heading)
        up = g.rotate_point((0.0, float(hp.side)), math.radians(hp.heading))
        right = (up[1], -up[0])

        def H(x: float, y: float) -> Point:
            return (cb[0] + right[0] * x + up[0] * y, cb[1] + right[1] * x + up[1] * y)

        hh = max(3.0, min(7.0, HELI_H_RATIO * t))
        hw_ = 0.6 * hh
        stroke = max(0.4, min(1.0, hh / 6.0))
        for x0, x1, y0, y1 in ((-hw_ / 2, -hw_ / 2 + stroke, -hh / 2, hh / 2), (hw_ / 2 - stroke, hw_ / 2, -hh / 2, hh / 2),
                               (-hw_ / 2 + stroke, hw_ / 2 - stroke, -stroke / 2, stroke / 2)):
            painter.add("White", g.ensure_ccw([H(x0, y0), H(x1, y0), H(x1, y1), H(x0, y1)]), 'OTHER', bd, kind="helipad")
        # ---- touchdown / positioning marking: yellow circle, inner diameter 0.5 D
        r_in = 0.25 * hp.D
        painter.add_ring("Yellow", g.circle(cb, r_in + HELI_TDPM_W, 64), g.circle(cb, r_in, 64), zone='OTHER', kind="helipad")
        # ---- FATO perimeter: dashed white on a paved FATO
        if hp.fato_paved:
            fh = hp.fato * 0.5
            hw0 = hp.w_t * 0.5 + 0.5
            for side_i in range(4):
                for a, b in _dashes(-fh + 1.2, fh - 1.2, HELI_FATO_DASH, HELI_FATO_GAP, centre_on=0.0):
                    if side_i == 0 and a < hw0 and b > -hw0:         # the helitaxiway enters here
                        continue
                    lo, hi = fh - 0.3 - HELI_LINE_W, fh - 0.3
                    quad = [(a, -hi), (b, -hi), (b, -lo), (a, -lo)]
                    rot = side_i * math.pi / 2
                    pts = [B((c[0] + x * math.cos(rot) - y * math.sin(rot), c[1] + x * math.sin(rot) + y * math.cos(rot))) for x, y in quad]
                    painter.add("White", g.ensure_ccw(pts), 'OTHER', bd, kind="helipad")
    # ---- helitaxiway centrelines + edge lines (yellow)
    lw = MK.taxi_cl_w if ms.taxi_line_width == '6' else MK.taxi_cl_w_wide
    if ms.taxiway_centerline:
        for pl in hp.paint_lines:
            pts = [B(p) for p in pl]
            if hp.connected and abs(pl[0][0]) < 1e-6 and abs(pl[0][1]) < 1e-6 and hp.host_half > 0.0:
                pts = [B((0.0, -hp.host_half))] + pts          # lead-on: join the host taxiway centreline
            painter.add("Yellow", g.polyline_strip(pts, lw), 'OTHER', False, kind="taxi_centerline")
    if ms.taxiway_edges:
        ew = MK.taxi_edge_w
        for run, inward in hp.edge_runs:
            pts = [B((p[0] + inward[0] * ew * 1.5, p[1] + inward[1] * ew * 1.5)) for p in run]
            if len(pts) >= 2:
                painter.add("Yellow", g.polyline_strip(pts, ew), 'OTHER', False, kind="taxi_edge")
    # ---- stands: positioning circle, lead-in line, boundary, number
    if ms.apron_markings:
        r_in = 0.25 * hp.D
        up_b = (0.0, float(hp.side))
        right_b = (up_b[1], -up_b[0])
        for st in hp.stands:
            cb = B(st.center)
            painter.add_ring("Yellow", g.circle(cb, r_in + HELI_TDPM_W * 0.6, 48), g.circle(cb, r_in, 48), zone='OTHER', kind="helipad")
            lead = [B(p) for p in st.lead_in]
            # stop short of the circle
            d = g.normalize((lead[1][0] - lead[0][0], lead[1][1] - lead[0][1]))
            end = (lead[1][0] - d[0] * (r_in + HELI_TDPM_W * 0.6), lead[1][1] - d[1] * (r_in + HELI_TDPM_W * 0.6))
            painter.add("Yellow", g.polyline_strip([lead[0], end], lw), 'OTHER', False, kind="lead_in")
            m = 0.6
            o = [B((st.u0 + m, st.v0 + m)), B((st.u1 - m, st.v0 + m)), B((st.u1 - m, st.v1 - m)), B((st.u0 + m, st.v1 - m))]
            i_ = [B((st.u0 + m + lw, st.v0 + m + lw)), B((st.u1 - m - lw, st.v0 + m + lw)),
                  B((st.u1 - m - lw, st.v1 - m - lw)), B((st.u0 + m + lw, st.v1 - m - lw))]
            painter.add_ring("Yellow", o, i_, zone='OTHER', kind="apron_edge")
            # number near the far edge, reading toward the stand from the spine
            h_n = 1.2
            pos, hol, width = glyphs.layout_text(st.number, 'SIGN', height=h_n, align='CENTER')
            origin = B((st.center[0], st.v1 - m - lw - 1.0 - h_n))

            def tb(gx: float, gy: float) -> Point:
                return (origin[0] + right_b[0] * gx + up_b[0] * gy, origin[1] + right_b[1] * gx + up_b[1] * gy)
            for p in pos:
                painter.add("Yellow", g.ensure_ccw([tb(x, y) for x, y in p]), 'OTHER', False,
                            holes=[g.ensure_ccw([tb(x, y) for x, y in hh]) for hh in hol if g.point_in_poly(g.centroid(hh), p)],
                            kind="spot_number", lift=TEXT_LIFT)
        for p in hp.tie_downs:
            painter.add_ring("Yellow", g.circle(B(p), 0.25 + inch(3), 16), g.circle(B(p), 0.25 - inch(3), 16), zone='OTHER', kind="tie_down")


def _compass(painter: Painter, info: dict) -> None:
    """UFC 3-260-04 §8-15 compass calibration pad: 24 orange stripes every 15 deg with white borders, azimuth labels."""
    cx, cy = info['center']
    r_in, r_out = 0.47, MK.compass_stripe_len + 0.47
    sw = MK.compass_stripe_w
    painter.add("White", g.circle((cx, cy), inch(9), 24), 'OTHER', False, kind="compass")
    for i in range(24):
        ang = math.radians(90.0 - 15.0 * i)      # azimuth clockwise from north (+Y)
        d = (math.cos(ang), math.sin(ang))
        n = g.perp_left(d)
        a = (cx + d[0] * r_in, cy + d[1] * r_in)
        b = (cx + d[0] * r_out, cy + d[1] * r_out)
        painter.add("White", g.strip(a, b, sw + 2 * inch(1.5)), 'OTHER', False, kind="compass")
        painter.add("Orange", g.strip(a, b, sw), 'OTHER', False, kind="compass")
        # label on a white rectangle at 27 ft
        lab = f"{(15 * i) % 360:03d}"
        pos, hol, width = glyphs.layout_text(lab, 'SIGN', height=inch(24), align='CENTER')
        lc = (cx + d[0] * ft(27), cy + d[1] * ft(27))
        rot = math.atan2(d[1], d[0]) - math.pi / 2      # glyph 'up' points outward along the radial
        bg = g.rect(lc[0], lc[1], inch(51), inch(26), rot + math.pi / 2)
        painter.add("White", bg, 'OTHER', False, kind="compass")
        for p in pos:
            pp = g.transform_poly([(x, y - inch(12)) for x, y in p], rot, lc)
            painter.add("Orange", g.ensure_ccw(pp), 'OTHER', False, kind="compass")


# =========================================================================== roads
def _dist_point_segment(p: Point, a: Point, b: Point) -> float:
    abx, aby = b[0] - a[0], b[1] - a[1]
    l2 = abx * abx + aby * aby
    t = 0.0 if l2 < 1e-12 else max(0.0, min(1.0, ((p[0] - a[0]) * abx + (p[1] - a[1]) * aby) / l2))
    return math.hypot(p[0] - (a[0] + t * abx), p[1] - (a[1] + t * aby))


def _road(painter: Painter, road: planmod.RoadPlan, others: Sequence[planmod.RoadPlan] = ()) -> None:
    """Edge lines and lane dashes of one road; they stop where another road crosses (intersections are unpainted)."""
    w = road.width
    ew = MK.road_edge_w
    cl = list(road.centerline)
    cross = []
    for o in others:
        pts = list(o.centerline) + ([o.centerline[0]] if o.closed and o.centerline else [])
        if len(pts) >= 2:
            cross.append((o.width / 2.0 + ew, pts))

    def free(p: Point) -> bool:
        for hw, pts in cross:
            for i in range(len(pts) - 1):
                if _dist_point_segment(p, pts[i], pts[i + 1]) < hw:
                    return False
        return True

    for sg in (1.0, -1.0):
        off = g.offset_polyline(cl, sg * (w / 2 - ew / 2), closed=road.closed)
        if len(off) < 2:
            continue
        line = list(off) + ([off[0]] if road.closed else [])
        if not cross:
            if road.closed:
                outer, inner = g.polyline_strip(off, ew, closed=True)
                painter.add_ring("RoadWhite", outer, inner, zone='ROAD', kind="road_edge")
            else:
                painter.add("RoadWhite", g.polyline_strip(off, ew), 'ROAD', False, kind="road_edge")
            continue
        for run in _runs(_split_polyline(line, 1.0), free):
            if len(run) >= 2:
                painter.add("RoadWhite", g.polyline_strip(run, ew), 'ROAD', False, kind="road_edge")
    if not road.closed and w > 6.0:
        total = g.polyline_length(cl)
        for a, b in _dashes(0.0, total, MK.road_lane_dash, MK.road_lane_gap):
            pa, _ = g.point_along(cl, a)
            pb, _ = g.point_along(cl, b)
            if cross and not (free(pa) and free(pb) and free(g.lerp(pa, pb, 0.5))):
                continue
            painter.add("RoadWhite", g.strip(pa, pb, ew), 'ROAD', False, kind="road_lane")
    for pos, d in road.stop_bars:
        n = g.perp_left(d)
        a = (pos[0] - n[0] * w / 2, pos[1] - n[1] * w / 2)
        b = (pos[0] + n[0] * w / 2, pos[1] + n[1] * w / 2)
        painter.add("RoadWhite", g.strip(a, b, MK.stop_bar_w), 'ROAD', False, kind="road_stop_bar")


def marking_catalog() -> list[str]:
    return ["threshold_bar", "threshold_stripe", "designator", "centerline_stripe", "side_stripe", "aiming_point", "tdz_bar",
            "displaced_arrow", "displaced_chevron", "overrun_chevron", "shoulder_stripe", "arresting_disc",
            "taxi_centerline", "taxi_edge", "hold_a", "hold_b", "enhanced_cl", "sphps",
            "apron_edge", "taxilane", "lead_in", "stop_bar", "spot_number", "tie_down", "grounding", "hydrant",
            "restricted_boundary", "helipad", "compass", "road_edge", "road_lane", "road_stop_bar"]
