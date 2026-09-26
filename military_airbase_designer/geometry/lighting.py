"""Airfield lighting: fixture meshes + placement per UFC 3-535-01 / FAA AC 150/5340-30J.

Everything here is *instanced*: one shared mesh datablock per fixture type and
lens-colour combination (``ctx.shared_mesh``) and one linked-duplicate object per
fixture (``ctx.add_instance``), so a full Class B base (about 1,000-2,500
fixtures) builds in well under a second and exports as a handful of meshes.

Conventions
-----------
* Base frame (see ``core.geom2d``): runway along +X from the low-numbered end,
  +Y left, origin at the runway centre, metres.
* Bidirectional fixtures (edge, threshold/end, centreline, in-pavement) have two
  lens material slots: **A faces local +X** (its beam travels toward +X and is
  seen by an aircraft that is further along +X, i.e. rolling *toward the low end*),
  **B faces local -X** (seen by aircraft rolling toward the high end). They are
  placed with rotation 0 so colour logic stays in runway coordinates.
* Unidirectional rows (approach barrettes, TDZ, stop bars, PAPI, flashers) have
  their lens on local +X and are rotated so that +X points *toward the approach*
  (or toward the holding aircraft).
* Elevated fixtures stand on the ground (z = 0, or the runway crown on the
  shoulder); in-pavement fixtures are 12 mm proud discs at the crown height.

Standards references are quoted inline as "UFC §x-y" (UFC 3-535-01, Change 4)
and "30J §x" (FAA AC 150/5340-30J).
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

from mathutils import Vector

from ..core import geom2d as g
from ..core.constants import LT
from ..core.meshbuild import MeshBuilder
from ..core.units import ft, inch
from ..layout import plan as P

Point = g.Point
CAT = "Lighting"

# --------------------------------------------------------------------------- material spec names
M_WHITE, M_RED, M_GREEN, M_BLUE, M_YELLOW, M_FLASH, M_FLOOD = (
    'LightWhite', 'LightRed', 'LightGreen', 'LightBlue', 'LightYellow', 'LightFlasher', 'LightFlood')
M_BODY, M_ALU, M_CAST, M_PAD, M_GALV, M_DARK, M_PAINT = (
    'FixtureYellow', 'FixtureAluminum', 'FixtureCast', 'ConcreteBase', 'Galvanized', 'MetalDark', 'MetalPainted')
BLANK_INPAV = M_CAST      # blank cover instead of a prism window
BLANK_ELEV = M_BODY       # blank side of an elevated head

# --------------------------------------------------------------------------- fixture dimensions (UFC §12 cheat-sheet / mfr data)
ELEV_TOP = LT.fixture_elevated_h          # 14 in top of lens (L-862 / L-861T / L-862E)
STEM_R = inch(1.0)                        # 2 in EMT stem
COUPLING_R = inch(1.5)                    # frangible coupling (snaps <= 3 in above grade)
COUPLING_H = inch(3.0)
PAD_SIZE = 0.30                           # concrete base pad, 0.3 m square
PAD_H = 0.012                             # 12 mm proud so it reads against the ground
INPAV_R = LT.fixture_inpavement_d * 0.5   # 12 in diameter L-850 / L-852
INPAV_H = 0.012                           # <= 0.5 in proud
LENS_R = inch(2.0)                        # 4 in lenses on the L-862 head
HEAD_H = inch(6.8)                        # L-862 head height (mfr 173 mm)
BARRETTE_PITCH = inch(40.5)               # 1.03 m lamp pitch (UFC §3-1)
BARRETTE_LIGHTS = 5
PAR56_R = inch(3.5)                       # 7 in PAR-56 lamp
PAR38_R = inch(2.4)                       # PAR-38 lamp (MALSR)
ALS_STEM_H = 1.0                          # light plane at threshold elevation: 1.0 m stems on flat ground
LIR_MIN_H = 1.8                           # above 6 ft the support becomes an LIR lattice mast (UFC §3-1.8.2)
FLOOD_MAST_H = LT.flood_mast_h            # 24 m high-mast pole
FLASHER_HEAD = 0.30                       # L-849 flash head
RGL_LENS_Z = 0.75                         # L-804 lens centres ~30 in above grade
PAPI_LENS_Z = 0.75                        # L-880 lens centre ~30 in above grade
PAPI_BOX = (0.50, 0.92, 0.42)             # depth (X), width (Y), height
ARRESTING_CLEAR = ft(30)                  # omit RCL fixtures this close to a pendant cable (UFC §4-7: up to 5 omitted)
TAXI_PT_OFFSET = LT.edge_offset + ft(3)   # last blue light at runway edge-light line + 3 ft (UFC §5-2)
TAXI_PT_PAIR = ft(5)                      # entrance/exit pair 5 ft apart
RGL_EDGE_OFFSET = ft(12)                  # wig-wags 10-17 ft outboard of the taxiway edge (UFC §5-5)
STOP_BAR_PITCH = 3.0                      # 9 ft 10 in c-c (UFC §5-6)
STOP_BAR_SETBACK = ft(2)                  # 2 ft on the holding side of the hold marking
STOP_BAR_ELEV_OFFSET = ft(8)              # elevated red light each side, <= 10 ft from the edge
THR_LINE_OFFSET = ft(5)                   # threshold line <= 10 ft outside the landing surface (UFC §4-4)
THR_WING_SPACING = ft(5)
REIL_LATERAL = ft(40)                     # REIL 40 ft outboard of the edge lights (UFC §3-6)
REIL_TOE_OUT = math.radians(15)

# Approach lighting systems (stations in feet from the threshold; UFC Ch. 3, Air Force 3,000 ft ALSF)
ALS_SYSTEMS: dict[str, dict] = {
    'ALSF2': dict(barrettes=(100, 3000, 100), flashers=(1000, 3000, 100), side_rows=(100, 900, 100),
                  bar500=True, bar1000=8, length=3000),
    'ALSF1': dict(barrettes=(100, 3000, 100), flashers=(1000, 3000, 100), terminating=True, prethreshold=True,
                  bar1000=8, length=3000),
    'MALSR': dict(barrettes=(200, 1400, 200), flashers=(1600, 2400, 200), bar1000=5, lamp='PAR38', length=2400),
    'SSALR': dict(barrettes=(200, 1400, 200), flashers=(1600, 2400, 200), bar1000=5, length=2400),
    'ODALS': dict(odals=True, length=1500),
}

FIXTURE_CATALOG = (
    ('EdgeElev', "L-862 elevated bidirectional runway edge light (two 4 in lenses, 14 in, frangible coupling)"),
    ('ThresholdElev', "L-862E elevated threshold / end light (green toward approach, red toward runway)"),
    ('TaxiEdge', "L-861T elevated omnidirectional blue taxiway edge light (14 in)"),
    ('Inpav', "L-850 / L-852 in-pavement fixture, 12 in disc, two prism windows (lens colour per slot)"),
    ('InpavRow', "row of in-pavement fixtures (TDZ barrette, stop bar, approach barrette in the overrun)"),
    ('Barrette', "elevated approach barrette: PAR-56 / PAR-38 lamps on a horizontal tube, frangible stems or LIR lattice mast"),
    ('Flasher', "L-849 sequenced flasher / RAIL / ODALS head with power can"),
    ('REIL', "L-849 runway end identifier flash head"),
    ('PAPI', "L-880 PAPI light housing assembly on two frangible legs (3 lamp windows, white over red)"),
    ('GuardLight', "L-804 elevated runway guard light (two 8 in amber lamps, wig-wag)"),
    ('StopBarElev', "L-862S elevated stop bar light (red)"),
    ('FloodMast', "24 m apron high-mast floodlight (4 luminaires, concrete base)"),
    ('Obstruction', "L-810 red obstruction light"),
    ('BeaconHead', "L-802M rotating beacon head (drum, white + green lenses) on a railed platform"),
)


def fixture_catalog() -> list[str]:
    """Fixture keys built by this module (documentation / UI)."""
    return [k for k, _ in FIXTURE_CATALOG]


def fixture_descriptions() -> dict[str, str]:
    return dict(FIXTURE_CATALOG)


# =========================================================================== mesh helpers
def _prop_uv(x: float, y: float, z: float) -> tuple[float, float]:
    """Simple non-degenerate UVs for flat-coloured props (0.5 m per repeat)."""
    return ((x + y) * 2.0, z * 2.0)


class _Fx:
    """MeshBuilder + material-slot bookkeeping for one fixture mesh."""

    def __init__(self, ctx, key: str):
        self.ctx = ctx
        self.key = key
        self.mb = MeshBuilder(default_uv=_prop_uv)
        self.names: list[str] = []
        d = ctx.detail
        self.seg_scale = 0.75 if d < 0.75 else (1.25 if d > 1.25 else 1.0)

    def m(self, name: str) -> int:
        if name not in self.names:
            self.names.append(name)
        return self.names.index(name)

    def seg(self, n: int) -> int:
        return max(6, int(round(n * self.seg_scale)))

    def build(self):
        mats = [self.ctx.mats(n) for n in self.names]
        return self.mb.build(self.key, mats, smooth_angle=math.radians(60.0))


def _hcyl(mb: MeshBuilder, p0, p1, r: float, seg: int, mat_side: int,
          mat_cap0: int | None = None, mat_cap1: int | None = None, smooth: bool = True) -> None:
    """Cylinder between two arbitrary points with independent cap materials (lenses, lamp cans, tubes)."""
    a, b = Vector(p0), Vector(p1)
    d = b - a
    if d.length < 1e-9:
        return
    d.normalize()
    up = Vector((0.0, 0.0, 1.0)) if abs(d.z) < 0.9 else Vector((1.0, 0.0, 0.0))
    n1 = d.cross(up).normalized()
    n2 = d.cross(n1).normalized()
    ring0, ring1 = [], []
    for k in range(seg):
        t = 2.0 * math.pi * k / seg
        off = (n1 * math.cos(t) + n2 * math.sin(t)) * r
        q0, q1 = a + off, b + off
        ring0.append(mb.add_vertex((q0.x, q0.y, q0.z)))
        ring1.append(mb.add_vertex((q1.x, q1.y, q1.z)))
    for k in range(seg):
        j = (k + 1) % seg
        mb.add_face_idx((ring0[k], ring0[j], ring1[j], ring1[k]), mat_side, smooth,
                        uvs=[(k / seg, 0.0), ((k + 1) / seg, 0.0), ((k + 1) / seg, 1.0), (k / seg, 1.0)])
    if mat_cap0 is not None:
        mb.add_face_idx(tuple(reversed(ring0)), mat_cap0, False, uvs=[(0.0, 0.0)] * seg)
    if mat_cap1 is not None:
        mb.add_face_idx(tuple(ring1), mat_cap1, False, uvs=[(0.0, 0.0)] * seg)


def _dome(mb: MeshBuilder, cx: float, cy: float, z0: float, r: float, h: float, seg_u: int, seg_v: int, mat: int) -> None:
    """Dome (quarter-ellipse profile) from a base ring at z0 up to z0 + h."""
    rings = []
    for i in range(seg_v):
        phi = 0.5 * math.pi * i / seg_v
        rr = r * math.cos(phi)
        zz = z0 + h * math.sin(phi)
        rings.append([mb.add_vertex((cx + rr * math.cos(2 * math.pi * k / seg_u), cy + rr * math.sin(2 * math.pi * k / seg_u), zz))
                      for k in range(seg_u)])
    top = mb.add_vertex((cx, cy, z0 + h))
    for r0, r1 in zip(rings, rings[1:]):
        for k in range(seg_u):
            j = (k + 1) % seg_u
            mb.add_face_idx((r0[k], r0[j], r1[j], r1[k]), mat, True, uvs=[(0, 0), (1, 0), (1, 1), (0, 1)])
    last = rings[-1]
    for k in range(seg_u):
        j = (k + 1) % seg_u
        mb.add_face_idx((last[k], last[j], top), mat, True, uvs=[(0, 0), (1, 0), (0.5, 1)])


def _quad_x(mb: MeshBuilder, x: float, y0: float, y1: float, z0: float, z1: float, mat: int, facing: int = 1) -> None:
    """Axis-aligned quad in a YZ plane at ``x`` with its normal toward +X (facing=1) or -X."""
    if facing > 0:
        mb.add_face(((x, y0, z0), (x, y1, z0), (x, y1, z1), (x, y0, z1)), mat, False, uvs=[(0, 0), (1, 0), (1, 1), (0, 1)])
    else:
        mb.add_face(((x, y1, z0), (x, y0, z0), (x, y0, z1), (x, y1, z1)), mat, False, uvs=[(0, 0), (1, 0), (1, 1), (0, 1)])


def _quad_z(mb: MeshBuilder, z: float, x0: float, x1: float, y0: float, y1: float, mat: int, facing: int = -1) -> None:
    """Horizontal quad at ``z`` facing down (default) or up."""
    if facing < 0:
        mb.add_face(((x0, y0, z), (x0, y1, z), (x1, y1, z), (x1, y0, z)), mat, False, uvs=[(0, 0), (0, 1), (1, 1), (1, 0)])
    else:
        mb.add_face(((x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)), mat, False, uvs=[(0, 0), (1, 0), (1, 1), (0, 1)])


def _stem(fx: _Fx, cx: float, cy: float, top_z: float, pads: bool, stem_r: float = STEM_R,
          coupling: bool = True) -> None:
    """Concrete pad + L-867 base-can ring + frangible coupling + 2 in EMT stem (UFC §3-1.8.2, 30J §2)."""
    mb = fx.mb
    alu = fx.m(M_ALU)
    z = 0.0
    if pads:
        mb.add_box((cx, cy, PAD_H * 0.5), (PAD_SIZE, PAD_SIZE, PAD_H), fx.m(M_PAD), uv_tile=0.5)
        z = PAD_H
        # base can flange ring
        mb.add_cylinder((cx, cy), inch(5.0), z, z + 0.008, fx.seg(8), alu, cap_top=True)
        z += 0.008
    if coupling:
        mb.add_cylinder((cx, cy), COUPLING_R, z, z + COUPLING_H, fx.seg(8), alu, cap_top=True)
        z += COUPLING_H
    mb.add_cylinder((cx, cy), stem_r, z, top_z, fx.seg(8), alu, cap_top=False)


def _lattice_mast(fx: _Fx, cx: float, cy: float, top_z: float, side: float = 0.45) -> None:
    """LIR triangular lattice mast (UFC §3-1.8.2: 6-40 ft supports are low-impact-resistant lattice)."""
    mb = fx.mb
    galv = fx.m(M_GALV)
    pad = fx.m(M_PAD)
    mb.add_box((cx, cy, 0.05), (side + 0.6, side + 0.6, 0.10), pad, uv_tile=0.5)
    r = side / math.sqrt(3.0)
    legs = [(cx + r * math.cos(math.radians(90 + 120 * i)), cy + r * math.sin(math.radians(90 + 120 * i))) for i in range(3)]
    seg = fx.seg(6)
    for lx, ly in legs:
        mb.add_cylinder((lx, ly), 0.02, 0.10, top_z, seg, galv, cap_top=True)
    n_levels = max(1, int(top_z / 1.2))
    for lv in range(n_levels + 1):
        z = 0.10 + (top_z - 0.10) * lv / n_levels
        for i in range(3):
            a, b = legs[i], legs[(i + 1) % 3]
            _hcyl(mb, (a[0], a[1], z), (b[0], b[1], z), 0.012, 4, galv)
            if lv < n_levels:   # diagonal
                z2 = 0.10 + (top_z - 0.10) * (lv + 1) / n_levels
                _hcyl(mb, (a[0], a[1], z), (b[0], b[1], z2), 0.010, 4, galv)


# =========================================================================== fixture factories
def _mesh_edge_elevated(ctx, key: str, lens_a: str, lens_b: str, pads: bool):
    """L-862 (or L-862E / L-862S) elevated bidirectional head on a frangible stem (UFC §4-2; mfr dims)."""
    fx = _Fx(ctx, key)
    mb = fx.mb
    body = fx.m(M_BODY)
    z_head0 = ELEV_TOP - HEAD_H
    _stem(fx, 0.0, 0.0, z_head0 + 0.01, pads)
    # head: flared skirt, tapered cast body, top cap
    s8 = fx.seg(8)
    mb.add_cylinder((0.0, 0.0), 0.045, z_head0 - 0.005, z_head0 + 0.03, s8, body, radius_top=0.072, cap_top=False)
    mb.add_cylinder((0.0, 0.0), 0.072, z_head0 + 0.03, ELEV_TOP - 0.03, s8, body, radius_top=0.066, cap_top=False)
    mb.add_cylinder((0.0, 0.0), 0.066, ELEV_TOP - 0.03, ELEV_TOP, s8, body, radius_top=0.040, cap_top=True)
    # two 4 in lenses in bezels on +X (slot A) and -X (slot B)
    zl = ELEV_TOP - 0.065
    s12 = fx.seg(12)
    for sign, lens in ((1, lens_a), (-1, lens_b)):
        _hcyl(mb, (sign * 0.050, 0.0, zl), (sign * 0.088, 0.0, zl), LENS_R + 0.006, s12, body, None, fx.m(lens))
    return fx.build()


def _mesh_taxi_edge(ctx, key: str, pads: bool):
    """L-861T omnidirectional blue taxiway edge light, 14 in top of lens (UFC §5-2; mfr)."""
    fx = _Fx(ctx, key)
    mb = fx.mb
    body = fx.m(M_BODY)
    blue = fx.m(M_BLUE)
    _stem(fx, 0.0, 0.0, 0.205, pads)
    s12 = fx.seg(12)
    mb.add_cylinder((0.0, 0.0), 0.045, 0.20, 0.235, s12, body, radius_top=0.070, cap_top=False)   # collar
    mb.add_cylinder((0.0, 0.0), 0.066, 0.235, 0.300, s12, blue, cap_top=False)                   # blue glass
    _dome(mb, 0.0, 0.0, 0.300, 0.066, ELEV_TOP - 0.300 - 0.008, s12, 3, blue)
    mb.add_cylinder((0.0, 0.0), 0.022, ELEV_TOP - 0.012, ELEV_TOP, s12, body, cap_top=True)       # top cap
    return fx.build()


def _inpav_disc(fx: _Fx, cy: float, lens_a: str, lens_b: str) -> None:
    """One L-850/L-852 12 in in-pavement fixture centred at (0, cy): cast disc + two prism windows."""
    mb = fx.mb
    cast = fx.m(M_CAST)
    s = fx.seg(16)
    mb.add_cylinder((0.0, cy), INPAV_R, 0.0, INPAV_H, s, cast, cap_top=True)
    # raised optical centre carrying the two prism windows
    mb.add_cylinder((0.0, cy), 0.095, INPAV_H, INPAV_H + 0.007, fx.seg(12), cast, radius_top=0.080, cap_top=True)
    for sign, lens in ((1, lens_a), (-1, lens_b)):
        mat = fx.m(lens)
        x0, x1 = sign * 0.070, sign * 0.105
        if sign < 0:
            x0, x1 = x1, x0
        # prism window: a small sloped block whose outer face carries the lens material
        z0, z1 = INPAV_H + 0.001, INPAV_H + 0.009
        w = 0.042
        p = [(x0, cy - w, z0), (x1, cy - w, z0), (x1, cy + w, z0), (x0, cy + w, z0)]
        q = [(x0, cy - w, z1), (x1, cy - w, z1), (x1, cy + w, z1), (x0, cy + w, z1)]
        mb.add_face((q[0], q[1], q[2], q[3]), mat, False, uvs=[(0, 0), (1, 0), (1, 1), (0, 1)])
        # side faces
        mb.add_face((p[0], p[1], q[1], q[0]), mat, False, uvs=[(0, 0), (1, 0), (1, 1), (0, 1)])
        mb.add_face((p[2], p[3], q[3], q[2]), mat, False, uvs=[(0, 0), (1, 0), (1, 1), (0, 1)])
        outer = (p[1], p[2], q[2], q[1]) if sign > 0 else (p[3], p[0], q[0], q[3])
        mb.add_face(outer, mat, False, uvs=[(0, 0), (1, 0), (1, 1), (0, 1)])


def _mesh_inpav(ctx, key: str, lens_a: str, lens_b: str):
    fx = _Fx(ctx, key)
    _inpav_disc(fx, 0.0, lens_a, lens_b)
    return fx.build()


def _mesh_inpav_row(ctx, key: str, n: int, pitch: float, lens: str):
    """Row of unidirectional in-pavement fixtures along local Y, lens toward local +X."""
    fx = _Fx(ctx, key)
    half = (n - 1) * pitch * 0.5
    for i in range(n):
        _inpav_disc(fx, -half + i * pitch, lens, BLANK_INPAV)
    return fx.build()


def _mesh_barrette(ctx, key: str, n: int, pitch: float, height: float, lens: str, lamp_r: float, pads: bool):
    """Elevated approach barrette: ``n`` PAR lamps in hooded cans on a horizontal tube (FAA-E-2325 lamp holders)."""
    fx = _Fx(ctx, key)
    mb = fx.mb
    body = fx.m(M_BODY)
    alu = fx.m(M_ALU)
    half = (n - 1) * pitch * 0.5
    if height > LIR_MIN_H:
        _lattice_mast(fx, 0.0, 0.0, height - 0.05)
    elif n >= 3:
        for sy in (-1.0, 1.0):
            _stem(fx, 0.0, sy * max(0.3, half - pitch * 0.5), height - 0.02, pads)
    else:
        _stem(fx, 0.0, 0.0, height - 0.02, pads)
    # horizontal carrier tube
    _hcyl(mb, (0.0, -half - 0.12, height), (0.0, half + 0.12, height), 0.024, fx.seg(8), alu, alu, alu)
    seg = fx.seg(10 if n <= 5 else 8)        # LOD budget: keep rows of 8 under ~400 tris
    for i in range(n):
        y = -half + i * pitch
        # hooded lamp can pointing toward the approach (+X); the front cap is the lens
        zc = height + lamp_r * 0.2
        _hcyl(mb, (-0.10, y, zc), (0.16, y, zc), lamp_r + 0.008, seg, body, body, fx.m(lens))
    return fx.build()


def _mesh_flasher(ctx, key: str, pads: bool):
    """L-849 sequenced flasher / REIL: 0.3 m flash head on a stem with a power-supply can at grade."""
    fx = _Fx(ctx, key)
    mb = fx.mb
    body = fx.m(M_BODY)
    dark = fx.m(M_DARK)
    flash = fx.m(M_FLASH)
    zc = 0.52
    _stem(fx, 0.0, 0.0, zc - FLASHER_HEAD * 0.5 + 0.01, pads, stem_r=0.02)
    mb.add_box((0.0, 0.0, zc), (FLASHER_HEAD * 0.85, FLASHER_HEAD, FLASHER_HEAD), body, uv_tile=0.5)
    # hood lip and window
    _quad_x(mb, FLASHER_HEAD * 0.425 + 0.003, -0.11, 0.11, zc - 0.10, zc + 0.10, flash, 1)
    mb.add_box((FLASHER_HEAD * 0.425 + 0.03, 0.0, zc + FLASHER_HEAD * 0.5 - 0.01), (0.09, FLASHER_HEAD + 0.02, 0.018), body, uv_tile=0.5)
    # power supply can (L-849 PSU) beside the stem
    if pads:
        mb.add_box((-0.45, 0.0, PAD_H * 0.5), (0.75, 0.50, PAD_H), fx.m(M_PAD), uv_tile=0.5)
    mb.add_box((-0.45, 0.0, 0.14 + PAD_H), (0.60, 0.36, 0.26), dark, uv_tile=0.5)
    return fx.build()


def _mesh_papi(ctx, key: str, pads: bool):
    """L-880 PAPI light housing assembly: box on two frangible legs, three lamp windows, white over red (UFC §3-7)."""
    fx = _Fx(ctx, key)
    mb = fx.mb
    body = fx.m(M_BODY)
    white = fx.m(M_WHITE)
    red = fx.m(M_RED)
    dx, dy, dz = PAPI_BOX
    z_box0 = PAPI_LENS_Z - dz * 0.5
    for sy in (-0.30, 0.30):
        _stem(fx, -0.05, sy, z_box0 + 0.01, pads, stem_r=0.03)
    if pads:
        mb.add_box((-0.05, 0.0, PAD_H * 0.5), (0.5, dy + 0.4, PAD_H), fx.m(M_PAD), uv_tile=0.5)
    mb.add_box((0.0, 0.0, PAPI_LENS_Z), (dx, dy, dz), body, uv_tile=0.5)
    # three lamp windows on the +X face, upper half white / lower half red
    xf = dx * 0.5 + 0.003
    for cy in (-0.30, 0.0, 0.30):
        _quad_x(mb, xf, cy - 0.10, cy + 0.10, PAPI_LENS_Z, PAPI_LENS_Z + 0.085, white, 1)
        _quad_x(mb, xf, cy - 0.10, cy + 0.10, PAPI_LENS_Z - 0.085, PAPI_LENS_Z, red, 1)
    # sun hood over the windows + rear service door outline
    mb.add_box((dx * 0.5 + 0.05, 0.0, PAPI_LENS_Z + dz * 0.5 + 0.008), (0.14, dy + 0.02, 0.016), body, uv_tile=0.5)
    mb.add_box((-dx * 0.5 - 0.006, 0.0, PAPI_LENS_Z), (0.012, dy * 0.7, dz * 0.7), fx.m(M_DARK), uv_tile=0.5)
    return fx.build()


def _mesh_guard_light(ctx, key: str, pads: bool):
    """L-804 elevated runway guard light: two 8 in amber PAR-56 lamps side by side under a hood (UFC §5-5)."""
    fx = _Fx(ctx, key)
    mb = fx.mb
    body = fx.m(M_BODY)
    amber = fx.m(M_YELLOW)
    _stem(fx, 0.0, 0.0, RGL_LENS_Z - 0.14, pads, stem_r=0.03)
    mb.add_box((0.0, 0.0, RGL_LENS_Z), (0.24, 0.66, 0.26), body, uv_tile=0.5)
    s12 = fx.seg(12)
    for sy in (-0.165, 0.165):
        _hcyl(mb, (0.05, sy, RGL_LENS_Z), (0.16, sy, RGL_LENS_Z), inch(4.0) + 0.008, s12, body, None, None)
        _hcyl(mb, (0.15, sy, RGL_LENS_Z), (0.165, sy, RGL_LENS_Z), inch(4.0), s12, body, None, amber)
    mb.add_box((0.06, 0.0, RGL_LENS_Z + 0.13 + 0.008), (0.36, 0.70, 0.016), body, uv_tile=0.5)   # visor
    return fx.build()


def _mesh_flood_mast(ctx, key: str):
    """Apron high-mast floodlight: 24 m tapered galvanized pole, 4 luminaires, concrete pier (UFC 3-530-01 §5-9.2)."""
    fx = _Fx(ctx, key)
    mb = fx.mb
    galv = fx.m(M_GALV)
    dark = fx.m(M_DARK)
    flood = fx.m(M_FLOOD)
    pad = fx.m(M_PAD)
    h = FLOOD_MAST_H
    mb.add_box((0.0, 0.0, 0.35), (1.3, 1.3, 0.70), pad, uv_tile=0.5)
    s12 = fx.seg(12)
    mb.add_cylinder((0.0, 0.0), 0.24, 0.70, h, s12, galv, radius_top=0.10, cap_top=False)
    mb.add_cylinder((0.0, 0.0), 0.16, h, h + 0.30, s12, dark, cap_top=True)          # lowering-device head
    # luminaire rack: two crossing arms + 4 heads aimed down/out
    arm_z = h - 0.45
    _hcyl(mb, (-1.15, 0.0, arm_z), (1.15, 0.0, arm_z), 0.035, fx.seg(8), galv, galv, galv)
    _hcyl(mb, (0.0, -1.15, arm_z), (0.0, 1.15, arm_z), 0.035, fx.seg(8), galv, galv, galv)
    for ang in (0.0, 90.0, 180.0, 270.0):
        a = math.radians(ang)
        cx, cy = 1.15 * math.cos(a), 1.15 * math.sin(a)
        mb.add_box((cx, cy, arm_z - 0.18), (0.62, 0.48, 0.20), dark, angle=a, uv_tile=0.5)
        # emissive lens on the underside
        c, s = math.cos(a), math.sin(a)
        hw, hd, z = 0.28, 0.21, arm_z - 0.283
        pts = [(cx + c * (-hw) - s * (-hd), cy + s * (-hw) + c * (-hd), z),
               (cx + c * (-hw) - s * hd, cy + s * (-hw) + c * hd, z),
               (cx + c * hw - s * hd, cy + s * hw + c * hd, z),
               (cx + c * hw - s * (-hd), cy + s * hw + c * (-hd), z)]
        mb.add_face(pts, flood, False, uvs=[(0, 0), (0, 1), (1, 1), (1, 0)])
    return fx.build()


def _mesh_obstruction(ctx, key: str):
    """L-810 steady red obstruction light: 4-6 in red globe on a short stem (UFC Ch. 6)."""
    fx = _Fx(ctx, key)
    mb = fx.mb
    alu = fx.m(M_ALU)
    red = fx.m(M_RED)
    mb.add_cylinder((0.0, 0.0), 0.05, 0.0, 0.03, fx.seg(8), alu, cap_top=True)
    mb.add_cylinder((0.0, 0.0), 0.014, 0.03, 0.16, fx.seg(8), alu, cap_top=False)
    mb.add_sphere((0.0, 0.0, 0.22), 0.06, fx.seg(12), fx.seg(6), red)
    return fx.build()


def _mesh_beacon_head(ctx, key: str):
    """L-802M rotating beacon head on a railed platform (tower built by the structures module)."""
    fx = _Fx(ctx, key)
    mb = fx.mb
    galv = fx.m(M_GALV)
    dark = fx.m(M_DARK)
    paint = fx.m(M_PAINT)
    mb.add_box((0.0, 0.0, 0.04), (2.4, 2.4, 0.08), galv, uv_tile=0.5)
    corners = [(-1.15, -1.15), (1.15, -1.15), (1.15, 1.15), (-1.15, 1.15)]
    s6 = fx.seg(6)
    for cx, cy in corners:
        mb.add_cylinder((cx, cy), 0.02, 0.08, 1.05, s6, galv, cap_top=True)
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        for z in (0.55, 1.05):
            _hcyl(mb, (a[0], a[1], z), (b[0], b[1], z), 0.015, 4, galv)
    mb.add_cylinder((0.0, 0.0), 0.16, 0.08, 0.50, fx.seg(10), dark, cap_top=True)
    mb.add_cylinder((0.0, 0.0), 0.45, 0.50, 1.25, fx.seg(16), paint, cap_top=True)
    zl = 0.875
    _hcyl(mb, (0.30, 0.0, zl), (0.50, 0.0, zl), 0.16, fx.seg(12), paint, None, fx.m(M_WHITE))
    _hcyl(mb, (-0.30, 0.0, zl), (-0.50, 0.0, zl), 0.16, fx.seg(12), paint, None, fx.m(M_GREEN))
    return fx.build()


# =========================================================================== pavement index
class _PavementIndex:
    """Point-in-pavement tests with bbox pre-filter (HAS loop rings handled as outer minus inner)."""

    def __init__(self, plan: P.Plan):
        self.items: list[tuple[tuple[float, float, float, float], list[Point], list[list[Point]]]] = []
        for poly, zone in plan.runway.zones:
            self._add(poly)
        for tw in plan.taxiways:
            for poly in tw.polys + tw.fillet_polys:
                self._add(poly)
        for ap in plan.aprons:
            if ap.loop_centerline is not None and ap.loop_width > 0:
                ring = g.polyline_strip(ap.loop_centerline, ap.loop_width, closed=True)
                self._add(ring[0], [ring[1]])
            else:
                self._add(ap.poly)
            for poly in ap.access_polys:
                self._add(poly)

    def _add(self, poly: Sequence[Point], holes: Sequence[Sequence[Point]] = ()) -> None:
        if len(poly) < 3:
            return
        self.items.append((g.bbox(poly), list(poly), [list(h) for h in holes]))

    def inside(self, pt: Point, margin: float = 0.0) -> bool:
        x, y = pt
        for (x0, y0, x1, y1), poly, holes in self.items:
            if x < x0 - margin or x > x1 + margin or y < y0 - margin or y > y1 + margin:
                continue
            if g.point_in_poly(pt, poly):
                if any(g.point_in_poly(pt, h) for h in holes):
                    continue
                return True
        return False


# =========================================================================== builder
class _Lighting:
    def __init__(self, ctx):
        self.ctx = ctx
        self.plan: P.Plan = ctx.plan
        self.rw: P.RunwayPlan = ctx.plan.runway
        self.s = ctx.settings.lighting
        self.pads = bool(self.s.fixture_detail) and ctx.detail >= 0.75
        self.pav = _PavementIndex(ctx.plan)
        self.counts: dict[str, int] = {}
        self.queue: list[tuple[str, object, tuple[float, float, float], float]] = []
        rw = self.rw
        ov_lo = rw.overrun_length if rw.overrun_low is not None else 0.0
        ov_hi = rw.overrun_length if rw.overrun_high is not None else 0.0
        self.runway_rect = g.rect_xy(-rw.half_len - ov_lo, -rw.paved_half_w, rw.half_len + ov_hi, rw.paved_half_w)

    # ------------------------------------------------------------------ meshes (cached by key)
    def mesh(self, key: str, factory: Callable[[str], object]):
        return self.ctx.shared_mesh("Lighting_" + key, lambda: factory("Lighting_" + key))

    def edge_elev(self, lens_a: str, lens_b: str):
        return self.mesh(f"EdgeElev_{_short(lens_a)}_{_short(lens_b)}",
                         lambda k: _mesh_edge_elevated(self.ctx, k, lens_a, lens_b, self.pads))

    def inpav(self, lens_a: str, lens_b: str):
        return self.mesh(f"Inpav_{_short(lens_a)}_{_short(lens_b)}", lambda k: _mesh_inpav(self.ctx, k, lens_a, lens_b))

    def inpav_row(self, n: int, pitch: float, lens: str):
        return self.mesh(f"InpavRow_{n}_{int(round(pitch * 100))}_{_short(lens)}",
                         lambda k: _mesh_inpav_row(self.ctx, k, n, pitch, lens))

    def barrette(self, n: int, pitch: float, height: float, lens: str, lamp_r: float):
        return self.mesh(f"Barrette_{n}_{int(round(pitch * 100))}_{int(round(height * 100))}_{_short(lens)}_{int(round(lamp_r * 1000))}",
                         lambda k: _mesh_barrette(self.ctx, k, n, pitch, height, lens, lamp_r, self.pads))

    def taxi_edge(self):
        return self.mesh("TaxiEdge", lambda k: _mesh_taxi_edge(self.ctx, k, self.pads))

    def flasher(self):
        return self.mesh("Flasher", lambda k: _mesh_flasher(self.ctx, k, self.pads))

    def papi_box(self):
        return self.mesh("PAPI", lambda k: _mesh_papi(self.ctx, k, self.pads))

    def guard(self):
        return self.mesh("GuardLight", lambda k: _mesh_guard_light(self.ctx, k, self.pads))

    def flood_mast(self):
        return self.mesh("FloodMast", lambda k: _mesh_flood_mast(self.ctx, k))

    def obstruction(self):
        return self.mesh("Obstruction", lambda k: _mesh_obstruction(self.ctx, k))

    def beacon_head(self):
        return self.mesh("BeaconHead", lambda k: _mesh_beacon_head(self.ctx, k))

    # ------------------------------------------------------------------ placement queue
    def place(self, kind: str, mesh, x: float, y: float, z: float, rot: float = 0.0) -> None:
        self.queue.append((kind, mesh, (x, y, z), rot))

    def flush(self) -> None:
        ctx = self.ctx
        counts = self.counts
        for kind, mesh, loc, rot in self.queue:
            n = counts.get(kind, 0) + 1
            counts[kind] = n
            ctx.add_instance(f"Lighting_{kind}_{n:04d}", mesh, CAT, loc, rot)
        self.queue.clear()

    def z(self, x: float, y: float) -> float:
        return self.rw.crown(x, y)

    # ------------------------------------------------------------------ runway ends
    def ends(self) -> list[dict]:
        rw = self.rw
        s = self.s
        return [
            dict(name='low', thr=rw.thr_low, pav_end=-rw.half_len, dir=-1, displaced=rw.displaced_low, ils=rw.ils_low,
                 als=s.als_low, overrun=rw.overrun_low is not None),
            dict(name='high', thr=rw.thr_high, pav_end=rw.half_len, dir=1, displaced=rw.displaced_high, ils=rw.ils_high,
                 als=s.als_high, overrun=rw.overrun_high is not None),
        ]

    # ------------------------------------------------------------------ 1. runway edge lights (UFC §4-2)
    def _caution(self, x: float, facing: int) -> str:
        """Lens colour for a bidirectional edge light. ``facing`` = +1 for the +X lens.

        The +X lens beams toward +X and is seen by an aircraft rolling toward the LOW end,
        whose remaining distance is x - thr_low; the -X lens is seen by traffic rolling toward
        the HIGH end (remaining thr_high - x). Last 2,000 ft amber (UFC §4-2.3)."""
        rw = self.rw
        remaining = (x - rw.thr_low) if facing > 0 else (rw.thr_high - x)
        return M_YELLOW if remaining <= LT.caution_zone + 1e-6 else M_WHITE

    def _fillet_openings(self) -> dict[int, list[tuple[float, float]]]:
        out: dict[int, list[tuple[float, float]]] = {1: [], -1: []}
        for tw in self.plan.taxiways:
            if tw.runway_tangents:
                xs = sorted(p[0] for p in tw.runway_tangents)
                out[tw.side].append((xs[0] - 1.0, xs[-1] + 1.0))
        return out

    def runway_edge(self) -> None:
        rw = self.rw
        ye = rw.half_w + LT.edge_offset
        L = rw.thr_high - rw.thr_low
        step_max = min(max(self.s.edge_spacing, ft(25)), LT.edge_spacing)
        n = max(1, int(math.ceil(L / step_max - 1e-6)))
        step = L / n                                             # equal spaces, symmetric from each threshold
        openings = self._fillet_openings()

        def emit(x: float, lens_a: str, lens_b: str) -> None:
            for side in (1, -1):
                y = side * ye
                if any(a <= x <= b for a, b in openings[side]):
                    # fillet opening: in-pavement L-850C edge light in the taxiway fillet (UFC §4-2.4)
                    self.place('EdgeInpav', self.inpav(lens_a, lens_b), x, y, self.z(x, y), 0.0)
                else:
                    self.place('Edge', self.edge_elev(lens_a, lens_b), x, y, self.z(x, y), 0.0)

        for i in range(1, n):
            x = rw.thr_low + i * step
            emit(x, self._caution(x, 1), self._caution(x, -1))
        # displaced threshold areas: red toward the approach, white toward the runway (30J §3.3)
        if rw.displaced_low > 0.5:
            k = 1
            while rw.thr_low - k * step > -rw.half_len + 1.0:
                emit(rw.thr_low - k * step, M_WHITE, M_RED)
                k += 1
        if rw.displaced_high > 0.5:
            k = 1
            while rw.thr_high + k * step < rw.half_len - 1.0:
                emit(rw.thr_high + k * step, M_RED, M_WHITE)
                k += 1

    # ------------------------------------------------------------------ 2. threshold & end lights (UFC §4-4, §4-6)
    def threshold_end(self) -> None:
        rw = self.rw
        ye = rw.half_w + LT.edge_offset
        for e in self.ends():
            a = e['dir']                                    # approach lies toward a * +X
            x_thr = e['thr'] + a * THR_LINE_OFFSET           # 5 ft outside the landing surface
            # inboard green line between the edge-light lines, spacing as near 5 ft as possible (<= 5 ft 2 in)
            n_sp = max(2, int(math.ceil(2 * ye / LT.end_spacing - 1e-9)))
            sp = 2 * ye / n_sp
            displaced = e['displaced'] > 0.5
            for i in range(n_sp + 1):
                y = -ye + i * sp
                from_edge = min(i, n_sp - i)
                red = (not displaced) and from_edge < LT.end_count_per_side   # two groups of 5 red end lights
                toward_rw = M_RED if red else None
                self._bidir_fixture(x_thr, y, toward_approach=M_GREEN, toward_runway=toward_rw, approach_dir=a, kind='Threshold')
            # wing bars: 8 green lights at 5 ft, 40 ft outboard each side
            for side in (1, -1):
                for k in range(1, LT.threshold_wingbar + 1):
                    y = side * (ye + k * THR_WING_SPACING)
                    self._bidir_fixture(x_thr, y, toward_approach=M_GREEN, toward_runway=None, approach_dir=a, kind='Threshold')
            if displaced:
                # red runway end lights at the pavement end, 2 groups of 5 at 5 ft
                x_end = e['pav_end'] + a * THR_LINE_OFFSET
                for side in (1, -1):
                    for k in range(LT.end_count_per_side):
                        y = side * (ye - k * LT.end_spacing)
                        self._bidir_fixture(x_end, y, toward_approach=None, toward_runway=M_RED, approach_dir=a, kind='RunwayEnd')

    def _bidir_fixture(self, x: float, y: float, toward_approach: str | None, toward_runway: str | None,
                       approach_dir: int, kind: str) -> None:
        """Threshold-line fixture: in-pavement when on runway/overrun pavement, otherwise elevated L-862E."""
        on_pav = self.pav.inside((x, y))
        if on_pav:
            ta = toward_approach or BLANK_INPAV
            tr = toward_runway or BLANK_INPAV
            lens_a, lens_b = (ta, tr) if approach_dir > 0 else (tr, ta)
            self.place(kind + 'Inpav', self.inpav(lens_a, lens_b), x, y, self.z(x, y), 0.0)
        else:
            ta = toward_approach or BLANK_ELEV
            tr = toward_runway or BLANK_ELEV
            lens_a, lens_b = (ta, tr) if approach_dir > 0 else (tr, ta)
            self.place(kind, self.edge_elev(lens_a, lens_b), x, y, self.z(x, y), 0.0)

    # ------------------------------------------------------------------ 3. runway centreline lights (UFC §4-7)
    def _rcl_colour(self, remaining: float) -> str:
        if remaining <= LT.centerline_red_zone + 1e-6:
            return M_RED
        if remaining <= LT.centerline_alt_zone + 1e-6:
            k = int(round((LT.centerline_alt_zone - remaining) / LT.centerline_spacing))
            return M_RED if k % 2 == 0 else M_WHITE      # alternating, starting with red at the 3,000 ft mark
        return M_WHITE

    def centerline(self) -> None:
        rw = self.rw
        start = ft(75)
        y = LT.centerline_offset                        # 2 ft left of the painted stripe
        x = rw.thr_low + start
        x_end = rw.thr_high - start + 1e-6
        while x <= x_end:
            if all(abs(x - xc) > ARRESTING_CLEAR for xc in rw.arresting):
                lens_a = self._rcl_colour(x - rw.thr_low)      # +X lens: seen rolling toward the low end
                lens_b = self._rcl_colour(rw.thr_high - x)     # -X lens: seen rolling toward the high end
                self.place('Centerline', self.inpav(lens_a, lens_b), x, y, self.z(x, y), 0.0)
            x += LT.centerline_spacing

    # ------------------------------------------------------------------ 4. touchdown zone lights (UFC §4-8, 30J Fig A-35)
    def tdz(self) -> None:
        rw = self.rw
        L = rw.thr_high - rw.thr_low
        for e in self.ends():
            if not e['ils']:
                continue
            a = e['dir']
            rot = 0.0 if a > 0 else math.pi
            max_s = min(LT.tdz_length, L * 0.5 - ft(50))
            s = LT.tdz_spacing
            mesh = self.inpav_row(3, LT.tdz_barrette_spacing, M_WHITE)
            while s <= max_s + 1e-6:
                x = e['thr'] - a * s
                if all(abs(x - xc) > ARRESTING_CLEAR for xc in rw.arresting):
                    for side in (1, -1):
                        yc = side * (LT.tdz_inner + LT.tdz_barrette_spacing)      # barrette centre 36..46 ft
                        self.place('TDZ', mesh, x, yc, self.z(x, yc), rot)
                s += LT.tdz_spacing

    # ------------------------------------------------------------------ 5. PAPI (UFC §3-7, 30J Fig A-80)
    def papi(self) -> None:
        rw = self.rw
        mesh = self.papi_box()
        d = self.s.papi_distance
        for e in self.ends():
            a = e['dir']
            # "left" as seen by the approaching pilot: +Y for the low end, -Y for the high end
            left = -a
            side = left if self.s.papi_side == 'LEFT' else -left
            x = e['thr'] - a * d
            rot = 0.0 if a > 0 else math.pi
            for k in range(4):
                y = side * (rw.half_w + LT.papi_offset_edge + k * ft(25))
                self.place('PAPI', mesh, x, y, 0.0, rot)

    # ------------------------------------------------------------------ 6. approach lighting (UFC Ch. 3)
    def approach(self) -> None:
        for e in self.ends():
            sysname = e['als']
            if sysname not in ALS_SYSTEMS:
                if self.s.reil:
                    self._reil(e)
                continue
            spec = ALS_SYSTEMS[sysname]
            a = e['dir']
            thr = e['thr']
            rot = 0.0 if a > 0 else math.pi

            def X(s_ft: float) -> float:
                return thr + a * ft(s_ft)

            if spec.get('odals'):
                # ODALS: 5 omnidirectional flashers on the extended CL at 300 ft, 2 abeam the threshold (30J §3.5)
                for s_ in range(300, 1501, 300):
                    self._flasher_at(X(s_), 0.0, rot, 'ODALS')
                self._reil(e, kind='ODALS')
                continue
            lamp_r = PAR38_R if spec.get('lamp') == 'PAR38' else PAR56_R
            b0, b1, bs = spec['barrettes']
            for s_ in range(b0, b1 + 1, bs):
                self._row_at(X(s_), 0.0, rot, BARRETTE_LIGHTS, BARRETTE_PITCH, M_WHITE, lamp_r, 'ALS')
                if s_ == 1000:
                    n_bar = spec['bar1000']
                    # 8 lights: outermost 50 ft (15..50 ft); 5 lights (MALSR/SSALR): 13.5..33.5 ft
                    centre = ft(32.5) if n_bar == 8 else ft(23.5)
                    for side in (1, -1):
                        self._row_at(X(1000), side * centre, rot, n_bar, ft(5), M_WHITE, lamp_r, 'ALS')
                if spec.get('bar500') and s_ == 500:
                    for side in (1, -1):                       # 4 white at 13.9..28.9 ft
                        self._row_at(X(500), side * ft(21.4), rot, 4, ft(5), M_WHITE, lamp_r, 'ALS')
            if spec.get('side_rows'):
                r0, r1, rs = spec['side_rows']
                for s_ in range(r0, r1 + 1, rs):               # ALSF-2 red side rows at 36/41/46 ft
                    for side in (1, -1):
                        self._row_at(X(s_), side * ft(41), rot, 3, ft(5), M_RED, PAR56_R, 'ALS')
            if spec.get('terminating'):                        # ALSF-1 terminating bar: red at 15/20/25 ft, station 2+00
                for side in (1, -1):
                    self._row_at(X(200), side * ft(20), rot, 3, ft(5), M_RED, PAR56_R, 'ALS')
            if spec.get('prethreshold'):                       # ALSF-1 pre-threshold bar: 5 red at 3.5 ft, 75..89 ft, station 1+00
                for side in (1, -1):
                    self._row_at(X(100), side * ft(82), rot, 5, ft(3.5), M_RED, PAR56_R, 'ALS')
            f0, f1, fs = spec['flashers']
            for s_ in range(f0, f1 + 1, fs):
                self._flasher_at(X(s_), 0.0, rot, 'Flasher')

    def _row_at(self, x: float, y: float, rot: float, n: int, pitch: float, lens: str, lamp_r: float, kind: str) -> None:
        if self.pav.inside((x, y)):
            self.place(kind + 'Inpav', self.inpav_row(n, pitch, lens), x, y, self.z(x, y), rot)
        else:
            self.place(kind, self.barrette(n, pitch, ALS_STEM_H, lens, lamp_r), x, y, 0.0, rot)

    def _flasher_at(self, x: float, y: float, rot: float, kind: str) -> None:
        if self.pav.inside((x, y)):
            lens_a, lens_b = (M_FLASH, BLANK_INPAV) if abs(rot) < 1e-6 else (BLANK_INPAV, M_FLASH)
            self.place(kind + 'Inpav', self.inpav(lens_a, lens_b), x, y, self.z(x, y), 0.0)
        else:
            self.place(kind, self.flasher(), x, y, 0.0, rot)

    def _reil(self, e: dict, kind: str = 'REIL') -> None:
        """Two flash heads in line with the threshold lights, 40 ft outboard of the edge lights, toed out 15 deg (UFC §3-6)."""
        rw = self.rw
        a = e['dir']
        x = e['thr'] + a * THR_LINE_OFFSET
        ye = rw.half_w + LT.edge_offset + REIL_LATERAL
        for side in (1, -1):
            dx, dy = a * math.cos(REIL_TOE_OUT), side * math.sin(REIL_TOE_OUT)
            self.place(kind, self.flasher(), x, side * ye, 0.0, math.atan2(dy, dx))

    # ------------------------------------------------------------------ 7. taxiway lighting (UFC Ch. 5)
    def taxiways(self) -> None:
        s = self.s
        rw = self.rw
        if s.taxiway_edge:
            pts: list[Point] = []
            for tw in self.plan.taxiways:
                if not tw.edge_lights:
                    continue
                hw = tw.width * 0.5 + LT.taxi_edge_offset
                for sign in (1.0, -1.0):
                    edge = g.offset_polyline(tw.centerline, sign * hw)
                    pts.extend(_edge_points(edge, False, s.taxiway_spacing, LT.taxi_edge_spacing_curve))
                # fillet arcs: uniform spacing <= 1/2 taxiway width, light at each PT (UFC §5-2.4)
                for fil in tw.fillet_polys:
                    pts.extend(_fillet_points(fil, LT.taxi_edge_offset, min(tw.width * 0.5, LT.taxi_edge_spacing_curve)))
                # runway entrance/exit pair at each fillet PT on the runway edge
                if tw.runway_tangents:
                    for t in tw.runway_tangents:
                        yb = tw.side * (rw.half_w + TAXI_PT_OFFSET)
                        pts.append((t[0], yb))
                        pts.append((t[0], yb + tw.side * TAXI_PT_PAIR))
            for ap in self.plan.aprons:
                if ap.loop_centerline is not None and ap.loop_width > 0:
                    hw = ap.loop_width * 0.5 + LT.taxi_edge_offset
                    for sign in (1.0, -1.0):
                        edge = g.offset_polyline(ap.loop_centerline, sign * hw, closed=True)
                        pts.extend(_edge_points(edge, True, s.taxiway_spacing, LT.taxi_edge_spacing_curve))
            pts = g.dedupe_points(pts, tol=1.5)
            mesh = self.taxi_edge()
            near_rw = rw.half_w + TAXI_PT_OFFSET - 0.1
            for p in pts:
                # never on pavement; never inside the runway strip except the explicit PT pair on the shoulder
                if g.point_in_poly(p, self.runway_rect) and abs(p[1]) < near_rw:
                    continue
                if self.pav.inside(p):
                    continue
                self.place('TaxiEdge', mesh, p[0], p[1], self.z(p[0], p[1]), 0.0)
        # green centreline lights (in-pavement L-852), alternating green/yellow between the hold line and the runway
        for tw in self.plan.taxiways:
            if not (tw.centerline_lights or s.taxiway_centerline):
                continue
            self._taxiway_centerline(tw)

    def _taxiway_centerline(self, tw: P.TaxiwayPlan) -> None:
        rw = self.rw
        cl = tw.centerline
        pts = g.resample_polyline(cl, LT.taxi_cl_spacing)
        hold_a = next((h for h in tw.hold_lines if h.kind == 'A'), None)
        hold_dist = _arc_length_at(cl, hold_a.center) if hold_a else -1.0
        gg = self.inpav(M_GREEN, M_GREEN)
        yy = self.inpav(M_YELLOW, M_YELLOW)
        acc = 0.0
        idx = 0
        for i, p in enumerate(pts):
            if i > 0:
                acc += g.length(pts[i - 1], p)
            if g.point_in_poly(p, self.runway_rect):
                continue                           # lead-off lights on the runway itself are not generated
            if not self.pav.inside(p, margin=0.5):
                continue                           # only where there is taxiway pavement
            mesh = gg
            if hold_a and acc < hold_dist:         # between runway and hold line: alternating green / yellow
                mesh = yy if idx % 2 else gg
                idx += 1
            self.place('TaxiCL', mesh, p[0], p[1], self.z(p[0], p[1]), 0.0)

    # ------------------------------------------------------------------ 8. hold positions: RGL + stop bars (UFC §5-5, §5-6)
    def hold_positions(self) -> None:
        s = self.s
        for tw in self.plan.taxiways:
            for h in tw.hold_lines:
                if h.kind != 'A':
                    continue
                d = g.normalize(h.direction)             # toward the runway
                n = g.perp_left(d)
                rot = math.atan2(-d[1], -d[0])           # lenses face the aircraft approaching from the taxiway
                if s.guard_lights:
                    off = tw.width * 0.5 + RGL_EDGE_OFFSET
                    for sign in (1.0, -1.0):
                        x, y = h.center[0] + n[0] * off * sign, h.center[1] + n[1] * off * sign
                        self.place('GuardLight', self.guard(), x, y, self.z(x, y), rot)
                if s.stop_bars:
                    cx, cy = h.center[0] + d[0] * STOP_BAR_SETBACK, h.center[1] + d[1] * STOP_BAR_SETBACK
                    k = int((tw.width * 0.5 - ft(2) - INPAV_R) // STOP_BAR_PITCH)
                    n_l = 2 * k + 1
                    self.place('StopBar', self.inpav_row(n_l, STOP_BAR_PITCH, M_RED), cx, cy, self.z(cx, cy), rot)
                    off = tw.width * 0.5 + STOP_BAR_ELEV_OFFSET
                    for sign in (1.0, -1.0):
                        x, y = cx + n[0] * off * sign, cy + n[1] * off * sign
                        self.place('StopBarElev', self.edge_elev(M_RED, BLANK_ELEV), x, y, self.z(x, y), rot)

    # ------------------------------------------------------------------ 9. aprons: high-mast floodlights (+ obstruction lights)
    def aprons(self) -> None:
        s = self.s
        if not s.apron_floods:
            return
        mast = self.flood_mast()
        obs = self.obstruction() if s.obstruction else None
        for ap in self.plan.aprons:
            if not ap.floodlights:
                continue
            cx, cy = g.centroid(ap.poly)
            for p in ap.floodlights:
                rot = math.atan2(cy - p[1], cx - p[0])
                self.place('FloodMast', mast, p[0], p[1], 0.0, rot)
                if obs is not None:
                    self.place('Obstruction', obs, p[0], p[1], FLOOD_MAST_H + 0.30, 0.0)

    # ------------------------------------------------------------------ 10. rotating beacon head (UFC §10-1)
    def beacon(self) -> None:
        if not self.s.beacon:
            return
        for st in self.plan.structures:
            if st.kind == 'BEACON':
                self.place('BeaconHead', self.beacon_head(), st.position[0], st.position[1], st.size[2], st.rotation)


# =========================================================================== 2D helpers
def _short(lens: str) -> str:
    return lens[5:] if lens.startswith('Light') else ('Blank' if lens in (BLANK_INPAV, BLANK_ELEV) else lens)


def _arc_length_at(cl: Sequence[Point], pt: Point) -> float:
    """Arc length along a polyline at the point nearest to ``pt``."""
    best, best_d, acc = 0.0, float('inf'), 0.0
    for i in range(len(cl) - 1):
        a, b = cl[i], cl[i + 1]
        seg = g.length(a, b)
        if seg < 1e-9:
            continue
        t = max(0.0, min(1.0, ((pt[0] - a[0]) * (b[0] - a[0]) + (pt[1] - a[1]) * (b[1] - a[1])) / (seg * seg)))
        q = g.lerp(a, b, t)
        d = g.length(q, pt)
        if d < best_d:
            best_d, best = d, acc + seg * t
        acc += seg
    return best


def _edge_points(edge: Sequence[Point], closed: bool, spacing_straight: float, spacing_curve: float) -> list[Point]:
    """Light positions along a taxiway edge-light line (UFC §5-2.2 / 30J §2.5).

    Straights > 400 ft: uniform <= ``spacing_straight`` (<= 200 ft) with an extra light 40 ft from each
    end when the spacing exceeds 100 ft; straights <= 400 ft: uniform <= 100 ft; curves: <= ``spacing_curve``.
    """
    pts = list(edge)
    if closed and len(pts) > 1:
        pts.append(pts[0])
    if len(pts) < 2:
        return []
    segs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    segs = [(a, b) for a, b in segs if g.length(a, b) > 1e-6]
    if not segs:
        return []
    dirs = [g.normalize((b[0] - a[0], b[1] - a[1])) for a, b in segs]
    lens = [g.length(a, b) for a, b in segs]
    m = len(segs)

    def turn(i: int, j: int) -> float:
        if i < 0 or j >= m:
            if not closed:
                return 0.0
            i %= m
            j %= m
        c = max(-1.0, min(1.0, g.dot(dirs[i], dirs[j])))
        return math.acos(c)

    curved = [lens[i] < 20.0 and (turn(i - 1, i) > math.radians(0.5) or turn(i, i + 1) > math.radians(0.5)) for i in range(m)]
    runs: list[list[int]] = [[0]]
    for i in range(1, m):
        if curved[i] == curved[i - 1]:
            runs[-1].append(i)
        else:
            runs.append([i])
    out: list[Point] = []
    for run in runs:
        pl = [segs[run[0]][0]] + [segs[i][1] for i in run]
        Lr = g.polyline_length(pl)
        if Lr < 1e-6:
            continue
        if curved[run[0]]:
            step_max = spacing_curve
        else:
            step_max = spacing_straight if Lr > ft(400) else min(spacing_straight, ft(100))
        n = max(1, int(math.ceil(Lr / step_max - 1e-9)))
        out.extend(g.resample_polyline(pl, Lr / n))
        if not curved[run[0]] and Lr > ft(400) and Lr / n > ft(100):
            out.append(g.point_along(pl, ft(40))[0])
            out.append(g.point_along(pl, Lr - ft(40))[0])
    return out


def _fillet_points(fillet: Sequence[Point], offset: float, spacing: float) -> list[Point]:
    """Edge lights along a fillet arc: the arc offset ``offset`` outward (toward the arc centre), uniform spacing.

    ``fillet`` is ``[corner] + arc`` as produced by ``geom2d.fillet_corner`` (any orientation).
    """
    if len(fillet) < 5:
        return []
    pts = list(fillet)
    # the corner is the vertex farthest from the centroid of the others (arc points lie on a circle)
    arc = None
    for k in range(len(pts)):
        cand = pts[k + 1:] + pts[:k]
        c = _circumcentre(cand[0], cand[len(cand) // 2], cand[-1])
        if c is None:
            continue
        r = g.length(c, cand[0])
        if all(abs(g.length(c, p) - r) < 0.02 * r + 0.01 for p in cand):
            arc = cand
            centre = c
            radius = r
            break
    if arc is None:
        return []
    if radius <= offset + 0.05:
        return []
    Lr = g.polyline_length(arc)
    n = max(2, int(math.ceil(Lr / spacing - 1e-9)))
    samples = g.resample_polyline(arc, Lr / n)
    k = (radius - offset) / radius
    return [(centre[0] + (p[0] - centre[0]) * k, centre[1] + (p[1] - centre[1]) * k) for p in samples]


def _circumcentre(a: Point, b: Point, c: Point) -> Point | None:
    d = 2.0 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-9:
        return None
    a2, b2, c2 = a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2
    ux = (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d
    uy = (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d
    return (ux, uy)


# =========================================================================== entry point
def build(ctx) -> None:
    s = ctx.settings.lighting
    L = _Lighting(ctx)
    if s.edge != 'NONE':
        L.runway_edge()
    if s.threshold:
        L.threshold_end()
    if s.centerline:
        L.centerline()
    if s.tdz:
        L.tdz()
    if s.papi:
        L.papi()
    L.approach()
    L.taxiways()
    L.hold_positions()
    L.aprons()
    L.beacon()
    L.flush()
