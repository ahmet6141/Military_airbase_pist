"""Building kit shared by the structure builders: material palette wrapper, solid primitives, small props.

Everything here is pure geometry in a *local* frame (structure centre at the origin, local +X =
"forward"); the caller places the finished mesh with ``ctx.add_object(..., location, rotation_z)``.
Orientation rules (materials are back-face culled in the viewport): every visible face is built with
its normal pointing out of the solid. Helpers that take a 2D profile in the (y, z) plane expect the
polygon in any winding; they normalise it themselves.
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

import bpy
from mathutils import Matrix, Vector

from ..core import geom2d as g
from ..core import scene_utils as su
from ..core.meshbuild import MeshBuilder, box_uv, planar_uv

Vec3 = tuple[float, float, float]
Point = tuple[float, float]

GROUND_Z = -0.03          # infield terrain level (core.constants.DIRT_STEP)
SLAB_THICK = 0.30         # standard slab thickness under every building (brief)
SLAB_TOP = 0.15           # plinth: building slabs protrude 15 cm above grade
PAD_TOP = 0.005           # pads that aircraft / vehicles roll onto sit flush with pavement (+5 mm)
EPS = 0.002


# --------------------------------------------------------------------------- palette wrapper
class Part:
    """MeshBuilder plus a named material palette: ``part.m('ConcreteWall')`` -> material index."""

    def __init__(self, ctx, uv_tile: float = 2.0):
        self.ctx = ctx
        self.mb = MeshBuilder(default_uv=planar_uv(uv_tile))
        self.names: list[str] = []
        self.tile = uv_tile

    # materials ------------------------------------------------------------
    def m(self, name: str) -> int:
        try:
            return self.names.index(name)
        except ValueError:
            self.names.append(name)
            return len(self.names) - 1

    def build(self, mesh_name: str, smooth_angle: float | None = None) -> bpy.types.Mesh:
        mats = [self.ctx.mats(n) for n in self.names]
        return self.mb.build("MAD_" + mesh_name, materials=mats, smooth_angle=smooth_angle)

    def append(self, other: "Part", matrix: Matrix | None = None) -> None:
        """Merge another Part (remapping its palette into ours)."""
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

    def tri_count(self) -> int:
        return sum(len(f) - 2 for f in self.mb.faces)

    # basic solids -----------------------------------------------------------
    def box(self, center: Vec3, size: Vec3, mat: str, angle: float = 0.0, mat_top: str | None = None,
            mat_bottom: str | None = None, uv_tile: float | None = None) -> None:
        self.mb.add_box(center, size, self.m(mat), angle,
                        mat_top=None if mat_top is None else self.m(mat_top),
                        mat_bottom=None if mat_bottom is None else self.m(mat_bottom),
                        uv_tile=uv_tile or self.tile)

    def box_xyz(self, x0: float, x1: float, y0: float, y1: float, z0: float, z1: float, mat: str, **kw) -> None:
        """Box from min/max extents."""
        self.box(((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2), (abs(x1 - x0), abs(y1 - y0), abs(z1 - z0)), mat, **kw)

    def prism(self, poly: Sequence[Point], z0: float, z1: float, mat_side: str, mat_top: str | None = None,
              holes: Sequence[Sequence[Point]] = (), bottom: bool = False, uv_tile: float | None = None) -> None:
        t = uv_tile or self.tile
        self.mb.add_prism(poly, z0, z1, mat_top=self.m(mat_top or mat_side), mat_side=self.m(mat_side),
                          mat_bottom=self.m(mat_side) if bottom else None, holes=holes,
                          side_uv=box_uv(t), uv_fn=planar_uv(t))

    def slab(self, cx: float, cy: float, sx: float, sy: float, mat: str = 'PadConcrete', top: float = SLAB_TOP,
             thick: float = SLAB_THICK, margin: float = 1.0, angle: float = 0.0) -> None:
        """Concrete footprint slab (protrudes ``margin`` beyond the building, ``thick`` deep)."""
        poly = g.rect(cx, cy, sx + 2 * margin, sy + 2 * margin, angle)
        self.prism(poly, top - thick, top, mat, uv_tile=4.0)

    def cylinder(self, center: Point, radius: float, z0: float, z1: float, mat: str, segments: int = 16,
                 cap_top: bool = True, cap_bottom: bool = False, smooth: bool = True, radius_top: float | None = None,
                 angle0: float = 0.0, uv_tile: float | None = None) -> None:
        self.mb.add_cylinder(center, radius, z0, z1, segments, self.m(mat), cap_top, cap_bottom, smooth, radius_top,
                             angle0, uv_tile or self.tile)

    def tube(self, path: Sequence[Vec3], radius: float, mat: str, segments: int = 8, caps: bool = True,
             smooth: bool = True) -> None:
        self.mb.add_tube(path, radius, segments, self.m(mat), smooth, caps)

    def torus(self, center: Vec3, major: float, minor: float, mat: str, seg_major: int = 24, seg_minor: int = 8,
              axis: str = 'Z') -> None:
        self.mb.add_torus(center, major, minor, seg_major, seg_minor, self.m(mat), axis)

    def sphere(self, center: Vec3, radius: float, mat: str, seg_u: int = 12, seg_v: int = 6) -> None:
        self.mb.add_sphere(center, radius, seg_u, seg_v, self.m(mat))

    def quad(self, a: Vec3, b: Vec3, c: Vec3, d: Vec3, mat: str, uv_tile: float | None = None) -> None:
        """Single quad (a,b,c,d CCW seen from the side the normal points to), UVs = box mapping."""
        self.mb.add_quad(a, b, c, d, self.m(mat), False, uv_fn=box_uv(uv_tile or self.tile))

    def plate(self, center: Vec3, size: Vec3, mat: str, rot_x: float = 0.0, rot_y: float = 0.0, rot_z: float = 0.0,
              uv_tile: float | None = None) -> None:
        """Box rotated about all three axes (roof slopes, louvres, tilted deflector plates)."""
        tmp = MeshBuilder()
        tmp.add_box((0.0, 0.0, 0.0), size, 0, uv_tile=uv_tile or self.tile)
        mat_idx = self.m(mat)
        mtx = Matrix.Translation(Vector(center)) @ Matrix.Rotation(rot_z, 4, 'Z') @ Matrix.Rotation(rot_y, 4, 'Y') @ Matrix.Rotation(rot_x, 4, 'X')
        base = len(self.mb.verts)
        for v in tmp.verts:
            w = mtx @ Vector(v)
            self.mb.verts.append((w.x, w.y, w.z))
        for f, s, uv in zip(tmp.faces, tmp.face_smooth, tmp.loop_uv):
            self.mb.faces.append(tuple(i + base for i in f))
            self.mb.face_mat.append(mat_idx)
            self.mb.face_smooth.append(s)
            self.mb.loop_uv.append(uv)
            self.mb.loop_col.append(None)

    # profile solids ---------------------------------------------------------
    def yz_prism(self, poly_yz: Sequence[Point], x0: float, x1: float, mat_side: str, mat_cap: str | None = None,
                 holes: Sequence[Sequence[Point]] = (), smooth: bool = False, uv_tile: float | None = None,
                 cap_start: bool = True, cap_end: bool = True, cap_mat_start: str | None = None) -> None:
        """Extrude a polygon given in the (y, z) plane along +X from x0 to x1, with correctly oriented caps.

        (``MeshBuilder.add_profile_extrusion`` caps are inverted for arch profiles, so caps are built here.)
        """
        if x1 < x0:
            x0, x1 = x1, x0
        t = uv_tile or self.tile
        ms = self.m(mat_side)
        mc = self.m(mat_cap or mat_side)
        mcs = self.m(cap_mat_start) if cap_mat_start else mc
        pts, tris = g.triangulate(poly_yz, holes)
        mb = self.mb
        if cap_end:      # +X face: CCW in (y, z) -> normal +X
            base = len(mb.verts)
            for y, z in pts:
                mb.add_vertex((x1, y, z))
            for a, b, c in tris:
                mb.add_face_idx((base + a, base + b, base + c), mc, False,
                                uvs=[(pts[i][0] / t, pts[i][1] / t) for i in (a, b, c)])
        if cap_start:    # -X face
            base = len(mb.verts)
            for y, z in pts:
                mb.add_vertex((x0, y, z))
            for a, b, c in tris:
                mb.add_face_idx((base + a, base + c, base + b), mcs, False,
                                uvs=[(-pts[i][0] / t, pts[i][1] / t) for i in (a, c, b)])
        loops = [g.ensure_ccw(poly_yz)] + [g.ensure_ccw(h)[::-1] for h in holes]
        for lp in loops:
            n = len(lp)
            acc = 0.0
            for i in range(n):
                a, b = lp[i], lp[(i + 1) % n]
                seg = math.hypot(b[0] - a[0], b[1] - a[1])
                if seg < 1e-9:
                    continue
                # (x0,a) (x0,b) (x1,b) (x1,a): normal = (b-a) x (+X) = outward for a CCW loop
                mb.add_face(((x0, a[0], a[1]), (x0, b[0], b[1]), (x1, b[0], b[1]), (x1, a[0], a[1])), ms, smooth,
                            uvs=[(x0 / t, acc / t), (x0 / t, (acc + seg) / t), (x1 / t, (acc + seg) / t), (x1 / t, acc / t)])
                acc += seg

    def xz_prism(self, poly_xz: Sequence[Point], y0: float, y1: float, mat_side: str, mat_cap: str | None = None,
                 holes: Sequence[Sequence[Point]] = (), smooth: bool = False, uv_tile: float | None = None) -> None:
        """Extrude a polygon given in the (x, z) plane along +Y (gable end walls, etc.)."""
        tmp = Part(self.ctx, uv_tile or self.tile)
        tmp.yz_prism(poly_xz, y0, y1, mat_side, mat_cap, holes, smooth, uv_tile)
        # rotate: local X(extrusion) -> Y, local Y(profile u) -> X : (x, y, z) -> (y, x, z) is a reflection; use a
        # proper rotation about Z by -90 deg (x,y)->(y,-x) then mirror handled by reversing winding.
        rot = Matrix.Rotation(-math.pi / 2, 4, 'Z')
        base = len(self.mb.verts)
        remap = [self.m(n) for n in tmp.names]
        for v in tmp.mb.verts:
            w = rot @ Vector(v)
            self.mb.verts.append((w.x, -w.y, w.z))       # mirror y back so the profile u axis maps onto +X
        for f, mi, s, uv in zip(tmp.mb.faces, tmp.mb.face_mat, tmp.mb.face_smooth, tmp.mb.loop_uv):
            self.mb.faces.append(tuple(i + base for i in reversed(f)))   # mirror flips winding: reverse it
            self.mb.face_mat.append(remap[mi])
            self.mb.face_smooth.append(s)
            self.mb.loop_uv.append(list(reversed(uv)))
            self.mb.loop_col.append(None)

    def profile_x(self, profile: Sequence[Point], x0: float, x1: float, mat: str, smooth: bool = True,
                  close: bool = False, uv_tile: float | None = None) -> None:
        """Open/closed (y, z) profile swept along +X (arches, berms). Profile runs left->right over the top."""
        self.mb.add_profile_extrusion(profile, min(x0, x1), max(x0, x1), self.m(mat), smooth, close, uv_tile or self.tile)

    def loft(self, sections: Sequence[tuple[float, Sequence[Point]]], mat: str, smooth: bool = True,
             uv_tile: float | None = None, close: bool = False) -> None:
        """Quads between consecutive (x, profile) sections of equal point count (earth mounds, tapered hulls).

        Profiles run left->right over the top (y increasing) so normals point outward; x must increase.
        """
        t = uv_tile or self.tile
        mi = self.m(mat)
        mb = self.mb
        rings: list[list[int]] = []
        for x, prof in sections:
            rings.append([mb.add_vertex((x, y, z)) for y, z in prof])
        n = len(sections[0][1])
        count = n if close else n - 1
        for (xa, pa), (xb, pb), ra, rb in zip(sections, sections[1:], rings, rings[1:]):
            acc = 0.0
            for i in range(count):
                j = (i + 1) % n
                seg = math.hypot(pa[j][0] - pa[i][0], pa[j][1] - pa[i][1])
                mb.add_face_idx((ra[i], rb[i], rb[j], ra[j]), mi, smooth,
                                uvs=[(xa / t, acc / t), (xb / t, acc / t), (xb / t, (acc + seg) / t), (xa / t, (acc + seg) / t)])
                acc += seg

    def ring(self, inner: Sequence[Vec3], outer: Sequence[Vec3], mat: str, uv_tile: float | None = None) -> None:
        self.mb.add_ring(inner, outer, self.m(mat), uv_fn=planar_uv(uv_tile or self.tile))

    def polygon(self, poly: Sequence[Point], z: float, mat: str, holes: Sequence[Sequence[Point]] = (),
                flip: bool = False, uv_tile: float | None = None) -> None:
        self.mb.add_polygon(poly, z, self.m(mat), holes=holes, flip=flip, uv_fn=planar_uv(uv_tile or self.tile))

    # compound helpers -------------------------------------------------------
    def wall(self, p0: Point, p1: Point, z0: float, height: float, thick: float, mat: str, chamfer: float = 0.0,
             mat_top: str | None = None) -> None:
        """Straight wall between two plan points with an optional chamfered top (revetments, kerbs)."""
        d = g.normalize((p1[0] - p0[0], p1[1] - p0[1]))
        length = g.length(p0, p1)
        ht = thick * 0.5
        if chamfer <= 0.0:
            prof = [(-ht, z0), (ht, z0), (ht, z0 + height), (-ht, z0 + height)]
        else:
            c = min(chamfer, ht * 0.9, height * 0.4)
            prof = [(-ht, z0), (ht, z0), (ht, z0 + height - c), (ht - c, z0 + height), (-ht + c, z0 + height), (-ht, z0 + height - c)]
        tmp = Part(self.ctx, self.tile)
        tmp.yz_prism(prof, 0.0, length, mat, mat_top or mat)
        ang = math.atan2(d[1], d[0])
        self.append(tmp, Matrix.Translation((p0[0], p0[1], 0.0)) @ Matrix.Rotation(ang, 4, 'Z'))

    def panel(self, center: Vec3, w: float, h: float, mat: str, facing: str = '+X', proud: float = 0.03,
              thick: float | None = None) -> None:
        """Thin rectangular panel standing on a wall face (doors, window strips, signs).

        ``facing`` is the outward direction of the wall face ('+X', '-X', '+Y', '-Y'); the panel is centred on
        ``center`` (a point on the wall face) and protrudes ``proud`` from it.
        """
        t = thick if thick is not None else proud * 2.0
        cx, cy, cz = center
        if facing in ('+X', '-X'):
            s = 1.0 if facing == '+X' else -1.0
            self.box((cx + s * (proud - t / 2), cy, cz), (t, w, h), mat)
        else:
            s = 1.0 if facing == '+Y' else -1.0
            self.box((cx, cy + s * (proud - t / 2), cz), (w, t, h), mat)

    def window_band(self, cx: float, cy: float, sx: float, sy: float, z0: float, z1: float, mat: str = 'Glass',
                    faces: str = 'XY', inset_ends: float = 1.0, proud: float = 0.02) -> None:
        """Continuous glazing strip around a rectangular building (all 4 faces or only 'X' / 'Y' faces)."""
        hx, hy = sx * 0.5, sy * 0.5
        zc, h = (z0 + z1) * 0.5, z1 - z0
        if 'X' in faces:
            self.panel((cx + hx, cy, zc), sy - 2 * inset_ends, h, mat, '+X', proud)
            self.panel((cx - hx, cy, zc), sy - 2 * inset_ends, h, mat, '-X', proud)
        if 'Y' in faces:
            self.panel((cx, cy + hy, zc), sx - 2 * inset_ends, h, mat, '+Y', proud)
            self.panel((cx, cy - hy, zc), sx - 2 * inset_ends, h, mat, '-Y', proud)

    def parapet(self, cx: float, cy: float, sx: float, sy: float, z0: float, height: float = 0.5, thick: float = 0.3,
                mat: str = 'ConcreteWall') -> None:
        outer = g.rect(cx, cy, sx, sy)
        inner = g.rect(cx, cy, sx - 2 * thick, sy - 2 * thick)
        self.prism(outer, z0, z0 + height, mat, holes=[inner])

    def canopy(self, cx: float, cy: float, sx: float, sy: float, z_clear: float, thick: float = 0.4,
               col: float = 0.35, mat_roof: str = 'RoofMembrane', mat_fascia: str = 'Trim', mat_col: str = 'MetalPainted',
               col_inset: float = 0.6) -> None:
        """Flat canopy slab on four columns (fuel fill stand, ECP lanes, building entrances)."""
        hx, hy = sx * 0.5 - col_inset, sy * 0.5 - col_inset
        for sx_, sy_ in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            self.box((cx + sx_ * hx, cy + sy_ * hy, z_clear * 0.5), (col, col, z_clear), mat_col)
        self.box((cx, cy, z_clear + thick * 0.5), (sx, sy, thick), mat_fascia, mat_top=mat_roof, mat_bottom=mat_fascia)

    def gable_roof(self, cx: float, cy: float, sx: float, sy: float, z_eave: float, z_ridge: float, ridge_axis: str = 'X',
                   overhang: float = 0.4, thick: float = 0.25, mat: str = 'RoofMembrane', mat_edge: str = 'Trim') -> None:
        """Two sloped roof slabs; ridge runs along ``ridge_axis`` through (cx, cy)."""
        rise = z_ridge - z_eave
        if ridge_axis == 'X':
            half = sy * 0.5
            slope_len = math.hypot(half + overhang, rise * (half + overhang) / half)
            ang = math.atan2(rise, half)
            for s in (1, -1):
                yc = cy + s * (half + overhang) * 0.5
                zc = z_eave + rise * 0.5 + thick * 0.5
                self.plate((cx, yc, zc), (sx + 2 * overhang, slope_len, thick), mat, rot_x=-s * ang)
        else:
            half = sx * 0.5
            slope_len = math.hypot(half + overhang, rise * (half + overhang) / half)
            ang = math.atan2(rise, half)
            for s in (1, -1):
                xc = cx + s * (half + overhang) * 0.5
                zc = z_eave + rise * 0.5 + thick * 0.5
                self.plate((xc, cy, zc), (slope_len, sy + 2 * overhang, thick), mat, rot_y=s * ang)

    def lattice_tower(self, cx: float, cy: float, z0: float, z1: float, base_half: float, top_half: float,
                      leg_r: float = 0.08, brace_r: float = 0.04, pitch: float = 2.0, mat: str = 'Galvanized',
                      detail: float = 1.0, segments: int = 6) -> None:
        """Four-leg tapered lattice (beacon tower, guard tower, masts): legs + X-braces every ``pitch`` metres."""
        h = z1 - z0

        def corner(k: int, z: float) -> Vec3:
            f = (z - z0) / max(h, 1e-6)
            hw = base_half + (top_half - base_half) * f
            sx, sy = ((1, 1), (-1, 1), (-1, -1), (1, -1))[k % 4]
            return (cx + sx * hw, cy + sy * hw, z)

        for k in range(4):
            self.tube([corner(k, z0), corner(k, z1)], leg_r, mat, segments)
        if detail < 0.75:
            n = max(1, int(h / (pitch * 2)))
            for i in range(1, n + 1):
                z = z0 + h * i / (n + 0.0001)
                for k in range(4):
                    self.tube([corner(k, z), corner(k + 1, z)], brace_r, mat, 4, caps=False)
            return
        n = max(1, int(round(h / pitch)))
        for i in range(n):
            za, zb = z0 + h * i / n, z0 + h * (i + 1) / n
            for k in range(4):
                a0, b0 = corner(k, za), corner(k + 1, za)
                a1, b1 = corner(k, zb), corner(k + 1, zb)
                self.tube([a0, b1], brace_r, mat, 4, caps=False)
                self.tube([b0, a1], brace_r, mat, 4, caps=False)
                if i == n - 1 or i == 0:
                    self.tube([a1, b1], brace_r, mat, 4, caps=False)

    def ladder(self, x: float, y: float, z0: float, z1: float, facing: str = '+X', width: float = 0.5,
               mat: str = 'Galvanized', rung_pitch: float = 0.3) -> None:
        """Vertical access ladder standing off a wall face."""
        hw = width * 0.5
        if facing in ('+X', '-X'):
            rails = [((x, y - hw, z0), (x, y - hw, z1)), ((x, y + hw, z0), (x, y + hw, z1))]
            rung = lambda z: [(x, y - hw, z), (x, y + hw, z)]
        else:
            rails = [((x - hw, y, z0), (x - hw, y, z1)), ((x + hw, y, z0), (x + hw, y, z1))]
            rung = lambda z: [(x - hw, y, z), (x + hw, y, z)]
        for a, b in rails:
            self.tube([a, b], 0.025, mat, 4)
        n = int((z1 - z0) / rung_pitch)
        for i in range(1, n + 1):
            self.tube(rung(z0 + i * rung_pitch), 0.015, mat, 4, caps=False)

    def handrail(self, pts: Sequence[Vec3], height: float = 1.1, post_pitch: float = 1.5, mat: str = 'Galvanized',
                 closed: bool = False) -> None:
        pts = list(pts)
        if closed:
            pts.append(pts[0])
        top = [(p[0], p[1], p[2] + height) for p in pts]
        self.tube(top, 0.025, mat, 4, caps=not closed)
        mid = [(p[0], p[1], p[2] + height * 0.5) for p in pts]
        self.tube(mid, 0.015, mat, 4, caps=not closed)
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            seg = math.dist(a, b)
            n = max(1, int(seg / post_pitch))
            for k in range(n + (0 if closed else 1)):
                t = k / n
                p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)
                self.tube([p, (p[0], p[1], p[2] + height)], 0.02, mat, 4, caps=False)

    def text(self, text: str, height: float, mat: str, origin: Vec3, facing: str = '-X', offset: float = 0.005,
             bold: bool = True) -> None:
        """Painted lettering standing on a vertical face (shelter numbers, signs). ``origin`` is the text centre."""
        me = su.text_mesh("txt", text, height / 0.7, bold=bold)      # cap height ~0.7 x font size
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


# --------------------------------------------------------------------------- profiles
def arch_profile(half_w: float, height: float, n: int, exponent: float = 2.3, z0: float = 0.0) -> list[Point]:
    """Super-elliptical arch in the (y, z) plane from (-half_w, z0) over the crown to (+half_w, z0).

    exponent 2 = ellipse (semicircle when height == half_w); 2.2-2.5 approximates the NATO 3rd-generation
    "double-radius pseudo-elliptical" shelter section (steeper flanks, flatter crown); ~1.6 gives an earth mound.
    """
    pts: list[Point] = []
    k = 2.0 / exponent
    for i in range(n + 1):
        t = math.pi * (1.0 - i / n)
        c, s = math.cos(t), math.sin(t)
        y = half_w * math.copysign(abs(c) ** k, c)
        z = z0 + height * (abs(s) ** k)
        pts.append((y, z))
    pts[0] = (-half_w, z0)
    pts[-1] = (half_w, z0)
    return pts


def arch_z(half_w: float, height: float, y: float, exponent: float = 2.3, z0: float = 0.0) -> float:
    """Height of the arch profile at lateral position y (0 outside the span)."""
    u = abs(y) / half_w
    if u >= 1.0:
        return z0
    return z0 + height * (1.0 - u ** exponent) ** (1.0 / exponent)


def arch_y(half_w: float, height: float, z: float, exponent: float = 2.3, z0: float = 0.0) -> float:
    """Half-width of the arch profile at height z."""
    v = (z - z0) / height
    if v >= 1.0:
        return 0.0
    if v <= 0.0:
        return half_w
    return half_w * (1.0 - v ** exponent) ** (1.0 / exponent)


# --------------------------------------------------------------------------- shared prop factories
def bollard_mesh(ctx) -> bpy.types.Mesh:
    """Yellow safety bollard: 200 mm steel post 1.0 m tall on a small concrete footing."""
    p = Part(ctx)
    p.cylinder((0.0, 0.0), 0.10, 0.0, 1.0, 'Bollard', segments=10, cap_top=True)
    p.cylinder((0.0, 0.0), 0.22, -0.02, 0.08, 'ConcreteBase', segments=10)
    return p.build("Structures_Bollard", smooth_angle=math.radians(40))


def extinguisher_cart_mesh(ctx) -> bpy.types.Mesh:
    """150 lb wheeled flightline extinguisher: red cylinder on a two-wheel frame."""
    p = Part(ctx)
    p.cylinder((0.0, 0.0), 0.22, 0.25, 1.35, 'DoorRed', segments=12, cap_top=True)
    p.sphere((0.0, 0.0, 1.35), 0.22, 'DoorRed', 12, 4)
    p.torus((0.0, 0.38, 0.32), 0.26, 0.06, 'RubberBlack', 16, 6, axis='Y')
    p.torus((0.0, -0.38, 0.32), 0.26, 0.06, 'RubberBlack', 16, 6, axis='Y')
    p.tube([(0.0, -0.38, 0.32), (0.0, 0.38, 0.32)], 0.03, 'MetalDark', 6)
    p.tube([(-0.25, 0.0, 0.3), (-0.9, 0.0, 0.3)], 0.025, 'MetalDark', 6)
    return p.build("Structures_ExtinguisherCart", smooth_angle=math.radians(40))


def donut_mesh(ctx) -> bpy.types.Mesh:
    """BAK-12 pendant support disc: 150 mm rubber donut, 60 mm thick, axis along the cable (Y)."""
    p = Part(ctx)
    p.torus((0.0, 0.0, 0.0), 0.045, 0.030, 'RubberBlack', 14, 6, axis='Y')
    return p.build("Structures_CableDonut")


def fence_post_mesh(ctx, height: float = 2.4) -> bpy.types.Mesh:
    p = Part(ctx)
    p.cylinder((0.0, 0.0), 0.04, -0.05, height, 'Galvanized', segments=6, cap_top=True)
    return p.build("Structures_FencePost")


def ray_to_pavement(plan, origin: Point, direction: Point, max_dist: float = 150.0, step: float = 1.5) -> float | None:
    """Distance along a ray until it enters any pavement polygon of the plan (None if nothing within range)."""
    d = g.normalize(direction)
    polys = [(p, g.bbox(p)) for p, _zone in plan.pavement]
    t = 0.0
    while t <= max_dist:
        x, y = origin[0] + d[0] * t, origin[1] + d[1] * t
        for poly, (x0, y0, x1, y1) in polys:
            if x0 - 0.5 <= x <= x1 + 0.5 and y0 - 0.5 <= y <= y1 + 0.5 and g.point_in_poly((x, y), poly):
                return t
        t += step
    return None


def rxy(x0: float, x1: float, y0: float, y1: float) -> list[Point]:
    """Axis-aligned rectangle from x/y extents (note: ``geom2d.rect_xy`` takes corners (x0, y0, x1, y1))."""
    return g.rect_xy(x0, y0, x1, y1)


def kind_label(kind: str) -> str:
    """'FIRE_STATION' -> 'FireStation' (object name fragment); acronyms stay upper-case."""
    if kind in ('HAS', 'ECP'):
        return kind
    return "".join(w.capitalize() for w in kind.lower().split('_'))
