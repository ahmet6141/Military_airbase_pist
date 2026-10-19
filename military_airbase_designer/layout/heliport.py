"""Heliport layout: FATO / TLOF pad, helitaxiways and helicopter parking stands.

Pure Python (no ``bpy``). ``plan_heliport`` turns one heliport item into a :class:`HeliportPlan` that lives in a
*local frame*: ``u`` runs along the host taxiway (the runway direction, +X), ``v`` runs away from the host
pavement edge (``v = 0``). ``HeliportPlan.to_base`` maps (u, v) into the base frame, mirroring for the right side.

Layout (all rectangles are axis aligned in (u, v)), from the host edge outward::

    host edge  v = 0
    ACCESS     helitaxiway, u in [-w/2, w/2], v in [0, La]                       (junction fillets at v = 0)
    SPINE      helitaxiway along u, v in [La, La + w]   (only with parking stands, fillets at both T corners)
    stands     square hardstands on the far spine edge (alternating to the right / left of the access route)
    FEEDER     helitaxiway, v in [La + w, v_F0]                                  (fillets at the cross corners)
    FATO       square F x F, near edge at v_F0, TLOF (square or round) in its centre
               TURF FATO: only the (square) TLOF is paved, a LEAD strip joins it to the feeder.

Design rules (rule-of-thumb values in the spirit of ICAO Annex 14 Vol II, FAA AC 150/5390-2C and UFC 3-260-01;
verify them against the governing document before using the model for a real design):

* TLOF = 1.0 D, FATO = 1.5 D (D = overall length of the design helicopter, rotors turning)
* safety area = max(3 m, 0.25 D) around the FATO (graded, unpaved: the pavement shoulder)
* ground helitaxiway width = max(2 UC, 0.75 RD, 8 m); junction fillets 0.6 RD
* parking stand = 1.25 RD wide x (RD + 2 m) deep, one continuous hardstand row

Everything the geometry builders need to produce clean quad topology is recorded here: the breakpoints
(``breaks_u`` / ``breaks_v``) shared by neighbouring grids, the pieces and the junction fillets.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..core import geom2d as g

Point = g.Point
Poly = g.Poly

Z_HELI = "HELI"                    # dedicated quad-meshed heliport pavement (excluded from the CDT slab)

# class: overall length D (rotors turning), rotor diameter RD, undercarriage width UC (metres)
HELI_CLASSES: dict[str, dict[str, Any]] = {
    'LIGHT':  dict(label="Light (H125 / UH-1)", D=13.0, RD=10.7, UC=2.2),
    'ATTACK': dict(label="Attack (AH-64 / T129)", D=17.8, RD=14.6, UC=2.4),
    'MEDIUM': dict(label="Medium (UH-60 / T70)", D=19.8, RD=16.4, UC=2.9),
    'HEAVY':  dict(label="Heavy (CH-47 / CH-53)", D=30.2, RD=18.3, UC=3.8),
}

TLOF_K = 1.0
FATO_K = 1.5
SAFETY_K = 0.25
SAFETY_MIN = 3.0
TAXI_UC_K = 2.0
TAXI_RD_K = 0.75
TAXI_MIN = 8.0
FILLET_K = 0.6
FILLET_MIN, FILLET_MAX = 4.0, 9.0
STAND_MARGIN = 2.0
STAND_PITCH_K = 1.25
MESH_STEP = 3.0                    # target quad size (m) of every heliport grid
FILLET_SEG = 1.5                   # target segment length (m) along a junction fillet (edges and arc)

MAT_TLOF = 'PadConcrete'
MAT_FATO = 'ApronConcrete'
MAT_STAND = 'ApronConcrete'
MAT_TAXI = 'TaxiwayAsphalt'


@dataclass
class HeliPiece:
    kind: str                      # ACCESS / SPINE / FEEDER / STAND / LEAD / TLOF_SQ
    u0: float
    u1: float
    v0: float
    v1: float
    mat: str

    def poly(self) -> Poly:
        return g.rect_xy(self.u0, self.v0, self.u1, self.v1)


@dataclass
class HeliFillet:
    """Concave-corner fillet (90 degrees): corner C, unit directions ``da`` / ``db`` along the two edges, radius ``r``.

    ``a_lines`` / ``b_lines`` are the grid-line distances (ascending, 0 .. r) of the neighbouring pieces along the two
    straight edges; the L-shaped path Ta -> C -> Tb through them and the arc share one parametrisation, so the
    footprint polygon and the quad patch (``geometry.gridmesh.fillet_patch``) have the very same outline."""
    corner: Point
    da: Point
    db: Point
    r: float
    n: int
    a_lines: list[float] = field(default_factory=list)
    b_lines: list[float] = field(default_factory=list)

    def _lines(self, which: str) -> list[float]:
        lines = self.a_lines if which == 'a' else self.b_lines
        return lines or [self.r * k / self.n for k in range(self.n + 1)]

    def edge_points(self) -> list[Point]:
        """Polyline Ta -> C -> Tb along the two straight edges."""
        c = self.corner
        a = [(c[0] + self.da[0] * d, c[1] + self.da[1] * d) for d in reversed(self._lines('a'))]
        b = [(c[0] + self.db[0] * d, c[1] + self.db[1] * d) for d in self._lines('b')[1:]]
        return a + b

    def centre(self) -> Point:
        return (self.corner[0] + self.r * (self.da[0] + self.db[0]), self.corner[1] + self.r * (self.da[1] + self.db[1]))

    def arc(self) -> list[Point]:
        """Arc points paired with ``edge_points`` by cumulative path length (Ta .. Tb)."""
        o = self.centre()
        path = self.edge_points()
        cum = [0.0]
        for p, q in zip(path, path[1:]):
            cum.append(cum[-1] + math.dist(p, q))
        total = cum[-1] or 1.0
        out = []
        for cl in cum:
            a = 0.5 * math.pi * cl / total
            out.append((o[0] - self.r * (self.db[0] * math.cos(a) + self.da[0] * math.sin(a)),
                        o[1] - self.r * (self.db[1] * math.cos(a) + self.da[1] * math.sin(a))))
        return out

    def poly(self) -> Poly:
        return [self.corner] + self.arc()


@dataclass
class HeliStand:
    number: str
    center: Point                  # local, centre of the pad
    u0: float
    u1: float
    v0: float
    v1: float
    lead_in: list[Point]           # local polyline from the spine centreline to the positioning circle


@dataclass
class HeliportPlan:
    name: str
    side: int
    x_c: float                     # base-frame x of the access route
    y_host: float                  # base-frame y of the host pavement edge (v = 0)
    connected: bool                # joins a host taxiway (junction fillets, hold-free)
    hclass: str
    D: float
    RD: float
    UC: float
    tlof_shape: str                # SQUARE / ROUND
    tlof: float                    # TLOF side / diameter
    fato: float                    # FATO side
    safety: float
    fato_surface: str              # PAVED / TURF
    w_t: float                     # helitaxiway width
    r_f: float                     # junction fillet radius
    La: float                      # access length (host edge -> spine / FATO)
    v_f0: float                    # FATO near edge
    v_fc: float                    # FATO centre
    heading: float                 # H rotation (degrees, 0 = H up toward +v)
    pieces: list[HeliPiece] = field(default_factory=list)
    fillets: list[HeliFillet] = field(default_factory=list)      # junction fillets (quad patches)
    stands: list[HeliStand] = field(default_factory=list)
    strips: list[tuple[list[Point], float]] = field(default_factory=list)   # (local centreline, width) of every helitaxiway
    paint_lines: list[list[Point]] = field(default_factory=list)  # local centrelines to paint (yellow)
    edge_runs: list[tuple[list[Point], Point]] = field(default_factory=list)   # (local straight edge run, inward unit vector)
    host_half: float = 0.0                                        # half width of the host taxiway (centreline lead-on)
    tie_downs: list[Point] = field(default_factory=list)          # local
    floodlights: list[Point] = field(default_factory=list)        # local
    windsock: Point | None = None                                 # local
    row_breaks: list[float] = field(default_factory=list)         # stand boundaries along u (grid lines of the rows)
    breaks_u: list[float] = field(default_factory=list)
    breaks_v: list[float] = field(default_factory=list)
    edge_lights: bool = True
    centerline_lights: bool = False
    perimeter_lights: bool = True
    junction_u: tuple[float, float] = (0.0, 0.0)                  # host-edge opening (u range incl. fillets)

    # ------------------------------------------------------------------ frame
    def to_base(self, p: Point) -> Point:
        return (self.x_c + p[0], self.y_host + self.side * p[1])

    def poly_base(self, poly: Poly) -> Poly:
        return g.ensure_ccw([self.to_base(p) for p in poly])

    def dir_base(self, d: Point) -> Point:
        return (d[0], self.side * d[1])

    @property
    def fato_center(self) -> Point:
        return (0.0, self.v_fc)

    @property
    def fato_paved(self) -> bool:
        return self.fato_surface == 'PAVED'

    def fato_poly(self) -> Poly:
        h = self.fato * 0.5
        return g.rect_xy(-h, self.v_fc - h, h, self.v_fc + h)

    def pavement(self) -> list[tuple[Poly, str]]:
        """(base polygon, zone) of everything the CDT slab must know about (terrain holes, shoulders, ...)."""
        out: list[tuple[Poly, str]] = []
        for pc in self.pieces:
            out.append((self.poly_base(pc.poly()), Z_HELI))
        if self.fato_paved:
            out.append((self.poly_base(self.fato_poly()), Z_HELI))
        for f in self.fillets:
            out.append((self.poly_base(f.poly()), Z_HELI))
        return out

    def label_number(self, k: int) -> str:
        return str(k)


def _num(v: float) -> float:
    return round(v, 6)


def axis_breaks(vals: list[float]) -> list[float]:
    return sorted({_num(v) for v in vals})


def _edge_lines(corner: Point, d: Point, r: float, bu: list[float], bv: list[float]) -> list[float]:
    """Distances (0 .. r) from ``corner`` along axis-aligned direction ``d`` at which the shared grid has lines."""
    from ..geometry.gridmesh import axis_lines
    if abs(d[0]) > 0.5:
        lo, hi = sorted((corner[0], corner[0] + d[0] * r))
        pts = axis_lines(bu, lo, hi, MESH_STEP)
        return sorted(abs(p - corner[0]) for p in pts)
    lo, hi = sorted((corner[1], corner[1] + d[1] * r))
    pts = axis_lines(bv, lo, hi, MESH_STEP)
    return sorted(abs(p - corner[1]) for p in pts)


def plan_heliport(item, index: int, x_c: float, y_host: float, side: int, connected: bool) -> HeliportPlan:
    """Local layout of one heliport item (duck-typed property group). ``x_c`` / ``y_host`` place the access route."""
    cls = HELI_CLASSES.get(item.hclass, HELI_CLASSES['MEDIUM'])
    D, RD, UC = cls['D'], cls['RD'], cls['UC']
    tlof = max(12.0, TLOF_K * D)
    fato = max(tlof + 6.0, FATO_K * D)
    safety = max(SAFETY_MIN, SAFETY_K * D)
    w = max(TAXI_MIN, TAXI_UC_K * UC, TAXI_RD_K * RD)
    w = round(w * 2.0) / 2.0
    r_f = max(FILLET_MIN, min(FILLET_MAX, FILLET_K * RD))
    shape = item.tlof_shape
    surface = item.fato_surface
    if surface == 'TURF':
        shape = 'SQUARE'                 # the lead strip meets the pad on a straight edge
    n_st = max(0, int(item.stands))
    pad = round(RD + STAND_MARGIN)                  # stand depth
    pitch = round(STAND_PITCH_K * RD, 1)            # stand width: rotor tip clearance of 0.25 RD between neighbours

    hp = HeliportPlan(name=item.name, side=side, x_c=x_c, y_host=y_host, connected=connected, hclass=item.hclass,
                      D=D, RD=RD, UC=UC, tlof_shape=shape, tlof=round(tlof, 2), fato=round(fato, 2), safety=safety,
                      fato_surface=surface, w_t=w, r_f=r_f, La=0.0, v_f0=0.0, v_fc=0.0,
                      heading=float(item.heading), edge_lights=bool(item.edge_lights),
                      centerline_lights=bool(item.centerline_lights), perimeter_lights=bool(item.perimeter_lights))
    # ---- vertical stations
    La = float(item.route_length) if item.route_length > 0 else max(2.0 * r_f + 6.0, 24.0)
    La = max(La, 2.0 * r_f + 2.0) if n_st else max(La, r_f + 2.0)
    hp.La = La
    if n_st:
        v_sp0, v_sp1 = La, La + w                    # spine
        lf = max(pad + 6.0, 2.0 * r_f + 4.0)
        hp.v_f0 = v_sp1 + lf
    else:
        v_sp0 = v_sp1 = None
        hp.v_f0 = La
    hp.v_fc = hp.v_f0 + fato * 0.5

    hw = w * 0.5
    # ---- access route
    hp.pieces.append(HeliPiece('ACCESS', -hw, hw, 0.0, La, MAT_TAXI))
    # ---- spine + stands (one continuous hardstand row per spine arm, stand positions every ``pitch``)
    u_l = u_r = hw
    if n_st:
        nr = (n_st + 1) // 2
        nl = n_st - nr
        u_start = hw + r_f + 1.0                       # the first stand starts after the cross fillet
        v_s0 = v_sp1                                   # the row sits on the far spine edge
        k = 1
        for arm, count in ((1, nr), (-1, nl)):
            if not count:
                continue
            u_a, u_b = u_start, u_start + count * pitch
            lo, hi = (u_a, u_b) if arm > 0 else (-u_b, -u_a)
            hp.pieces.append(HeliPiece('STAND', lo, hi, v_s0, v_s0 + pad, MAT_STAND))
            for i in range(count):
                uc = arm * (u_start + (i + 0.5) * pitch)
                st = HeliStand(str(k), (uc, v_s0 + pad * 0.5), uc - pitch * 0.5, uc + pitch * 0.5, v_s0, v_s0 + pad,
                               [(uc, (v_sp0 + v_sp1) * 0.5), (uc, v_s0 + pad * 0.5)])
                hp.stands.append(st)
                hp.tie_downs.extend([(uc - pitch * 0.5 + 1.5, v_s0 + pad - 1.5), (uc + pitch * 0.5 - 1.5, v_s0 + pad - 1.5)])
                hp.row_breaks.extend([st.u0, st.u1])
                k += 1
            end = u_b + 3.0
            if arm > 0:
                u_r = end
            else:
                u_l = end
        hp.pieces.append(HeliPiece('SPINE', -u_l, u_r, v_sp0, v_sp1, MAT_TAXI))
        hp.pieces.append(HeliPiece('FEEDER', -hw, hw, v_sp1, hp.v_f0, MAT_TAXI))
        vc = (v_sp0 + v_sp1) * 0.5
        hp.paint_lines.append([(-u_l + 0.5 if u_l > hw else -hw, vc), (u_r - 0.5, vc)])
        hp.strips.append(([(-u_l, vc), (u_r, vc)], w))
    # ---- FATO / TLOF
    t = hp.tlof
    if surface == 'TURF':
        v_t0 = hp.v_fc - t * 0.5
        hp.pieces.append(HeliPiece('LEAD', -hw, hw, hp.v_f0, v_t0, MAT_TAXI))
        hp.pieces.append(HeliPiece('TLOF_SQ', -t * 0.5, t * 0.5, v_t0, v_t0 + t, MAT_TLOF))
        v_end = v_t0
    else:
        v_end = hp.v_fc - t * 0.5                    # centreline runs through the FATO ring to the TLOF edge
    hp.paint_lines.append([(0.0, 0.0), (0.0, v_end)])
    hp.strips.append(([(0.0, 0.0), (0.0, hp.v_f0)], w))
    # ---- junction fillets (concave corners of the helitaxiway network)
    n_f = max(3, int(math.ceil(r_f / FILLET_SEG)))

    def fil(corner: Point, da: Point, db: Point) -> None:
        hp.fillets.append(HeliFillet(corner, da, db, r_f, n_f))
    if connected:
        fil((-hw, 0.0), (-1.0, 0.0), (0.0, 1.0))
        fil((hw, 0.0), (1.0, 0.0), (0.0, 1.0))
    hp.junction_u = (-hw - (r_f if connected else 0.0), hw + (r_f if connected else 0.0))
    if n_st:
        if u_r > hw:
            fil((hw, v_sp0), (1.0, 0.0), (0.0, -1.0))
            fil((hw, v_sp1), (1.0, 0.0), (0.0, 1.0))
        if u_l > hw:
            fil((-hw, v_sp0), (-1.0, 0.0), (0.0, -1.0))
            fil((-hw, v_sp1), (-1.0, 0.0), (0.0, 1.0))
    # ---- edge-marking runs: straight parts of the strip edges between the fillet tangent points
    a0 = r_f if connected else 0.0
    if not n_st:
        for sgn in (1.0, -1.0):
            hp.edge_runs.append(([(sgn * hw, a0), (sgn * hw, La)], (-sgn, 0.0)))
    else:
        for sgn, u_arm in ((1.0, u_r), (-1.0, u_l)):
            if u_arm > hw:
                hp.edge_runs.append(([(sgn * hw, a0), (sgn * hw, La - r_f)], (-sgn, 0.0)))
                hp.edge_runs.append(([(sgn * hw, v_sp1 + r_f), (sgn * hw, hp.v_f0)], (-sgn, 0.0)))
                # spine near edge (the far edge carries the stands)
                hp.edge_runs.append(([(sgn * (hw + r_f), v_sp0), (sgn * (u_arm - 0.5), v_sp0)], (0.0, 1.0)))
            else:
                hp.edge_runs.append(([(sgn * hw, a0), (sgn * hw, hp.v_f0)], (-sgn, 0.0)))
    # ---- obstacles-clear furniture: floodlights at the two far FATO corners, windsock beyond one corner
    fh = fato * 0.5
    off = fh + safety + 6.0
    if getattr(item, "floodlights", True):
        hp.floodlights = [(-off, hp.v_fc + off), (off, hp.v_fc + off)]
    if getattr(item, "windsock", True):
        hp.windsock = (fh + safety + 10.0, hp.v_fc - fh)
    # ---- breakpoints shared by neighbouring grids
    bu: list[float] = []
    bv: list[float] = []
    for pc in hp.pieces:
        bu += [pc.u0, pc.u1]
        bv += [pc.v0, pc.v1]
    for f in hp.fillets:                              # fillet edges are grid lines of the neighbouring pieces
        for k in range(f.n + 1):
            bu += [f.corner[0] + f.da[0] * f.r * k / f.n, f.corner[0] + f.db[0] * f.r * k / f.n]
            bv += [f.corner[1] + f.da[1] * f.r * k / f.n, f.corner[1] + f.db[1] * f.r * k / f.n]
    bu += hp.row_breaks
    hp.breaks_u = axis_breaks(bu)
    hp.breaks_v = axis_breaks(bv)
    for f in hp.fillets:                              # the neighbours' grid lines along the fillet edges
        f.a_lines = _edge_lines(f.corner, f.da, f.r, hp.breaks_u, hp.breaks_v)
        f.b_lines = _edge_lines(f.corner, f.db, f.r, hp.breaks_u, hp.breaks_v)
    return hp


def host_for(plan_taxiways, hold_setback: float, rw_half_w: float, side: int):
    """(host edge y, connected, host half width) for the heliports of one side: the far edge of the parallel taxiway,
    else a free position beyond the runway holding area (the heliport then stands alone)."""
    par = None
    for t in plan_taxiways:
        if t.kind == 'PARALLEL' and t.side == side and (par is None or t.offset < par.offset):
            par = t
    if par is not None:
        return side * (par.offset + par.width * 0.5), True, par.width * 0.5
    return side * (rw_half_w + hold_setback + 90.0), False, 0.0
