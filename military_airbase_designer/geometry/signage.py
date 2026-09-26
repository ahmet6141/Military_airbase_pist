"""Airfield signage and miscellaneous visual aids (UFC 3-535-01 Ch. 9-10; FAA AC 150/5345-44L,
AC 150/5340-18H; FAA AC 150/5220-9B; FAA AC 150/5345-27 wind cones).

What is built (category ``Signage``, one object per sign *array*, meshes shared between identical arrays):

* **Runway holding position arrays** at every Pattern-A hold line: ``[location "A2" | mandatory "05-23"]``
  on the left of the taxiway (as seen approaching the runway), abeam the hold line, near edge
  ``settings.signage.sign_offset`` beyond the paved shoulder. Back faces: location + a direction /
  destination legend for aircraft leaving the runway (UFC 9-3, AC 18H Ch. 2).
* **ILS critical-area arrays** ("ILS", red/white) at Pattern-B hold lines, ILS boundary pictogram on the back.
* **Runway exit signs** ("← A2" / "A5 →", 45° arrows on high-speed exits) on the runway side, on the exit
  side, 45 ft from the runway edge, 120 m before the junction for each landing direction served.
* **Taxiway intersection arrays** on the far side of the parallel taxiway at every connector:
  ``[← A1 | A | A4 →]`` (direction to the neighbouring connectors, location of the parallel).
* **Destination signs** on apron access stubs ("APRON ↑", back "RWY 05-23 →").
* **Runway distance remaining signs** (L-858B, Size 4, white numeral on black, double faced) both sides at
  1,000 ft intervals from each runway end, near edge 60 ft from the full-strength edge (UFC 9-6).
* **Arresting gear markers** (Size 4, 1 m yellow disc on black, double faced) at every pendant cable
  station in the RDR row (UFC 9-7); an RDR sign within 20 ft moves in line and 5 ft outboard.
* **Wind cones** (L-807 style: 6 m aluminium pole, 3.6 m orange cone, L-810 red obstruction light) at
  ``plan.wind_cones`` and a segmented circle (FAA AC 150/5340-5) around the primary (mid-field) cone.

Sign geometry (AC 150/5345-44L §3): internally lit box housing of the size class in ``core.constants.SIGN_SIZES``
(legend height, panel height, top of panel above grade), 0.25 m deep, recessed faces with a 25 mm frame rim,
legends as flat meshes 3 mm in front of the face, two legs on frangible couplings (≤ 3 in above the pad) and a
continuous concrete pad (0.6 m x (array length + 0.3 m) x 0.1 m).

Local frame of an array mesh: panels run along local **+Y**, the **front face normal is local +X**; an
instance is rotated so +X points toward the aircraft that must read the front face. Reading direction on the
front face is local +Y (viewer's left -> right), on the back face local -Y, so panel order in a list is the
front-face reading order.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Callable, Sequence

import bpy
from mathutils import Matrix, Vector

from ..core import geom2d as g
from ..core import scene_utils as su
from ..core.constants import LT, MARKING_Z, SIGN_SIZES
from ..core.meshbuild import MeshBuilder
from ..core.units import FT, ft, inch
from ..layout import plan as P

Point = g.Point
CAT = "Signage"

# --------------------------------------------------------------------------- materials (materials/library.py SPECS)
M_FRAME, M_LEG, M_PAD = 'SignFrame', 'FixtureAluminum', 'ConcreteBase'
M_POLE, M_CONE, M_OBS, M_PAINT, M_DARK = 'FixtureAluminum', 'WindConeOrange', 'LightRed', 'PaintWhite', 'MetalDark'

# face kind -> (background material, legend material)   AC 150/5345-44L §1.2.1 / UFC 9-2
FACE_STYLE: dict[str, tuple[str, str | None]] = {
    'MANDATORY': ('SignRed', 'SignWhite'),        # L-858R runway holding position / ILS
    'LOCATION': ('SignBlack', 'SignYellow'),      # L-858L (+ yellow border)
    'DIRECTION': ('SignYellow', 'SignBlack'),     # L-858Y direction
    'DESTINATION': ('SignYellow', 'SignBlack'),   # L-858Y destination
    'BOUNDARY': ('SignYellow', 'SignBlack'),      # L-858Y ILS critical area boundary pictogram
    'RDR': ('SignBlack', 'SignWhite'),            # L-858B runway distance remaining
    'AGM': ('SignBlack', 'AGMYellow'),            # arresting gear marker disc
}

# --------------------------------------------------------------------------- housing geometry (AC 44L §3.2.5, mfr data)
PANEL_DEPTH = 0.25            # housing depth (8-12 in)
RIM = 0.025                   # frame rim around the face
RECESS = 0.008                # face recessed behind the rim
LEGEND_LIFT = 0.003           # legend 3 mm in front of the face
BORDER_LIFT = 0.002           # location-sign border 2 mm in front of the face
HOUSING_GAP = 0.10            # gap between housings in an array (3-12 in)
TOKEN_GAP = 0.25              # gap between message elements, x legend height
SIDE_MARGIN = 0.5             # legend -> panel end margin, x legend height
MIN_LENGTH_RATIO = 1.4        # minimum panel length = 1.4 x panel height (Size 3: 42 in for a 30 in panel)
LEG_R = 0.03                  # 2.5 in leg tube
LEG_INSET = 0.30              # legs 0.3 m in from the panel ends
COUPLING_R = 0.045
COUPLING_H = inch(3)          # frangible coupling breaks <= 3 in above the pad
PAD_W = 0.60                  # concrete pad 0.6 m deep, array length + 0.3 m long, 0.1 m thick
PAD_TOP = 0.08                # 8 cm of the 10 cm pad shows above grade
PAD_H = 0.10
# L-858L border width and black margin per size (AC 44L §3.2.5: 13/16, 1-1/16, 1-1/4 in / 11/16, 1-7/16, 2 in)
BORDER = {1: (inch(13 / 16), inch(11 / 16)), 2: (inch(1 + 1 / 16), inch(1 + 7 / 16)),
          3: (inch(1.25), inch(2)), 4: (inch(1.25), inch(2)), 5: (inch(1.25), inch(2))}
ILS_SYMBOL_H = 15 / 18        # ILS pictogram height 15 in for an 18 in legend (AC 44L App. B)
ILS_SYMBOL_L = 42 / 18        # ... length 42 in

# --------------------------------------------------------------------------- placement (UFC Ch. 9)
HOLD_SIGN_SETBACK = 1.5       # sign face 0-10 ft on the holding side of the hold line (UFC 9-3): use 5 ft
EXIT_SIGN_OFFSET = ft(45)     # runway exit sign near edge 35-60 ft from the runway edge (Size 3)
EXIT_SIGN_LEAD = 120.0        # 100-150 m before the exit junction
RDR_OFFSET = ft(60)           # RDR / AGM row: near edge 50-75 ft from the full-strength edge (UFC 9-6 / 9-7)
RDR_SIZE = 4                  # military RDR and AGM: FAA Size 4 only
RDR_NUMERAL_H = inch(40)      # 40 in numeral on the 48 in Size 4 panel (UFC 9-6; AC 44L Table 3-1)
RDR_INTERVAL_FT = 1000.0
RDR_TOLERANCE = ft(50)        # a station may shift +-50 ft to clear an obstacle, else it is omitted
AGM_DISC_D = 1.0              # ~39 in yellow translucent disc
AGM_RDR_CLEAR = ft(20)        # RDR within 20 ft of an AGM moves in line with it ...
AGM_RDR_SHIFT = ft(5)         # ... and 5 ft outboard
SIGN_CLEARANCE = 3.0          # a sign footprint keeps >= 10 ft from any other pavement
# wind cone (L-807, AC 150/5345-27; UFC 10-2)
WIND_POLE_H = 6.0
WIND_POLE_R = 0.06
WIND_CONE_L = 3.6             # 12 ft cone
WIND_THROAT_D = 0.9           # 36 in throat
WIND_TIP_D = 0.3
WIND_TILT = math.radians(20)  # inflated cone droops 20 deg from horizontal, pointing +X (wind from -X)
SEG_CIRCLE_D = 30.0           # segmented circle 100 ft diameter (AC 150/5340-5)
SEG_COUNT = 8
SEG_LEN = 3.0
SEG_W = 0.9

# destination legend by apron kind (FAA destination signs name the destination: APRON, MIL, FUEL, CARGO...)
APRON_DEST = {'MAIN': 'APRON', 'HANGAR_LINE': 'HANGARS', 'HAS_LOOP': 'HAS', 'ALERT': 'ALERT', 'HOT_CARGO': 'HOT CARGO',
              'ARM_DEARM': 'EOR', 'TRIM_PAD': 'TRIM PAD', 'HELIPAD': 'HELIPAD', 'COMPASS': 'COMPASS'}

SIGN_CATALOG = (
    ('HoldArray', "runway holding position array: location (yellow on black) + mandatory runway designators (white on red); back: location + direction/destination"),
    ('ILSArray', "ILS critical area holding position sign (white on red), ILS boundary pictogram on the back"),
    ('ExitSign', "runway exit direction sign (black on yellow, arrow toward the exit; 45 deg arrow on high-speed exits)"),
    ('LocationArray', "taxiway intersection array on the far side of the parallel taxiway: direction | location | direction"),
    ('DestinationSign', "destination sign on apron access taxiways (APRON / HANGARS / HAS ... with arrow; back: RWY xx-yy with arrow)"),
    ('RDR', "runway distance remaining sign, Size 4, white numeral on black, double faced"),
    ('AGM', "arresting gear marker, Size 4, 1 m yellow disc on black, double faced"),
    ('WindCone', "L-807 wind cone: 6 m pole, 3.6 m orange cone, red obstruction light"),
    ('SegmentedCircle', "segmented circle (8 white segments, 30 m dia) around the primary wind cone"),
)


def sign_catalog() -> list[str]:
    """Sign kinds this module generates (object names are ``MAD_Signage_<Kind>_<n>``)."""
    return [k for k, _ in SIGN_CATALOG]


def sign_descriptions() -> dict[str, str]:
    return dict(SIGN_CATALOG)


# =========================================================================== data
Token = tuple      # ('T', text) | ('A', angle_deg: 0 right, 90 up, 180 left, 45 up-right, 135 up-left) | ('DISC',) | ('ILS',)


@dataclass(frozen=True)
class Face:
    kind: str
    tokens: tuple

    def key(self) -> str:
        return self.kind + ":" + "|".join("".join(str(v) for v in t) for t in self.tokens)


@dataclass(frozen=True)
class Panel:
    front: Face | None
    back: Face | None = None
    size: int = 3
    square: bool = False

    def key(self) -> str:
        return f"{self.size}{'Q' if self.square else ''}[{self.front.key() if self.front else '-'}/{self.back.key() if self.back else '-'}]"


def _arrow(angle_deg: float, h: float) -> list[Point]:
    """FAA-style bold arrow (shaft + wide chevron head), overall length 1.0 h, head height 1.0 h."""
    pts = [(-0.5, -0.11), (0.05, -0.11), (0.05, -0.5), (0.5, 0.0), (0.05, 0.5), (0.05, 0.11), (-0.5, 0.11)]
    a = math.radians(angle_deg)
    return g.ensure_ccw([g.rotate_point((x * h, y * h), a) for x, y in pts])


class _Glyphs:
    """Text -> flat triangle soups (Blender default font), cached per string."""

    def __init__(self):
        self._raw: dict[str, tuple[list[Point], list[tuple[int, ...]], tuple[float, float, float, float]]] = {}
        self._cap_h: float | None = None

    def raw(self, text: str):
        got = self._raw.get(text)
        if got is None:
            me = su.text_mesh("sign_glyph", text, 1.0)
            verts = [(v.co.x, v.co.y) for v in me.vertices]
            faces = [tuple(p.vertices) for p in me.polygons if len(p.vertices) >= 3]
            bpy.data.meshes.remove(me)
            bb = g.bbox(verts) if verts else (0.0, 0.0, 0.0, 0.0)
            got = (verts, faces, bb)
            self._raw[text] = got
        return got

    @property
    def cap_h(self) -> float:
        if self._cap_h is None:
            _v, _f, bb = self.raw("H")
            self._cap_h = max(bb[3] - bb[1], 1e-3)
        return self._cap_h

    def token(self, tok: Token, h: float, size: int = 3):
        """(verts2d, faces, width) for a message element scaled to legend height ``h``, left edge at u = 0,
        vertically centred on v = 0."""
        kind = tok[0]
        if kind == 'T':
            verts, faces, bb = self.raw(str(tok[1]).upper())
            k = h / self.cap_h
            vc = 0.5 * (bb[1] + bb[3])
            out = [((x - bb[0]) * k, (y - vc) * k) for x, y in verts]
            return out, faces, (bb[2] - bb[0]) * k
        if kind == 'A':
            poly = _arrow(float(tok[1]), h)
            bb = g.bbox(poly)
            pts, tris = g.triangulate([(x - bb[0], y) for x, y in poly])
            return pts, tris, bb[2] - bb[0]
        if kind == 'DISC':
            r = AGM_DISC_D * 0.5
            seg = 48
            pts, tris = g.triangulate(g.circle((r, 0.0), r, seg))
            return pts, tris, 2 * r
        if kind == 'ILS':
            # ILS critical area boundary pictogram (AC 44L App. B): two rails with rungs, 15 x 42 in at Size 3
            sh, sl = ILS_SYMBOL_H * h, ILS_SYMBOL_L * h
            rail = 0.16 * sh
            verts: list[Point] = []
            faces: list[tuple[int, ...]] = []

            def quad(x0, y0, x1, y1):
                b = len(verts)
                verts.extend([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
                faces.append((b, b + 1, b + 2, b + 3))
            quad(0.0, -sh / 2, sl, -sh / 2 + rail)
            quad(0.0, sh / 2 - rail, sl, sh / 2)
            n_rung = 5
            rung_w = 0.09 * sh
            for i in range(n_rung):
                x = rung_w + (sl - 2 * rung_w) * i / (n_rung - 1) - rung_w / 2
                quad(x, -sh / 2 + rail, x + rung_w, sh / 2 - rail)
            return verts, faces, sl
        raise ValueError(f"unknown sign token {tok!r}")


class _Mats:
    """Material slot allocator for one mesh."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.names: list[str] = []

    def i(self, name: str) -> int:
        if name not in self.names:
            self.names.append(name)
        return self.names.index(name)

    def list(self):
        return [self.ctx.mats(n) for n in self.names]


# =========================================================================== sign array mesh
class _ArrayBuilder:
    def __init__(self, ctx, glyphs: _Glyphs):
        self.ctx = ctx
        self.glyphs = glyphs
        self.seg = max(8, int(round(12 * ctx.detail)))

    # ------------------------------------------------------------------ dimensions
    @staticmethod
    def legend_h(face: Face, size: int) -> float:
        if face.kind == 'RDR':
            return RDR_NUMERAL_H
        return SIGN_SIZES[size][0]

    def message_width(self, face: Face | None, size: int) -> float:
        if face is None or not face.tokens:
            return 0.0
        h = self.legend_h(face, size)
        w = sum(self.glyphs.token(t, h, size)[2] for t in face.tokens)
        return w + TOKEN_GAP * h * (len(face.tokens) - 1)

    def panel_length(self, p: Panel) -> float:
        lh, ph, _th = SIGN_SIZES[p.size]
        if p.square:
            return ph
        w = max(self.message_width(p.front, p.size), self.message_width(p.back, p.size))
        return max(MIN_LENGTH_RATIO * ph, w + 2 * SIDE_MARGIN * lh)

    def array_length(self, panels: Sequence[Panel]) -> float:
        return sum(self.panel_length(p) for p in panels) + HOUSING_GAP * (len(panels) - 1)

    # ------------------------------------------------------------------ face content (u right, v up, w outward)
    def face_builder(self, face: Face, size: int, half_u: float, half_v: float, mats: _Mats) -> MeshBuilder:
        fb = MeshBuilder()
        bg, fg = FACE_STYLE[face.kind]
        i_bg, i_frame = mats.i(bg), mats.i(M_FRAME)
        a, b, d = half_u, half_v, RECESS
        # recessed background
        fb.add_face([(-a, -b, -d), (a, -b, -d), (a, b, -d), (-a, b, -d)], i_bg)
        # recess walls (normals point into the opening)
        fb.add_face([(-a, -b, 0), (a, -b, 0), (a, -b, -d), (-a, -b, -d)], i_frame, uv_fn=lambda x, y, z: (x, z))
        fb.add_face([(a, b, 0), (-a, b, 0), (-a, b, -d), (a, b, -d)], i_frame, uv_fn=lambda x, y, z: (x, z))
        fb.add_face([(-a, b, 0), (-a, -b, 0), (-a, -b, -d), (-a, b, -d)], i_frame, uv_fn=lambda x, y, z: (y, z))
        fb.add_face([(a, -b, 0), (a, b, 0), (a, b, -d), (a, -b, -d)], i_frame, uv_fn=lambda x, y, z: (y, z))
        if fg is None:
            return fb
        i_fg = mats.i(fg)
        if face.kind == 'LOCATION':      # yellow border inside a black margin (L-858L)
            bw, bm = BORDER[size]
            outer = g.rect(0.0, 0.0, 2 * (a - bm), 2 * (b - bm))
            inner = g.rect(0.0, 0.0, 2 * (a - bm - bw), 2 * (b - bm - bw))
            fb.add_polygon(outer, z=-d + BORDER_LIFT, mat=i_fg, holes=[inner])
        # legend
        h = self.legend_h(face, size)
        toks = [self.glyphs.token(t, h, size) for t in face.tokens]
        total = sum(t[2] for t in toks) + TOKEN_GAP * h * (len(toks) - 1)
        u = -total / 2
        w = -d + LEGEND_LIFT
        for verts, faces, width in toks:
            base = len(fb.verts)
            for x, y in verts:
                fb.add_vertex((x + u, y, w))
            for f in faces:
                fb.add_face_idx([base + i for i in f], i_fg)
            u += width + TOKEN_GAP * h
        return fb

    # ------------------------------------------------------------------ one housing
    def panel(self, mb: MeshBuilder, p: Panel, yc: float, length: float, mats: _Mats) -> None:
        lh, ph, th = SIGN_SIZES[p.size]
        z0, z1 = th - ph, th
        zc = 0.5 * (z0 + z1)
        hl, hd = length * 0.5, PANEL_DEPTH * 0.5
        i_frame, i_leg = mats.i(M_FRAME), mats.i(M_LEG)
        # housing top / bottom / ends
        mb.add_face([(-hd, yc - hl, z1), (hd, yc - hl, z1), (hd, yc + hl, z1), (-hd, yc + hl, z1)], i_frame)
        mb.add_face([(-hd, yc + hl, z0), (hd, yc + hl, z0), (hd, yc - hl, z0), (-hd, yc - hl, z0)], i_frame)
        mb.add_face([(hd, yc + hl, z0), (-hd, yc + hl, z0), (-hd, yc + hl, z1), (hd, yc + hl, z1)], i_frame,
                    uv_fn=lambda x, y, z: (x, z))
        mb.add_face([(-hd, yc - hl, z0), (hd, yc - hl, z0), (hd, yc - hl, z1), (-hd, yc - hl, z1)], i_frame,
                    uv_fn=lambda x, y, z: (x, z))
        # faces: front (+X) reads along +Y, back (-X) reads along -Y
        m_front = Matrix(((0, 0, 1, hd), (1, 0, 0, yc), (0, 1, 0, zc), (0, 0, 0, 1)))
        m_back = Matrix(((0, 0, -1, -hd), (-1, 0, 0, yc), (0, 1, 0, zc), (0, 0, 0, 1)))
        for face, m, sgn in ((p.front, m_front, 1.0), (p.back, m_back, -1.0)):
            if face is None:     # single faced: plain aluminium back
                x = sgn * hd
                pts = [(x, yc - hl, z0), (x, yc + hl, z0), (x, yc + hl, z1), (x, yc - hl, z1)]
                if sgn < 0:
                    pts = pts[::-1]
                mb.add_face(pts, i_frame, uv_fn=lambda x, y, z: (y, z))
                continue
            # rim ring at the face plane
            fb = MeshBuilder()
            outer = g.rect(0.0, 0.0, length, ph)
            inner = g.rect(0.0, 0.0, length - 2 * RIM, ph - 2 * RIM)
            fb.add_polygon(outer, z=0.0, mat=i_frame, holes=[inner])
            mb.append(fb, m)
            mb.append(self.face_builder(face, p.size, hl - RIM, ph * 0.5 - RIM, mats), m)
        # legs on frangible couplings
        inset = LEG_INSET if length > 3 * LEG_INSET else length * 0.25
        for ly in (yc - hl + inset, yc + hl - inset):
            mb.add_cylinder((0.0, ly), COUPLING_R, PAD_TOP, PAD_TOP + COUPLING_H, max(8, self.seg // 2), i_leg, cap_top=True)
            mb.add_cylinder((0.0, ly), LEG_R, PAD_TOP + COUPLING_H, z0 + 0.01, max(8, self.seg // 2), i_leg, cap_top=False)

    def build(self, panels: Sequence[Panel], name: str) -> bpy.types.Mesh:
        mats = _Mats(self.ctx)
        mats.i(M_FRAME)
        mb = MeshBuilder()
        total = self.array_length(panels)
        y = -total * 0.5
        for p in panels:
            L = self.panel_length(p)
            self.panel(mb, p, y + L * 0.5, L, mats)
            y += L + HOUSING_GAP
        # continuous concrete pad (AC 44L: (length + 12 in) x 24 in)
        mb.add_box((0.0, 0.0, PAD_TOP - PAD_H * 0.5), (PAD_W, total + 0.3, PAD_H), mats.i(M_PAD), uv_tile=2.0)
        return mb.build(name, materials=mats.list())


# =========================================================================== wind cone / segmented circle meshes
def _frustum(mb: MeshBuilder, p0: Vector, p1: Vector, r0: float, r1: float, seg: int, mat: int) -> None:
    """Open truncated cone from p0 (radius r0) to p1 (radius r1)."""
    axis = (p1 - p0).normalized()
    up = Vector((0, 0, 1)) if abs(axis.z) < 0.9 else Vector((1, 0, 0))
    n1 = axis.cross(up).normalized()
    n2 = axis.cross(n1).normalized()
    rings = []
    for p, r in ((p0, r0), (p1, r1)):
        ring = []
        for k in range(seg):
            a = 2 * math.pi * k / seg
            q = p + (n1 * math.cos(a) + n2 * math.sin(a)) * r
            ring.append(mb.add_vertex((q.x, q.y, q.z)))
        rings.append(ring)
    length = (p1 - p0).length
    for k in range(seg):
        j = (k + 1) % seg
        u0, u1 = k / seg, (k + 1) / seg
        mb.add_face_idx((rings[0][k], rings[0][j], rings[1][j], rings[1][k]), mat, True,
                        uvs=[(u0, 0.0), (u1, 0.0), (u1, length), (u0, length)])


def _mesh_wind_cone(ctx, name: str) -> bpy.types.Mesh:
    mats = _Mats(ctx)
    i_pole, i_cone, i_obs, i_pad, i_dark = mats.i(M_POLE), mats.i(M_CONE), mats.i(M_OBS), mats.i(M_PAD), mats.i(M_DARK)
    seg = max(12, int(round(16 * ctx.detail)))
    mb = MeshBuilder()
    # concrete base and tilt-down pole (L-807 rigid mount)
    mb.add_box((0.0, 0.0, 0.05), (0.8, 0.8, 0.15), i_pad, uv_tile=2.0)
    mb.add_cylinder((0.0, 0.0), WIND_POLE_R * 1.6, 0.125, 0.45, seg, i_pole)
    mb.add_cylinder((0.0, 0.0), WIND_POLE_R, 0.45, WIND_POLE_H, seg, i_pole)
    # hinge collar
    mb.add_cylinder((0.0, 0.0), WIND_POLE_R * 1.5, 1.15, 1.35, seg, i_dark)
    # cone floodlight on a short arm below the cone
    mb.add_tube([(0.0, 0.0, 5.0), (-0.45, 0.0, 5.0)], 0.02, 6, i_dark)
    mb.add_box((-0.5, 0.0, 5.05), (0.22, 0.26, 0.14), i_dark)
    # swivel cap, pivot arm and throat ring (basket)
    mb.add_cylinder((0.0, 0.0), WIND_POLE_R * 1.4, WIND_POLE_H, WIND_POLE_H + 0.12, seg, i_pole)
    throat = Vector((0.55, 0.0, WIND_POLE_H - 0.05))
    mb.add_tube([(0.0, 0.0, WIND_POLE_H + 0.05), (throat.x, throat.y, throat.z)], 0.025, 8, i_pole)
    axis = Vector((math.cos(WIND_TILT), 0.0, -math.sin(WIND_TILT)))
    tip = throat + axis * WIND_CONE_L
    ring_mb = MeshBuilder()
    ring_mb.add_torus((0.0, 0.0, 0.0), WIND_THROAT_D * 0.5 + 0.02, 0.02, max(16, seg), 6, i_pole, axis='X')
    rot = Matrix.Rotation(-WIND_TILT, 4, 'Y')
    mb.append(ring_mb, Matrix.Translation(throat) @ rot)
    # basket struts holding the fabric throat
    for k in range(4):
        a = math.pi / 4 + k * math.pi / 2
        r = WIND_THROAT_D * 0.5 + 0.02
        end = throat + (rot @ Vector((0.0, r * math.cos(a), r * math.sin(a))))
        mb.add_tube([(throat.x, throat.y, throat.z), (end.x, end.y, end.z)], 0.012, 6, i_pole, caps=False)
    # the fabric cone (two-sided material)
    _frustum(mb, throat, tip, WIND_THROAT_D * 0.5, WIND_TIP_D * 0.5, seg, i_cone)
    # L-810 red obstruction light on top
    mb.add_cylinder((0.0, 0.0), 0.015, WIND_POLE_H + 0.12, WIND_POLE_H + 0.32, 8, i_pole, cap_top=False)
    mb.add_sphere((0.0, 0.0, WIND_POLE_H + 0.38), 0.06, 12, 8, i_obs)
    return mb.build(name, materials=mats.list())


def _mesh_segmented_circle(ctx, name: str) -> bpy.types.Mesh:
    mats = _Mats(ctx)
    i_paint = mats.i(M_PAINT)
    mb = MeshBuilder(default_color=(0.0, 0.0, 0.0, 0.0))
    r = SEG_CIRCLE_D * 0.5
    half = 0.5 * SEG_LEN / r
    for k in range(SEG_COUNT):
        a = 2 * math.pi * k / SEG_COUNT
        poly = g.ring_sector((0.0, 0.0), r - SEG_W * 0.5, r + SEG_W * 0.5, a - half, a + half, 6)
        mb.add_polygon(poly, z=MARKING_Z, mat=i_paint)
    return mb.build(name, materials=mats.list())


# =========================================================================== placement
class _Signage:
    def __init__(self, ctx):
        self.ctx = ctx
        self.plan: P.Plan = ctx.plan
        self.rw: P.RunwayPlan = ctx.plan.runway
        self.s = ctx.settings.signage
        self.size = int(self.s.sign_size)
        self.glyphs = _Glyphs()
        self.arrays = _ArrayBuilder(ctx, self.glyphs)
        self.counts: dict[str, int] = {}
        self.skipped: dict[str, int] = {}
        # pavement that a sign must not stand on (runway / overrun handled by the offsets themselves)
        skip = {P.Z_RUNWAY, P.Z_RUNWAY_ASPHALT, P.Z_OVERRUN}
        self.obstacles: list[tuple[tuple[float, float, float, float], list[Point]]] = []
        for poly, zone in self.plan.pavement:
            if zone in skip or len(poly) < 3:
                continue
            self.obstacles.append((g.bbox(poly), poly))
        self.roads = [(r.centerline, r.width * 0.5, r.closed) for r in self.plan.roads]

    # ------------------------------------------------------------------ helpers
    def blocked(self, x: float, y: float, clear: float = SIGN_CLEARANCE) -> bool:
        for (x0, y0, x1, y1), poly in self.obstacles:
            if x < x0 - clear or x > x1 + clear or y < y0 - clear or y > y1 + clear:
                continue
            if g.point_in_poly((x, y), poly) or g.dist_point_poly_edge((x, y), poly) < clear:
                return True
        for cl, hw, closed in self.roads:
            pts = list(cl) + ([cl[0]] if closed else [])
            for i in range(len(pts) - 1):
                if g.dist_point_segment((x, y), pts[i], pts[i + 1]) < hw + clear:
                    return True
        return False

    def mesh_for(self, kind: str, panels: Sequence[Panel]) -> bpy.types.Mesh:
        key = f"{kind}|" + "|".join(p.key() for p in panels)
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:8]
        name = f"Signage_{kind}_{digest}"
        return self.ctx.shared_mesh("Signage_" + key, lambda: self.arrays.build(panels, name))

    def place(self, kind: str, mesh: bpy.types.Mesh, x: float, y: float, normal: Point, z: float | None = None) -> None:
        n = self.counts.get(kind, 0) + 1
        self.counts[kind] = n
        rot = math.atan2(normal[1], normal[0])
        zz = self.rw.crown(x, y) if z is None else z
        self.ctx.add_instance(f"Signage_{kind}_{n:03d}", mesh, CAT, (x, y, zz), rot)

    def place_array(self, kind: str, panels: Sequence[Panel], x: float, y: float, normal: Point,
                    shift_dir: Point | None = None, shifts: Sequence[float] = ()) -> bool:
        """Place an array whose *centre* is at (x, y) with the front face normal ``normal``; tries the
        alternative positions ``x + shift_dir * s`` when the footprint touches other pavement."""
        L = self.arrays.array_length(panels)
        ax = g.perp_left(g.normalize(normal))
        cands = [(x, y)] + ([(x + shift_dir[0] * s, y + shift_dir[1] * s) for s in shifts] if shift_dir else [])
        for px, py in cands:
            if any(self.blocked(px + ax[0] * t, py + ax[1] * t) for t in (-L / 2, 0.0, L / 2)):
                continue
            self.place(kind, self.mesh_for(kind, panels), px, py, normal)
            return True
        self.skipped[kind] = self.skipped.get(kind, 0) + 1
        return False

    def apron_for(self, tw: P.TaxiwayPlan) -> P.ApronPlan | None:
        for ap in self.plan.aprons:
            if tw in ap.access_taxiways:
                return ap
        return None

    def destination_text(self, tw: P.TaxiwayPlan) -> str:
        ap = self.apron_for(tw)
        return APRON_DEST.get(ap.kind, 'APRON') if ap is not None else 'APRON'

    def connectors(self, side: int) -> list[P.TaxiwayPlan]:
        return [t for t in self.plan.taxiways if t.side == side and t.parallel_junction is not None
                and t.kind in ('CONNECTOR', 'HIGH_SPEED', 'END')]

    @staticmethod
    def arrow_toward(forward: Point, target: Point) -> float:
        """Arrow angle (deg, 0 = right, 90 = ahead, 180 = left) for a viewer facing ``forward`` toward a target direction."""
        f = g.normalize(forward)
        t = g.normalize(target)
        ahead = g.dot(f, t)
        left = g.dot(g.perp_left(f), t)
        if ahead > 0.92:
            return 90.0
        if ahead > 0.38:
            return 135.0 if left > 0 else 45.0
        return 180.0 if left > 0 else 0.0

    # ------------------------------------------------------------------ 1. holding position arrays (UFC 9-3, AC 18H Ch. 2)
    def hold_signs(self) -> None:
        s = self.s
        if not s.hold_signs:
            return
        for tw in self.plan.taxiways:
            if not tw.signs:
                continue
            par = self.plan.parallel_taxiway(tw.side)
            for h in tw.hold_lines:
                d = g.normalize(h.direction)                # toward the runway
                left = g.perp_left(d)                       # left of an aircraft approaching the runway
                front = (-d[0], -d[1])                      # face reads toward the approaching aircraft
                panels: list[Panel] = []
                if h.kind == 'A':
                    name = (h.taxiway or tw.name).upper()
                    if s.location_signs and name:
                        loc = Face('LOCATION', (('T', name),))
                        panels.append(Panel(loc, loc, self.size))       # location outboard, double faced
                    back: Face | None = None
                    if s.direction_signs:
                        if tw.kind == 'ACCESS':
                            back = Face('DESTINATION', (('T', self.destination_text(tw)), ('A', 90)))
                        elif par is not None and par.name:
                            back = Face('DIRECTION', (('A', 180), ('T', par.name.upper()), ('A', 0)))
                    panels.append(Panel(Face('MANDATORY', (('T', h.runway_text or self.runway_text()),)), back, self.size))
                    kind = 'HoldArray'
                elif h.kind == 'B':
                    back = Face('BOUNDARY', (('ILS',),)) if s.direction_signs else None
                    panels.append(Panel(Face('MANDATORY', (('T', 'ILS'),)), back, self.size))
                    kind = 'ILSArray'
                else:
                    continue
                L = self.arrays.array_length(panels)
                off = tw.width * 0.5 + tw.shoulder + s.sign_offset + L * 0.5
                cx = h.center[0] + left[0] * off - d[0] * HOLD_SIGN_SETBACK
                cy = h.center[1] + left[1] * off - d[1] * HOLD_SIGN_SETBACK
                self.place_array(kind, panels, cx, cy, front, shift_dir=(-d[0], -d[1]), shifts=(3.0, 6.0))

    def runway_text(self) -> str:
        return f"{self.rw.numerals_low}-{self.rw.numerals_high}"

    # ------------------------------------------------------------------ 2. runway exit signs (UFC 9-3; AC 18H 2.12)
    def exit_signs(self) -> None:
        if not self.s.direction_signs:
            return
        rw = self.rw
        for tw in self.plan.taxiways:
            if tw.runway_junction is None or not tw.signs or not tw.name:
                continue
            if tw.kind not in ('CONNECTOR', 'HIGH_SPEED', 'END', 'ACCESS'):
                continue
            jx = tw.runway_junction[0]
            side = tw.side
            if tw.kind == 'HIGH_SPEED':
                rolls = [1.0 if tw.exit_dir == 'HIGH' else -1.0]     # serves one landing direction only
            else:
                rolls = [1.0, -1.0]
            for r in rolls:
                sx = jx - r * EXIT_SIGN_LEAD                          # before the junction for aircraft rolling +-X
                if abs(sx) > rw.half_len - 20.0:
                    continue
                is_left = side * r > 0                                 # exit on the viewer's left?
                if tw.kind == 'HIGH_SPEED':
                    ang = 135.0 if is_left else 45.0
                else:
                    ang = 180.0 if is_left else 0.0
                name = tw.name.upper()
                tokens = (('A', ang), ('T', name)) if is_left else (('T', name), ('A', ang))
                panels = [Panel(Face('DIRECTION', tokens), None, self.size)]
                L = self.arrays.array_length(panels)
                y = side * (rw.half_w + EXIT_SIGN_OFFSET + L * 0.5)
                self.place_array('ExitSign', panels, sx, y, (-r, 0.0), shift_dir=(-r, 0.0), shifts=(15.0, 30.0))

    # ------------------------------------------------------------------ 3. taxiway intersection arrays on the parallel (UFC 9-4)
    def intersection_arrays(self) -> None:
        s = self.s
        if not (s.location_signs or s.direction_signs):
            return
        for side in (1, -1):
            par = self.plan.parallel_taxiway(side)
            conns = self.connectors(side)
            if par is None or not conns:
                continue
            for tw in conns:
                if not tw.signs:
                    continue
                pj = tw.parallel_junction
                cl = tw.centerline
                d_end = g.normalize((cl[-1][0] - cl[-2][0], cl[-1][1] - cl[-2][1]))   # arriving at the parallel
                if g.length(d_end, (0.0, 0.0)) < 0.5:
                    continue
                left = g.perp_left(d_end)
                best_l = best_r = None
                for o in conns:
                    if o is tw or o.parallel_junction is None:
                        continue
                    v = (o.parallel_junction[0] - pj[0], o.parallel_junction[1] - pj[1])
                    dist = math.hypot(*v)
                    if dist < 1.0:
                        continue
                    if g.dot(v, left) > 0:
                        if best_l is None or dist < best_l[0]:
                            best_l = (dist, o)
                    elif best_r is None or dist < best_r[0]:
                        best_r = (dist, o)
                panels: list[Panel] = []
                if s.direction_signs and best_l and best_l[1].name:
                    panels.append(Panel(Face('DIRECTION', (('A', 180), ('T', best_l[1].name.upper()))), None, self.size))
                if s.location_signs and par.name:
                    loc = Face('LOCATION', (('T', par.name.upper()),))
                    panels.append(Panel(loc, loc, self.size))
                if s.direction_signs and best_r and best_r[1].name:
                    panels.append(Panel(Face('DIRECTION', (('T', best_r[1].name.upper()), ('A', 0))), None, self.size))
                if not panels:
                    continue
                off = par.width * 0.5 + par.shoulder + s.sign_offset + PANEL_DEPTH * 0.5
                cx, cy = pj[0] + d_end[0] * off, pj[1] + d_end[1] * off
                self.place_array('LocationArray', panels, cx, cy, (-d_end[0], -d_end[1]), shift_dir=d_end, shifts=(4.0, 8.0))

    # ------------------------------------------------------------------ 4. destination signs on apron access stubs
    def destination_signs(self) -> None:
        if not self.s.direction_signs:
            return
        for tw in self.plan.taxiways:
            if tw.kind != 'ACCESS' or tw.parallel_junction is None or len(tw.centerline) < 2:
                continue
            par = self.plan.parallel_taxiway(tw.side)
            p0, p1 = tw.centerline[0], tw.centerline[-1]
            d = g.normalize((p1[0] - p0[0], p1[1] - p0[1]))          # toward the apron
            if g.length(d, (0.0, 0.0)) < 0.5:
                continue
            left = g.perp_left(d)
            stub = g.length(p0, p1)
            along = max((par.width * 0.5 + par.shoulder + 4.0) if par else 0.0, stub * 0.5)
            along = min(along, stub - 4.0)
            lateral = tw.width * 0.5 + tw.shoulder + self.s.sign_offset + PANEL_DEPTH * 0.5
            cx = p0[0] + d[0] * along + left[0] * lateral
            cy = p0[1] + d[1] * along + left[1] * lateral
            front = Face('DESTINATION', (('T', self.destination_text(tw)), ('A', 90)))
            # back: runway direction for aircraft leaving the apron (toward the nearest connector)
            back = None
            conns = self.connectors(tw.side)
            if conns:
                near = min(conns, key=lambda c: abs(c.parallel_junction[0] - p0[0]))
                v = (near.parallel_junction[0] - p0[0], near.parallel_junction[1] - p0[1])
                ang = self.arrow_toward((-d[0], -d[1]), v) if math.hypot(*v) > 1.0 else 90.0
                txt = ('T', f"RWY {self.runway_text()}")
                back = Face('DESTINATION', ((('A', ang), txt) if ang == 180.0 else (txt, ('A', ang))))
            panels = [Panel(front, back, self.size)]
            self.place_array('DestinationSign', panels, cx, cy, (-d[0], -d[1]), shift_dir=d, shifts=(4.0, 8.0))

    # ------------------------------------------------------------------ 5. RDR + AGM row (UFC 9-6, 9-7)
    def rdr_row_y(self, sgn: float) -> float:
        return sgn * (self.rw.half_w + RDR_OFFSET + SIGN_SIZES[RDR_SIZE][1] * 0.5)

    def rdr_signs(self) -> None:
        if not self.s.rdr_signs:
            return
        rw = self.rw
        length_ft = rw.length / FT
        n_int = int(length_ft // RDR_INTERVAL_FT)
        if n_int < 2:
            return
        excess = length_ft - n_int * RDR_INTERVAL_FT
        first = RDR_INTERVAL_FT + excess * 0.5                     # half the excess added at each end
        stations = []
        st = first
        while st <= length_ft - first + 1e-6:
            stations.append(st)
            st += RDR_INTERVAL_FT
        agm = list(rw.arresting) if (self.s.agm_signs and rw.arresting_type != 'NONE') else []
        for st in stations:
            x = -rw.half_len + ft(st)
            to_low = int(round((x + rw.half_len) / ft(RDR_INTERVAL_FT)))       # remaining toward the low end (face +X)
            to_high = int(round((rw.half_len - x) / ft(RDR_INTERVAL_FT)))      # remaining toward the high end (face -X)
            if to_low < 1 or to_high < 1:
                continue
            panels = [Panel(Face('RDR', (('T', str(to_low)),)), Face('RDR', (('T', str(to_high)),)), RDR_SIZE, square=True)]
            for sgn in (1.0, -1.0):
                px, py = x, self.rdr_row_y(sgn)
                near = [xa for xa in agm if abs(xa - x) < AGM_RDR_CLEAR]
                if near:                                     # in line with the marker, 5 ft outboard
                    px = near[0]
                    py += sgn * AGM_RDR_SHIFT
                self.place_array('RDR', panels, px, py, (1.0, 0.0), shift_dir=(1.0, 0.0), shifts=(RDR_TOLERANCE, -RDR_TOLERANCE))

    def agm_signs(self) -> None:
        rw = self.rw
        if not self.s.agm_signs or rw.arresting_type == 'NONE':
            return
        disc = Face('AGM', (('DISC',),))
        panels = [Panel(disc, disc, RDR_SIZE, square=True)]
        for xa in rw.arresting:
            if abs(xa) > rw.half_len - 5.0:
                continue
            for sgn in (1.0, -1.0):
                self.place_array('AGM', panels, xa, self.rdr_row_y(sgn), (1.0, 0.0), shift_dir=(1.0, 0.0), shifts=(ft(10), -ft(10)))

    # ------------------------------------------------------------------ 6. wind cones (UFC 10-2; L-807)
    def wind_cones(self) -> None:
        if not self.s.wind_cones or not self.plan.wind_cones:
            return
        mesh = self.ctx.shared_mesh("Signage_WindCone", lambda: _mesh_wind_cone(self.ctx, "Signage_WindCone"))
        primary = min(self.plan.wind_cones, key=lambda p: abs(p[0]))
        for p in self.plan.wind_cones:
            if self.blocked(p[0], p[1], 2.0):
                self.skipped['WindCone'] = self.skipped.get('WindCone', 0) + 1
                continue
            self.place('WindCone', mesh, p[0], p[1], (1.0, 0.0), z=0.0)
            if p is primary:
                circ = self.ctx.shared_mesh("Signage_SegmentedCircle",
                                            lambda: _mesh_segmented_circle(self.ctx, "Signage_SegmentedCircle"))
                self.place('SegmentedCircle', circ, p[0], p[1], (1.0, 0.0), z=0.0)

    # ------------------------------------------------------------------ run
    def run(self) -> None:
        self.hold_signs()
        self.exit_signs()
        self.intersection_arrays()
        self.destination_signs()
        self.rdr_signs()
        self.agm_signs()
        self.wind_cones()
        for kind, n in sorted(self.skipped.items()):
            self.ctx.warn(f"signage: {n} {kind} omitted (no clear position within tolerance, UFC 9-6/9-2)")


def build(ctx) -> None:
    _Signage(ctx).run()
