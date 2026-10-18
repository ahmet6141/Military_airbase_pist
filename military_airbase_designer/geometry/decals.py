"""Decals: tyre rubber skid marks in the touchdown zones, turn scuffs at runway exits, oil /
hydraulic stains and exhaust soot on apron spots.

Everything is one alpha-blended mesh (``MAD_Decals``) of thin strips floating ``constants.DECAL_Z``
above the pavement (above the paint: rubber and soot cover markings, as on a real runway). Each
strip maps onto one cell of the ``T_MAD_Decals`` atlas (``constants.DECAL_ATLAS``); the per-decal
opacity is carried in the vertex-colour alpha (``Wear`` attribute, A channel) and multiplied into
the material alpha, so one material serves every decal.

Placement model (observational, not a standard):
* touchdown marks cluster ~1,000-1,500 ft beyond the threshold (FAA aiming point 1,020 ft; the
  touchdown-zone rubber in aerial photos peaks 1,200-1,800 ft in), on the main-gear tracks of the
  traffic mix (F-16 2.4 m, F-15/F-35 ~3.4-4.0 m, C-130 4.3 m, C-17 / A400M twin bogies ~10-11 m);
  the first metres of a mark are the darkest (wheel spin-up) and it fades over 15-60 m;
* a share of landings leaves long faint braking streaks further down the runway;
* exits: aircraft turning off leave arcs of light scuff from the centreline toward the taxiway;
* parking spots: drips under the engine bay / main gear, a grime halo at the nose-wheel stop and a
  soot fan behind the tailpipe(s).
"""
from __future__ import annotations

import math
import random
from typing import Callable, Sequence

from ..core import constants as C
from ..core import geom2d as g
from ..core.meshbuild import MeshBuilder
from ..layout import plan as planmod

Point = g.Point
CATEGORY = "Decals"
UV_PAD = 0.004          # inset inside an atlas cell (bleed guard)

# main-gear track (m), twin bogie?, streak width (m), streak length range (m), weight
_TRAFFIC = {
    'FIGHTER': [(2.4, False, 0.30, (10.0, 40.0), 4), (3.4, False, 0.34, (12.0, 45.0), 3), (4.0, False, 0.36, (12.0, 50.0), 2),
                (4.3, False, 0.42, (15.0, 55.0), 1)],
    'MIXED':   [(2.4, False, 0.30, (10.0, 40.0), 3), (3.4, False, 0.34, (12.0, 45.0), 3), (4.3, False, 0.42, (15.0, 55.0), 2),
                (7.5, False, 0.55, (20.0, 70.0), 1), (10.8, True, 0.9, (25.0, 80.0), 1)],
    'HEAVY':   [(7.5, False, 0.55, (20.0, 70.0), 2), (10.8, True, 0.9, (25.0, 90.0), 3), (12.0, True, 1.0, (30.0, 100.0), 2),
                (4.3, False, 0.42, (15.0, 55.0), 1)],
}


def _cell_uv(name: str) -> tuple[float, float, float, float]:
    col, row = C.DECAL_ATLAS[name]
    n = float(C.DECAL_GRID)
    u0, u1 = col / n + UV_PAD, (col + 1) / n - UV_PAD
    v1, v0 = 1.0 - row / n - UV_PAD, 1.0 - (row + 1) / n + UV_PAD
    return u0, v0, u1, v1


class DecalSheet:
    """Collects decal strips into one mesh."""

    def __init__(self, z_fn: Callable[[float, float], float]):
        self.b = MeshBuilder(default_color=(0.0, 0.0, 0.5, 1.0))
        self.z_fn = z_fn
        self.count = 0

    def strip(self, pts: Sequence[Point], width, cell: str, alpha: float, taper: tuple[float, float] = (1.0, 1.0)) -> None:
        """Strip along the polyline ``pts`` (>= 2 points). ``width`` is a scalar or per-point list;
        ``taper`` scales the width at the start / end. u runs along the strip, v across."""
        n = len(pts)
        if n < 2 or alpha <= 0.0:
            return
        widths = list(width) if isinstance(width, (list, tuple)) else [float(width)] * n
        total = g.polyline_length(pts, False)
        if total < 1e-3:
            return
        u0, v0, u1, v1 = _cell_uv(cell)
        col = (0.0, 0.0, 0.5, max(0.0, min(1.0, alpha)))
        left_ids: list[int] = []
        right_ids: list[int] = []
        us: list[float] = []
        acc = 0.0
        for i, p in enumerate(pts):
            if i > 0:
                acc += g.length(pts[i - 1], p)
            t = acc / total
            if i == 0:
                d = g.normalize((pts[1][0] - p[0], pts[1][1] - p[1]))
            elif i == n - 1:
                d = g.normalize((p[0] - pts[i - 1][0], p[1] - pts[i - 1][1]))
            else:
                d = g.normalize((pts[i + 1][0] - pts[i - 1][0], pts[i + 1][1] - pts[i - 1][1]))
            nrm = g.perp_left(d)
            w = widths[i] * (taper[0] + (taper[1] - taper[0]) * t) * 0.5
            lp = (p[0] + nrm[0] * w, p[1] + nrm[1] * w)
            rp = (p[0] - nrm[0] * w, p[1] - nrm[1] * w)
            left_ids.append(self.b.add_vertex((lp[0], lp[1], self.z_fn(lp[0], lp[1]) + C.DECAL_Z)))
            right_ids.append(self.b.add_vertex((rp[0], rp[1], self.z_fn(rp[0], rp[1]) + C.DECAL_Z)))
            us.append(u0 + (u1 - u0) * t)
        for i in range(n - 1):
            quad = (right_ids[i], right_ids[i + 1], left_ids[i + 1], left_ids[i])
            uvs = [(us[i], v0), (us[i + 1], v0), (us[i + 1], v1), (us[i], v1)]
            self.b.add_face_idx(quad, 0, False, uvs=uvs, colors=[col] * 4)
        self.count += 1

    def streak(self, start: Point, direction: Point, length: float, width: float, cell: str, alpha: float,
               curve: float = 0.0, segments: int = 6) -> None:
        """Straight (or gently curving, ``curve`` = total heading change in radians) strip from ``start``."""
        d = g.normalize(direction)
        pts: list[Point] = [start]
        x, y = start
        heading = math.atan2(d[1], d[0])
        step = length / segments
        for i in range(segments):
            heading += curve / segments
            x += math.cos(heading) * step
            y += math.sin(heading) * step
            pts.append((x, y))
        self.strip(pts, width, cell, alpha)

    def patch(self, centre: Point, size: float, cell: str, alpha: float, angle: float = 0.0, aspect: float = 1.0) -> None:
        """Square-ish decal (stains): a 2-point strip of width ``size``."""
        d = (math.cos(angle), math.sin(angle))
        half = size * aspect * 0.5
        a = (centre[0] - d[0] * half, centre[1] - d[1] * half)
        b = (centre[0] + d[0] * half, centre[1] + d[1] * half)
        self.strip([a, b], size, cell, alpha)

    def build(self, name: str, material) -> object:
        return self.b.build(name, materials=[material])


# =========================================================================== placement
def _weighted_choice(rng: random.Random, items):
    total = sum(it[-1] for it in items)
    r = rng.uniform(0.0, total)
    for it in items:
        r -= it[-1]
        if r <= 0:
            return it
    return items[-1]


def _tdz_skids(sheet: DecalSheet, rw: planmod.RunwayPlan, d, rng: random.Random, detail: float) -> None:
    mix = _TRAFFIC.get(d.aircraft_mix, _TRAFFIC['FIGHTER'])
    total = int(round(d.skid_count * min(1.0, 0.5 + 0.5 * detail)))
    half_w = rw.half_w - 1.0
    for end in ('LOW', 'HIGH'):
        share = d.traffic_split if end == 'LOW' else 1.0 - d.traffic_split
        n = int(round(total * share))
        thr = rw.thr_low if end == 'LOW' else rw.thr_high
        sgn = 1.0 if end == 'LOW' else -1.0
        usable = rw.length - (rw.displaced_low + rw.displaced_high)
        for _ in range(n):
            track, twin, width, (l0, l1), _w = _weighted_choice(rng, mix)
            dist = rng.gauss(d.touchdown_mean, d.touchdown_spread)
            dist = max(45.0, min(usable * 0.6, dist))
            lateral = rng.gauss(0.0, d.lateral_spread)
            yaw = math.radians(rng.gauss(0.0, 0.7))
            length = rng.uniform(l0, l1)
            peak = math.exp(-((dist - d.touchdown_mean) / max(d.touchdown_spread * 1.4, 1.0)) ** 2)
            alpha = rng.uniform(0.45, 1.0) * d.skid_strength * (0.6 + 0.4 * peak)
            cell = "skid_twin" if twin else ("touch_blob" if rng.random() < 0.3 else "skid")
            direction = (sgn * math.cos(yaw), math.sin(yaw))
            x0 = thr + sgn * dist
            for side in (-1.0, 1.0):
                y = lateral + side * track * 0.5
                if abs(y) > half_w:
                    continue
                w = width * rng.uniform(0.85, 1.25)
                sheet.streak((x0, y), direction, length, w, cell, alpha)
            # braking / rollout streak further down (a share of landings)
            if rng.random() < 0.18:
                length2 = rng.uniform(60.0, 160.0)
                start = dist + length + rng.uniform(20.0, 120.0)
                if start + length2 < usable * 0.85:
                    for side in (-1.0, 1.0):
                        y = lateral + side * track * 0.5
                        if abs(y) <= half_w:
                            sheet.streak((thr + sgn * start, y), direction, length2, width * 0.9, "skid_fade",
                                         alpha * rng.uniform(0.3, 0.5))


def _turn_scuffs(sheet: DecalSheet, plan: planmod.Plan, d, rng: random.Random) -> None:
    rw = plan.runway
    for tw in plan.taxiways:
        if tw.kind not in ('CONNECTOR', 'HIGH_SPEED', 'END') or len(tw.centerline) < 2:
            continue
        p0 = tw.centerline[0]
        if abs(p0[1]) > rw.half_w + 1.0:
            continue                                     # does not start on the runway
        side = 1.0 if tw.side > 0 else -1.0
        n_arcs = rng.randint(2, 4)
        for _ in range(n_arcs):
            # landing traffic from the low end exits turning left/right toward the taxiway side
            from_low = rng.random() < d.traffic_split
            sgn = 1.0 if from_low else -1.0
            if tw.kind == 'HIGH_SPEED' and ((tw.exit_dir == 'HIGH') != from_low):
                continue                                 # a high-speed exit serves one landing direction
            radius = rng.uniform(35.0, 70.0)
            sweep = math.radians(rng.uniform(18.0, 40.0))
            lateral = rng.gauss(0.0, 1.5) + side * rng.uniform(0.0, 2.0)
            start_x = p0[0] - sgn * radius * math.sin(sweep) * rng.uniform(0.8, 1.2)
            length = radius * sweep
            curve = side * sgn * sweep                    # turn toward the taxiway side
            width = rng.uniform(0.25, 0.4)
            alpha = rng.uniform(0.3, 0.6) * d.skid_strength
            sheet.streak((start_x, lateral), (sgn, 0.0), length, width, "scuff_arc", alpha, curve=curve, segments=10)


def _spot_frame(sp: planmod.Spot) -> tuple[Point, Point]:
    fwd = (math.cos(sp.heading), math.sin(sp.heading))
    right = (fwd[1], -fwd[0])
    return fwd, right


def _at(sp: planmod.Spot, ahead: float, right_of: float) -> Point:
    fwd, right = _spot_frame(sp)
    return (sp.position[0] + fwd[0] * ahead + right[0] * right_of, sp.position[1] + fwd[1] * ahead + right[1] * right_of)


def _apron_stains(sheet: DecalSheet, plan: planmod.Plan, d, rng: random.Random) -> None:
    for ap in plan.aprons:
        heavy = ap.aircraft == 'HEAVY'
        for sp in ap.spots:
            if rng.random() < 0.12:
                continue                                 # a clean spot now and then
            k = d.stain_strength
            # grime halo where the nose wheel stops / tow bar drops
            sheet.patch(_at(sp, rng.uniform(-0.5, 0.5), rng.uniform(-0.3, 0.3)), rng.uniform(1.6, 2.6), "grime",
                        rng.uniform(0.3, 0.6) * k, angle=sp.heading + rng.uniform(-0.4, 0.4))
            # drips under the engine bay / main gear
            n = rng.randint(1, 3) + (1 if heavy else 0)
            for _ in range(n):
                if heavy:
                    pos = _at(sp, rng.uniform(-24.0, -14.0), rng.choice((-1.0, 1.0)) * rng.uniform(4.0, 9.0))
                    size = rng.uniform(0.8, 2.2)
                else:
                    pos = _at(sp, rng.uniform(-9.5, -5.0), rng.gauss(0.0, 1.2))
                    size = rng.uniform(0.45, 1.4)
                cell = rng.choice(("oil_round", "oil_round", "oil_spread", "drip_cluster"))
                sheet.patch(pos, size, cell, rng.uniform(0.4, 0.9) * k, angle=rng.uniform(0.0, math.pi))
            if rng.random() < 0.25:
                pos = _at(sp, rng.uniform(-12.0, -2.0), rng.gauss(0.0, 2.5))
                sheet.patch(pos, rng.uniform(2.5, 4.5), "fuel_spill", rng.uniform(0.3, 0.6) * k, angle=rng.uniform(0.0, math.pi))


def _exhaust(sheet: DecalSheet, plan: planmod.Plan, d, rng: random.Random) -> None:
    for ap in plan.aprons:
        heavy = ap.aircraft == 'HEAVY'
        for sp in ap.spots:
            if rng.random() < 0.2:
                continue
            fwd, right = _spot_frame(sp)
            back = (-fwd[0], -fwd[1])
            k = d.stain_strength
            if heavy:
                for off in (-7.0, 7.0):
                    start = _at(sp, -22.0, off)
                    sheet.strip([start, (start[0] + back[0] * 26.0, start[1] + back[1] * 26.0)], 4.0, "exhaust",
                                rng.uniform(0.35, 0.6) * k, taper=(0.5, 1.6))
            else:
                start = _at(sp, rng.uniform(-14.0, -11.0), rng.gauss(0.0, 0.3))
                length = rng.uniform(9.0, 16.0)
                sheet.strip([start, (start[0] + back[0] * length, start[1] + back[1] * length)], rng.uniform(2.2, 3.2),
                            "exhaust", rng.uniform(0.45, 0.75) * k, taper=(0.45, 1.5))


# =========================================================================== entry point
def build(ctx) -> None:
    s = ctx.settings
    d = s.decals
    if not d.enabled:
        return
    plan = ctx.plan
    rw = plan.runway
    rng = random.Random(int(d.seed) * 7919 + 17)

    def z_fn(x: float, y: float) -> float:
        if abs(y) <= rw.half_w and -rw.half_len - rw.overrun_length <= x <= rw.half_len + rw.overrun_length:
            return rw.crown(x, y)
        return 0.0

    sheet = DecalSheet(z_fn)
    if d.skids:
        _tdz_skids(sheet, rw, d, rng, ctx.detail)
    if d.turn_scuffs:
        _turn_scuffs(sheet, plan, d, rng)
    if d.apron_stains:
        _apron_stains(sheet, plan, d, rng)
    if d.exhaust_stains:
        _exhaust(sheet, plan, d, rng)
    if sheet.count == 0:
        return
    me = sheet.build(f"{C.PREFIX}Decals", ctx.mats("Decals"))
    ob = ctx.add_object("Decals", me, CATEGORY)
    for attr, val in (("visible_shadow", False), ("display_type", 'TEXTURED')):
        try:
            setattr(ob, attr, val)
        except (AttributeError, TypeError):
            pass
