"""Site plan: pure-Python layout computed from the settings.

``build_plan(settings)`` turns the property groups into a :class:`Plan` — plain
dataclasses in the base frame (see ``core.geom2d``) that every geometry builder
consumes. Nothing in this module touches ``bpy`` objects, which keeps the layout
logic unit-testable and deterministic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..core import geom2d as g
from ..core.units import ft

Point = g.Point
Poly = g.Poly

# --------------------------------------------------------------------------- zones
Z_RUNWAY = "RUNWAY"
Z_RUNWAY_ASPHALT = "RUNWAY_ASPHALT"
Z_OVERRUN = "OVERRUN"
Z_TAXIWAY = "TAXIWAY"
Z_APRON = "APRON"
Z_PAD = "PAD"              # small pads (helipad, compass, hot cargo) — concrete
Z_SHOULDER = "SHOULDER"
Z_ROAD = "ROAD"

ZONE_PRIORITY = {Z_RUNWAY: 100, Z_RUNWAY_ASPHALT: 99, Z_OVERRUN: 90, Z_APRON: 80, Z_PAD: 79, Z_TAXIWAY: 70, Z_SHOULDER: 10, Z_ROAD: 5}

# --------------------------------------------------------------------------- aircraft data (UFC 3-260-01 Table 6-1 style)
AIRCRAFT = {
    #            wingspan  length  nose->nosewheel  wingtip clearance  lead-in R
    'FIGHTER':    dict(span=ft(33), length=ft(50), nose=ft(11), clearance=ft(25), radius=ft(50)),
    'TRAINER':    dict(span=ft(28), length=ft(46), nose=ft(9), clearance=ft(25), radius=ft(40)),
    'TRANSPORT':  dict(span=ft(133), length=ft(98), nose=ft(12), clearance=ft(30), radius=ft(60)),
    'HEAVY':      dict(span=ft(170), length=ft(174), nose=ft(17), clearance=ft(50), radius=ft(90)),
    'HELICOPTER': dict(span=ft(64), length=ft(65), nose=ft(10), clearance=ft(40), radius=ft(25)),
}
HAS_SIZE = {   # NATO 3rd generation shelter (outer): length, width, height
    'NATO3': (36.0, 24.0, 10.0),
    'TABVEE': (34.0, 22.0, 9.0),
    'EARTH': (38.0, 28.0, 11.0),
}


# --------------------------------------------------------------------------- dataclasses
@dataclass
class RunwayPlan:
    length: float
    width: float
    half_len: float
    half_w: float
    shoulder: float
    shoulder_type: str
    surface: str
    thr_low: float                 # x of the low threshold (after displacement)
    thr_high: float
    displaced_low: float
    displaced_high: float
    overrun_low: Poly | None       # pavement polygon (includes shoulder width)
    overrun_high: Poly | None
    overrun_length: float
    overrun_type: str
    designator_low: str            # e.g. "05" / "05L"
    designator_high: str
    numerals_low: str              # "05"
    numerals_high: str
    letter_low: str                # "" / "L" / "C" / "R"
    letter_high: str
    heading_low: float
    arresting: list[float]         # cable stations (x)
    arresting_type: str
    crown_percent: float
    ils_low: bool
    ils_high: bool
    grooved: bool
    zones: list[tuple[Poly, str]] = field(default_factory=list)   # pavement pieces
    hybrid_end: float = 0.0

    def crown(self, x: float, y: float) -> float:
        """Elevation of the runway surface (crown) at a base-frame point; 0 off-runway."""
        if self.crown_percent <= 0.0:
            return 0.0
        if abs(y) > self.half_w or x < -self.half_len or x > self.half_len:
            return 0.0
        return (self.half_w - abs(y)) * self.crown_percent * 0.01

    @property
    def polygon(self) -> Poly:
        return g.rect(0.0, 0.0, self.length, self.width)

    @property
    def paved_half_w(self) -> float:
        return self.half_w + self.shoulder

    def is_concrete_at(self, x: float) -> bool:
        if self.surface == 'PCC':
            return True
        if self.surface == 'ASPHALT':
            return False
        return abs(x) > self.half_len - self.hybrid_end


@dataclass
class HoldLine:
    kind: str                       # 'A' or 'B'
    center: Point                   # on the taxiway centreline
    direction: Point                # unit vector pointing TOWARD the runway
    length: float                   # full line length (across taxiway incl. shoulders)
    taxiway: str = ""
    runway_text: str = ""           # e.g. "05-23"


@dataclass
class TaxiwayPlan:
    name: str
    kind: str
    side: int                      # +1 left (+Y), -1 right
    width: float
    shoulder: float
    centerline: list[Point]        # from runway centreline (or start) outward
    polys: list[Poly]              # pavement pieces (strip + fillets)
    fillet_polys: list[Poly]
    hold_lines: list[HoldLine]
    fillet_radius: float
    edge_lights: bool
    centerline_lights: bool
    signs: bool
    angle: float = 90.0
    exit_dir: str = 'HIGH'
    offset: float = 0.0
    runway_junction: Point | None = None     # where the centreline meets the runway edge
    runway_tangents: tuple[Point, Point] | None = None   # fillet tangent points on the runway edge (x positions)
    parallel_junction: Point | None = None   # where a connector meets the parallel taxiway centreline

    @property
    def edges(self) -> tuple[list[Point], list[Point]]:
        """Left and right edge polylines of the strip (untrimmed)."""
        hw = self.width * 0.5
        return g.offset_polyline(self.centerline, hw), g.offset_polyline(self.centerline, -hw)


@dataclass
class Spot:
    position: Point                 # nose-wheel stop point
    heading: float                  # radians, direction the aircraft faces
    aircraft: str
    number: str
    lead_in: list[Point]            # polyline from the taxilane to the stop point


@dataclass
class ApronPlan:
    name: str
    kind: str
    side: int
    poly: Poly
    aircraft: str
    spots: list[Spot]
    taxilanes: list[list[Point]]           # centrelines painted on the apron
    access_polys: list[Poly]               # stub taxiways (pavement) joining the parallel taxiway
    access_centerlines: list[list[Point]]
    edge_loop: Poly                        # apron boundary for edge marking
    structures: list["StructurePlan"]      # HAS / revetments / deflectors that belong to this apron
    floodlights: list[Point]
    hydrants: list[Point]
    tie_downs: list[Point]
    grounding: list[Point]
    loop_centerline: list[Point] | None = None   # HAS loop taxiway centreline (closed)
    loop_width: float = 0.0
    station: float = 0.0
    zone: str = Z_APRON
    extra_markings: dict[str, Any] = field(default_factory=dict)   # helipad H, compass rose etc.
    access_taxiways: list["TaxiwayPlan"] = field(default_factory=list)   # dedicated stubs (also appended to plan.taxiways)


@dataclass
class RoadPlan:
    name: str
    kind: str                       # PERIMETER / SERVICE / FLIGHTLINE / ECP
    centerline: list[Point]
    width: float
    closed: bool = False
    stop_bars: list[tuple[Point, Point]] = field(default_factory=list)   # (position, direction)


@dataclass
class StructurePlan:
    kind: str
    position: Point
    rotation: float                 # radians about Z
    size: tuple[float, float, float]
    params: dict[str, Any] = field(default_factory=dict)
    name: str = ""


@dataclass
class FencePlan:
    loop: list[Point]
    height: float
    double: bool
    gap: float
    gates: list[tuple[Point, Point]]      # (position on the loop, outward direction)
    corners: list[Point]


@dataclass
class Plan:
    runway: RunwayPlan
    taxiways: list[TaxiwayPlan]
    aprons: list[ApronPlan]
    roads: list[RoadPlan]
    structures: list[StructurePlan]
    fence: FencePlan | None
    airside: int                          # side (+1/-1) where the main parallel taxiway / aprons are
    bounds: tuple[float, float, float, float]      # everything inside the fence
    site_bounds: tuple[float, float, float, float] # incl. terrain margin
    hold_setback: float
    settings: Any = None
    pavement: list[tuple[Poly, str]] = field(default_factory=list)   # all pavement polygons with zone ids
    warnings: list[str] = field(default_factory=list)
    wind_cones: list[Point] = field(default_factory=list)
    navaids: list[StructurePlan] = field(default_factory=list)

    def parallel_taxiway(self, side: int) -> TaxiwayPlan | None:
        best = None
        for t in self.taxiways:
            if t.kind == 'PARALLEL' and t.side == side:
                if best is None or t.offset < best.offset:
                    best = t
        return best


# --------------------------------------------------------------------------- helpers
def _side(v: str) -> int:
    return 1 if v == 'LEFT' else -1


def runway_designators(heading_deg: float, leading_zero: bool, suffix: str) -> tuple[str, str, str, str, str, str]:
    """(designator_low, designator_high, numerals_low, numerals_high, letter_low, letter_high)."""
    n = int(round((heading_deg % 360.0) / 10.0))
    if n == 0:
        n = 36
    r = (n + 18 - 1) % 36 + 1
    fmt = "{:02d}" if leading_zero else "{:d}"
    nl, nh = fmt.format(n), fmt.format(r)
    letter_low = "" if suffix == 'NONE' else suffix
    # the reciprocal end swaps L and R (C stays C)
    swap = {'L': 'R', 'R': 'L', 'C': 'C', '': ''}
    letter_high = swap[letter_low]
    return nl + letter_low, nh + letter_high, nl, nh, letter_low, letter_high


def hold_setback_for(aircraft_classes: set[str], elevation: float) -> float:
    """Hold line distance from the runway EDGE: 175 ft (= 250 ft from CL of a 150 ft runway, FAA C/D & UFC practice), 205 ft for spans > 171 ft, +1 ft per 100 ft of elevation."""
    base = ft(175)
    if 'TRANSPORT' in aircraft_classes or 'HELICOPTER' in aircraft_classes:
        base = max(base, ft(175))
    if 'HEAVY' in aircraft_classes:
        base = max(base, ft(205))
    base += max(0.0, elevation) / 100.0
    return base


def _junction_fillets(edge_polys: tuple[list[Point], list[Point]], host_y: float, side: int,
                      radius: float, toward_host: bool, max_tangent: float = 90.0) -> tuple[list[Poly], list[Point]]:
    """Fillets where the two edges of a taxiway strip cross a horizontal host edge line (y = host_y).

    ``toward_host`` is True when the strip *starts* at the host (runway) and False when it *ends*
    at the host (parallel taxiway). Returns (fillet polygons, tangent points on the host edge).
    """
    fillets: list[Poly] = []
    tangents: list[Point] = []
    for edge in edge_polys:
        pts = edge if toward_host else edge[::-1]      # walk away from the host
        # find the first segment that crosses the host line
        corner = None
        d = None
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            if (a[1] - host_y) * (b[1] - host_y) <= 0 and abs(b[1] - a[1]) > 1e-9:
                t = (host_y - a[1]) / (b[1] - a[1])
                corner = (a[0] + (b[0] - a[0]) * t, host_y)
                # direction along the edge, away from the host
                j = i + 1
                nxt = pts[min(j + 1, len(pts) - 1)] if (j + 1 < len(pts)) else b
                d = g.normalize((b[0] - a[0], b[1] - a[1])) if g.length(corner, b) > 1e-3 else g.normalize((nxt[0] - b[0], nxt[1] - b[1]))
                break
        if corner is None or d is None:
            continue
        if d[1] * side < 0:
            d = (-d[0], -d[1])
        # which way along the host edge is "away from the strip"? the other edge is on the opposite side
        other = edge_polys[1] if edge is edge_polys[0] else edge_polys[0]
        ox = sum(p[0] for p in other) / len(other)
        away = (1.0, 0.0) if corner[0] > ox else (-1.0, 0.0)
        th = math.acos(max(-1.0, min(1.0, g.dot(away, d))))
        if th < 1e-3 or th > math.pi - 1e-3:
            continue
        r = radius
        tl = g.fillet_tangent_length(th, r)
        if tl > max_tangent:                      # acute throat: limit the lead-in length
            r = max_tangent * math.tan(th * 0.5)
            tl = max_tangent
        f = g.fillet_corner(corner, away, d, r)
        if f:
            fillets.append(f)
            tangents.append((corner[0] + away[0] * tl, host_y))
    return fillets, tangents


def _taxiway_from_centerline(cl: list[Point], width: float, side: int, runway_half_w: float,
                             par_y: float | None, par_half_w: float | None, r_run: float, r_par: float
                             ) -> tuple[list[Poly], list[Poly], tuple[Point, Point] | None, Point]:
    """Strip + fillets for a taxiway whose centreline starts on the runway centreline and ends on
    the parallel taxiway centreline (or free). Returns (strip polys, fillets, runway tangents, runway junction)."""
    strip = g.polyline_strip(cl, width)
    edges = (g.offset_polyline(cl, width * 0.5), g.offset_polyline(cl, -width * 0.5))
    fillets, tang = _junction_fillets(edges, side * runway_half_w, side, r_run, True)
    if par_y is not None and par_half_w is not None:
        f2, _ = _junction_fillets(edges, par_y - side * par_half_w, -side, r_par, False)
        fillets.extend(f2)
    # junction of the centreline with the runway edge
    junction = cl[0]
    for i in range(len(cl) - 1):
        a, b = cl[i], cl[i + 1]
        if (a[1] - side * runway_half_w) * (b[1] - side * runway_half_w) <= 0 and abs(b[1] - a[1]) > 1e-9:
            t = (side * runway_half_w - a[1]) / (b[1] - a[1])
            junction = (a[0] + (b[0] - a[0]) * t, side * runway_half_w)
            break
    tangents = (tang[0], tang[1]) if len(tang) == 2 else None
    return [strip], fillets, tangents, junction


def high_speed_centerline(station: float, side: int, dir_x: float, angle_deg: float, target_y: float,
                          radius: float = ft(1500)) -> list[Point]:
    """FAA/UFC acute exit: arc of ``radius`` turning ``angle`` off the runway CL, then a straight tangent."""
    ang = math.radians(angle_deg)
    # arc centre is offset laterally from the exit point
    cx, cy = station, side * radius
    pts: list[Point] = []
    n = max(8, int(radius * ang / 12.0))
    for i in range(n + 1):
        a = ang * i / n
        # angle measured from the runway direction; point on arc
        x = cx + dir_x * radius * math.sin(a)
        y = cy - side * radius * math.cos(a)
        pts.append((x, y))
    # straight tangent to the parallel taxiway centreline
    d = (dir_x * math.cos(ang), side * math.sin(ang))
    last = pts[-1]
    remaining = (abs(target_y) - abs(last[1])) / math.sin(ang)
    if remaining > 0:
        pts.append((last[0] + d[0] * remaining, target_y))
    return pts


def _rounded_loop(cx: float, cy: float, lx: float, ly: float, r: float, seg_len: float = 4.0) -> list[Point]:
    pts = [(cx - lx / 2, cy - ly / 2), (cx + lx / 2, cy - ly / 2), (cx + lx / 2, cy + ly / 2), (cx - lx / 2, cy + ly / 2)]
    return g.round_polyline(pts, r, closed=True, max_seg_len=seg_len)


# --------------------------------------------------------------------------- main
def build_plan(s) -> Plan:
    """Compute the site plan from a MAD_BaseSettings property group (or duck-typed object)."""
    rw = s.runway
    warnings: list[str] = []
    L, W = rw.length, rw.width
    hl, hw = L * 0.5, W * 0.5
    sh = rw.shoulder_width
    d_low, d_high, n_low, n_high, l_low, l_high = runway_designators(rw.heading, s.markings.leading_zero, rw.suffix)

    # overruns (USAF: overrun width = runway + shoulders)
    ov_len = rw.overrun_length if rw.overrun_type != 'NONE' else 0.0
    ov_w = W          # UFC Table 3-4: paved overrun width = runway width (shoulder strips are graded turf)
    ov_low = g.rect_xy(-hl - ov_len, -ov_w / 2, -hl, ov_w / 2) if (ov_len > 0 and rw.overrun_low) else None
    ov_high = g.rect_xy(hl, -ov_w / 2, hl + ov_len, ov_w / 2) if (ov_len > 0 and rw.overrun_high) else None

    arresting: list[float] = []
    if rw.arresting_type != 'NONE':
        if rw.arresting_low:
            arresting.append(-hl + rw.displaced_low + rw.arresting_distance)
        if rw.arresting_high:
            arresting.append(hl - rw.displaced_high - rw.arresting_distance)
        if rw.arresting_mid:
            arresting.append(0.0)

    runway = RunwayPlan(
        length=L, width=W, half_len=hl, half_w=hw, shoulder=sh, shoulder_type=rw.shoulder_type, surface=rw.surface,
        thr_low=-hl + rw.displaced_low, thr_high=hl - rw.displaced_high,
        displaced_low=rw.displaced_low, displaced_high=rw.displaced_high,
        overrun_low=ov_low, overrun_high=ov_high, overrun_length=ov_len, overrun_type=rw.overrun_type,
        designator_low=d_low, designator_high=d_high, numerals_low=n_low, numerals_high=n_high,
        letter_low=l_low, letter_high=l_high, heading_low=rw.heading,
        arresting=arresting, arresting_type=rw.arresting_type, crown_percent=rw.crown_percent,
        ils_low=rw.ils_low, ils_high=rw.ils_high, grooved=rw.grooved, hybrid_end=rw.hybrid_end_length,
    )
    # runway pavement zones
    if rw.surface == 'HYBRID' and rw.hybrid_end_length * 2 < L:
        e = rw.hybrid_end_length
        runway.zones.append((g.rect_xy(-hl, -hw, -hl + e, hw), Z_RUNWAY))
        runway.zones.append((g.rect_xy(-hl + e, -hw, hl - e, hw), Z_RUNWAY_ASPHALT))
        runway.zones.append((g.rect_xy(hl - e, -hw, hl, hw), Z_RUNWAY))
    else:
        runway.zones.append((runway.polygon, Z_RUNWAY if rw.surface != 'ASPHALT' else Z_RUNWAY_ASPHALT))
    if ov_low is not None and rw.overrun_type == 'PAVED':
        runway.zones.append((ov_low, Z_OVERRUN))
    if ov_high is not None and rw.overrun_type == 'PAVED':
        runway.zones.append((ov_high, Z_OVERRUN))

    # ---------------------------------------------------------------- aircraft classes / hold setback
    classes = {a.aircraft for a in s.aprons if a.enabled} or {'FIGHTER'}
    hold_setback = hold_setback_for(classes, rw.elevation)

    # ---------------------------------------------------------------- taxiways
    taxiways: list[TaxiwayPlan] = []
    parallels: dict[int, TaxiwayPlan] = {}
    items = [t for t in s.taxiways if t.enabled]
    for t in items:
        if t.kind != 'PARALLEL':
            continue
        side = _side(t.side)
        y = side * t.offset
        x0, x1 = -hl - (ov_len if ov_low is not None else 0.0) * 0.0, hl
        # parallel taxiway spans the full runway length (thresholds) and a little beyond for end connectors
        ext = t.width  # extend past runway ends by one width so END connectors have something to join
        cl = [(-hl - ext, y), (hl + ext, y)]
        strip = g.strip(cl[0], cl[1], t.width)
        tp = TaxiwayPlan(name=t.name, kind='PARALLEL', side=side, width=t.width, shoulder=t.shoulder_width,
                         centerline=cl, polys=[strip], fillet_polys=[], hold_lines=[], fillet_radius=t.fillet_radius,
                         edge_lights=t.edge_lights, centerline_lights=t.centerline_lights, signs=t.signs, offset=t.offset)
        taxiways.append(tp)
        if side not in parallels or t.offset < parallels[side].offset:
            parallels[side] = tp
    airside = 1 if 1 in parallels else (-1 if -1 in parallels else 1)

    runway_text = f"{n_low}-{n_high}"
    r_run_default = ft(125)
    for t in items:
        if t.kind == 'PARALLEL':
            continue
        side = _side(t.side)
        par = parallels.get(side)
        target_y = side * (par.offset if par else t.offset)
        par_half = (par.width * 0.5) if par else None
        par_y = target_y if par else None
        r_run = t.fillet_radius
        r_par = t.fillet_radius * 1.2
        if t.kind == 'END':
            station = (-1.0 if t.station <= 0 else 1.0) * (hl - t.width * 0.5)
        else:
            station = max(-hl + t.width, min(hl - t.width, t.station))
        if t.kind == 'HIGH_SPEED':
            dir_x = 1.0 if t.exit_dir == 'HIGH' else -1.0
            cl = high_speed_centerline(station, side, dir_x, t.angle, target_y)
        else:
            cl = [(station, 0.0), (station, target_y)]
        polys, fillets, tang, junction = _taxiway_from_centerline(cl, t.width, side, hw, par_y, par_half, r_run, r_par)
        tp = TaxiwayPlan(name=t.name, kind=t.kind, side=side, width=t.width, shoulder=t.shoulder_width,
                         centerline=cl, polys=polys, fillet_polys=fillets, hold_lines=[], fillet_radius=t.fillet_radius,
                         edge_lights=t.edge_lights, centerline_lights=t.centerline_lights, signs=t.signs,
                         angle=t.angle if t.kind == 'HIGH_SPEED' else 90.0, exit_dir=t.exit_dir, offset=abs(target_y),
                         runway_junction=junction, runway_tangents=tang,
                         parallel_junction=cl[-1] if par else None)
        # hold lines: perpendicular to the taxiway centreline at the standard distance from the runway edge
        if t.hold != 'NONE':
            setback = t.hold_distance if t.hold_distance > 0 else hold_setback
            hp, d = g.point_along(cl, _arc_length_to_y(cl, side * (hw + setback)))
            toward = (-d[0], -d[1])
            length = t.width + 2 * t.shoulder_width
            tp.hold_lines.append(HoldLine('A', hp, toward, length, t.name, runway_text))
            want_b = t.hold == 'PATTERN_B' or (t.hold == 'AUTO' and _in_ils_critical_area(runway, station))
            if want_b:
                dist_b = max(ft(500), hw + setback + ft(150))
                if dist_b < abs(target_y) - (par.width if par else 0.0):
                    hb, db = g.point_along(cl, _arc_length_to_y(cl, side * dist_b))
                    tp.hold_lines.append(HoldLine('B', hb, (-db[0], -db[1]), length, t.name, runway_text))
        taxiways.append(tp)

    # ---------------------------------------------------------------- aprons
    aprons: list[ApronPlan] = []
    structures: list[StructurePlan] = []
    spot_counter = 1
    access_letter = ord('J')
    for a in s.aprons:
        if not a.enabled:
            continue
        side = _side(a.side)
        par = parallels.get(side)
        if par is not None:
            par_y = side * par.offset
            par_hw = par.width * 0.5
            gap = a.offset if a.offset > 0 else ft(150)
            near = par_y + side * (par_hw + gap)
            host = 'PARALLEL'
        else:
            # no parallel taxiway on this side: a dedicated taxiway runs straight from the runway (alert pads)
            par_y = side * hw
            par_hw = 0.0
            gap = a.offset if a.offset > 0 else hold_setback + ft(300)
            near = side * (hw + gap)
            host = 'RUNWAY'
        ap = _build_apron(a, side, near, par_hw, par_y, s, runway, spot_counter, hold_setback, host, taxiways, runway_text)
        for tw in ap.access_taxiways:
            if not tw.name:
                tw.name = chr(access_letter)
                access_letter += 1
                if chr(access_letter) in ('O', 'I', 'X'):
                    access_letter += 1
            taxiways.append(tw)
        spot_counter += len(ap.spots)
        aprons.append(ap)
        structures.extend(ap.structures)

    # ---------------------------------------------------------------- bounds (airfield core)
    pav: list[tuple[Poly, str]] = list(runway.zones)
    for tw in taxiways:
        for p in tw.polys + tw.fillet_polys:
            pav.append((p, Z_TAXIWAY))
    for ap in aprons:
        pav.append((ap.poly, ap.zone))
        for p in ap.access_polys:
            pav.append((p, Z_TAXIWAY))
    x0, y0, x1, y1 = g.bbox_polys([p for p, _ in pav])

    # ---------------------------------------------------------------- base structures & navaids
    plan = Plan(runway=runway, taxiways=taxiways, aprons=aprons, roads=[], structures=structures, fence=None,
                airside=airside, bounds=(x0, y0, x1, y1), site_bounds=(x0, y0, x1, y1), hold_setback=hold_setback,
                settings=s, pavement=pav, warnings=warnings)
    _place_structures(plan, s)
    _place_navaids(plan, s)
    _place_perimeter(plan, s)
    _validate(plan, s)
    return plan


def _arc_length_to_y(cl: list[Point], y: float) -> float:
    """Arc length along a polyline at which it first reaches |y| (used for hold-line placement)."""
    acc = 0.0
    for i in range(len(cl) - 1):
        a, b = cl[i], cl[i + 1]
        seg = g.length(a, b)
        if (a[1] - y) * (b[1] - y) <= 0 and abs(b[1] - a[1]) > 1e-9:
            t = (y - a[1]) / (b[1] - a[1])
            return acc + seg * max(0.0, min(1.0, t))
        acc += seg
    return acc


def _in_ils_critical_area(runway: RunwayPlan, station: float) -> bool:
    """TDZ critical area: 3,200 ft from the runway end of an ILS end (UFC Fig 6-5)."""
    if runway.ils_low and station < -runway.half_len + ft(3200):
        return True
    if runway.ils_high and station > runway.half_len - ft(3200):
        return True
    return False


# --------------------------------------------------------------------------- aprons
def _build_apron(a, side: int, near: float, par_hw: float, par_y: float, s, runway: RunwayPlan,
                 spot_start: int, hold_setback: float, host: str = 'PARALLEL',
                 taxiways: list[TaxiwayPlan] | None = None, runway_text: str = "") -> ApronPlan:
    ac = AIRCRAFT[a.aircraft]
    access_tws: list[TaxiwayPlan] = []
    far = near + side * a.depth
    cx = max(-runway.half_len + a.length / 2, min(runway.half_len - a.length / 2, a.station))
    y_near, y_far = (near, far) if near < far else (far, near)
    poly = g.rect_xy(cx - a.length / 2, y_near, cx + a.length / 2, y_far)
    spots: list[Spot] = []
    taxilanes: list[list[Point]] = []
    access_polys: list[Poly] = []
    access_cls: list[list[Point]] = []
    structures: list[StructurePlan] = []
    floods: list[Point] = []
    hydrants: list[Point] = []
    tie: list[Point] = []
    ground: list[Point] = []
    extra: dict[str, Any] = {}
    zone = Z_APRON
    loop_cl = None
    loop_w = 0.0
    kind = a.kind

    def access(xs: list[float], width: float = ft(75), radius: float = ft(100)):
        """Stub taxiways from the host (parallel taxiway edge or runway edge) to the apron near edge."""
        for x in xs:
            p0 = (x, par_y + side * par_hw)
            p1 = (x, near)
            strip = g.strip(p0, p1, width, extend0=0.0, extend1=0.0)
            fillets: list[Poly] = []
            for sgn in (1, -1):
                corner = (x + sgn * width / 2, p0[1])
                f = g.fillet_corner(corner, (sgn, 0.0), (0.0, side), radius if host == 'PARALLEL' else ft(125))
                if f:
                    fillets.append(f)
                corner2 = (x + sgn * width / 2, p1[1])
                f2 = g.fillet_corner(corner2, (sgn, 0.0), (0.0, -side), radius * 0.6)
                if f2:
                    fillets.append(f2)
            access_polys.append(strip)
            access_polys.extend(fillets)
            cl = [(x, par_y), (x, near + side * ft(10))]
            tw = TaxiwayPlan(name="", kind='ACCESS', side=side, width=width, shoulder=ft(25), centerline=cl,
                             polys=[], fillet_polys=[], hold_lines=[], fillet_radius=radius, edge_lights=True,
                             centerline_lights=False, signs=host == 'RUNWAY', offset=abs(near),
                             runway_junction=(x, side * runway.half_w) if host == 'RUNWAY' else None,
                             parallel_junction=(x, par_y) if host == 'PARALLEL' else None)
            if host == 'RUNWAY':
                hp = (x, side * (runway.half_w + hold_setback))
                tw.hold_lines.append(HoldLine('A', hp, (0.0, -float(side)), width + 2 * ft(25), "", runway_text))
            access_cls.append(cl)
            access_tws.append(tw)

    end_tw = None
    if kind == 'ARM_DEARM' and taxiways:
        cands = [t for t in taxiways if t.kind == 'END' and t.side == side and (t.centerline[0][0] > 0) == (a.station >= 0)]
        end_tw = cands[0] if cands else None
    if kind == 'ARM_DEARM' and end_tw is not None:
        # EOR pad beside the end connector, between the hold line and the parallel taxiway (UFC 6-15/6-17)
        xc = end_tw.centerline[0][0]
        outward = 1.0 if xc > 0 else -1.0
        y_a = side * (runway.half_w + hold_setback + ft(40))
        y_b = par_y - side * (par_hw + ft(40)) if host == 'PARALLEL' else side * (runway.half_w + hold_setback + a.depth)
        x_a = xc + outward * end_tw.width * 0.5
        x_b = x_a + outward * a.length
        poly = g.rect_xy(x_a, y_a, x_b, y_b)
        cx = (x_a + x_b) * 0.5
        # angled (45°) spots along the pad, noses pointing away from the base (toward the runway end)
        pitch = 1.414 * (ac['span'] + ft(10))
        usable = abs(y_b - y_a) - ft(60)
        n = a.spots if a.spots > 0 else max(1, int(usable // pitch))
        num = spot_start
        heading = math.atan2(0.0, outward) + math.radians(45) * (-side * outward)
        for i in range(n):
            y = min(y_a, y_b) + ft(30) + pitch * (i + 0.5)
            if y > max(y_a, y_b) - ft(30):
                break
            stop = (cx + outward * a.length * 0.15, y)
            lead = g.round_polyline([(xc, y - side * ft(60)), (xc + outward * end_tw.width * 0.5 + ft(10), y), stop], ac['radius'] * 0.7, max_seg_len=1.5)
            spots.append(Spot(stop, heading, a.aircraft, f"E{num}", lead))
            num += 1
        if a.floodlights:
            floods.append((x_b - outward * ft(15), (y_a + y_b) * 0.5))
        kind = 'ARM_DEARM_DONE'
    if kind in ('MAIN', 'HANGAR_LINE', 'ARM_DEARM', 'HOT_CARGO'):
        n_access = 1 if a.length < ft(600) else (2 if a.length < ft(2000) else 3)
        xs = [cx] if n_access == 1 else [cx - a.length / 2 + a.length * (i + 1) / (n_access + 1) for i in range(n_access)]
        access(xs)
        # apron taxilane parallel to the near edge, inset by half the taxilane clearance
        lane_inset = ac['span'] * 0.5 + ac['clearance'] + ft(25)
        lane_y = near + side * lane_inset
        taxilanes.append([(cx - a.length / 2 + ft(30), lane_y), (cx + a.length / 2 - ft(30), lane_y)])
        # parking rows behind the taxilane, nose toward the taxilane (flow-through style)
        pitch = ac['span'] + ac['clearance']
        usable = a.length - 2 * ft(50)
        per_row = max(1, int(usable // pitch))
        rows = max(1, a.rows)
        row_depth = ac['length'] + ac['clearance'] + ft(20)
        total = a.spots if a.spots > 0 else per_row * rows
        idx = 0
        num = spot_start
        for r in range(rows):
            stop_y = lane_y + side * (ac['radius'] + ac['nose'] + r * row_depth + (ac['length'] if kind == 'ARM_DEARM' else 0.0))
            if abs(stop_y - near) > a.depth - ft(30):
                break
            for i in range(per_row):
                if idx >= total:
                    break
                x = cx - usable / 2 + pitch * (i + 0.5)
                if kind == 'ARM_DEARM':
                    heading = math.atan2(side, 0.0) + math.radians(45) * side
                else:
                    heading = math.atan2(side, 0.0)          # facing away from the taxilane (nose toward the far edge)... nose-in toward lane:
                    heading = math.atan2(-side, 0.0)         # nose toward the apron taxilane (flow-through parking)
                if r == 0:
                    lead = _lead_in(taxilanes[0], (x, lane_y), (x, stop_y), ac['radius'], side)
                else:
                    lead = [(x, lane_y), (x, stop_y)]
                spots.append(Spot((x, stop_y), heading, a.aircraft, str(num), lead))
                num += 1
                idx += 1
                if a.tie_downs:
                    for ddx, ddy in ((-ac['span'] * 0.3, -ac['length'] * 0.2), (ac['span'] * 0.3, -ac['length'] * 0.2), (0.0, ac['length'] * 0.35)):
                        tie.append((x + ddx, stop_y + side * ddy))
                    ground.append((x + ac['span'] * 0.45, stop_y))
        if a.hydrants:
            for i in range(per_row):
                x = cx - usable / 2 + pitch * (i + 0.5)
                hydrants.append((x, lane_y + side * (ac['radius'] + ac['nose'] - ft(15))))
        if a.floodlights:
            nfl = max(2, int(a.length // ft(400)) + 1)
            for i in range(nfl):
                x = cx - a.length / 2 + a.length * i / (nfl - 1)
                floods.append((x, far - side * ft(15)))
        if kind == 'HANGAR_LINE':
            n_h = max(1, s.structures.hangars)
            hl_ = min(60.0, a.length / max(n_h, 1) * 0.7)
            for i in range(n_h):
                x = cx - a.length / 2 + a.length * (i + 0.5) / n_h
                structures.append(StructurePlan('HANGAR', (x, far + side * 25.0), math.atan2(-side, 0.0), (hl_, 45.0, 16.0), {'doors': True}, f"Hangar {i + 1}"))
        if kind == 'HOT_CARGO':
            zone = Z_PAD
            extra['restricted_boundary'] = True
        if a.blast_deflector:
            structures.append(StructurePlan('BLAST_DEFLECTOR', (cx, far - side * 3.0), math.atan2(side, 0.0), (min(a.length * 0.6, 60.0), 1.0, 3.5), {}, "Blast deflector"))

    elif kind == 'ALERT':
        # spine taxilane going away from the parallel taxiway with angled stubs (Christmas tree)
        access([cx])
        spine = [(cx, near), (cx, far - side * ft(60))]
        taxilanes.append(spine)
        n = a.spots if a.spots > 0 else 8
        pitch = ac['span'] + ac['clearance'] + ft(30)
        num = spot_start
        for i in range(n):
            k = i // 2
            sgn = 1 if i % 2 == 0 else -1
            y = near + side * (ft(120) + k * pitch)
            if abs(y - near) > a.depth - ft(80):
                break
            ang = math.radians(45)
            dx = sgn * math.cos(ang)
            dy = side * math.sin(ang)
            stub_len = ac['length'] + ac['radius'] + ft(30)
            end = (cx + dx * stub_len, y + dy * stub_len)
            lead = g.round_polyline([(cx, y - side * ft(40)), (cx, y), end], ac['radius'], max_seg_len=2.0)
            heading = math.atan2(-dy, -dx)      # nose toward the spine (quick exit)
            spots.append(Spot(end, heading, a.aircraft, str(num), lead))
            num += 1
            if s.structures.revetments:
                structures.append(StructurePlan('REVETMENT', end, math.atan2(dy, dx), (ac['span'] + 6.0, ac['length'] + 6.0, 3.6), {}, f"Revetment {num - 1}"))
        if a.floodlights:
            floods.extend([(cx - a.length * 0.4, far - side * ft(15)), (cx + a.length * 0.4, far - side * ft(15))])
        zone = Z_APRON
        # the apron polygon becomes the envelope of the stubs
    elif kind == 'HAS_LOOP':
        # loop taxiway; shelters both sides; the "apron" polygon is the loop pavement itself
        loop_w = ft(50)
        access([cx], width=ft(50))
        lx = a.length - 2 * ft(60)
        ly = a.depth - 2 * ft(60)
        cy = near + side * a.depth / 2
        loop_cl = _rounded_loop(cx, cy, lx, ly, ft(120), seg_len=6.0)
        # spine from access to loop near edge
        taxilanes.append([(cx, near), (cx, cy - side * ly / 2)])
        shelters = max(1, a.shelters)
        size = HAS_SIZE.get(s.structures.has_style, HAS_SIZE['NATO3'])
        perim = g.polyline_length(loop_cl, closed=True)
        # place shelters on the OUTER side of the loop, spaced evenly, skipping the access side
        step = perim / shelters
        pts = g.resample_polyline(loop_cl, step, closed=True)
        placed = 0
        stub_len = size[0] * 0.5 + ft(60)
        for i, p in enumerate(pts[:shelters + 1]):
            if placed >= shelters:
                break
            q, tang = g.point_along(loop_cl + [loop_cl[0]], step * i + step * 0.5)
            outward = g.perp_left(tang)
            # ensure outward points away from the loop centre
            if g.dot(outward, (q[0] - cx, q[1] - cy)) < 0:
                outward = (-outward[0], -outward[1])
            # skip positions too close to the access stub
            if abs(q[0] - cx) < ft(80) and (q[1] - near) * side < ft(80):
                continue
            centre = (q[0] + outward[0] * stub_len, q[1] + outward[1] * stub_len)
            rot = math.atan2(outward[1], outward[0])
            structures.append(StructurePlan('HAS', centre, rot, size, {'style': s.structures.has_style}, f"HAS {placed + 1}"))
            lead = [q, (q[0] + outward[0] * (stub_len - size[0] * 0.5 - 2.0), q[1] + outward[1] * (stub_len - size[0] * 0.5 - 2.0))]
            spots.append(Spot(lead[-1], rot + math.pi, a.aircraft, f"H{placed + 1}", lead))
            # stub pavement (taxilane to the shelter)
            access_polys.append(g.strip(q, lead[-1], ft(40), extend0=loop_w * 0.5, extend1=size[0] * 0.5 + 2.0))
            placed += 1
        zone = Z_TAXIWAY
        poly = g.polyline_strip(loop_cl, loop_w, closed=True)[0]   # outer loop (inner handled as hole by pavement builder)
    elif kind == 'HELIPAD':
        zone = Z_PAD
        size = ft(100)
        poly = g.rect(cx, near + side * (size / 2 + ft(20)), size, size)
        access([cx], width=ft(40), radius=ft(40))
        extra['helipad'] = dict(center=(cx, near + side * (size / 2 + ft(20))), size=size, heading=math.atan2(side, 0.0))
    elif kind == 'COMPASS':
        zone = Z_PAD
        size = ft(120)
        poly = g.rect(cx, near + side * (size / 2 + ft(20)), size, size)
        access([cx], width=ft(50), radius=ft(50))
        extra['compass'] = dict(center=(cx, near + side * (size / 2 + ft(20))), size=size)
    elif kind == 'TRIM_PAD':
        zone = Z_APRON
        poly = g.rect(cx, near + side * a.depth / 2, a.length, a.depth)
        access([cx], width=ft(50))
        spots.append(Spot((cx, near + side * a.depth * 0.45), math.atan2(side, 0.0), a.aircraft, "RUN-UP", [(cx, near), (cx, near + side * a.depth * 0.45)]))
        structures.append(StructurePlan('BLAST_DEFLECTOR', (cx, near + side * (a.depth - 8.0)), math.atan2(side, 0.0), (min(a.length * 0.7, 40.0), 1.0, 4.0), {}, "Blast deflector"))
        if s.structures.hush_house:
            structures.append(StructurePlan('HUSH_HOUSE', (cx + a.length / 2 + 40.0, near + side * a.depth * 0.5), math.atan2(side, 0.0), (45.0, 22.0, 12.0), {}, "Hush house"))

    edge_loop = g.ensure_ccw(poly)
    kind = a.kind
    return ApronPlan(name=a.name, kind=kind, side=side, poly=poly, aircraft=a.aircraft, spots=spots, taxilanes=taxilanes,
                     access_polys=access_polys, access_centerlines=access_cls, edge_loop=edge_loop, structures=structures,
                     floodlights=floods, hydrants=hydrants, tie_downs=tie, grounding=ground, loop_centerline=loop_cl,
                     loop_width=loop_w, station=cx, zone=zone, extra_markings=extra, access_taxiways=access_tws)


def _lead_in(lane: list[Point], on_lane: Point, stop: Point, radius: float, side: int) -> list[Point]:
    """Lead-in line: along the taxilane, then a 90° arc into the spot, then straight to the stop bar."""
    x, y = on_lane
    # approach from the -x direction along the lane, turn toward the spot
    start = (x - radius - ft(20), y)
    corner = (x, y)
    return g.round_polyline([start, corner, stop], radius, max_seg_len=1.5)


# --------------------------------------------------------------------------- structures
def _place_structures(plan: Plan, s) -> None:
    st = s.structures
    rw = plan.runway
    side = plan.airside
    x0, y0, x1, y1 = plan.bounds
    # flight-line depth: the far edge of the airside pavement
    far_y = y1 if side > 0 else y0
    back = far_y + side * 60.0              # start of the "flight line" building strip
    par = plan.parallel_taxiway(side)
    par_y = side * (par.offset if par else ft(1000))
    main = next((a for a in plan.aprons if a.kind == 'MAIN'), None)
    main_x = main.station if main else 0.0
    if not st.enabled:
        return
    # control tower: between the parallel taxiway and the aprons, near mid-field, clear of the taxiway OFA
    if st.control_tower:
        tx = main_x + (main.poly[1][0] - main.poly[0][0]) / 2 + 120.0 if main else 200.0
        plan.structures.append(StructurePlan('TOWER', (tx, far_y + side * 40.0), 0.0, (12.0, 12.0, st.tower_height), {}, "Control tower"))
        if st.lighting_vault:
            plan.structures.append(StructurePlan('VAULT', (tx + 40.0, far_y + side * 45.0), 0.0, (10.0, 7.0, 3.6), {}, "Lighting vault"))
    # fire station: mid-field, on the flight line with direct access
    if st.fire_station:
        plan.structures.append(StructurePlan('FIRE_STATION', (main_x - (main.poly[1][0] - main.poly[0][0]) / 2 - 90.0 if main else -150.0, far_y + side * 35.0),
                                             math.atan2(-side, 0.0), (40.0, 22.0, 8.0), {}, "Fire / crash rescue"))
    # hangars behind the main apron (if no HANGAR_LINE apron exists)
    if not any(a.kind == 'HANGAR_LINE' for a in plan.aprons) and st.hangars > 0 and main is not None:
        n = st.hangars
        length = main.poly[1][0] - main.poly[0][0]
        for i in range(n):
            x = main.poly[0][0] + length * (i + 0.5) / n
            plan.structures.append(StructurePlan('HANGAR', (x, back + 30.0 * side), math.atan2(-side, 0.0), (min(60.0, length / n * 0.7), 45.0, 16.0), {'doors': True}, f"Hangar {i + 1}"))
        if st.large_hangar:
            plan.structures.append(StructurePlan('HANGAR', (main.poly[1][0] + 120.0, back + 45.0 * side), math.atan2(-side, 0.0), (100.0, 80.0, 25.0), {'doors': True, 'large': True}, "Airlift hangar"))
    # squadron ops / base ops behind the hangars
    if st.ops_buildings:
        bx = main_x - 150.0 if main else -150.0
        for i in range(3):
            plan.structures.append(StructurePlan('BUILDING', (bx + i * 70.0, back + side * 110.0), 0.0, (50.0, 18.0, 7.5), {'floors': 2}, f"Squadron ops {i + 1}"))
        plan.structures.append(StructurePlan('BUILDING', (bx + 260.0, back + side * 110.0), 0.0, (36.0, 20.0, 11.0), {'floors': 3}, "Base operations"))
    # fuel farm at one end of the flight line
    if st.fuel_farm:
        fx = x1 - 150.0 if main is None else main.poly[1][0] + 250.0
        plan.structures.append(StructurePlan('FUEL_FARM', (fx, back + side * 90.0), 0.0, (120.0, 90.0, 12.0), {'tanks': st.fuel_tanks}, "POL fuel farm"))
    # munitions storage far from everything: beyond the runway end on the far side
    if st.munitions:
        mx = rw.half_len + rw.overrun_length + 500.0
        my = -side * (rw.half_w + 600.0)
        plan.structures.append(StructurePlan('MUNITIONS', (mx, my), 0.0, (260.0, 180.0, 5.0), {'igloos': st.igloos}, "Munitions storage area"))
    # arresting gear housings at each cable station
    if st.arresting_housings and rw.arresting_type != 'NONE':
        for xc in rw.arresting:
            for sgn in (1, -1):
                plan.structures.append(StructurePlan('ARRESTING_HOUSING', (xc, sgn * (rw.paved_half_w + ft(40))), 0.0 if sgn > 0 else math.pi, (6.0, 3.0, 2.6), {'station': xc, 'side': sgn}, "BAK-12 absorber"))
    # wash rack near the hangars
    if st.wash_rack and main is not None:
        plan.structures.append(StructurePlan('WASH_RACK', (main.poly[0][0] - 90.0, far_y + side * 40.0), 0.0, (30.0, 30.0, 0.3), {}, "Wash rack"))
    # blast fences behind alert pads handled per apron. Beacon tower near the tower.
    if s.lighting.beacon:
        plan.structures.append(StructurePlan('BEACON', ((main_x if main else 0.0) + 300.0, back + side * 200.0), 0.0, (3.0, 3.0, 18.0), {}, "Rotating beacon"))


def _place_navaids(plan: Plan, s) -> None:
    rw = plan.runway
    far = -plan.airside
    if not s.structures.navaids:
        return
    # ILS for the low end: localizer beyond the HIGH end, glide slope near the low threshold
    if rw.ils_low:
        plan.navaids.append(StructurePlan('LOCALIZER', (rw.half_len + rw.overrun_length + ft(1000), 0.0), 0.0, (25.0, 2.0, 3.0), {'serves': 'LOW'}, "Localizer"))
        plan.navaids.append(StructurePlan('GLIDE_SLOPE', (rw.thr_low + ft(1000), far * (rw.half_w + ft(400))), 0.0, (3.0, 3.0, 9.0), {'serves': 'LOW'}, "Glide slope"))
    if rw.ils_high:
        plan.navaids.append(StructurePlan('LOCALIZER', (-rw.half_len - rw.overrun_length - ft(1000), 0.0), math.pi, (25.0, 2.0, 3.0), {'serves': 'HIGH'}, "Localizer"))
        plan.navaids.append(StructurePlan('GLIDE_SLOPE', (rw.thr_high - ft(1000), far * (rw.half_w + ft(400))), 0.0, (3.0, 3.0, 9.0), {'serves': 'HIGH'}, "Glide slope"))
    plan.navaids.append(StructurePlan('TACAN', (ft(1500), far * (rw.half_w + ft(700))), 0.0, (6.0, 6.0, 8.0), {}, "TACAN"))
    plan.navaids.append(StructurePlan('RADAR', (-ft(1500), far * (rw.half_w + ft(900))), 0.0, (8.0, 8.0, 10.0), {}, "Surveillance radar"))
    plan.navaids.append(StructurePlan('AWOS', (ft(300), far * (rw.half_w + ft(500))), 0.0, (2.0, 2.0, 10.0), {}, "Weather sensors"))
    # wind cones: near each end and mid-field on the far side
    plan.wind_cones = [(rw.thr_low + ft(1500), far * (rw.half_w + ft(350))), (rw.thr_high - ft(1500), far * (rw.half_w + ft(350))),
                       (0.0, plan.airside * (rw.half_w + ft(350)))]
    # RSU-style supervisory position omitted (training bases only)


def _place_perimeter(plan: Plan, s) -> None:
    per = s.perimeter
    rw = plan.runway
    # gather everything that must be inside the fence
    xs, ys = [], []
    for p, _ in plan.pavement:
        for q in p:
            xs.append(q[0]); ys.append(q[1])
    for st in plan.structures + plan.navaids:
        hx = max(st.size[0], st.size[1])
        xs.extend([st.position[0] - hx, st.position[0] + hx]); ys.extend([st.position[1] - hx, st.position[1] + hx])
    for p in plan.wind_cones:
        xs.append(p[0]); ys.append(p[1])
    # approach light lanes (2,400 ft ALSF) beyond the overruns
    als_len = ft(2400)
    xs.extend([-rw.half_len - rw.overrun_length - als_len, rw.half_len + rw.overrun_length + als_len])
    m = per.margin
    x0, y0, x1, y1 = min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m
    plan.bounds = (x0, y0, x1, y1)
    tm = s.terrain.margin if s.terrain.enabled else 0.0
    plan.site_bounds = (x0 - tm, y0 - tm, x1 + tm, y1 + tm)
    if not per.enabled:
        return
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    loop = g.round_polyline(corners, 60.0, closed=True, max_seg_len=8.0)
    # gate (ECP) on the landside behind the flight line, near the main apron station
    side = plan.airside
    gy = y1 if side > 0 else y0
    main = next((a for a in plan.aprons if a.kind == 'MAIN'), None)
    gx = (main.station if main else 0.0)
    gates = [((gx, gy), (0.0, float(side)))] if per.entry_control_point else []
    plan.fence = FencePlan(loop=loop, height=per.fence_height, double=per.double_fence, gap=per.fence_gap, gates=gates, corners=corners) if per.fence else None
    if per.patrol_road:
        inset = per.fence_gap + 6.0 if per.double_fence else 6.0
        road_loop = g.round_polyline([(x0 + inset, y0 + inset), (x1 - inset, y0 + inset), (x1 - inset, y1 - inset), (x0 + inset, y1 - inset)], 55.0, closed=True, max_seg_len=8.0)
        plan.roads.append(RoadPlan("Patrol road", 'PERIMETER', road_loop, per.road_width, closed=True))
    if per.entry_control_point and gates:
        # ECP road from the gate to the flight line road
        plan.roads.append(RoadPlan("ECP road", 'ECP', [(gx, gy), (gx, gy - side * 120.0)], 7.0))
        plan.structures.append(StructurePlan('ECP', (gx, gy - side * 45.0), math.atan2(side, 0.0), (30.0, 12.0, 4.0), {}, "Entry control point"))
    if per.guard_towers:
        for c in corners:
            inset_c = (c[0] + (12.0 if c[0] == x0 else -12.0), c[1] + (12.0 if c[1] == y0 else -12.0))
            plan.structures.append(StructurePlan('GUARD_TOWER', inset_c, 0.0, (3.0, 3.0, 9.0), {}, "Guard tower"))
    # flight-line road behind the aprons / hangars
    if per.service_roads:
        yb = (max(p[1] for a in plan.aprons for p in a.poly) if side > 0 else min(p[1] for a in plan.aprons for p in a.poly)) if plan.aprons else side * ft(1300)
        yr = yb + side * 180.0
        plan.roads.append(RoadPlan("Flight line road", 'FLIGHTLINE', [(x0 + 80.0, yr), (x1 - 80.0, yr)], 7.0))
        # service road to the localizer / glide slope / far-side navaids along the far side of the runway
        far = -side
        ys_ = far * (rw.half_w + ft(1100))
        plan.roads.append(RoadPlan("Far-side service road", 'SERVICE', [(x0 + 80.0, ys_), (x1 - 80.0, ys_)], 4.5))
        for nav in plan.navaids:
            if nav.kind in ('GLIDE_SLOPE', 'TACAN', 'RADAR', 'AWOS'):
                plan.roads.append(RoadPlan(f"Road {nav.name}", 'SERVICE', [(nav.position[0], ys_), (nav.position[0], nav.position[1] - far * 12.0)], 4.0))
        for nav in plan.navaids:
            if nav.kind == 'LOCALIZER':
                plan.roads.append(RoadPlan(f"Road {nav.name}", 'SERVICE', [(nav.position[0], ys_), (nav.position[0], nav.position[1] + far * 15.0)], 4.0))
        # stop bars where the ECP road meets the flight line road
        for r in plan.roads:
            if r.kind == 'ECP':
                r.stop_bars.append(((gx, yr + side * 8.0), (0.0, -float(side))))


def _validate(plan: Plan, s) -> None:
    rw = plan.runway
    L = rw.length
    if L < ft(4000):
        plan.warnings.append("Runway shorter than 4,000 ft: aiming point and TDZ markings are omitted (UFC 3-260-04).")
    elif L < ft(7990):
        plan.warnings.append("Runway shorter than 7,990 ft: touchdown-zone marking sets near the midpoint are dropped.")
    for t in plan.taxiways:
        if t.kind in ('CONNECTOR', 'HIGH_SPEED') and abs(t.centerline[0][0]) >= rw.half_len - t.width - 1e-6:
            plan.warnings.append(f"Taxiway {t.name} station clamped to the runway extent.")
    par_sides = {t.side for t in plan.taxiways if t.kind == 'PARALLEL'}
    for t in plan.taxiways:
        if t.kind in ('CONNECTOR', 'HIGH_SPEED', 'END') and t.side not in par_sides:
            plan.warnings.append(f"Taxiway {t.name} has no parallel taxiway on its side; it ends at its own offset.")
    if any(a.kind == 'HOT_CARGO' for a in plan.aprons) and any(a.kind == 'MAIN' for a in plan.aprons):
        hc = next(a for a in plan.aprons if a.kind == 'HOT_CARGO')
        mn = next(a for a in plan.aprons if a.kind == 'MAIN')
        if abs(hc.station - mn.station) < ft(1250):
            plan.warnings.append("Hot cargo pad is closer than 1,250 ft to the main apron (explosives safety distance).")
