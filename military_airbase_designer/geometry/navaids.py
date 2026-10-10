"""Navaids builder: ILS localizer array, ILS glide-slope mast, TACAN, ASR-type surveillance radar, AWOS sensor group.

Each ``StructurePlan`` in ``plan.navaids`` (kinds LOCALIZER, GLIDE_SLOPE, TACAN, RADAR, AWOS) becomes ONE mesh
object ``MAD_Navaids_<Kind>_<n>`` built in a local frame (origin at ``position``, rotated by ``rotation`` about Z
when placed). ``plan.wind_cones`` are built by the signage module and skipped here.

Siting comes from layout.plan (UFC 3-260-01 App. B §13 set-backs); the geometry follows research report
03 §11 / §12 and typical hardware:
* Localizer: 14-element log-periodic dipole array, ~30 m aperture, elements on a horizontal boom on frangible
  posts, red/white end posts, non-frangible shelter >= 60 m to one side (FAA 6750.16E: shelter off the array
  axis, >= 250 ft from the extended centreline is the FAA rule; here the plan spacing is used).
* Glide slope: 13 m lattice mast (capture-effect: three antenna bays at 4.5 / 8.5 / 12.5 m), 8 x 10 ft shelter,
  obstruction bands (AC 70/7460-1M §3.4.4: odd number of alternate orange/white bands -> 7), red light on top.
* TACAN: equipment building + 6 m tower with a cylindrical radome antenna (AN/URN-25 class).
* ASR-type radar: 12 m four-leg lattice tower, offset-fed reflector (approximated by a bent plate 8 x 3 m) on a
  rotating pedestal with an IFF/SSR bar, equipment shelter, fenced-compound gravel pad.
* AWOS / FMQ-19 style: 10 m wind mast, ceilometer, visibility sensor cross-arm, precipitation gauge, aspirated
  temperature shield and equipment cabinet on a 6 x 6 m gravel pad.
Every mast stands on a 0.6 m (or larger) concrete foundation pad and every compound gets a warning sign post.
"""
from __future__ import annotations

import math
import traceback
from collections import defaultdict

from mathutils import Matrix, Vector

from ..core import geom2d as g
from ..core.units import ft
from .perimeter import GROUND_Z, Kit, warning_sign

CATEGORY = "Navaids"
Vec3 = tuple[float, float, float]
Point = g.Point

PAD_Z = 0.005          # gravel pads sit 5 mm above the pavement plane (terrain is at GROUND_Z below it)
SLAB_TOP = 0.12        # building slabs protrude 12 cm above grade
OBS_MATS = ('ObstructionOrange', 'ObstructionWhite')


# =========================================================================== shared pieces
def _foundation(k: Kit, x: float, y: float, size: float = 0.6, top: float = 0.10) -> None:
    """Concrete foundation pad under a mast / post (UFC: pads flush or slightly proud of grade)."""
    k.box((x, y, (GROUND_Z - 0.3 + top) * 0.5), (size, size, top - GROUND_Z + 0.3), 'ConcreteBase')


def _gravel_pad(k: Kit, cx: float, cy: float, sx: float, sy: float) -> None:
    k.polygon(g.rect(cx, cy, sx, sy), PAD_Z, 'Gravel', uv_tile=3.0)


def _obstruction_light(k: Kit, x: float, y: float, z: float, detail: float) -> None:
    """L-810 style red obstruction light on a short stub."""
    k.tube([(x, y, z), (x, y, z + 0.25)], 0.02, 'MetalDark', 6)
    k.sphere((x, y, z + 0.33), 0.08, 'LightRed', 10 if detail >= 1.0 else 6, 5)


def _shelter(k: Kit, cx: float, cy: float, sx: float, sy: float, h: float, detail: float,
             door: str = '+X', wall: str = 'MetalPainted', hvac: bool = True) -> None:
    """Prefab equipment shelter: slab, walls, roof cap, door, HVAC unit, vent."""
    k.box((cx, cy, (GROUND_Z - 0.25 + SLAB_TOP) * 0.5), (sx + 0.6, sy + 0.6, SLAB_TOP - GROUND_Z + 0.25), 'ConcreteBase')
    z0, z1 = SLAB_TOP, SLAB_TOP + h
    k.box((cx, cy, (z0 + z1) * 0.5), (sx, sy, z1 - z0), wall, mat_top='MetalDark')
    k.box((cx, cy, z1 + 0.05), (sx + 0.12, sy + 0.12, 0.10), 'MetalDark')     # roof cap / drip edge
    # door (0.9 x 2.0) on the requested face
    dw, dh = 0.9, 2.0
    if door in ('+X', '-X'):
        s = 1.0 if door == '+X' else -1.0
        k.box((cx + s * (sx * 0.5 + 0.02), cy - sy * 0.2, z0 + dh * 0.5), (0.04, dw, dh), 'MetalDark')
    else:
        s = 1.0 if door == '+Y' else -1.0
        k.box((cx - sx * 0.2, cy + s * (sy * 0.5 + 0.02), z0 + dh * 0.5), (dw, 0.04, dh), 'MetalDark')
    if hvac and detail >= 0.75:
        k.box((cx - sx * 0.5 - 0.35, cy + sy * 0.25, z0 + 0.45), (0.6, 0.8, 0.7), 'FixtureAluminum')
        k.box((cx, cy, z1 + 0.28), (0.5, 0.5, 0.36), 'FixtureAluminum')          # roof vent / AC
    if detail >= 1.0:   # cable tray / conduit along one wall
        k.tube([(cx - sx * 0.5 + 0.1, cy - sy * 0.5 - 0.06, z0 + 0.3), (cx + sx * 0.5 - 0.1, cy - sy * 0.5 - 0.06, z0 + 0.3)],
               0.03, 'Galvanized', 6)


def _lattice(k: Kit, cx: float, cy: float, z0: float, z1: float, base_half: float, top_half: float, leg_r: float,
             brace_r: float, pitch: float, detail: float, bands: int = 0, mat: str = 'Galvanized') -> None:
    """Four-leg tapered lattice: legs (optionally painted in ``bands`` alternate orange/white bands), horizontal
    ring braces every ``pitch`` and one diagonal per face per bay. PREVIEW: legs + a few rings only."""
    h = z1 - z0

    def corner(i: int, z: float) -> Vec3:
        f = (z - z0) / max(h, 1e-6)
        hw = base_half + (top_half - base_half) * f
        sx, sy = ((1, 1), (-1, 1), (-1, -1), (1, -1))[i % 4]
        return (cx + sx * hw, cy + sy * hw, z)

    segs = 8 if detail >= 1.0 else 5
    for i in range(4):
        if bands > 1:
            k.banded_tube(corner(i, z0), corner(i, z1), leg_r, bands, OBS_MATS, segs)
        else:
            k.tube([corner(i, z0), corner(i, z1)], leg_r, mat, segs)
    n = max(1, int(round(h / pitch)))
    if detail < 0.75:
        n = max(1, n // 2)
    for b in range(n + 1):
        z = z0 + h * b / n
        if b == 0:
            continue
        for i in range(4):
            k.tube([corner(i, z), corner(i + 1, z)], brace_r, mat, 4, caps=False)
        if detail >= 0.75 and b > 0:
            za = z0 + h * (b - 1) / n
            for i in range(4):
                a0, b1 = corner(i, za), corner(i + 1, z)
                if (i + b) % 2:
                    a0, b1 = corner(i + 1, za), corner(i, z)
                k.tube([a0, b1], brace_r * 0.8, mat, 3, caps=False)


def _platform(k: Kit, cx: float, cy: float, z: float, half: float, detail: float, rail: bool = True) -> None:
    """Grating platform with handrail at the top of a tower."""
    k.box((cx, cy, z - 0.06), (2 * half, 2 * half, 0.12), 'MetalDark')
    if rail and detail >= 0.75:
        pts = [(cx - half, cy - half, z), (cx + half, cy - half, z), (cx + half, cy + half, z), (cx - half, cy + half, z), (cx - half, cy - half, z)]
        k.tube([(p[0], p[1], p[2] + 1.1) for p in pts], 0.025, 'Galvanized', 4, caps=False)
        k.tube([(p[0], p[1], p[2] + 0.55) for p in pts], 0.015, 'Galvanized', 4, caps=False)
        for p in pts[:-1]:
            k.tube([p, (p[0], p[1], p[2] + 1.1)], 0.02, 'Galvanized', 4, caps=False)


def _toward_runway_local(st) -> Point:
    """Unit vector (in the structure's local frame) pointing from the navaid towards the runway centre."""
    v = g.normalize((-st.position[0], -st.position[1]))
    if v == (0.0, 0.0):
        return (0.0, -1.0)
    return g.rotate_point(v, -st.rotation)


# =========================================================================== LOCALIZER
def _b_localizer(ctx, st, n: int, detail: float) -> None:
    k = Kit(ctx, 2.0)
    elements = 14
    aperture = 30.0
    boom_z = GROUND_Z + 1.2
    half = aperture * 0.5
    # frangible support posts every 2.5 m on small pads
    posts = int(round(aperture / 2.5))
    for i in range(posts + 1):
        y = -half + aperture * i / posts
        _foundation(k, 0.0, y, 0.4, 0.04)
        if i == 0 or i == posts:      # red/white painted end posts
            k.banded_tube((0.0, y, GROUND_Z + 0.04), (0.0, y, boom_z - 0.06), 0.035, 4, ('SignRed', 'ObstructionWhite'), 8)
        else:
            k.tube([(0.0, y, GROUND_Z + 0.04), (0.0, y, boom_z - 0.06)], 0.030, 'Galvanized', 6)
    # horizontal boom (steel channel) + distribution cable tray under it
    k.box((0.0, 0.0, boom_z), (0.14, aperture + 0.8, 0.12), 'Galvanized')
    if detail >= 1.0:
        k.box((0.16, 0.0, boom_z - 0.02), (0.10, aperture + 0.4, 0.06), 'MetalDark')
    # 14 log-periodic dipole elements: vertical 1.0 m stems with 0.6 m horizontal arms (T), feed box at the base
    stem_h = 1.0
    for i in range(elements):
        y = -half + aperture * i / (elements - 1)
        z_top = boom_z + 0.06 + stem_h
        k.tube([(0.0, y, boom_z + 0.06), (0.0, y, z_top)], 0.025, 'FixtureAluminum', 6)
        k.tube([(-0.6, y, z_top), (0.6, y, z_top)], 0.02, 'FixtureAluminum', 6)
        if detail >= 1.0:
            k.tube([(-0.45, y, z_top - 0.35), (0.45, y, z_top - 0.35)], 0.015, 'FixtureAluminum', 5)
            k.box((0.0, y, boom_z + 0.14), (0.16, 0.16, 0.16), 'MetalDark')
    # end-post obstruction markers / small yellow "no taxi" markers on the boom ends
    for sy in (-1.0, 1.0):
        k.box((0.0, sy * (half + 0.3), boom_z + 0.12), (0.3, 0.3, 0.12), 'FixtureYellow')
    # equipment shelter 3 x 2.5 x 2.5 m, 60 m to +Y on a gravel pad, with a small mast for the monitor antenna
    sy_ = 60.0
    _gravel_pad(k, 0.0, sy_, 7.0, 6.0)
    _shelter(k, 0.0, sy_, 3.0, 2.5, 2.5, detail, door='-Y')
    k.tube([(1.2, sy_ + 1.0, SLAB_TOP), (1.2, sy_ + 1.0, SLAB_TOP + 4.5)], 0.03, 'Galvanized', 6)
    k.box((1.2, sy_ + 1.0, SLAB_TOP + 4.4), (0.5, 0.12, 0.12), 'FixtureAluminum')
    # near-field monitor antenna 25 m in front of the array on the runway side (a dipole on a 2 m post)
    toward = _toward_runway_local(st)
    mx, my = toward[0] * 25.0, toward[1] * 25.0
    _foundation(k, mx, my, 0.5, 0.05)
    k.tube([(mx, my, GROUND_Z + 0.05), (mx, my, GROUND_Z + 2.0)], 0.025, 'Galvanized', 6)
    k.tube([(mx, my - 0.5, GROUND_Z + 2.0), (mx, my + 0.5, GROUND_Z + 2.0)], 0.02, 'FixtureAluminum', 5)
    # warning sign facing the service road (+Y side, beside the shelter)
    warning_sign(k, 3.5, sy_ - 4.0, math.pi * 0.5, detail, "ILS LOCALIZER\nNO ENTRY")
    me = k.build(f"Navaids_Localizer_{n}", smooth_angle=math.radians(40))
    ctx.add_object(f"Navaids_Localizer_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


# =========================================================================== GLIDE SLOPE
def _b_glide_slope(ctx, st, n: int, detail: float) -> None:
    k = Kit(ctx, 2.0)
    H = max(9.0, min(16.7, st.size[2] + 4.0))         # capture-effect mast ~13 m (UFC cap 55 ft = 16.7 m)
    toward = _toward_runway_local(st)
    away = (-toward[0], -toward[1])
    # foundation block + lattice mast with 7 obstruction bands
    _foundation(k, 0.0, 0.0, 1.6, 0.15)
    half = 0.32
    _lattice(k, 0.0, 0.0, 0.15, H, half, half, 0.035, 0.014, 1.5, detail, bands=7)
    _obstruction_light(k, 0.0, 0.0, H, detail)
    # three antenna bays (3 m long "coffin" boxes) facing the runway: 4.5 / 8.5 / 12.5 m (scaled to H)
    ax = math.atan2(toward[1], toward[0])
    for z_frac in (4.5 / 13.0, 8.5 / 13.0, 12.5 / 13.0):
        z = 0.15 + (H - 0.15) * z_frac
        c = (toward[0] * (half + 0.35), toward[1] * (half + 0.35), z)
        k.plate(c, (0.28, 3.0, 0.40), 'ObstructionWhite', rot_z=ax)         # radiating element housing
        k.plate((toward[0] * (half + 0.12), toward[1] * (half + 0.12), z), (0.24, 0.6, 0.12), 'MetalDark', rot_z=ax)
        if detail >= 1.0:
            for sy in (-1.2, 1.2):
                p0 = (toward[0] * (half + 0.2) - toward[1] * sy, toward[1] * (half + 0.2) + toward[0] * sy, z)
                p1 = (-toward[1] * sy * 0.55, toward[0] * sy * 0.55, z + 0.9)
                k.tube([p0, p1], 0.012, 'Galvanized', 4, caps=False)
    # shelter 8 x 10 ft on the side away from the runway, with a gravel pad
    sx_, sy_ = away[0] * 7.0, away[1] * 7.0
    _gravel_pad(k, sx_, sy_, 9.0, 8.0)
    _shelter(k, sx_, sy_, 3.05, 2.44, 2.5, detail, door='+X')
    if detail >= 1.0:   # cable duct from the shelter to the mast base
        k.box(((sx_) * 0.5, (sy_) * 0.5, GROUND_Z + 0.1), (abs(sx_) + 1.0 if abs(sx_) > abs(sy_) else 0.3,
                                                          abs(sy_) + 1.0 if abs(sy_) >= abs(sx_) else 0.3, 0.2), 'ConcreteBase')
    warning_sign(k, sx_ + 3.5, sy_ + 3.0, 0.0, detail, "ILS GLIDE SLOPE\nCRITICAL AREA")
    me = k.build(f"Navaids_GlideSlope_{n}", smooth_angle=math.radians(40))
    ctx.add_object(f"Navaids_GlideSlope_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


# =========================================================================== TACAN
def _b_tacan(ctx, st, n: int, detail: float) -> None:
    k = Kit(ctx, 2.0)
    _gravel_pad(k, 0.0, 0.0, 22.0, 16.0)
    # equipment building 6 x 4.5 x 3 m
    _shelter(k, -5.0, 0.0, 6.0, 4.5, 3.0, detail, door='+X')
    # 6 m tower on four 0.6 m pads, radome on top
    tz = 6.0
    for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
        _foundation(k, 4.0 + sx * 1.1, sy * 1.1, 0.6, 0.12)
    _lattice(k, 4.0, 0.0, 0.12, tz, 1.1, 0.6, 0.05, 0.02, 1.5, detail)
    _platform(k, 4.0, 0.0, tz, 0.9, detail)
    k.cylinder((4.0, 0.0), 0.25, tz, tz + 0.5, 'MetalDark', segments=10)           # pedestal
    segs = 20 if detail >= 1.0 else 12
    k.cylinder((4.0, 0.0), 0.45, tz + 0.5, tz + 1.7, 'TankWhite', segments=segs, cap_top=False)
    k.cylinder((4.0, 0.0), 0.45, tz + 1.7, tz + 2.0, 'TankWhite', segments=segs, radius_top=0.30)   # frustum cap
    k.cylinder((4.0, 0.0), 0.30, tz + 2.0, tz + 2.08, 'TankWhite', segments=segs)
    _obstruction_light(k, 4.0 + 0.85, 0.85, tz + 0.05, detail)
    if detail >= 1.0:   # ladder on the -X face of the tower, cable run to the building
        k.tube([(4.0 - 1.3, -0.25, 0.12), (4.0 - 0.75, -0.25, tz)], 0.02, 'Galvanized', 4)
        k.tube([(4.0 - 1.3, 0.25, 0.12), (4.0 - 0.75, 0.25, tz)], 0.02, 'Galvanized', 4)
        for i in range(1, int(tz / 0.3)):
            f = i * 0.3 / tz
            x = 4.0 - 1.3 + 0.55 * f
            k.tube([(x, -0.25, 0.12 + i * 0.3), (x, 0.25, 0.12 + i * 0.3)], 0.012, 'Galvanized', 4, caps=False)
        k.box((0.0, 0.0, GROUND_Z + 0.1), (8.0, 0.3, 0.2), 'ConcreteBase')
    warning_sign(k, -9.0, -5.0, math.pi, detail, "TACAN\nRF HAZARD")
    me = k.build(f"Navaids_TACAN_{n}", smooth_angle=math.radians(40))
    ctx.add_object(f"Navaids_TACAN_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


# =========================================================================== RADAR (ASR type)
def _reflector(k: Kit, width: float, height: float, depth: float, thick: float, detail: float) -> None:
    """Section of a paraboloid approximated by a bent plate: parabola x = depth * (2y/width)^2 in plan,
    extruded from z = 0 to ``height`` (local frame, concave side facing +X)."""
    n_pts = 24 if detail >= 1.0 else 12
    front: list[Point] = []
    for i in range(n_pts + 1):
        y = -width * 0.5 + width * i / n_pts
        x = depth * (2.0 * y / width) ** 2
        front.append((x, y))
    back = [(x + thick, y) for x, y in front][::-1]
    poly = front + back
    tmp = Kit(k.ctx, k.tile)
    tmp.mb.add_prism(poly, 0.0, height, mat_top=tmp.m('FixtureAluminum'), mat_side=tmp.m('FixtureAluminum'),
                     mat_bottom=tmp.m('FixtureAluminum'), smooth_sides=True)
    k.append(tmp)


def _b_radar(ctx, st, n: int, detail: float) -> None:
    k = Kit(ctx, 2.0)
    _gravel_pad(k, 0.0, 0.0, 26.0, 18.0)
    tz = 12.0
    # tower: 3 m square base tapering to 1.6 m, four foundations, 7 obstruction bands on the legs
    for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
        _foundation(k, sx * 1.5, sy * 1.5, 0.8, 0.15)
    _lattice(k, 0.0, 0.0, 0.15, tz, 1.5, 0.8, 0.07, 0.03, 2.0, detail, bands=7)
    _platform(k, 0.0, 0.0, tz, 1.5, detail)
    # rotating pedestal + gearbox
    k.box((0.0, 0.0, tz + 0.6), (1.4, 1.4, 1.2), 'MetalPainted', mat_top='MetalDark')
    k.cylinder((0.0, 0.0), 0.35, tz + 1.2, tz + 1.7, 'MetalDark', segments=12)
    ant = Kit(ctx, 2.0)
    # antenna assembly in its own frame: concave side to +X, then tilted up 12 deg and rotated 35 deg about Z
    ant.box((0.0, 0.0, 0.25), (1.0, 0.8, 0.5), 'MetalPainted')                       # turntable
    ant.box((-0.6, 0.0, 1.2), (0.5, 1.6, 1.6), 'MetalDark')                          # back-frame
    refl = Kit(ctx, 2.0)
    _reflector(refl, 8.0, 3.0, 1.1, 0.06, detail)
    ant.append(refl, Matrix.Translation((-0.9, 0.0, 0.4)) @ Matrix.Rotation(math.radians(-12.0), 4, 'Y'))
    # feed horn on a boom in front of the reflector focus
    ant.tube([(-0.6, 0.0, 0.9), (2.1, 0.0, 1.35)], 0.06, 'Galvanized', 6)
    ant.box((2.2, 0.0, 1.35), (0.5, 0.45, 0.45), 'FixtureAluminum')
    if detail >= 1.0:
        for sy in (-2.5, 2.5):
            ant.tube([(-0.5, sy * 0.8, 0.6), (-0.1, sy, 3.2)], 0.03, 'Galvanized', 4)
    # IFF / SSR bar on top of the reflector
    ant.box((-0.3, 0.0, 3.9), (0.25, 5.2, 0.35), 'FixtureAluminum')
    ant.tube([(-0.7, 0.0, 3.4), (-0.3, 0.0, 3.75)], 0.05, 'Galvanized', 6)
    k.append(ant, Matrix.Translation((0.0, 0.0, tz + 1.7)) @ Matrix.Rotation(math.radians(35.0), 4, 'Z'))
    _obstruction_light(k, -1.3, 1.3, tz + 0.05, detail)
    _obstruction_light(k, 1.3, -1.3, tz + 0.05, detail)
    # equipment shelter 6 x 3 x 3 m
    _shelter(k, 8.5, -3.0, 6.0, 3.0, 3.0, detail, door='-Y')
    if detail >= 1.0:
        k.box((3.5, -3.0, GROUND_Z + 0.1), (6.0, 0.3, 0.2), 'ConcreteBase')          # cable duct
        k.box((8.5, -6.2, GROUND_Z + 0.5), (1.6, 1.0, 1.0), 'MetalDark')              # generator
    warning_sign(k, -8.0, 7.0, math.pi * 0.5, detail, "RADAR SITE\nRF HAZARD")
    me = k.build(f"Navaids_Radar_{n}", smooth_angle=math.radians(40))
    ctx.add_object(f"Navaids_Radar_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


# =========================================================================== AWOS
def _b_awos(ctx, st, n: int, detail: float) -> None:
    k = Kit(ctx, 2.0)
    _gravel_pad(k, 0.0, 0.0, 6.0, 6.0)
    # 10 m tilt-down wind mast (tapered tube) with a cross-arm: anemometer + vane
    _foundation(k, -2.0, -2.0, 0.6, 0.10)
    k.tube([(-2.0, -2.0, 0.10), (-2.0, -2.0, 6.0)], 0.06, 'Galvanized', 8)
    k.tube([(-2.0, -2.0, 6.0), (-2.0, -2.0, 10.0)], 0.04, 'Galvanized', 8)
    k.tube([(-2.6, -2.0, 10.0), (-1.4, -2.0, 10.0)], 0.02, 'FixtureAluminum', 6)
    k.tube([(-2.6, -2.0, 10.0), (-2.6, -2.0, 10.35)], 0.015, 'FixtureAluminum', 5)
    k.sphere((-2.6, -2.0, 10.45), 0.10, 'FixtureAluminum', 8, 4)                     # sonic / cup sensor head
    k.tube([(-1.4, -2.0, 10.0), (-1.4, -2.0, 10.35)], 0.015, 'FixtureAluminum', 5)
    k.plate((-1.25, -2.0, 10.35), (0.5, 0.02, 0.18), 'FixtureAluminum')               # vane
    _obstruction_light(k, -2.0, -2.0, 10.0, detail)
    if detail >= 1.0:   # tilt-down hinge / winch base
        k.box((-2.0, -2.0, 0.5), (0.25, 0.25, 0.8), 'MetalDark')
    # 2 m mast with tipping-bucket rain gauge
    _foundation(k, 2.0, -2.0, 0.6, 0.10)
    k.tube([(2.0, -2.0, 0.10), (2.0, -2.0, 2.0)], 0.03, 'Galvanized', 6)
    k.cylinder((2.0, -2.0), 0.12, 2.0, 2.45, 'TankWhite', segments=12, cap_top=True)
    k.cylinder((2.0, -2.0), 0.13, 2.4, 2.5, 'MetalDark', segments=12, radius_top=0.10)
    # cloud ceilometer 0.5 x 0.5 x 1.2 with a tilted window
    _foundation(k, 2.0, 2.0, 0.7, 0.10)
    k.box((2.0, 2.0, 0.10 + 0.6), (0.5, 0.5, 1.2), 'TankWhite', mat_top='MetalDark')
    k.plate((2.0, 2.0, 1.36), (0.44, 0.44, 0.03), 'Glass', rot_x=math.radians(15.0))
    # visibility / present-weather sensor: 3 m post with a 3 m cross-arm and angled heads
    _foundation(k, -2.0, 2.0, 0.6, 0.10)
    k.tube([(-2.0, 2.0, 0.10), (-2.0, 2.0, 3.0)], 0.035, 'Galvanized', 6)
    k.tube([(-3.5, 2.0, 3.0), (-0.5, 2.0, 3.0)], 0.025, 'Galvanized', 6)
    k.plate((-3.4, 2.0, 2.85), (0.45, 0.22, 0.22), 'FixtureAluminum', rot_y=math.radians(-25.0))
    k.plate((-0.6, 2.0, 2.85), (0.45, 0.22, 0.22), 'FixtureAluminum', rot_y=math.radians(25.0))
    k.box((-2.0, 2.0, 2.75), (0.3, 0.25, 0.3), 'MetalDark')                            # electronics box
    # aspirated temperature / RH shield on a 1.5 m post
    k.tube([(0.0, 2.3, 0.10), (0.0, 2.3, 1.5)], 0.025, 'Galvanized', 6)
    k.cylinder((0.0, 2.3), 0.09, 1.5, 1.85, 'TankWhite', segments=10)
    # equipment cabinet 1.5 x 1.0 x 1.4 on a plinth
    k.box((0.0, -1.5, GROUND_Z + 0.1), (1.8, 1.3, 0.25), 'ConcreteBase')
    k.box((0.0, -1.5, 0.15 + 0.7), (1.5, 1.0, 1.4), 'MetalPainted', mat_top='MetalDark')
    if detail >= 1.0:
        k.box((0.0, -1.0 - 0.02, 0.85), (1.3, 0.04, 1.1), 'MetalDark')                # cabinet doors
        k.box((0.0, -1.5, 1.6), (0.35, 0.35, 0.3), 'FixtureAluminum')                 # cabinet AC
        k.tube([(0.0, -1.5, 1.9), (0.0, -1.5, 4.0)], 0.02, 'Galvanized', 6)          # comms whip
    warning_sign(k, 3.5, -3.5, -math.pi * 0.5, detail, "WEATHER SENSORS\nDO NOT DISTURB")
    me = k.build(f"Navaids_AWOS_{n}", smooth_angle=math.radians(40))
    ctx.add_object(f"Navaids_AWOS_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


DISPATCH = {
    'LOCALIZER': _b_localizer,
    'GLIDE_SLOPE': _b_glide_slope,
    'TACAN': _b_tacan,
    'RADAR': _b_radar,
    'AWOS': _b_awos,
}


# =========================================================================== entry point
def build(ctx) -> None:
    detail = ctx.detail
    counts: dict[str, int] = defaultdict(int)
    for st in ctx.plan.navaids:
        fn = DISPATCH.get(st.kind)
        if fn is None:
            ctx.warn(f"navaids: unknown navaid kind {st.kind!r} skipped")
            continue
        counts[st.kind] += 1
        try:
            fn(ctx, st, counts[st.kind], detail)
        except Exception as exc:
            traceback.print_exc()
            ctx.warn(f"navaids: {st.kind}: {type(exc).__name__}: {exc}")
