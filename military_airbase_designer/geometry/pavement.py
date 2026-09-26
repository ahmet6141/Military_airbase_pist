"""Pavement builder: airfield slab (runway / overruns / taxiways / aprons / pads), paved or turf
shoulders, pavement edge drop and roads.

Everything is built in the base frame (runway along +X, origin at the runway centre).

Pipeline (see docs/ARCHITECTURE.md §3.2)
1. Collect every pavement polygon from ``plan.pavement`` (zone ids) — HAS loop taxiways are rings, so
   their inner outline is passed to the CDT as a hole.
2. ``geom2d.union_triangulate`` twice: a cheap first pass yields the union outline, whose inward offset
   (2.5 m) becomes refinement points so the *edge dirt* vertex mask falls off over a realistic distance
   instead of over a whole 20 m lattice cell; the second pass adds the lattice (20 m / detail) plus a
   dedicated runway lattice with a vertex row on the centreline (the crown is a ridge — UFC 3-260-01
   Table 3-2: centreline crown, 1.0–1.5 % transverse) and rows at ±2.5/5/8/12/17 m so the rubber mask
   (Gaussian, σ≈6 m) is resolved.
3. z from ``plan.runway.crown(x, y)`` (0 off the runway).
4. Material per triangle from the highest-priority covering zone (``layout.plan.ZONE_PRIORITY``).
5. Vertex colour ``Wear`` (FLOAT_COLOR, corner): R rubber deposits (touchdown zones 300–450 m from each
   threshold + faint main-gear tracks), G edge dirt (2.5 m band along the outline), B macro tonal noise.
6. Shoulders: boundary loops offset outward (runway.shoulder on runway/overrun edges, taxiway shoulder
   elsewhere), sloping 2 cm down (UFC: paved shoulders 2–3 % down and away; the drop is kept small so the
   shoulder stays above the -3 cm infield). Paved -> ShoulderAsphalt, turf -> Grass.
7. Edge drop: a 35 cm vertical skirt below the outermost edge so the slab reads as a slab and the terrain
   seam can never be seen through at grazing angles.
8. Roads: polyline strips (open) / rings (patrol road) at z = -1 cm, one object.

``compute_footprint`` / ``get_footprint`` are public: ``geometry.terrain`` reuses the union outline and
shoulder loops (cached on the BuildContext) so the two modules never disagree about where the pavement ends.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from mathutils.geometry import tessellate_polygon
from mathutils.kdtree import KDTree
from mathutils import Vector

from ..core import constants as C
from ..core import geom2d as g
from ..core.meshbuild import MeshBuilder, planar_uv
from ..layout import plan as planmod
from ..materials.library import SPECS

Point = g.Point
Poly = g.Poly
Tri = tuple[int, int, int]

# --------------------------------------------------------------------------- constants
ZONE_MATERIAL = {
    planmod.Z_RUNWAY: 'RunwayConcrete',
    planmod.Z_RUNWAY_ASPHALT: 'RunwayAsphalt',
    planmod.Z_OVERRUN: 'OverrunAsphalt',
    planmod.Z_TAXIWAY: 'TaxiwayAsphalt',
    planmod.Z_APRON: 'ApronConcrete',
    planmod.Z_PAD: 'PadConcrete',
}
PAVEMENT_MATERIALS = ['RunwayConcrete', 'RunwayAsphalt', 'OverrunAsphalt', 'TaxiwayAsphalt', 'ApronConcrete', 'PadConcrete']
MAT_INDEX = {n: i for i, n in enumerate(PAVEMENT_MATERIALS)}

SHOULDER_OUTER_Z = -0.02      # shoulder drops 2 cm to its outer edge (UFC 2-3 % scaled to stay above the infield)
EDGE_DROP = 0.35              # vertical skirt below the outermost pavement edge (m)
ROAD_Z = -0.01                # roads sit 1 cm under apron level so they never z-fight where they touch
DIRT_FALLOFF = 2.5            # edge dirt band width (m)
TINY_HOLE_AREA = 400.0        # holes in the union smaller than this are paved over (slivers, tiny islands)
LATTICE = 20.0                # refinement lattice spacing at STANDARD (m)
RUNWAY_LATTICE_X = 10.0       # runway lattice step along the runway (m)
RUNWAY_ROWS = (0.0, 2.5, 5.0, 8.0, 12.0, 17.0)    # |y| rows inside the runway (crown ridge + rubber profile)
RUBBER_PEAK = 380.0           # touchdown-zone rubber peak distance from the threshold (m)
RUBBER_SIGMA = 200.0          # longitudinal spread of the touchdown rubber (m)


# --------------------------------------------------------------------------- noise (pure math, no numpy)
def _hash01(ix: int, iy: int, seed: int) -> float:
    """Deterministic integer hash -> [0, 1)."""
    h = (ix * 0x8DA6B343 + iy * 0xD8163841 + seed * 0xCB1AB31F) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x2C1B3C6D) & 0xFFFFFFFF
    h ^= h >> 12
    h = (h * 0x297A2D39) & 0xFFFFFFFF
    h ^= h >> 15
    return h / 4294967296.0


def value_noise(x: float, y: float, period: float, seed: int = 0) -> float:
    """Smooth value noise in [0, 1] with one feature per ``period`` metres."""
    fx = x / period
    fy = y / period
    ix = math.floor(fx)
    iy = math.floor(fy)
    tx = fx - ix
    ty = fy - iy
    sx = tx * tx * (3.0 - 2.0 * tx)
    sy = ty * ty * (3.0 - 2.0 * ty)
    a = _hash01(ix, iy, seed)
    b = _hash01(ix + 1, iy, seed)
    c = _hash01(ix, iy + 1, seed)
    d = _hash01(ix + 1, iy + 1, seed)
    top = a + (b - a) * sx
    bot = c + (d - c) * sx
    return top + (bot - top) * sy


def fbm(x: float, y: float, period: float, seed: int = 0, octaves: int = 3) -> float:
    """Fractal value noise normalised to [0, 1]."""
    amp = 1.0
    total = 0.0
    norm = 0.0
    p = period
    for i in range(octaves):
        total += amp * value_noise(x + 17.3 * i, y - 11.1 * i, p, seed + i * 101)
        norm += amp
        amp *= 0.5
        p *= 0.5
    return total / norm


def smoothstep(e0: float, e1: float, x: float) -> float:
    if x <= e0:
        return 0.0
    if x >= e1:
        return 1.0
    t = (x - e0) / (e1 - e0)
    return t * t * (3.0 - 2.0 * t)


# --------------------------------------------------------------------------- loop helpers
def offset_loop_var(loop: Sequence[Point], dists: Sequence[float], miter_limit: float = 3.0) -> Poly:
    """``geom2d.offset_loop`` with a per-vertex distance (paved shoulder width changes where the runway
    edge hands over to a taxiway edge). Positive = away from the pavement for CCW outer / CW hole loops."""
    n = len(loop)
    out: list[Point] = []
    for i in range(n):
        p_prev, p, p_next = loop[(i - 1) % n], loop[i], loop[(i + 1) % n]
        dist = dists[i]
        d0 = g.normalize((p[0] - p_prev[0], p[1] - p_prev[1]))
        d1 = g.normalize((p_next[0] - p[0], p_next[1] - p[1]))
        n0 = (d0[1], -d0[0])
        n1 = (d1[1], -d1[0])
        sx, sy = n0[0] + n1[0], n0[1] + n1[1]
        denom = 1.0 + g.dot(n0, n1)
        if denom < 1e-6:
            m = g.normalize((sx, sy)) if (abs(sx) + abs(sy)) > g.EPS else n0
            out.append((p[0] + m[0] * dist * miter_limit, p[1] + m[1] * dist * miter_limit))
            continue
        f = dist / denom
        mx, my = sx * f, sy * f
        ml = math.hypot(mx, my)
        if ml > abs(dist) * miter_limit:
            k = abs(dist) * miter_limit / ml
            mx, my = mx * k, my * k
        out.append((p[0] + mx, p[1] + my))
    return out


class DistanceField:
    """Approximate unsigned distance to a set of closed loops (KD-tree over densely resampled outlines)."""

    def __init__(self, loops: Sequence[Sequence[Point]], step: float = 1.0):
        samples: list[Point] = []
        for lp in loops:
            if len(lp) < 3:
                continue
            samples.extend(g.resample_polyline(lp, step, closed=True))
        self.count = len(samples)
        self.tree = KDTree(max(1, self.count))
        for i, p in enumerate(samples):
            self.tree.insert((p[0], p[1], 0.0), i)
        if self.count == 0:
            self.tree.insert((1e9, 1e9, 0.0), 0)
        self.tree.balance()

    def dist(self, x: float, y: float) -> float:
        _co, _idx, d = self.tree.find((x, y, 0.0))
        return d if d is not None else 1e9


def _loop_points(points: Sequence[Point], loop: Sequence[int]) -> Poly:
    return [points[i] for i in loop]


def _tessellate_loop(points: Sequence[Point], loop: Sequence[int]) -> list[Tri]:
    """Triangles (as global indices, CCW) filling a small hole loop."""
    pts = [Vector(points[i]) for i in loop]
    out: list[Tri] = []
    for a, b, c in tessellate_polygon([pts]):
        t = (loop[a], loop[b], loop[c])
        if g.signed_area([points[t[0]], points[t[1]], points[t[2]]]) < 0:
            t = (t[0], t[2], t[1])
        out.append(t)
    return out


# --------------------------------------------------------------------------- footprint
@dataclass
class Footprint:
    """Shared description of where the aircraft pavement is (used by pavement and terrain)."""
    points: list[Point]
    tris: list[Tri]
    zones: list[str]                              # zone id per triangle
    mats: list[int]                               # PAVEMENT_MATERIALS index per triangle
    loops: list[list[int]]                        # boundary loops (vertex indices): outer CCW, holes CW
    loop_points: list[Poly]                       # the same loops as points
    loop_is_outer: list[bool]
    shoulder_widths: list[list[float]]            # per loop vertex
    shoulder_outer: list[Poly]                    # loops offset by the shoulder (== loop_points when no shoulder)
    has_shoulder: bool
    shoulder_type: str
    edge_field: DistanceField                     # distance to the pavement outline (without shoulders)
    edge_dist: list[float]                        # per vertex distance to the outline
    bounds: tuple[float, float, float, float]
    detail: float = 1.0


def _collect(plan: planmod.Plan) -> tuple[list[Poly], list[str], list[Poly]]:
    polys: list[Poly] = []
    zones: list[str] = []
    holes: list[Poly] = []
    for poly, zone in plan.pavement:
        if len(poly) >= 3 and g.area(poly) > 1e-6:
            polys.append(list(poly))
            zones.append(zone)
    for ap in plan.aprons:
        if ap.loop_centerline and ap.loop_width > 0.0 and len(ap.loop_centerline) >= 3:
            ring = g.polyline_strip(ap.loop_centerline, ap.loop_width, closed=True)
            if isinstance(ring, list) and len(ring) == 2 and len(ring[1]) >= 3:
                holes.append(ring[1])
    return polys, zones, holes


def _runway_lattice(rw: planmod.RunwayPlan, detail: float) -> list[Point]:
    """Rows along the runway (crown ridge on the centreline + rubber profile rows) and a few rows into the
    overruns so the crown -> overrun transition is resolved."""
    pts: list[Point] = []
    step = RUNWAY_LATTICE_X / max(detail, 0.25)
    rows = [0.0]
    for r in RUNWAY_ROWS[1:]:
        if r < rw.half_w - 0.75:
            rows.extend((r, -r))
    n = max(2, int(math.ceil(rw.length / step)))
    for i in range(1, n):
        x = -rw.half_len + rw.length * i / n
        for y in rows:
            pts.append((x, y))
    # overrun transition rows (UFC Table 3-4: overrun grades transition within the first 150 ft)
    if rw.overrun_length > 0.0:
        for sgn, ov in ((-1.0, rw.overrun_low), (1.0, rw.overrun_high)):
            if ov is None:
                continue
            for k in range(1, 7):
                x = sgn * (rw.half_len + min(rw.overrun_length - 1.0, 7.5 * k))
                for y in rows:
                    pts.append((x, y))
    return pts


def _general_lattice(polys: Sequence[Poly], zones: Sequence[str], spacing: float) -> list[Point]:
    """Lattice points inside the bbox of every non-runway polygon (points outside the union are dropped
    by the CDT), deduplicated on a global grid so overlapping polygons do not double up."""
    seen: set[tuple[int, int]] = set()
    pts: list[Point] = []
    for poly, zone in zip(polys, zones):
        if zone in (planmod.Z_RUNWAY, planmod.Z_RUNWAY_ASPHALT):
            continue
        x0, y0, x1, y1 = g.bbox(poly)
        i0, i1 = int(math.floor(x0 / spacing)) + 1, int(math.ceil(x1 / spacing))
        j0, j1 = int(math.floor(y0 / spacing)) + 1, int(math.ceil(y1 / spacing))
        if (i1 - i0) * (j1 - j0) > 40000:      # absurdly large polygon: coarsen
            continue
        for i in range(i0, i1):
            for j in range(j0, j1):
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                pts.append((i * spacing, j * spacing))
    return pts


def compute_footprint(plan: planmod.Plan, detail: float = 1.0) -> Footprint:
    """Union of all aircraft pavement + boundary loops + shoulder outlines. Pure geometry, no bpy."""
    rw = plan.runway
    polys, zones, holes = _collect(plan)
    if not polys:
        raise ValueError("pavement: plan has no pavement polygons")

    # pass 1: outline only, used for the inward refinement offsets
    pts1, tris1, _ = g.union_triangulate(polys, holes)
    extra: list[Point] = []
    for loop in g.boundary_loops(tris1):
        lp = _loop_points(pts1, loop)
        if len(lp) < 3 or g.area(lp) < TINY_HOLE_AREA * 0.5:
            continue
        inner = g.offset_loop(lp, -DIRT_FALLOFF)
        extra.extend(g.resample_polyline(inner, 6.0 / max(detail, 0.25), closed=True))
    extra.extend(_runway_lattice(rw, detail))
    extra.extend(_general_lattice(polys, zones, LATTICE / max(detail, 0.25)))

    # pass 2: the real mesh
    points, tris, origins = g.union_triangulate(polys, holes, extra_points=extra)
    if not tris:
        raise ValueError("pavement: union produced no triangles")
    tri_zone: list[str] = []
    for orig in origins:
        best = None
        for i in orig:
            z = zones[i]
            if best is None or planmod.ZONE_PRIORITY.get(z, 0) > planmod.ZONE_PRIORITY.get(best, 0):
                best = z
        tri_zone.append(best or planmod.Z_TAXIWAY)

    # boundary loops; pave over tiny holes (slivers between fillets and strips, islands narrower than a shoulder)
    loops_all = g.boundary_loops(tris)
    vertex_zone: dict[int, str] = {}
    for t, z in zip(tris, tri_zone):
        for i in t:
            if planmod.ZONE_PRIORITY.get(z, 0) >= planmod.ZONE_PRIORITY.get(vertex_zone.get(i, ""), -1):
                vertex_zone[i] = z
    loops: list[list[int]] = []
    for loop in loops_all:
        lp = _loop_points(points, loop)
        sa = g.signed_area(lp)
        if sa < 0.0 and -sa < TINY_HOLE_AREA:
            fill = _tessellate_loop(points, loop)
            # a filled sliver takes the lowest-priority zone touching it (it is taxiway-like pavement)
            zs = [vertex_zone.get(i, planmod.Z_TAXIWAY) for i in loop]
            zone = min(zs, key=lambda z: planmod.ZONE_PRIORITY.get(z, 0))
            for t in fill:
                tris.append(t)
                tri_zone.append(zone)
            continue
        if abs(sa) < 1e-6:
            continue
        loops.append(loop)
    loop_points = [_loop_points(points, lp) for lp in loops]
    loop_is_outer = [g.signed_area(lp) > 0.0 for lp in loop_points]
    mats = [MAT_INDEX[ZONE_MATERIAL.get(z, 'TaxiwayAsphalt')] for z in tri_zone]

    # shoulders: runway width on runway / overrun outlines, taxiway width elsewhere
    tw_sh = max([t.shoulder for t in plan.taxiways] + [0.0])
    rw_polys = [p for p, z in plan.pavement if z in (planmod.Z_RUNWAY, planmod.Z_RUNWAY_ASPHALT, planmod.Z_OVERRUN)]
    rw_bbox = g.bbox_polys(rw_polys) if rw_polys else (0, 0, 0, 0)
    shoulder_widths: list[list[float]] = []
    shoulder_outer: list[Poly] = []
    any_sh = False
    for lp in loop_points:
        ws: list[float] = []
        for p in lp:
            on_rw = False
            if rw_bbox[0] - 0.05 <= p[0] <= rw_bbox[2] + 0.05 and rw_bbox[1] - 0.05 <= p[1] <= rw_bbox[3] + 0.05:
                for poly in rw_polys:
                    if g.dist_point_poly_edge(p, poly) < 0.03:
                        on_rw = True
                        break
            ws.append(rw.shoulder if on_rw else tw_sh)
        if any(w > 0.0 for w in ws):
            any_sh = True
            floor = min(w for w in ws if w > 0.0)
            ws = [w if w > 0.0 else min(floor, 0.5) for w in ws]     # avoid degenerate ring quads
        shoulder_widths.append(ws)
        shoulder_outer.append(offset_loop_var(lp, ws) if any(w > 0.0 for w in ws) else list(lp))

    edge_field = DistanceField(loop_points, step=1.0)
    edge_dist = [edge_field.dist(p[0], p[1]) for p in points]
    return Footprint(points=points, tris=tris, zones=tri_zone, mats=mats, loops=loops, loop_points=loop_points,
                     loop_is_outer=loop_is_outer, shoulder_widths=shoulder_widths, shoulder_outer=shoulder_outer,
                     has_shoulder=any_sh, shoulder_type=rw.shoulder_type, edge_field=edge_field, edge_dist=edge_dist,
                     bounds=g.bbox(points), detail=detail)


def get_footprint(ctx) -> Footprint:
    """Cached footprint on the BuildContext (pavement and terrain share it)."""
    fp = getattr(ctx, "_mad_footprint", None)
    if fp is None or fp.detail != ctx.detail:
        fp = compute_footprint(ctx.plan, ctx.detail)
        try:
            setattr(ctx, "_mad_footprint", fp)
        except AttributeError:
            pass
    return fp


def shoulder_edge_z(fp: Footprint) -> float:
    """z of the outermost pavement edge (shoulder outer edge, or pavement edge when no shoulder)."""
    return SHOULDER_OUTER_Z if fp.has_shoulder else 0.0


# --------------------------------------------------------------------------- wear masks
class WearModel:
    """Vertex-colour masks (R rubber, G edge dirt, B macro variation) for the pavement slab."""

    def __init__(self, plan: planmod.Plan, settings, seed: int = 0):
        rw = plan.runway
        self.thr = (rw.thr_low, rw.thr_high)
        self.half_w = rw.half_w
        self.rubber = float(getattr(getattr(settings, "materials", None), "rubber", 0.7))
        self.dirt = float(getattr(getattr(settings, "materials", None), "dirt", 0.5))
        self.seed = seed

    def rubber_at(self, x: float, y: float) -> float:
        if self.rubber <= 0.0:
            return 0.0
        ay = abs(y)
        lateral = 0.75 * math.exp(-(y / 6.0) ** 2) + 0.35 * math.exp(-(y / 12.0) ** 2)
        long = 0.0
        for k, thr in enumerate(self.thr):
            s = (x - thr) if k == 0 else (thr - x)       # distance into the runway from that threshold
            if s < -5.0 or s > 1100.0:
                continue
            bell = math.exp(-((s - RUBBER_PEAK) / RUBBER_SIGMA) ** 2)
            long += bell * smoothstep(0.0, 90.0, s)
        # faint main-gear tracks along the whole runway (tyres ~3-4 m either side of the CL)
        tracks = 0.12 * math.exp(-((ay - 3.5) / 2.2) ** 2) * (0.6 + 0.8 * value_noise(x, y, 90.0, self.seed + 7))
        n = 0.7 + 0.6 * fbm(x, y * 2.0, 22.0, self.seed + 3, octaves=2)
        v = (min(1.0, long) * lateral * n + tracks) * self.rubber
        return max(0.0, min(1.0, v))

    def dirt_at(self, edge_dist: float, x: float, y: float) -> float:
        if self.dirt <= 0.0:
            return 0.0
        band = max(0.0, 1.0 - edge_dist / DIRT_FALLOFF)
        band *= 0.7 + 0.6 * value_noise(x, y, 9.0, self.seed + 11)
        return max(0.0, min(1.0, band * self.dirt))

    def macro_at(self, x: float, y: float) -> float:
        return fbm(x, y, 120.0, self.seed + 5, octaves=3)


# --------------------------------------------------------------------------- builders
def _build_airfield(ctx, fp: Footprint) -> None:
    rw = ctx.plan.runway
    seed = int(getattr(getattr(ctx.settings, "materials", None), "seed", 0)) & 0xFFFF
    wear = WearModel(ctx.plan, ctx.settings, seed)
    uv_fns = [planar_uv(SPECS[n].tile) for n in PAVEMENT_MATERIALS]
    mb = MeshBuilder()
    # vertices
    vcol: list[tuple[float, float, float, float]] = []
    for p, d in zip(fp.points, fp.edge_dist):
        x, y = p
        mb.add_vertex((x, y, rw.crown(x, y)))
        vcol.append((wear.rubber_at(x, y), wear.dirt_at(d, x, y), wear.macro_at(x, y), 1.0))
    runway_zones = (planmod.Z_RUNWAY, planmod.Z_RUNWAY_ASPHALT)
    for t, m, z in zip(fp.tris, fp.mats, fp.zones):
        on_rw = z in runway_zones
        cols = []
        for i in t:
            c = vcol[i]
            cols.append(c if on_rw else (0.0, c[1], c[2], 1.0))
        mb.add_face_idx(t, m, False, uv_fn=uv_fns[m], colors=cols)
    mats = [ctx.mats(n) for n in PAVEMENT_MATERIALS]
    me = mb.build("MAD_Pavement_Airfield", mats)
    ctx.add_object("Pavement_Airfield", me, "Pavement")


def _build_shoulders(ctx, fp: Footprint) -> None:
    if not fp.has_shoulder:
        return
    rw = ctx.plan.runway
    paved = fp.shoulder_type == 'PAVED'
    mat_name = 'ShoulderAsphalt' if paved else 'Grass'
    mb = MeshBuilder(default_uv=planar_uv(SPECS[mat_name].tile))
    seed = int(getattr(getattr(ctx.settings, "materials", None), "seed", 0)) & 0xFFFF
    dirt = float(getattr(getattr(ctx.settings, "materials", None), "dirt", 0.5))
    for lp, outer, ws in zip(fp.loop_points, fp.shoulder_outer, fp.shoulder_widths):
        if not any(w > 0.0 for w in ws) or len(lp) < 3:
            continue
        inner3 = [(p[0], p[1], rw.crown(p[0], p[1])) for p in lp]
        outer3 = [(q[0], q[1], SHOULDER_OUTER_Z) for q in outer]

        def col(x, y, z):
            # inner edge picks up dirt/rubber tracked off the pavement, outer edge is full dirt
            t = 1.0 if z <= SHOULDER_OUTER_Z + 1e-6 else 0.35
            return (0.0, min(1.0, t * (0.7 + 0.6 * value_noise(x, y, 7.0, seed + 13)) * max(dirt, 0.3)),
                    fbm(x, y, 120.0, seed + 5), 1.0)
        mb.add_ring(inner3, outer3, 0, color_fn=col)
    if mb.is_empty():
        return
    me = mb.build("MAD_Pavement_Shoulders", [ctx.mats(mat_name)])
    ctx.add_object("Pavement_Shoulders", me, "Pavement")


def _build_edge_drop(ctx, fp: Footprint) -> None:
    """Vertical skirt under the outermost pavement edge (hides the terrain seam at grazing angles)."""
    mb = MeshBuilder()
    z_top = shoulder_edge_z(fp)
    tile = SPECS['ConcreteBase'].tile
    for lp in fp.shoulder_outer:
        n = len(lp)
        if n < 3:
            continue
        acc = 0.0
        for i in range(n):
            a, b = lp[i], lp[(i + 1) % n]
            seg = g.length(a, b)
            if seg < 1e-6:
                continue
            u0, u1 = acc / tile, (acc + seg) / tile
            # order top-a, bottom-a, bottom-b, top-b -> normal is the right-hand perpendicular = away from pavement
            mb.add_face(((a[0], a[1], z_top), (a[0], a[1], z_top - EDGE_DROP), (b[0], b[1], z_top - EDGE_DROP), (b[0], b[1], z_top)),
                        0, False, uvs=[(u0, z_top / tile), (u0, (z_top - EDGE_DROP) / tile), (u1, (z_top - EDGE_DROP) / tile), (u1, z_top / tile)],
                        colors=[(0.0, 1.0, 0.5, 1.0)] * 4)
            acc += seg
    if mb.is_empty():
        return
    me = mb.build("MAD_Pavement_Edge", [ctx.mats('ConcreteBase')])
    ctx.add_object("Pavement_Edge", me, "Pavement")


def _build_roads(ctx) -> None:
    roads = [r for r in ctx.plan.roads if r.width > 0.0 and len(r.centerline) >= 2]
    if not roads:
        return
    detail = ctx.detail
    seed = int(getattr(getattr(ctx.settings, "materials", None), "seed", 0)) & 0xFFFF
    tile = SPECS['Road'].tile
    mb = MeshBuilder(default_uv=planar_uv(tile))
    step = 30.0 / max(detail, 0.25)

    def col(x, y, z):
        # road edges collect dirt; B carries the macro variation, R = 0
        return (0.0, 0.0, fbm(x, y, 120.0, seed + 5), 1.0)

    def add_soup(points, tris, edge_loops):
        field = DistanceField(edge_loops, step=1.0) if edge_loops else None
        base = len(mb.verts)
        cols = []
        for p in points:
            mb.add_vertex((p[0], p[1], ROAD_Z))
            d = field.dist(p[0], p[1]) if field else 0.0
            gdirt = max(0.0, 1.0 - d / 1.2) * (0.6 + 0.4 * value_noise(p[0], p[1], 6.0, seed + 17))
            cols.append((0.0, gdirt, fbm(p[0], p[1], 120.0, seed + 5), 1.0))
        for a, b, c in tris:
            mb.add_face_idx((base + a, base + b, base + c), 0, False, colors=[cols[a], cols[b], cols[c]])

    open_polys: list[Poly] = []
    for r in roads:
        cl = g.resample_polyline(r.centerline, step, closed=r.closed)
        if r.closed and len(cl) >= 3:
            outer, inner = g.polyline_strip(cl, r.width, closed=True)
            pts, tris, _ = g.union_triangulate([outer], holes=[inner])
            if tris:
                add_soup(pts, tris, [[pts[i] for i in lp] for lp in g.boundary_loops(tris)])
        elif len(cl) >= 2:
            open_polys.append(g.polyline_strip(cl, r.width))
    if open_polys:
        pts, tris, _ = g.union_triangulate(open_polys)
        if tris:
            loops = [[pts[i] for i in lp] for lp in g.boundary_loops(tris)]
            add_soup(pts, tris, loops)
    if mb.is_empty():
        return
    me = mb.build("MAD_Pavement_Roads", [ctx.mats('Road')])
    ctx.add_object("Pavement_Roads", me, "Pavement")


# --------------------------------------------------------------------------- entry point
def build(ctx) -> None:
    fp = get_footprint(ctx)
    _build_airfield(ctx, fp)
    _build_shoulders(ctx, fp)
    _build_edge_drop(ctx, fp)
    _build_roads(ctx)
