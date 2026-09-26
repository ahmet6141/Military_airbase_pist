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
6. Shoulders (``offset_band``): the outline loops are offset outward (runway.shoulder on runway / overrun
   edges, the taxiway shoulder elsewhere), the offsets are cleaned of self-intersections and the band
   between outline and offset is triangulated as a CDT region (classified by polygon coverage depth, so
   nested islands and overlapping shoulders of neighbouring pavements are handled). z slopes from the
   pavement edge down 2 cm at the outer edge (UFC Table 3-2: paved shoulders 2–3 % down and away; the
   drop is kept small so the shoulder stays above the -3 cm infield). Paved -> ShoulderAsphalt, turf -> Grass.
7. Edge drop: a 35 cm vertical skirt below the outermost edge so the slab reads as a slab and the terrain
   seam can never be seen through at grazing angles.
8. Roads: polyline strips (open) / rings (patrol road) at z = -1 cm, one object.

Public helpers used by ``geometry.terrain``: ``get_footprint`` (cached on the BuildContext), ``offset_band``,
``DistanceField``, ``surface_z``, ``value_noise`` / ``fbm`` / ``smoothstep``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from mathutils import Vector
from mathutils.geometry import tessellate_polygon
from mathutils.kdtree import KDTree

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


# ---- vectorised twins (bit-identical hash, used for the per-vertex masks of big meshes)
def _hash01_np(ix: np.ndarray, iy: np.ndarray, seed: int) -> np.ndarray:
    ix = ix.astype(np.int64).astype(np.uint32)
    iy = iy.astype(np.int64).astype(np.uint32)
    h = ix * np.uint32(0x8DA6B343) + iy * np.uint32(0xD8163841) + np.uint32((seed * 0xCB1AB31F) & 0xFFFFFFFF)
    h ^= h >> np.uint32(15)
    h = h * np.uint32(0x2C1B3C6D)
    h ^= h >> np.uint32(12)
    h = h * np.uint32(0x297A2D39)
    h ^= h >> np.uint32(15)
    return h.astype(np.float64) / 4294967296.0


def value_noise_np(x: np.ndarray, y: np.ndarray, period: float, seed: int = 0) -> np.ndarray:
    fx = x / period
    fy = y / period
    ix = np.floor(fx)
    iy = np.floor(fy)
    tx = fx - ix
    ty = fy - iy
    sx = tx * tx * (3.0 - 2.0 * tx)
    sy = ty * ty * (3.0 - 2.0 * ty)
    a = _hash01_np(ix, iy, seed)
    b = _hash01_np(ix + 1, iy, seed)
    c = _hash01_np(ix, iy + 1, seed)
    d = _hash01_np(ix + 1, iy + 1, seed)
    top = a + (b - a) * sx
    bot = c + (d - c) * sx
    return top + (bot - top) * sy


def fbm_np(x: np.ndarray, y: np.ndarray, period: float, seed: int = 0, octaves: int = 3) -> np.ndarray:
    amp = 1.0
    total = np.zeros_like(x, dtype=np.float64)
    norm = 0.0
    p = period
    for i in range(octaves):
        total += amp * value_noise_np(x + 17.3 * i, y - 11.1 * i, p, seed + i * 101)
        norm += amp
        amp *= 0.5
        p *= 0.5
    return total / norm


def smoothstep_np(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
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


def _seg_intersection(a: Point, b: Point, c: Point, d: Point) -> Point | None:
    """Proper intersection point of segments ab and cd (None when they do not cross)."""
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-12:
        return None
    qx, qy = c[0] - a[0], c[1] - a[1]
    t = (qx * sy - qy * sx) / den
    u = (qx * ry - qy * rx) / den
    if 1e-9 < t < 1.0 - 1e-9 and 1e-9 < u < 1.0 - 1e-9:
        return (a[0] + rx * t, a[1] + ry * t)
    return None


def _first_self_intersection(pts: Sequence[Point]) -> tuple[int, int, Point] | None:
    """(i, j, P) for the first pair of non-adjacent edges i < j that cross (sweep on x)."""
    n = len(pts)
    edges = []
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        edges.append((min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]), i))
    edges.sort()
    active: list[tuple[float, float, float, float, int]] = []
    best: tuple[int, int, Point] | None = None
    for e in edges:
        x0, x1, y0, y1, i = e
        active = [f for f in active if f[1] >= x0]
        for f in active:
            j = f[4]
            if abs(i - j) <= 1 or abs(i - j) == n - 1:
                continue
            if f[3] < y0 or f[2] > y1:
                continue
            p = _seg_intersection(pts[i], pts[(i + 1) % n], pts[j], pts[(j + 1) % n])
            if p is not None:
                lo, hi = (i, j) if i < j else (j, i)
                if best is None or (lo, hi) < (best[0], best[1]):
                    best = (lo, hi, p)
        active.append(e)
    return best


def clean_loop(loop: Sequence[Point], max_iter: int = 40) -> Poly:
    """Remove self-intersections from an offset loop by cutting away the smaller sub-loop at every
    crossing (inverted notches, mitre spikes). Keeps the orientation of the surviving loop."""
    pts: list[Point] = []
    for p in loop:
        if not pts or g.length(pts[-1], p) > 1e-6:
            pts.append(p)
    if len(pts) > 1 and g.length(pts[0], pts[-1]) <= 1e-6:
        pts.pop()
    for _ in range(max_iter):
        if len(pts) < 4:
            break
        x = _first_self_intersection(pts)
        if x is None:
            break
        i, j, p = x
        sub_a = [p] + pts[i + 1:j + 1]           # loop between the crossing edges
        sub_b = [p] + pts[j + 1:] + pts[:i + 1]  # the rest
        keep = sub_a if abs(g.signed_area(sub_a)) > abs(g.signed_area(sub_b)) else sub_b
        pts = keep
    return pts


class DistanceField:
    """Approximate unsigned distance to a set of polylines (KD-tree over densely resampled outlines),
    with an optional per-vertex value carried along (e.g. the local shoulder width)."""

    def __init__(self, loops: Sequence[Sequence[Point]], step: float = 1.0, closed: bool = True,
                 values: Sequence[Sequence[float]] | None = None):
        samples: list[Point] = []
        vals: list[float] = []
        for k, lp in enumerate(loops):
            n = len(lp)
            if n < (3 if closed else 2):
                continue
            vv = values[k] if values is not None else None
            m = n if closed else n - 1
            for i in range(m):
                a, b = lp[i], lp[(i + 1) % n]
                seg = g.length(a, b)
                cnt = max(1, int(seg / step))
                v = vv[i] if vv is not None else 0.0
                for s in range(cnt):
                    t = s / cnt
                    samples.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
                    vals.append(v)
            if not closed:
                samples.append(lp[-1])
                vals.append(vv[-1] if vv is not None else 0.0)
        self.count = len(samples)
        self.values = vals
        self.tree = KDTree(max(1, self.count))
        for i, p in enumerate(samples):
            self.tree.insert((p[0], p[1], 0.0), i)
        if self.count == 0:
            self.tree.insert((1e9, 1e9, 0.0), 0)
        self.tree.balance()

    def dist(self, x: float, y: float) -> float:
        _co, _idx, d = self.tree.find((x, y, 0.0))
        return d if d is not None else 1e9

    def find(self, x: float, y: float) -> tuple[float, float]:
        """(distance, value of the nearest outline sample)."""
        _co, idx, d = self.tree.find((x, y, 0.0))
        if d is None or self.count == 0:
            return 1e9, 0.0
        return d, self.values[idx]


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


def _key(p: Point) -> tuple[int, int]:
    return (round(p[0] * 1000.0), round(p[1] * 1000.0))


# --------------------------------------------------------------------------- bands (shoulders, dirt strips)
@dataclass
class Band:
    """A constant-width band outside a region (its outline loops: outer CCW, holes CW)."""
    points: list[Point]
    tris: list[Tri]
    outline: list[Poly]              # new outline of region + band
    outline_is_outer: list[bool]


def offset_band(loops: Sequence[Poly], is_outer: Sequence[bool], widths: Sequence[Sequence[float]]) -> Band:
    """Triangulate the band of ``widths`` around a region bounded by ``loops``.

    Every loop is offset away from the region (mitred, clamped, then cleaned of self-intersections); the
    band is the set of CDT triangles lying outside the region and inside some offset. Overlapping bands of
    neighbouring loops merge, nested pavements inside islands are handled, and the returned outline loops
    are simple polygons straight out of the CDT.
    """
    polys: list[Poly] = []
    kind: list[int] = []        # +1 region outer loop, -1 region hole, +2 offset of outer, -2 shrunk hole
    for lp, outer, ws in zip(loops, is_outer, widths):
        if len(lp) < 3:
            continue
        w_max = max(ws) if ws else 0.0
        if w_max <= 0.0:
            polys.append(list(lp))
            kind.append(1 if outer else -1)
            continue
        off = clean_loop(offset_loop_var(lp, ws))
        if outer:
            polys.append(list(lp))
            kind.append(1)
            if len(off) >= 3 and g.signed_area(off) > 0.0:
                polys.append(off)
                kind.append(2)
        else:
            polys.append(list(lp))
            kind.append(-1)
            if len(off) >= 3 and g.signed_area(off) < 0.0 and g.area(off) > 1.0:
                polys.append(off)
                kind.append(-2)
    if not polys:
        return Band([], [], [], [])
    pts, tris, origins = g.union_triangulate(polys, ())
    # A triangle is band when it lies outside the region (outer loops +1, holes -1 -> depth 0) and inside
    # the offset region (offsets of outer loops +1, shrunk holes -1 -> depth > 0). Counting both ways keeps
    # nesting right: an outer offset also covers every island inside it, but the island's shrunk loop
    # cancels it again, so only the true band strip remains.
    band_tris: list[Tri] = []
    for t, orig in zip(tris, origins):
        depth = 0
        off_depth = 0
        for i in orig:
            k = kind[i]
            if k == 1:
                depth += 1
            elif k == -1:
                depth -= 1
            elif k == 2:
                off_depth += 1
            else:
                off_depth -= 1
        if depth == 0 and off_depth > 0:
            band_tris.append(t)
    region_keys = {_key(p) for lp in loops for p in lp}
    outline: list[Poly] = []
    outline_outer: list[bool] = []
    for loop in g.boundary_loops(band_tris):
        lp = _loop_points(pts, loop)
        if len(lp) < 3 or g.area(lp) < 1e-6:
            continue
        on_region = sum(1 for p in lp if _key(p) in region_keys)
        if on_region > len(lp) * 0.5:
            continue                      # inner boundary (the region itself)
        outline.append(lp)
        outline_outer.append(g.signed_area(lp) > 0.0)
    # loops with zero width keep their outline unchanged
    for lp, outer, ws in zip(loops, is_outer, widths):
        if len(lp) >= 3 and (not ws or max(ws) <= 0.0):
            outline.append(list(lp))
            outline_outer.append(outer)
    used = sorted({i for t in band_tris for i in t})
    remap = {o: n for n, o in enumerate(used)}
    return Band([pts[i] for i in used], [(remap[a], remap[b], remap[c]) for a, b, c in band_tris], outline, outline_outer)


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
    shoulder: Band                                # shoulder band; .outline = outermost paved edge
    has_shoulder: bool
    shoulder_type: str
    dirt_width: float                             # terrain dirt strip width (for surface_z)
    edge_field: DistanceField                     # distance to the pavement outline (+ local shoulder width)
    edge_dist: list[float]                        # per vertex distance to the outline
    bounds: tuple[float, float, float, float]
    detail: float = 1.0

    def surface_z(self, x: float, y: float) -> float:
        """Ground surface height just outside the pavement: shoulder slope -> dirt strip -> infield."""
        d, w = self.edge_field.find(x, y)
        return surface_z(d, w if self.has_shoulder else 0.0, self.dirt_width)


def surface_z(d: float, w: float, dirt: float) -> float:
    """Pavement edge (0) -> shoulder outer edge (-2 cm at d = w) -> dirt strip -> infield (DIRT_STEP)."""
    if w > 0.0 and d <= w:
        return SHOULDER_OUTER_Z * d / w
    z0 = SHOULDER_OUTER_Z if w > 0.0 else 0.0
    if dirt > 0.0 and d <= w + dirt:
        t = (d - w) / dirt
        return z0 + (C.DIRT_STEP - z0) * t
    return C.DIRT_STEP


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
    """Union of all aircraft pavement + boundary loops + shoulder band. Pure geometry, no bpy."""
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
        inner = clean_loop(g.offset_loop(lp, -DIRT_FALLOFF))
        if len(inner) >= 3:
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

    # shoulder widths: runway width on runway / overrun outlines, taxiway width elsewhere
    tw_sh = max([t.shoulder for t in plan.taxiways] + [0.0])
    rw_polys = [p for p, z in plan.pavement if z in (planmod.Z_RUNWAY, planmod.Z_RUNWAY_ASPHALT, planmod.Z_OVERRUN)]
    rw_bbox = g.bbox_polys(rw_polys) if rw_polys else (0, 0, 0, 0)
    shoulder_widths: list[list[float]] = []
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
        shoulder_widths.append(ws)
    has_sh = any(w > 0.0 for ws in shoulder_widths for w in ws)
    shoulder = offset_band(loop_points, loop_is_outer, shoulder_widths) if has_sh else Band([], [], list(loop_points), list(loop_is_outer))

    terrain = getattr(getattr(plan, "settings", None), "terrain", None)
    dirt_w = float(getattr(terrain, "dirt_strip", 0.8)) if terrain is not None and getattr(terrain, "enabled", True) else 0.0
    edge_field = DistanceField(loop_points, step=1.0, values=shoulder_widths)
    edge_dist = [edge_field.dist(p[0], p[1]) for p in points]
    return Footprint(points=points, tris=tris, zones=tri_zone, mats=mats, loops=loops, loop_points=loop_points,
                     loop_is_outer=loop_is_outer, shoulder_widths=shoulder_widths, shoulder=shoulder,
                     has_shoulder=has_sh, shoulder_type=rw.shoulder_type, dirt_width=max(0.0, dirt_w),
                     edge_field=edge_field, edge_dist=edge_dist, bounds=g.bbox(points), detail=detail)


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

    # ---- vectorised versions (same formulas) for whole point arrays
    def rubber_np(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        if self.rubber <= 0.0:
            return np.zeros_like(x)
        ay = np.abs(y)
        lateral = 0.75 * np.exp(-(y / 6.0) ** 2) + 0.35 * np.exp(-(y / 12.0) ** 2)
        long = np.zeros_like(x)
        for k, thr in enumerate(self.thr):
            s = (x - thr) if k == 0 else (thr - x)
            bell = np.exp(-((s - RUBBER_PEAK) / RUBBER_SIGMA) ** 2) * smoothstep_np(0.0, 90.0, s)
            long += np.where((s >= -5.0) & (s <= 1100.0), bell, 0.0)
        tracks = 0.12 * np.exp(-((ay - 3.5) / 2.2) ** 2) * (0.6 + 0.8 * value_noise_np(x, y, 90.0, self.seed + 7))
        n = 0.7 + 0.6 * fbm_np(x, y * 2.0, 22.0, self.seed + 3, octaves=2)
        return np.clip((np.minimum(long, 1.0) * lateral * n + tracks) * self.rubber, 0.0, 1.0)

    def dirt_np(self, edge_dist: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        if self.dirt <= 0.0:
            return np.zeros_like(x)
        band = np.maximum(0.0, 1.0 - edge_dist / DIRT_FALLOFF) * (0.7 + 0.6 * value_noise_np(x, y, 9.0, self.seed + 11))
        return np.clip(band * self.dirt, 0.0, 1.0)

    def macro_np(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return fbm_np(x, y, 120.0, self.seed + 5, octaves=3)


# --------------------------------------------------------------------------- builders
def _seed(ctx) -> int:
    return int(getattr(getattr(ctx.settings, "materials", None), "seed", 0)) & 0xFFFF


def _build_airfield(ctx, fp: Footprint) -> None:
    rw = ctx.plan.runway
    wear = WearModel(ctx.plan, ctx.settings, _seed(ctx))
    uv_fns = [planar_uv(SPECS[n].tile) for n in PAVEMENT_MATERIALS]
    mb = MeshBuilder()
    xy = np.asarray(fp.points, dtype=np.float64).reshape(-1, 2)
    xs, ys = xy[:, 0], xy[:, 1]
    r_arr = wear.rubber_np(xs, ys)
    g_arr = wear.dirt_np(np.asarray(fp.edge_dist, dtype=np.float64), xs, ys)
    b_arr = wear.macro_np(xs, ys)
    vcol = [(float(r), float(gg), float(b), 1.0) for r, gg, b in zip(r_arr, g_arr, b_arr)]
    for p in fp.points:
        mb.add_vertex((p[0], p[1], rw.crown(p[0], p[1])))
    runway_zones = (planmod.Z_RUNWAY, planmod.Z_RUNWAY_ASPHALT)
    for t, m, z in zip(fp.tris, fp.mats, fp.zones):
        on_rw = z in runway_zones
        cols = []
        for i in t:
            c = vcol[i]
            cols.append(c if on_rw else (0.0, c[1], c[2], 1.0))
        mb.add_face_idx(t, m, False, uv_fn=uv_fns[m], colors=cols)
    me = mb.build("MAD_Pavement_Airfield", [ctx.mats(n) for n in PAVEMENT_MATERIALS])
    ctx.add_object("Pavement_Airfield", me, "Pavement")


def _build_shoulders(ctx, fp: Footprint) -> None:
    band = fp.shoulder
    if not fp.has_shoulder or not band.tris:
        return
    rw = ctx.plan.runway
    paved = fp.shoulder_type == 'PAVED'
    mat_name = 'ShoulderAsphalt' if paved else 'Grass'
    mb = MeshBuilder(default_uv=planar_uv(SPECS[mat_name].tile))
    seed = _seed(ctx)
    dirt = max(0.3, float(getattr(getattr(ctx.settings, "materials", None), "dirt", 0.5)))
    base = len(mb.verts)
    xy = np.asarray(band.points, dtype=np.float64).reshape(-1, 2)
    n_arr = 0.7 + 0.6 * value_noise_np(xy[:, 0], xy[:, 1], 7.0, seed + 13)
    b_arr = fbm_np(xy[:, 0], xy[:, 1], 120.0, seed + 5)
    cols = []
    for k, p in enumerate(band.points):
        x, y = p
        d, w = fp.edge_field.find(x, y)
        w = w if w > 0.0 else 1.0
        z = surface_z(min(d, w), w, 0.0)
        if d < 0.05:
            z = rw.crown(x, y)          # meet the slab exactly (runway ends without overrun carry the crown)
        mb.add_vertex((x, y, z))
        t = 0.35 + 0.65 * min(1.0, d / w)   # dirt grows towards the outer edge
        cols.append((0.0, min(1.0, t * float(n_arr[k]) * dirt), float(b_arr[k]), 1.0))
    for a, b, c in band.tris:
        mb.add_face_idx((base + a, base + b, base + c), 0, not paved, colors=[cols[a], cols[b], cols[c]])
    me = mb.build("MAD_Pavement_Shoulders", [ctx.mats(mat_name)])
    ctx.add_object("Pavement_Shoulders", me, "Pavement")


def _build_edge_drop(ctx, fp: Footprint) -> None:
    """Vertical skirt under the outermost paved edge (hides the terrain seam at grazing angles)."""
    mb = MeshBuilder()
    tile = SPECS['ConcreteBase'].tile
    z_top = SHOULDER_OUTER_Z if fp.has_shoulder else 0.0
    for lp in fp.shoulder.outline:
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
            # top-a, bottom-a, bottom-b, top-b -> normal is the right-hand perpendicular = away from pavement
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
    seed = _seed(ctx)
    mb = MeshBuilder(default_uv=planar_uv(SPECS['Road'].tile))
    step = 30.0 / max(ctx.detail, 0.25)

    def add_soup(points, tris):
        loops = [[points[i] for i in lp] for lp in g.boundary_loops(tris)]
        fld = DistanceField(loops, step=1.0) if loops else None
        base = len(mb.verts)
        xy = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        n_arr = 0.6 + 0.4 * value_noise_np(xy[:, 0], xy[:, 1], 6.0, seed + 17)
        b_arr = fbm_np(xy[:, 0], xy[:, 1], 120.0, seed + 5)
        cols = []
        for k, p in enumerate(points):
            mb.add_vertex((p[0], p[1], ROAD_Z))
            d = fld.dist(p[0], p[1]) if fld else 0.0
            cols.append((0.0, max(0.0, 1.0 - d / 1.2) * float(n_arr[k]), float(b_arr[k]), 1.0))
        for a, b, c in tris:
            mb.add_face_idx((base + a, base + b, base + c), 0, False, colors=[cols[a], cols[b], cols[c]])

    open_polys: list[Poly] = []
    for r in roads:
        cl = g.resample_polyline(r.centerline, step, closed=r.closed)
        if r.closed and len(cl) >= 3:
            outer, inner = g.polyline_strip(cl, r.width, closed=True)
            pts, tris, _ = g.union_triangulate([outer], holes=[inner])
            if tris:
                add_soup(pts, tris)
        elif len(cl) >= 2:
            open_polys.append(g.polyline_strip(cl, r.width))
    if open_polys:
        pts, tris, _ = g.union_triangulate(open_polys)
        if tris:
            add_soup(pts, tris)
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
