"""Perimeter builder: security fence(s), gates, ECP road furniture, culvert headwalls, fence signs.

Standards / practice followed (see docs/research 04 §11, 01 §12):
* UFC 4-022-03 Table 2-1 Type A fence: 2 in chain-link fabric on galvanised steel posts, three-strand barbed-wire
  top guard on outriggers angled 45 deg outward, 2.4 m overall; bottom of fabric within 2 in of grade (§2.3-2.10).
  Line posts at 10 ft (here ``POST_PITCH`` = 3.0 m); top rail + bottom tension wire (ASTM F567 practice).
* UFC 4-022-03 §2.15.1 dual fencing: second identical fence ``settings.perimeter.fence_gap`` inside the first,
  the space between kept clear (the patrol road runs just inside the inner fence, see layout.plan).
* UFC 4-022-03 Ch. 3 gates: cantilevered sliding leaves (spans 6-12 m) with the same fabric and top guard.
* UFC 4-022-01 §5-3 / §5-5 ECF: speed-control serpentine of passive barriers in the approach zone, a drop-arm and
  a passive bollard line at the access-control zone; AASHTO/"F-shape" precast concrete barrier profile
  (0.81 m tall, 0.61 m base, 3 m units).
* UFC 3-260-01 §7-2.11 / research 04 §14: culvert headwalls where roads cross the drainage swales.
* Fence signs: "RESTRICTED AREA" placards (AFI 31-101 practice: every 100-200 ft; here one per ~60 m).

Object-count control: line posts are linked duplicates of ONE shared post mesh (rotated to face outward),
chain-link panels + rails + wires are ONE merged mesh per fence loop, signs / barriers / bollards / headwalls are
instances of shared meshes. Everything is in the base frame; the ground outside pavement is at ``GROUND_Z``.
"""
from __future__ import annotations

import math
import traceback
from typing import Sequence

import bpy
from mathutils import Matrix, Vector

from ..core import constants as C
from ..core import geom2d as g
from ..core import scene_utils as su
from ..core.meshbuild import MeshBuilder, box_uv, planar_uv

Vec3 = tuple[float, float, float]
Point = g.Point

CATEGORY = "Perimeter"
GROUND_Z = C.DIRT_STEP            # infield / site ground level (-3 cm)
POST_PITCH = 3.0                  # line-post spacing (10 ft practice, task spec 3.0 m)
POST_R = 0.030                    # 60 mm dia line post
POST_EMBED = 0.05                 # posts start below grade (concrete footing not modelled)
FABRIC_BOTTOM = GROUND_Z + 0.04   # UFC 4-022-03: fabric bottom within 2 in (51 mm) of grade
OUTRIGGER_LEN = 0.6               # top-guard arm length, angled 45 deg outward
OUTRIGGER_ANGLE = math.radians(45.0)
STRANDS = (0.2, 0.4, 0.6)         # barbed-wire strand positions along the outrigger arm
RAIL_R = 0.021                    # 1-5/8 in top rail
TENSION_WIRE_R = 0.004
BARBED_R = 0.006
GATE_OPENING = 12.0
GATE_HALF = GATE_OPENING * 0.5
SIGN_PITCH = 60.0
SIGN_Z = 1.5
JERSEY_LEN = 3.0
JERSEY_PITCH = 8.0
CHAINLINK_TILE = 1.0              # ChainLink material tile (metres per repeat)


# =========================================================================== small building kit
class Kit:
    """MeshBuilder plus a named material palette: ``kit.m('Galvanized')`` -> material slot index.

    Shared by the perimeter and navaids builders. Geometry is in whatever frame the caller wants; the finished
    mesh is placed with ``ctx.add_object(..., location, rotation_z)``. Every solid is built with outward normals
    (materials are back-face culled).
    """

    def __init__(self, ctx, uv_tile: float = 2.0):
        self.ctx = ctx
        self.tile = uv_tile
        self.mb = MeshBuilder(default_uv=planar_uv(uv_tile))
        self.names: list[str] = []

    # palette ----------------------------------------------------------------
    def m(self, name: str) -> int:
        try:
            return self.names.index(name)
        except ValueError:
            self.names.append(name)
            return len(self.names) - 1

    def build(self, mesh_name: str, smooth_angle: float | None = None) -> bpy.types.Mesh:
        mats = [self.ctx.mats(n) for n in self.names]
        return self.mb.build(C.PREFIX + mesh_name, materials=mats, smooth_angle=smooth_angle)

    def is_empty(self) -> bool:
        return self.mb.is_empty()

    def append(self, other: "Kit", matrix: Matrix | None = None) -> None:
        """Merge another kit (remapping its palette into ours), optionally transformed."""
        remap = [self.m(n) for n in other.names]
        base = len(self.mb.verts)
        if matrix is None:
            self.mb.verts.extend(other.mb.verts)
        else:
            for v in other.mb.verts:
                w = matrix @ Vector(v)
                self.mb.verts.append((w.x, w.y, w.z))
        for f, mi, s, uv, col in zip(other.mb.faces, other.mb.face_mat, other.mb.face_smooth, other.mb.loop_uv, other.mb.loop_col):
            self.mb.faces.append(tuple(i + base for i in f))
            self.mb.face_mat.append(remap[mi] if 0 <= mi < len(remap) else 0)
            self.mb.face_smooth.append(s)
            self.mb.loop_uv.append(uv)
            self.mb.loop_col.append(col)

    # solids ------------------------------------------------------------------
    def box(self, center: Vec3, size: Vec3, mat: str, angle: float = 0.0, mat_top: str | None = None,
            mat_bottom: str | None = None, uv_tile: float | None = None) -> None:
        self.mb.add_box(center, size, self.m(mat), angle,
                        mat_top=None if mat_top is None else self.m(mat_top),
                        mat_bottom=None if mat_bottom is None else self.m(mat_bottom),
                        uv_tile=uv_tile or self.tile)

    def box_xyz(self, x0: float, x1: float, y0: float, y1: float, z0: float, z1: float, mat: str, **kw) -> None:
        self.box(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2), (abs(x1 - x0), abs(y1 - y0), abs(z1 - z0)), mat, **kw)

    def cylinder(self, center: Point, radius: float, z0: float, z1: float, mat: str, segments: int = 12,
                 cap_top: bool = True, cap_bottom: bool = False, radius_top: float | None = None,
                 smooth: bool = True) -> None:
        self.mb.add_cylinder(center, radius, z0, z1, segments, self.m(mat), cap_top, cap_bottom, smooth, radius_top,
                             0.0, self.tile)

    def tube(self, path: Sequence[Vec3], radius: float, mat: str, segments: int = 8, caps: bool = True,
             smooth: bool = True) -> None:
        self.mb.add_tube(path, radius, segments, self.m(mat), smooth, caps)

    def banded_tube(self, a: Vec3, b: Vec3, radius: float, bands: int, mats: Sequence[str], segments: int = 8) -> None:
        """Straight tube split into ``bands`` equal pieces with alternating materials (obstruction paint)."""
        va, vb = Vector(a), Vector(b)
        for i in range(bands):
            p0 = va.lerp(vb, i / bands)
            p1 = va.lerp(vb, (i + 1) / bands)
            self.tube([tuple(p0), tuple(p1)], radius, mats[i % len(mats)], segments, caps=(i == 0 or i == bands - 1))

    def sphere(self, center: Vec3, radius: float, mat: str, seg_u: int = 12, seg_v: int = 6) -> None:
        self.mb.add_sphere(center, radius, seg_u, seg_v, self.m(mat))

    def polygon(self, poly: Sequence[Point], z: float, mat: str, uv_tile: float | None = None, flip: bool = False) -> None:
        self.mb.add_polygon(poly, z, self.m(mat), flip=flip, uv_fn=planar_uv(uv_tile or self.tile))

    def prism(self, poly: Sequence[Point], z0: float, z1: float, mat: str, mat_top: str | None = None,
              bottom: bool = False, uv_tile: float | None = None) -> None:
        t = uv_tile or self.tile
        self.mb.add_prism(poly, z0, z1, mat_top=self.m(mat_top or mat), mat_side=self.m(mat),
                          mat_bottom=self.m(mat) if bottom else None, side_uv=box_uv(t), uv_fn=planar_uv(t))

    def quad(self, a: Vec3, b: Vec3, c: Vec3, d: Vec3, mat: str, uvs=None, uv_tile: float | None = None) -> None:
        self.mb.add_quad(a, b, c, d, self.m(mat), False, uvs=uvs, uv_fn=box_uv(uv_tile or self.tile))

    def plate(self, center: Vec3, size: Vec3, mat: str, rot_x: float = 0.0, rot_y: float = 0.0, rot_z: float = 0.0) -> None:
        """Box rotated about all three axes (tilted sensors, leaning signs, reflector mounts)."""
        tmp = Kit(self.ctx, self.tile)
        tmp.box((0.0, 0.0, 0.0), size, mat)
        mtx = (Matrix.Translation(Vector(center)) @ Matrix.Rotation(rot_z, 4, 'Z')
               @ Matrix.Rotation(rot_y, 4, 'Y') @ Matrix.Rotation(rot_x, 4, 'X'))
        self.append(tmp, mtx)

    def yz_prism(self, poly_yz: Sequence[Point], x0: float, x1: float, mat: str, mat_cap: str | None = None,
                 smooth: bool = False, uv_tile: float | None = None) -> None:
        """Extrude a (y, z) polygon along +X from x0 to x1 with correctly oriented end caps (barrier profiles)."""
        if x1 < x0:
            x0, x1 = x1, x0
        t = uv_tile or self.tile
        ms, mc = self.m(mat), self.m(mat_cap or mat)
        pts, tris = g.triangulate(poly_yz)
        mb = self.mb
        base = len(mb.verts)
        for y, z in pts:
            mb.add_vertex((x1, y, z))
        for a, b, c in tris:
            mb.add_face_idx((base + a, base + b, base + c), mc, False, uvs=[(pts[i][0] / t, pts[i][1] / t) for i in (a, b, c)])
        base = len(mb.verts)
        for y, z in pts:
            mb.add_vertex((x0, y, z))
        for a, b, c in tris:
            mb.add_face_idx((base + a, base + c, base + b), mc, False, uvs=[(-pts[i][0] / t, pts[i][1] / t) for i in (a, c, b)])
        lp = g.ensure_ccw(poly_yz)
        n = len(lp)
        acc = 0.0
        for i in range(n):
            a, b = lp[i], lp[(i + 1) % n]
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            if seg < 1e-9:
                continue
            mb.add_face(((x0, a[0], a[1]), (x0, b[0], b[1]), (x1, b[0], b[1]), (x1, a[0], a[1])), ms, smooth,
                        uvs=[(x0 / t, acc / t), (x0 / t, (acc + seg) / t), (x1 / t, (acc + seg) / t), (x1 / t, acc / t)])
            acc += seg

    def text(self, text: str, height: float, mat: str, origin: Vec3, facing: str = '+X', offset: float = 0.004) -> None:
        """Lettering standing on a vertical face; ``origin`` is the text centre, ``facing`` the face normal."""
        try:
            me = su.text_mesh("txt", text, height / 0.7, bold=True)
        except Exception:      # no font in this Blender build: legends are optional
            return
        try:
            n = {'+X': (1.0, 0.0), '-X': (-1.0, 0.0), '+Y': (0.0, 1.0), '-Y': (0.0, -1.0)}[facing]
            phi = math.atan2(n[0], -n[1])
            mtx = (Matrix.Translation((origin[0] + n[0] * offset, origin[1] + n[1] * offset, origin[2]))
                   @ Matrix.Rotation(phi, 4, 'Z') @ Matrix.Rotation(math.pi / 2, 4, 'X'))
            mi = self.m(mat)
            base = len(self.mb.verts)
            for v in me.vertices:
                w = mtx @ v.co
                self.mb.verts.append((w.x, w.y, w.z))
            for p in me.polygons:
                idx = [base + i for i in p.vertices]
                self.mb.add_face_idx(idx, mi, False, uvs=[(me.vertices[i].co.x, me.vertices[i].co.y) for i in p.vertices])
        finally:
            bpy.data.meshes.remove(me)


# =========================================================================== shared small props
def warning_sign(k: Kit, x: float, y: float, facing: float = 0.0, detail: float = 1.0, legend: str = "RESTRICTED AREA",
                 z_top: float = 1.9, w: float = 0.5, h: float = 0.35) -> None:
    """Free-standing red/white warning placard on a 60 mm galvanised post; ``facing`` = direction the face
    points (radians about Z). Used at fences (perimeter) and navaid compounds (navaids)."""
    tmp = Kit(k.ctx, k.tile)
    tmp.cylinder((0.0, 0.0), 0.03, GROUND_Z - 0.05, z_top, 'Galvanized', segments=6)
    zc = z_top - h * 0.5 - 0.02
    tmp.box((0.04, 0.0, zc), (0.02, w, h), 'SignWhite')                 # white border plate
    tmp.box((0.052, 0.0, zc), (0.006, w - 0.06, h - 0.06), 'SignRed')   # red field
    if detail >= 1.0 and legend:
        lines = legend.split("\n") if "\n" in legend else ([legend] if len(legend) <= 10 else legend.split(" ", 1))
        n = len(lines)
        lh = min(0.10, (h - 0.12) / n)
        for i, line in enumerate(lines):
            zz = zc + (n - 1 - 2 * i) * lh * 0.65
            tmp.text(line, lh * 0.75, 'SignWhite', (0.055, 0.0, zz), '+X', offset=0.003)
    k.append(tmp, Matrix.Translation((x, y, 0.0)) @ Matrix.Rotation(facing, 4, 'Z'))


def jersey_profile(height: float = 0.81) -> list[Point]:
    """F-shape / New Jersey precast barrier cross-section (y, z): 0.61 m base, 75 mm kerb, 0.20 m top."""
    s = height / 0.81
    return [(-0.305, 0.0), (-0.305, 0.075 * s), (-0.255, 0.075 * s), (-0.19, 0.33 * s), (-0.10, height),
            (0.10, height), (0.19, 0.33 * s), (0.255, 0.075 * s), (0.305, 0.075 * s), (0.305, 0.0)]


# =========================================================================== shared mesh factories
def fence_post_kit(ctx, height: float, detail: float) -> "Kit":
    """Line post (60 mm tube) with the 45 deg outrigger arm along local +X (= outward), as a kit to merge."""
    k = Kit(ctx, 1.0)
    segs = 6 if detail < 1.5 else 8
    k.cylinder((0.0, 0.0), POST_R, GROUND_Z - POST_EMBED, height + 0.02, 'Galvanized', segments=segs)
    c, s = math.cos(OUTRIGGER_ANGLE), math.sin(OUTRIGGER_ANGLE)
    k.tube([(0.0, 0.0, height - 0.05), (OUTRIGGER_LEN * c, 0.0, height - 0.05 + OUTRIGGER_LEN * s)], 0.018, 'Galvanized', 4)
    return k


def fence_post_mesh(ctx, height: float, detail: float) -> bpy.types.Mesh:
    return fence_post_kit(ctx, height, detail).build("Perimeter_FencePost", smooth_angle=math.radians(40))


def fence_sign_mesh(ctx, detail: float) -> bpy.types.Mesh:
    """0.4 x 0.3 m 'RESTRICTED AREA' placard hung on the fabric; local +X = outward face."""
    k = Kit(ctx, 1.0)
    w, h = 0.4, 0.3
    k.box((0.01, 0.0, SIGN_Z), (0.02, w, h), 'SignWhite')
    k.box((0.022, 0.0, SIGN_Z), (0.006, w - 0.05, h - 0.05), 'SignRed')
    if detail >= 1.0:
        k.text("RESTRICTED", 0.045, 'SignWhite', (0.025, 0.0, SIGN_Z + 0.045), '+X', offset=0.003)
        k.text("AREA", 0.045, 'SignWhite', (0.025, 0.0, SIGN_Z - 0.03), '+X', offset=0.003)
        k.text("KEEP OUT", 0.028, 'SignWhite', (0.025, 0.0, SIGN_Z - 0.095), '+X', offset=0.003)
    return k.build("Perimeter_FenceSign")


def jersey_barrier_mesh(ctx, detail: float) -> bpy.types.Mesh:
    """3 m precast concrete F-shape barrier along local X with lifting slots."""
    k = Kit(ctx, 1.0)
    k.yz_prism(jersey_profile(), -JERSEY_LEN / 2, JERSEY_LEN / 2, 'ConcreteBase')
    if detail >= 1.0:
        for x in (-0.9, 0.9):     # fork-lift slots through the base
            k.box((x, 0.0, 0.16), (0.25, 0.64, 0.10), 'MetalDark')
    k.mb.translate(0.0, 0.0, GROUND_Z)
    return k.build("Perimeter_JerseyBarrier")


def bollard_mesh(ctx) -> bpy.types.Mesh:
    """Yellow 200 mm steel bollard, 1.0 m tall, on a small concrete collar."""
    k = Kit(ctx, 1.0)
    k.cylinder((0.0, 0.0), 0.10, GROUND_Z, 1.0, 'Bollard', segments=10)
    k.sphere((0.0, 0.0, 1.0), 0.10, 'Bollard', 10, 4)
    k.cylinder((0.0, 0.0), 0.20, GROUND_Z - 0.02, GROUND_Z + 0.08, 'ConcreteBase', segments=10)
    return k.build("Perimeter_Bollard", smooth_angle=math.radians(40))


def culvert_headwall_mesh(ctx, detail: float) -> bpy.types.Mesh:
    """Concrete headwall with a 0.8 m pipe end; local +X points away from the road (into the ditch)."""
    k = Kit(ctx, 1.0)
    k.box_xyz(-0.25, 0.0, -1.1, 1.1, GROUND_Z - 0.7, GROUND_Z + 0.35, 'ConcreteBase')      # headwall
    k.box_xyz(0.0, 1.2, -1.1, -0.85, GROUND_Z - 0.7, GROUND_Z + 0.15, 'ConcreteBase')       # wing walls
    k.box_xyz(0.0, 1.2, 0.85, 1.1, GROUND_Z - 0.7, GROUND_Z + 0.15, 'ConcreteBase')
    k.box_xyz(0.0, 1.2, -1.1, 1.1, GROUND_Z - 0.75, GROUND_Z - 0.62, 'ConcreteBase')        # apron slab
    segs = 12 if detail >= 1.0 else 8
    zc = GROUND_Z - 0.62 + 0.4
    k.tube([(0.0, 0.0, zc), (0.6, 0.0, zc)], 0.40, 'ConcreteBase', segs, caps=False)          # pipe outer
    k.tube([(0.61, 0.0, zc), (0.05, 0.0, zc)], 0.34, 'MetalDark', segs, caps=True)          # dark bore
    return k.build("Perimeter_CulvertHeadwall", smooth_angle=math.radians(40))


# =========================================================================== loop helpers
def _cumulative(loop: Sequence[Point]) -> tuple[list[float], float]:
    """Arc length at each vertex of a closed loop, plus the total length."""
    cum = [0.0]
    n = len(loop)
    for i in range(n):
        cum.append(cum[-1] + g.length(loop[i], loop[(i + 1) % n]))
    return cum, cum[-1]


def _point_at(loop: Sequence[Point], cum: list[float], s: float) -> Point:
    n = len(loop)
    total = cum[-1]
    s = s % total
    for i in range(n):
        if cum[i] <= s <= cum[i + 1] + 1e-9:
            seg = cum[i + 1] - cum[i]
            t = 0.0 if seg < 1e-9 else (s - cum[i]) / seg
            return g.lerp(loop[i], loop[(i + 1) % n], t)
    return loop[0]


def _closest_on_loop(loop: Sequence[Point], cum: list[float], p: Point) -> tuple[float, Point, Point]:
    """(arc length, point, unit tangent) of the loop point nearest to ``p``."""
    n = len(loop)
    best = (1e30, 0.0, loop[0], (1.0, 0.0))
    for i in range(n):
        a, b = loop[i], loop[(i + 1) % n]
        abx, aby = b[0] - a[0], b[1] - a[1]
        l2 = abx * abx + aby * aby
        if l2 < 1e-12:
            continue
        t = max(0.0, min(1.0, ((p[0] - a[0]) * abx + (p[1] - a[1]) * aby) / l2))
        q = (a[0] + abx * t, a[1] + aby * t)
        d = g.length(p, q)
        if d < best[0]:
            best = (d, cum[i] + t * math.sqrt(l2), q, g.normalize((abx, aby)))
    return best[1], best[2], best[3]


def _cut_runs(loop: Sequence[Point], cuts: Sequence[float], half: float) -> list[list[Point]]:
    """Open polylines covering the closed loop minus +-``half`` around every arc-length in ``cuts``."""
    cum, total = _cumulative(loop)
    n = len(loop)
    if not cuts:
        return [list(loop) + [loop[0]]]
    ivals = sorted(((c - half) % total, (c + half) % total) for c in cuts)
    runs: list[list[Point]] = []
    for idx, (a0, a1) in enumerate(ivals):
        b0, _b1 = ivals[(idx + 1) % len(ivals)]
        start = a1                                  # keep from the end of this cut ...
        end = b0 if len(ivals) > 1 else a0 + total  # ... to the start of the next
        if end <= start:
            end += total
        pts = [_point_at(loop, cum, start)]
        for k in range(2 * n):                      # loop vertices strictly inside (start, end), wrapping
            s = cum[k % n] + (k // n) * total
            if start + 1e-6 < s < end - 1e-6:
                pts.append(loop[k % n])
        pts.append(_point_at(loop, cum, end))
        if len(pts) >= 2:
            runs.append(pts)
    return runs


def _outward(loop_ccw: Sequence[Point], i: int) -> tuple[Point, Point]:
    """(unit tangent, outward normal) at vertex i of a CCW loop."""
    n = len(loop_ccw)
    p_prev, p_next = loop_ccw[(i - 1) % n], loop_ccw[(i + 1) % n]
    t = g.normalize((p_next[0] - p_prev[0], p_next[1] - p_prev[1]))
    return t, (t[1], -t[0])


# =========================================================================== fence
class _GateRef:
    """A gate as seen by one fence loop: point on the loop, tangent, arc length, inward direction."""

    def __init__(self, loop: Sequence[Point], cum: list[float], ref: Point, outward: Point):
        self.s, self.point, self.tangent = _closest_on_loop(loop, cum, ref)
        self.outward = g.normalize(outward)
        self.inward = (-self.outward[0], -self.outward[1])

    def along(self, p: Point) -> float:
        return (p[0] - self.point[0]) * self.tangent[0] + (p[1] - self.point[1]) * self.tangent[1]

    def near(self, p: Point, r: float) -> bool:
        return g.length(p, self.point) < r


def _build_fence(ctx, loop: Sequence[Point], tag: str, height: float, gate_refs: list[_GateRef], detail: float,
                 signs: bool) -> None:
    """One complete fence line: instanced posts, merged panel/rail/wire mesh, gates, optional signs."""
    loop = g.ensure_ccw(loop)
    pts = g.resample_polyline(loop, POST_PITCH, closed=True)
    n = len(pts)
    if n < 3:
        return
    # ---- posts merged into the fence mesh (thousands of post objects would cripple the viewport) ----
    k = Kit(ctx, CHAINLINK_TILE)
    post_kit = fence_post_kit(ctx, height, detail)
    for i, p in enumerate(pts):
        if any(gr.near(p, GATE_HALF - 0.05) for gr in gate_refs):
            continue
        _t, nrm = _outward(pts, i)
        mat = Matrix.Translation((p[0], p[1], 0.0)) @ Matrix.Rotation(math.atan2(nrm[1], nrm[0]), 4, 'Z')
        k.append(post_kit, mat)

    # ---- fabric panels (same mesh) ----------------------------------------------
    z0, z1 = FABRIC_BOTTOM, height
    s_acc = 0.0
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        seg_len = g.length(a, b)
        mid = g.lerp(a, b, 0.5)
        ua, ub = s_acc, s_acc + seg_len
        s_acc += seg_len
        gr = next((gr for gr in gate_refs if gr.near(mid, GATE_HALF + POST_PITCH + 1.0)), None)
        if gr is not None:
            # along-distance of both ends from the gate centre (tangent = CCW direction, so da < db)
            da, db = gr.along(a), gr.along(b)
            if da > db:
                da, db = db, da
            if da >= -GATE_HALF and db <= GATE_HALF:
                continue                                   # fully inside the opening
            if da < -GATE_HALF < db:                       # straddles the first gate post: keep a -> post
                t_edge = (-GATE_HALF - da) / (db - da)
                b, ub = g.lerp(a, b, t_edge), ua + seg_len * t_edge
            elif da < GATE_HALF < db:                      # straddles the second gate post: keep post -> b
                t_edge = (GATE_HALF - da) / (db - da)
                a, ua = g.lerp(a, b, t_edge), ua + seg_len * t_edge
            if g.length(a, b) < 0.05:
                continue
        k.quad((a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1), 'ChainLink',
               uvs=[(ua / CHAINLINK_TILE, z0), (ub / CHAINLINK_TILE, z0), (ub / CHAINLINK_TILE, z1), (ua / CHAINLINK_TILE, z1)])

    # ---- rails and wires (same mesh) --------------------------------------------
    if detail >= 1.0:
        cuts = [gr.s for gr in gate_refs]
        # gate refs were computed on the un-resampled loop; recompute arc lengths on that loop for the cuts
        for run in _cut_runs(loop, cuts, GATE_HALF):
            k.tube([(p[0], p[1], height - RAIL_R - 0.01) for p in run], RAIL_R, 'Galvanized', 4, caps=True)
            k.tube([(p[0], p[1], FABRIC_BOTTOM + 0.06) for p in run], TENSION_WIRE_R, 'BarbedWire', 3, caps=False)
        c, s = math.cos(OUTRIGGER_ANGLE), math.sin(OUTRIGGER_ANGLE)
        for d in STRANDS:
            off = g.offset_loop(loop, d * c)
            zz = height - 0.05 + d * s
            for run in _cut_runs(off, cuts, GATE_HALF):
                k.tube([(p[0], p[1], zz) for p in run], BARBED_R, 'BarbedWire', 4, caps=False)
    if not k.is_empty():
        me = k.build(f"Perimeter_Fence_{tag}")
        ctx.add_object(f"Perimeter_Fence_{tag}", me, CATEGORY)

    # ---- gates ------------------------------------------------------------------
    for gi, gr in enumerate(gate_refs, 1):
        gk = Kit(ctx, 1.0)
        _gate_assembly(gk, height, detail)
        me = gk.build(f"Perimeter_Gate_{tag}_{gi}")
        ctx.add_object(f"Perimeter_Gate_{tag}_{gi}", me, CATEGORY, (gr.point[0], gr.point[1], 0.0),
                       math.atan2(gr.tangent[1], gr.tangent[0]) + (0.0 if _left_is_inward(gr) else math.pi))

    # ---- signs -------------------------------------------------------------------
    if signs and detail >= 1.0:
        sign_me = ctx.shared_mesh("Perimeter_FenceSign", lambda: fence_sign_mesh(ctx, detail))
        cum, total = _cumulative(loop)
        count = max(1, int(total / SIGN_PITCH))
        pitch = total / count
        k_sign = 0
        for j in range(count):
            sp = _point_at(loop, cum, pitch * (j + 0.5))
            if any(gr.near(sp, GATE_HALF + 4.0) for gr in gate_refs):
                continue
            _s, q, t = _closest_on_loop(loop, cum, sp)
            nrm = (t[1], -t[0])
            k_sign += 1
            ctx.add_instance(f"Perimeter_FenceSign_{k_sign}", sign_me, CATEGORY, (q[0] + nrm[0] * 0.02, q[1] + nrm[1] * 0.02, 0.0),
                             math.atan2(nrm[1], nrm[0]))


def _left_is_inward(gr: _GateRef) -> bool:
    left = g.perp_left(gr.tangent)
    return g.dot(left, gr.inward) > 0.0


def _gate_assembly(k: Kit, height: float, detail: float) -> None:
    """Two cantilever sliding leaves (half-open) + gate posts. Local frame: origin at the gate centre on the fence
    line, +X along the fence, +Y inward (the leaves run on the inside of the fence)."""
    c, s = math.cos(OUTRIGGER_ANGLE), math.sin(OUTRIGGER_ANGLE)
    # gate posts (heavier 120 mm tubes) with outrigger arms and caps
    for sx in (-1.0, 1.0):
        x = sx * GATE_HALF
        k.cylinder((x, 0.0), 0.06, GROUND_Z - 0.3, height + 0.05, 'Galvanized', segments=10)
        k.cylinder((x, 0.0), 0.075, height + 0.05, height + 0.09, 'Galvanized', segments=10)
        k.tube([(x, 0.0, height - 0.05), (x, -OUTRIGGER_LEN * c, height - 0.05 + OUTRIGGER_LEN * s)], 0.018, 'Galvanized', 4)
        k.cylinder((x, 0.0), 0.35, GROUND_Z - 0.02, GROUND_Z + 0.05, 'ConcreteBase', segments=10)
        # guide / catch post for the open leaf, 3.3 m outside the opening on the inside of the fence
        gx = sx * (GATE_HALF + 3.4)
        k.cylinder((gx, 0.55), 0.045, GROUND_Z - 0.3, height + 0.05, 'Galvanized', segments=8)
        k.cylinder((gx, 0.55), 0.06, height + 0.05, height + 0.09, 'Galvanized', segments=8)
    # leaves: 6.6 m frames, slid 3 m outward (half open); frame at y = 0.55 (inside the fence line)
    y = 0.55
    leaf_w = GATE_HALF + 0.6
    for sx in (-1.0, 1.0):
        x_in = sx * 3.0                 # leaf inner edge (toward the road centre)
        x_out = sx * (3.0 + leaf_w)     # leaf outer edge
        xa, xb = min(x_in, x_out), max(x_in, x_out)
        zb, zt = FABRIC_BOTTOM + 0.10, height
        for zz in (zb, zt):
            k.tube([(xa, y, zz), (xb, y, zz)], 0.03, 'Galvanized', 6)
        for xx in (xa, (xa + xb) * 0.5, xb):
            k.tube([(xx, y, zb), (xx, y, zt)], 0.03, 'Galvanized', 6)
        k.tube([(xa, y, zb), (xb, y, zt)], 0.012, 'Galvanized', 4, caps=False)      # diagonal truss rod
        k.quad((xa, y, zb), (xb, y, zb), (xb, y, zt), (xa, y, zt), 'ChainLink',
               uvs=[(xa, zb), (xb, zb), (xb, zt), (xa, zt)])
        # leaf top guard: 3 strands on short 45 deg arms leaning outward (-Y)
        for xx in (xa + 0.05, (xa + xb) * 0.5, xb - 0.05):
            k.tube([(xx, y, zt - 0.05), (xx, y - OUTRIGGER_LEN * c, zt - 0.05 + OUTRIGGER_LEN * s)], 0.015, 'Galvanized', 4)
        for d in STRANDS:
            zz = zt - 0.05 + d * s
            k.tube([(xa, y - d * c, zz), (xb, y - d * c, zz)], BARBED_R, 'BarbedWire', 4, caps=False)
        # bottom rollers on the fixed part (outside the opening)
        if detail >= 1.0:
            for xx in (sx * (GATE_HALF + 0.8), sx * (GATE_HALF + 3.0)):
                k.box((xx, y, GROUND_Z + 0.09), (0.30, 0.16, 0.18), 'MetalDark')
            k.box((sx * (GATE_HALF + 1.9), y, zb - 0.05), (leaf_w - 0.6, 0.10, 0.08), 'Galvanized')   # bottom track beam


# =========================================================================== ECP road furniture
def _ecp_furniture(ctx, road, detail: float, n: int) -> None:
    cl = road.centerline
    if len(cl) < 2:
        return
    p0, p1 = cl[0], cl[-1]
    d = g.normalize((p1[0] - p0[0], p1[1] - p0[1]))
    left = g.perp_left(d)
    ang = math.atan2(d[1], d[0])
    hw = road.width * 0.5
    length = g.length(p0, p1)

    def at(s_along: float, lat: float) -> Vec3:
        return (p0[0] + d[0] * s_along + left[0] * lat, p0[1] + d[1] * s_along + left[1] * lat, 0.0)

    # 1. serpentine of precast barriers in the approach zone (UFC 4-022-01 5-3: horizontal curvature for speed control)
    if detail >= 0.75:
        jb = ctx.shared_mesh("Perimeter_JerseyBarrier", lambda: jersey_barrier_mesh(ctx, detail))
        s_first, s_last = 8.0, min(34.0, length * 0.3)
        i = 0
        s_pos = s_first
        while s_pos <= s_last + 1e-6:
            side = 1.0 if i % 2 == 0 else -1.0
            i += 1
            lat = side * (hw - 1.35)
            ctx.add_instance(f"Perimeter_ECP{n}_Barrier_{i}", jb, CATEGORY, at(s_pos, lat), ang - side * math.radians(28.0))
            s_pos += JERSEY_PITCH
        # straight barrier pair flanking the approach lane entry just inside the gate
        for side in (1.0, -1.0):
            i += 1
            ctx.add_instance(f"Perimeter_ECP{n}_Barrier_{i}", jb, CATEGORY, at(3.5, side * (hw + 0.45)), ang)

    # 2. bollard line at the gate (passive barrier, UFC 4-022-01 5-5.2)
    bol = ctx.shared_mesh("Perimeter_Bollard", lambda: bollard_mesh(ctx))
    kb = 0
    for side in (1.0, -1.0):
        for j in range(4):
            kb += 1
            ctx.add_instance(f"Perimeter_ECP{n}_Bollard_{kb}", bol, CATEGORY, at(2.0, side * (hw + 0.7 + j * 1.0)), 0.0)

    # 3. drop-arm barrier across the road at the end of the access-control zone (behind the guard house)
    s_arm = min(57.0, length - 6.0)
    k = Kit(ctx, 1.0)
    pivot_y = -(hw + 0.45)              # inbound right-hand side
    k.box((0.0, pivot_y, GROUND_Z + 0.55), (0.45, 0.45, 1.1), 'MetalPainted', mat_top='MetalDark')
    k.box((0.0, pivot_y, GROUND_Z + 0.06), (0.9, 0.9, 0.12), 'ConcreteBase')
    k.box((0.0, pivot_y, GROUND_Z + 1.18), (0.55, 0.25, 0.16), 'MetalDark')                    # pivot head
    arm_z = GROUND_Z + 1.05
    y0, y1 = pivot_y + 0.3, hw + 0.2
    k.box((0.0, pivot_y - 0.45, arm_z), (0.16, 0.5, 0.35), 'MetalDark')                        # counterweight
    n_bands = max(4, int(round((y1 - y0) / 0.6)))
    k.banded_tube((0.0, y0, arm_z), (0.0, y1, arm_z), 0.055, n_bands, ('FixtureYellow', 'SignRed'), 8)
    # rest post with fork on the far side
    k.cylinder((0.0, hw + 0.45), 0.035, GROUND_Z - 0.05, arm_z - 0.1, 'Galvanized', segments=6)
    k.box((0.0, hw + 0.45, arm_z - 0.05), (0.16, 0.10, 0.10), 'MetalDark')
    for yy in (hw + 0.32, hw + 0.58):
        k.box((0.0, yy, arm_z + 0.02), (0.16, 0.04, 0.24), 'MetalDark')
    if detail >= 1.0:   # small red stop light on the pivot post
        k.sphere((0.25, pivot_y, GROUND_Z + 1.32), 0.07, 'LightRed', 8, 4)
        k.tube([(0.0, pivot_y, GROUND_Z + 1.26), (0.25, pivot_y, GROUND_Z + 1.26)], 0.02, 'MetalDark', 6)
    me = k.build(f"Perimeter_ECP{n}_DropArm", smooth_angle=math.radians(40))
    ctx.add_object(f"Perimeter_ECP{n}_DropArm", me, CATEGORY, at(s_arm, 0.0), ang)


# =========================================================================== culverts
def _ditch_lines(ctx) -> list[tuple[float, float, float]]:
    """Mirror of terrain._ditch_lines: (y, x_start, x_end) of every drainage swale."""
    s = ctx.settings.terrain
    if not (s.enabled and s.ditches):
        return []
    rw = ctx.plan.runway
    ov_lo = rw.overrun_length if rw.overrun_low is not None else 0.0
    ov_hi = rw.overrun_length if rw.overrun_high is not None else 0.0
    off = float(s.ditch_offset)
    if off <= rw.paved_half_w + 12.0:
        return []
    return [(off, -rw.half_len - ov_lo, rw.half_len + ov_hi), (-off, -rw.half_len - ov_lo, rw.half_len + ov_hi)]


def _culverts(ctx, detail: float) -> None:
    ditches = _ditch_lines(ctx)
    if not ditches:
        return
    me = None
    kc = 0
    for road in ctx.plan.roads:
        cl = list(road.centerline)
        if road.closed and len(cl) > 2:
            cl.append(cl[0])
        for i in range(len(cl) - 1):
            a, b = cl[i], cl[i + 1]
            for yd, xa, xb in ditches:
                if (a[1] - yd) * (b[1] - yd) >= 0.0 or abs(b[1] - a[1]) < 1e-9:
                    continue
                t = (yd - a[1]) / (b[1] - a[1])
                x = a[0] + (b[0] - a[0]) * t
                if not (xa - 20.0 <= x <= xb + 20.0):
                    continue
                d = g.normalize((b[0] - a[0], b[1] - a[1]))
                left = g.perp_left(d)
                if me is None:
                    me = ctx.shared_mesh("Perimeter_CulvertHeadwall", lambda: culvert_headwall_mesh(ctx, detail))
                for side in (1.0, -1.0):
                    kc += 1
                    off = road.width * 0.5 + 2.6
                    loc = (x + left[0] * side * off, yd + left[1] * side * off, 0.0)
                    ctx.add_instance(f"Perimeter_Culvert_{kc}", me, CATEGORY, loc, math.atan2(left[1] * side, left[0] * side))


# =========================================================================== entry point
def build(ctx) -> None:
    s = ctx.settings.perimeter
    plan = ctx.plan
    detail = ctx.detail
    fence = plan.fence
    if fence is not None and s.fence and len(fence.loop) >= 3:
        try:
            outer = g.ensure_ccw(fence.loop)
            height = float(fence.height)
            cum, _total = _cumulative(outer)
            refs_outer = [_GateRef(outer, cum, pos, out) for pos, out in fence.gates]
            _build_fence(ctx, outer, "Outer", height, refs_outer, detail, signs=True)
            if fence.double and fence.gap > 0.5:
                inner = g.offset_loop(outer, -float(fence.gap))
                cum_i, _ = _cumulative(inner)
                refs_inner = [_GateRef(inner, cum_i, (pos[0] - out[0] * fence.gap, pos[1] - out[1] * fence.gap), out)
                              for pos, out in fence.gates]
                _build_fence(ctx, inner, "Inner", height, refs_inner, detail, signs=False)
        except Exception as exc:
            traceback.print_exc()
            ctx.warn(f"perimeter: fence: {type(exc).__name__}: {exc}")
    if s.entry_control_point:
        n = 0
        for road in plan.roads:
            if road.kind != 'ECP':
                continue
            n += 1
            try:
                _ecp_furniture(ctx, road, detail, n)
            except Exception as exc:
                traceback.print_exc()
                ctx.warn(f"perimeter: ECP furniture: {type(exc).__name__}: {exc}")
    try:
        _culverts(ctx, detail)
    except Exception as exc:
        traceback.print_exc()
        ctx.warn(f"perimeter: culverts: {type(exc).__name__}: {exc}")
