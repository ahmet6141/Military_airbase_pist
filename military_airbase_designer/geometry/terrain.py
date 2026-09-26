"""Terrain builder: infield / site ground plane, dirt strip along the pavement edge, drainage ditches.

Ground model (UFC 3-260-01 Table 3-2 items 12-14, Fig 3-1 — everything drains away from the pavement):
pavement edge -> paved shoulder (pavement module, -2 cm at its outer edge) -> dirt strip (this module,
``terrain.dirt_strip`` wide, down to ``constants.DIRT_STEP`` = -3 cm: the UFC "1.5 in drop-off" at the
paved-shoulder edge) -> grass infield at -3 cm -> gentle undulation only beyond the graded airfield strip
(fades in 200..500 m from any pavement and is suppressed around roads, structures, navaids and the approach
light lanes so nothing floats or sinks) -> drainage swales parallel to the runway at ±``terrain.ditch_offset``
(cosine profile, 0.6 m deep, 12 m wide, tapering over 40 m at the ends and interrupted where a taxiway
crosses = culvert).

The ground is one CDT mesh: the site rectangle, the outer dirt-strip outlines and the infield islands are
all fed as polygons and every triangle is kept when its coverage depth (#outer outlines - #islands) is zero.
Refinement: a lattice (``terrain.subdivision``, clamped to a face budget), a ring of points 3 m outside
the dirt strip (dirt-blend mask resolution) and dense rows along the ditches. Hole-boundary vertices
coincide with the dirt strip's outer vertices, so the ground is watertight against the pavement.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from mathutils.kdtree import KDTree

from ..core import constants as C
from ..core import geom2d as g
from ..core.meshbuild import MeshBuilder, planar_uv
from ..materials.library import SPECS
from . import pavement as pv

Point = g.Point
Poly = g.Poly

DITCH_DEPTH = 0.6          # invert depth below the infield (m) — swale, ~2 ft
DITCH_TOP_W = 12.0         # top width (m): cosine profile, max slope ~16 %, mean 10 % (UFC: swale slopes <= 10:1)
DITCH_TAPER = 40.0         # ends taper to zero over this length (m)
DITCH_CLEAR = 5.0          # no displacement within this distance of any pavement (+ shoulder + dirt strip)
UNDULATION_FADE = (200.0, 500.0)   # distance from the pavement where undulation fades in (m)
UNDULATION_PERIOD = 180.0
ROAD_FLAT = (6.0, 40.0)    # undulation suppressed within 6 m of a road centreline, ramps in to 40 m
MAX_FACES_STANDARD = 60000
DIRT_BLEND = 3.0           # G = 1 at the dirt strip fading to 0 over 3 m


# --------------------------------------------------------------------------- helpers
class _PointField:
    """Nearest-distance to a set of points with per-point radius (structures, navaids)."""

    def __init__(self, items: Sequence[tuple[Point, float]]):
        self.items = list(items)
        self.tree = KDTree(max(1, len(self.items)))
        if self.items:
            for i, (p, _r) in enumerate(self.items):
                self.tree.insert((p[0], p[1], 0.0), i)
        else:
            self.tree.insert((1e9, 1e9, 0.0), 0)
        self.tree.balance()

    def flat_weight(self, x: float, y: float, ramp: float = 40.0) -> float:
        """1 inside an item's radius, 0 beyond radius + ramp."""
        if not self.items:
            return 0.0
        best = 0.0
        for _co, idx, d in self.tree.find_n((x, y, 0.0), 3):
            r = self.items[idx][1]
            w = 1.0 - pv.smoothstep(r, r + ramp, d)
            if w > best:
                best = w
        return best


def _ditch_lines(ctx) -> list[tuple[float, float, float]]:
    """(y, x_start, x_end) of every drainage swale."""
    s = ctx.settings.terrain
    if not s.ditches:
        return []
    rw = ctx.plan.runway
    ov_lo = rw.overrun_length if rw.overrun_low is not None else 0.0
    ov_hi = rw.overrun_length if rw.overrun_high is not None else 0.0
    off = float(s.ditch_offset)
    if off <= rw.paved_half_w + DITCH_TOP_W:
        return []
    return [(off, -rw.half_len - ov_lo, rw.half_len + ov_hi), (-off, -rw.half_len - ov_lo, rw.half_len + ov_hi)]


def _ditch_profile(u: float) -> float:
    """0..1 depression profile across the swale, u = |dy| / half top width."""
    if u >= 1.0:
        return 0.0
    return 0.5 * (1.0 + math.cos(math.pi * u))


# --------------------------------------------------------------------------- build
def build(ctx) -> None:
    s = ctx.settings.terrain
    fp = pv.get_footprint(ctx)
    detail = ctx.detail
    rw = ctx.plan.runway
    seed = int(getattr(getattr(ctx.settings, "materials", None), "seed", 0)) & 0xFFFF
    dirt_w = max(0.0, float(s.dirt_strip))

    # ---- dirt strip band outside the shoulders
    outline, outline_outer = fp.shoulder.outline, fp.shoulder.outline_is_outer
    if dirt_w > 0.0 and outline:
        dirt = pv.offset_band(outline, outline_outer, [[dirt_w] * len(lp) for lp in outline])
    else:
        dirt = pv.Band([], [], list(outline), list(outline_outer))
    if dirt.tris:
        mb = MeshBuilder(default_uv=planar_uv(SPECS['Dirt'].tile))
        base = len(mb.verts)
        xy = np.asarray(dirt.points, dtype=np.float64).reshape(-1, 2)
        b_arr = pv.fbm_np(xy[:, 0], xy[:, 1], 120.0, seed + 5)
        cols = []
        for k, p in enumerate(dirt.points):
            mb.add_vertex((p[0], p[1], fp.surface_z(p[0], p[1])))
            cols.append((0.0, 1.0, float(b_arr[k]), 1.0))
        for a, b, c in dirt.tris:
            mb.add_face_idx((base + a, base + b, base + c), 0, True, colors=[cols[a], cols[b], cols[c]])
        me = mb.build("MAD_Terrain_DirtStrip", [ctx.mats('Dirt')])
        ctx.add_object("Terrain_DirtStrip", me, "Terrain")

    # ---- ground: site rectangle, outer dirt outlines (holes) and islands (fills) classified by depth
    x0, y0, x1, y1 = ctx.plan.site_bounds
    site = g.rect_xy(x0, y0, x1, y1)
    polys: list[Poly] = [site]
    kind: list[int] = [0]
    for lp, is_outer in zip(dirt.outline, dirt.outline_is_outer):
        if len(lp) < 3 or g.area(lp) < 1.0:
            continue
        polys.append(list(lp))
        kind.append(1 if is_outer else -1)

    # refinement points: lattice clamped to the face budget
    area = max(1.0, (x1 - x0) * (y1 - y0))
    budget = MAX_FACES_STANDARD * max(0.25, detail * detail)
    spacing = max(float(s.subdivision) / max(detail, 0.25), math.sqrt(2.0 * area / budget))
    ditches = _ditch_lines(ctx)
    extra: list[Point] = []
    nx = int((x1 - x0) / spacing)
    ny = int((y1 - y0) / spacing)
    ox = x0 + ((x1 - x0) - nx * spacing) * 0.5
    oy = y0 + ((y1 - y0) - ny * spacing) * 0.5
    band = DITCH_TOP_W * 0.5 + 4.0
    for i in range(1, nx):
        xx = ox + i * spacing
        for j in range(1, ny):
            yy = oy + j * spacing
            if any(abs(yy - yd) < band and xa - DITCH_TAPER <= xx <= xb + DITCH_TAPER for yd, xa, xb in ditches):
                continue
            extra.append((xx, yy))
    # dirt-blend ring 3 m outside the dirt strip
    for lp, is_outer in zip(dirt.outline, dirt.outline_is_outer):
        if len(lp) >= 3:
            ring = pv.clean_loop(g.offset_loop(lp, DIRT_BLEND))
            if len(ring) >= 3:
                extra.extend(g.resample_polyline(ring, 8.0 / max(detail, 0.25), closed=True))
    # dense rows along the ditches (resolve the cosine profile + the end tapers)
    hw = DITCH_TOP_W * 0.5
    rows = (0.0, hw * 0.4, hw * 0.75, hw, hw + 2.5)
    dstep = 12.0 / max(detail, 0.25)
    for yd, xa, xb in ditches:
        xs = xa - DITCH_TAPER
        span = xb - xa + 2 * DITCH_TAPER
        n = max(2, int(math.ceil(span / dstep)))
        for i in range(n + 1):
            xx = xs + span * i / n
            for r in rows:
                extra.append((xx, yd + r))
                if r > 0.0:
                    extra.append((xx, yd - r))

    # ---- fields used for z / colour
    dirt_field = pv.DistanceField([lp for lp in dirt.outline if len(lp) >= 3], step=1.0)
    road_lines = [list(r.centerline) + ([r.centerline[0]] if r.closed else []) for r in ctx.plan.roads if len(r.centerline) >= 2]
    road_field = pv.DistanceField(road_lines, step=5.0, closed=False)
    items: list[tuple[Point, float]] = []
    for st in list(ctx.plan.structures) + list(ctx.plan.navaids):
        items.append((st.position, max(st.size[0], st.size[1]) * 0.75 + 6.0))
    # approach light lanes beyond the overruns stay graded (ALSF-2 / MALSR masts sit at z = 0)
    for sgn in (-1.0, 1.0):
        xe = sgn * (rw.half_len + rw.overrun_length)
        for k in range(0, 14):
            items.append(((xe + sgn * k * 60.0, 0.0), 45.0))
    struct_field = _PointField(items)
    undulation = float(s.undulation)
    fade0, fade1 = UNDULATION_FADE

    pts, tris, origins = g.union_triangulate(polys, (), extra_points=extra)
    keep: list[tuple[int, int, int]] = []
    for t, orig in zip(tris, origins):
        depth = 0
        for i in orig:
            depth += kind[i]
        if depth == 0:
            keep.append(t)
    if not keep:
        ctx.warn("terrain: ground produced no faces")
        return
    used = sorted({i for t in keep for i in t})
    remap = {o: n for n, o in enumerate(used)}

    # ---- heights and masks (vectorised where it counts)
    xy = np.asarray([pts[i] for i in used], dtype=np.float64).reshape(-1, 2)
    xs, ys = xy[:, 0], xy[:, 1]
    dist = np.asarray([dirt_field.dist(x, y) for x, y in zip(xs, ys)], dtype=np.float64)
    z = np.full(len(used), C.DIRT_STEP, dtype=np.float64)
    if undulation > 0.0:
        w = pv.smoothstep_np(fade0, fade1, dist)
        far = np.nonzero(w > 0.0)[0]
        for k in far:                      # graded flats around roads / structures / approach lanes
            x, y = xs[k], ys[k]
            f = 1.0 - struct_field.flat_weight(x, y)
            if road_lines:
                f *= pv.smoothstep(ROAD_FLAT[0], ROAD_FLAT[1], road_field.dist(x, y))
            w[k] *= f
        noise = pv.fbm_np(xs, ys, UNDULATION_PERIOD, seed + 21, octaves=3)
        z += w * undulation * (noise * 2.0 - 1.0)
    for yd, xa, xb in ditches:
        u = np.abs(ys - yd) / hw
        sel = np.nonzero((u < 1.0) & (xs >= xa - DITCH_TAPER) & (xs <= xb + DITCH_TAPER))[0]
        for k in sel:
            x = xs[k]
            taper = pv.smoothstep(xa - DITCH_TAPER, xa, x) * (1.0 - pv.smoothstep(xb, xb + DITCH_TAPER, x))
            clear = pv.smoothstep(DITCH_CLEAR * 0.5, DITCH_CLEAR + 4.0, dist[k])
            z[k] -= DITCH_DEPTH * _ditch_profile(u[k]) * taper * clear
    if dirt_w > 0.0 or fp.has_shoulder:
        gdirt = np.maximum(0.0, 1.0 - dist / DIRT_BLEND)
    else:
        gdirt = np.zeros(len(used))
    macro = pv.fbm_np(xs, ys, 150.0, seed + 5)

    mb = MeshBuilder(default_uv=planar_uv(SPECS['Grass'].tile))
    cols = []
    for k in range(len(used)):
        mb.add_vertex((float(xs[k]), float(ys[k]), float(z[k])))
        cols.append((0.0, float(gdirt[k]), float(macro[k]), 1.0))
    for a, b, c in keep:
        ia, ib, ic = remap[a], remap[b], remap[c]
        mb.add_face_idx((ia, ib, ic), 0, True, colors=[cols[ia], cols[ib], cols[ic]])
    me = mb.build("MAD_Terrain_Ground", [ctx.mats('Grass')])
    ctx.add_object("Terrain_Ground", me, "Terrain")
