"""MeshBuilder: accumulate geometry in plain Python lists, emit one Blender mesh.

Every builder in the addon (pavement, markings, fixtures, structures) produces
geometry through this class so that UVs, vertex colours, material indices and
smoothing are handled in exactly one place.

Conventions
-----------
* Coordinates are metres in the *base frame* (runway along +X, see geom2d).
* UVs default to planar world mapping ``(x, y) / tile`` so tiling PBR textures
  read at real-world scale in Blender and in game engines alike (the tiling is
  baked into the UVs — no Mapping node is required after export).
* Vertex colours live in a FLOAT_COLOR corner attribute named ``Wear`` and are
  data masks, not colours: R = rubber / soot, G = dirt & edge grime,
  B = macro tonal variation, A = paint wear (markings).
"""
from __future__ import annotations

import math
from typing import Callable, Iterable, Sequence

import bpy
from mathutils import Matrix, Vector

from . import geom2d

Vec3 = tuple[float, float, float]
UVFn = Callable[[float, float, float], tuple[float, float]]
ColorFn = Callable[[float, float, float], tuple[float, float, float, float]]

COLOR_ATTR = "Wear"
UV_NAME = "UVMap"


def planar_uv(tile: float, angle: float = 0.0, offset: tuple[float, float] = (0.0, 0.0)) -> UVFn:
    """World-planar UV function: 1 texture repeat every ``tile`` metres."""
    inv = 1.0 / max(tile, 1e-6)
    if abs(angle) < 1e-9:
        return lambda x, y, z: ((x + offset[0]) * inv, (y + offset[1]) * inv)
    c, s = math.cos(angle), math.sin(angle)
    return lambda x, y, z: (((x * c - y * s) + offset[0]) * inv, ((x * s + y * c) + offset[1]) * inv)


def box_uv(tile: float) -> UVFn:
    """Tri-planar-ish UV for walls: uses the dominant horizontal axis + Z."""
    inv = 1.0 / max(tile, 1e-6)
    return lambda x, y, z: ((x + y) * inv, z * inv)


class MeshBuilder:
    def __init__(self, default_uv: UVFn | None = None, default_color: tuple[float, float, float, float] | None = None):
        self.verts: list[Vec3] = []
        self.faces: list[tuple[int, ...]] = []
        self.face_mat: list[int] = []
        self.face_smooth: list[bool] = []
        self.loop_uv: list[list[tuple[float, float]]] = []
        self.loop_col: list[list[tuple[float, float, float, float]] | None] = []
        self.default_uv: UVFn = default_uv or planar_uv(1.0)
        self.default_color = default_color
        self.has_colors = default_color is not None

    # ------------------------------------------------------------------ raw
    def add_vertex(self, p: Vec3) -> int:
        self.verts.append((float(p[0]), float(p[1]), float(p[2])))
        return len(self.verts) - 1

    def add_face_idx(self, idx: Sequence[int], mat: int = 0, smooth: bool = False,
                     uvs: Sequence[tuple[float, float]] | None = None,
                     colors: Sequence[tuple[float, float, float, float]] | None = None,
                     uv_fn: UVFn | None = None, color_fn: ColorFn | None = None) -> int:
        idx = tuple(int(i) for i in idx)
        if len(idx) < 3:
            return -1
        self.faces.append(idx)
        self.face_mat.append(mat)
        self.face_smooth.append(smooth)
        if uvs is None:
            fn = uv_fn or self.default_uv
            uvs = [fn(*self.verts[i]) for i in idx]
        self.loop_uv.append([(float(u), float(v)) for u, v in uvs])
        if colors is None:
            if color_fn is not None:
                colors = [color_fn(*self.verts[i]) for i in idx]
            elif self.default_color is not None:
                colors = [self.default_color] * len(idx)
        if colors is not None:
            self.has_colors = True
        self.loop_col.append(list(colors) if colors is not None else None)
        return len(self.faces) - 1

    def add_face(self, pts: Sequence[Vec3], mat: int = 0, smooth: bool = False, **kw) -> int:
        idx = [self.add_vertex(p) for p in pts]
        return self.add_face_idx(idx, mat, smooth, **kw)

    def add_quad(self, a: Vec3, b: Vec3, c: Vec3, d: Vec3, mat: int = 0, smooth: bool = False, **kw) -> int:
        return self.add_face((a, b, c, d), mat, smooth, **kw)

    # ------------------------------------------------------------------ 2D -> 3D
    def add_polygon(self, poly: Sequence[geom2d.Point], z: float = 0.0, mat: int = 0,
                    holes: Sequence[Sequence[geom2d.Point]] = (), flip: bool = False,
                    z_fn: Callable[[float, float], float] | None = None, **kw) -> list[int]:
        """Flat (or z_fn-displaced) filled polygon, triangulated. Returns face indices."""
        pts, tris = geom2d.triangulate(poly, holes)
        base = len(self.verts)
        for p in pts:
            zz = z if z_fn is None else z + z_fn(p[0], p[1])
            self.add_vertex((p[0], p[1], zz))
        out = []
        for a, b, c in tris:
            idx = (base + a, base + c, base + b) if flip else (base + a, base + b, base + c)
            out.append(self.add_face_idx(idx, mat, False, **kw))
        return out

    def add_triangles(self, pts: Sequence[geom2d.Point], tris: Sequence[tuple[int, int, int]],
                      z_fn: Callable[[float, float], float] | float = 0.0,
                      mat_fn: Callable[[int], int] | int = 0, **kw) -> list[int]:
        """Add a pre-triangulated 2D soup (e.g. from geom2d.union_triangulate)."""
        base = len(self.verts)
        for p in pts:
            zz = z_fn(p[0], p[1]) if callable(z_fn) else z_fn
            self.add_vertex((p[0], p[1], zz))
        out = []
        for i, (a, b, c) in enumerate(tris):
            m = mat_fn(i) if callable(mat_fn) else mat_fn
            out.append(self.add_face_idx((base + a, base + b, base + c), m, False, **kw))
        return out

    def add_ring(self, inner: Sequence[Vec3], outer: Sequence[Vec3], mat: int = 0, **kw) -> list[int]:
        """Quads between two closed loops of equal length (shoulders, kerbs, dirt strips)."""
        n = len(inner)
        assert n == len(outer) and n >= 3
        bi = len(self.verts)
        for p in inner:
            self.add_vertex(p)
        bo = len(self.verts)
        for p in outer:
            self.add_vertex(p)
        out = []
        for i in range(n):
            j = (i + 1) % n
            out.append(self.add_face_idx((bi + i, bo + i, bo + j, bi + j), mat, False, **kw))
        return out

    def add_ring_open(self, inner: Sequence[Vec3], outer: Sequence[Vec3], mat: int = 0, **kw) -> list[int]:
        """Quads between two open polylines of equal length."""
        n = len(inner)
        bi = len(self.verts)
        for p in inner:
            self.add_vertex(p)
        bo = len(self.verts)
        for p in outer:
            self.add_vertex(p)
        out = []
        for i in range(n - 1):
            out.append(self.add_face_idx((bi + i, bo + i, bo + i + 1, bi + i + 1), mat, False, **kw))
        return out

    # ------------------------------------------------------------------ solids
    def add_prism(self, poly: Sequence[geom2d.Point], z0: float, z1: float, mat_top: int = 0,
                  mat_side: int | None = None, mat_bottom: int | None = None,
                  holes: Sequence[Sequence[geom2d.Point]] = (), side_uv: UVFn | None = None,
                  smooth_sides: bool = False, **kw) -> None:
        """Extrude a 2D polygon between z0 and z1 (top, sides, optional bottom)."""
        poly = geom2d.ensure_ccw(poly)
        mat_side = mat_top if mat_side is None else mat_side
        self.add_polygon(poly, z1, mat_top, holes=holes, **kw)
        if mat_bottom is not None:
            self.add_polygon(poly, z0, mat_bottom, holes=holes, flip=True, **kw)
        loops = [poly] + [geom2d.ensure_ccw(h)[::-1] for h in holes]
        suv = side_uv or box_uv(1.0)
        for lp in loops:
            n = len(lp)
            for i in range(n):
                a, b = lp[i], lp[(i + 1) % n]
                self.add_face(((a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1)),
                              mat_side, smooth_sides, uv_fn=suv)

    def add_box(self, center: Vec3, size: Vec3, mat: int = 0, angle: float = 0.0,
                mat_top: int | None = None, mat_bottom: int | None = None, uv_tile: float = 1.0) -> None:
        """Axis-aligned box (rotated about Z by ``angle``) centred at ``center``."""
        hx, hy, hz = size[0] * 0.5, size[1] * 0.5, size[2] * 0.5
        poly = geom2d.rect(center[0], center[1], size[0], size[1], angle)
        self.add_prism(poly, center[2] - hz, center[2] + hz,
                       mat_top=mat if mat_top is None else mat_top, mat_side=mat,
                       mat_bottom=mat if mat_bottom is None else mat_bottom,
                       side_uv=box_uv(uv_tile), uv_fn=planar_uv(uv_tile))

    def add_cylinder(self, center: geom2d.Point, radius: float, z0: float, z1: float, segments: int = 16,
                     mat: int = 0, cap_top: bool = True, cap_bottom: bool = False, smooth: bool = True,
                     radius_top: float | None = None, angle0: float = 0.0, uv_tile: float = 1.0) -> None:
        """Vertical cylinder / frustum."""
        rt = radius if radius_top is None else radius_top
        bottom = geom2d.regular_polygon(center, radius, segments, angle0)
        top = geom2d.regular_polygon(center, rt, segments, angle0)
        bb = len(self.verts)
        for p in bottom:
            self.add_vertex((p[0], p[1], z0))
        bt = len(self.verts)
        for p in top:
            self.add_vertex((p[0], p[1], z1))
        circ = 2 * math.pi * radius
        for i in range(segments):
            j = (i + 1) % segments
            u0 = circ * i / segments / uv_tile
            u1 = circ * (i + 1) / segments / uv_tile
            self.add_face_idx((bb + i, bb + j, bt + j, bt + i), mat, smooth,
                              uvs=[(u0, z0 / uv_tile), (u1, z0 / uv_tile), (u1, z1 / uv_tile), (u0, z1 / uv_tile)])
        if cap_top and rt > 1e-6:
            self.add_face_idx(tuple(range(bt, bt + segments)), mat, False, uv_fn=planar_uv(uv_tile))
        elif cap_top:  # cone apex
            apex = self.add_vertex((center[0], center[1], z1))
            for i in range(segments):
                j = (i + 1) % segments
                self.add_face_idx((bb + i, bb + j, apex), mat, smooth, uv_fn=planar_uv(uv_tile))
        if cap_bottom:
            self.add_face_idx(tuple(reversed(range(bb, bb + segments))), mat, False, uv_fn=planar_uv(uv_tile))

    def add_tube(self, path: Sequence[Vec3], radius: float, segments: int = 8, mat: int = 0,
                 smooth: bool = True, caps: bool = True) -> None:
        """Round tube along a 3D polyline (cables, pipes, handrails, wires)."""
        if len(path) < 2:
            return
        rings: list[list[int]] = []
        prev_n = None
        for i, p in enumerate(path):
            if i == 0:
                d = Vector(path[1]) - Vector(path[0])
            elif i == len(path) - 1:
                d = Vector(path[-1]) - Vector(path[-2])
            else:
                d = (Vector(path[i + 1]) - Vector(path[i - 1]))
            if d.length < 1e-9:
                d = Vector((1, 0, 0))
            d.normalize()
            up = Vector((0, 0, 1)) if abs(d.z) < 0.9 else Vector((1, 0, 0))
            n1 = d.cross(up).normalized()
            if prev_n is not None and n1.dot(prev_n) < 0:
                n1 = -n1
            prev_n = n1
            n2 = d.cross(n1).normalized()
            ring = []
            for k in range(segments):
                a = 2 * math.pi * k / segments
                q = Vector(p) + (n1 * math.cos(a) + n2 * math.sin(a)) * radius
                ring.append(self.add_vertex((q.x, q.y, q.z)))
            rings.append(ring)
        for r0, r1 in zip(rings, rings[1:]):
            for k in range(segments):
                j = (k + 1) % segments
                self.add_face_idx((r0[k], r0[j], r1[j], r1[k]), mat, smooth,
                                  uvs=[(k / segments, 0), ((k + 1) / segments, 0), ((k + 1) / segments, 1), (k / segments, 1)])
        if caps:
            self.add_face_idx(tuple(reversed(rings[0])), mat, False, uvs=[(0, 0)] * segments)
            self.add_face_idx(tuple(rings[-1]), mat, False, uvs=[(0, 0)] * segments)

    def add_torus(self, center: Vec3, major: float, minor: float, seg_major: int = 24, seg_minor: int = 8,
                  mat: int = 0, axis: str = 'Z') -> None:
        """Torus (rubber donuts on arresting cables, rings, tyres)."""
        rings = []
        for i in range(seg_major):
            a = 2 * math.pi * i / seg_major
            ring = []
            for k in range(seg_minor):
                b = 2 * math.pi * k / seg_minor
                r = major + minor * math.cos(b)
                x, y, z = r * math.cos(a), r * math.sin(a), minor * math.sin(b)
                if axis == 'X':
                    x, y, z = z, x, y
                elif axis == 'Y':
                    x, y, z = x, z, y
                ring.append(self.add_vertex((center[0] + x, center[1] + y, center[2] + z)))
            rings.append(ring)
        for i in range(seg_major):
            r0, r1 = rings[i], rings[(i + 1) % seg_major]
            for k in range(seg_minor):
                j = (k + 1) % seg_minor
                self.add_face_idx((r0[k], r1[k], r1[j], r0[j]), mat, True,
                                  uvs=[(i / seg_major, k / seg_minor), ((i + 1) / seg_major, k / seg_minor),
                                       ((i + 1) / seg_major, (k + 1) / seg_minor), (i / seg_major, (k + 1) / seg_minor)])

    def add_sphere(self, center: Vec3, radius: float, seg_u: int = 16, seg_v: int = 8, mat: int = 0) -> None:
        rings = []
        for i in range(1, seg_v):
            phi = math.pi * i / seg_v
            ring = []
            for k in range(seg_u):
                th = 2 * math.pi * k / seg_u
                ring.append(self.add_vertex((center[0] + radius * math.sin(phi) * math.cos(th),
                                             center[1] + radius * math.sin(phi) * math.sin(th),
                                             center[2] + radius * math.cos(phi))))
            rings.append(ring)
        top = self.add_vertex((center[0], center[1], center[2] + radius))
        bot = self.add_vertex((center[0], center[1], center[2] - radius))
        for k in range(seg_u):
            j = (k + 1) % seg_u
            self.add_face_idx((top, rings[0][k], rings[0][j]), mat, True, uvs=[(0, 1)] * 3)
            self.add_face_idx((bot, rings[-1][j], rings[-1][k]), mat, True, uvs=[(0, 0)] * 3)
        for r0, r1 in zip(rings, rings[1:]):
            for k in range(seg_u):
                j = (k + 1) % seg_u
                self.add_face_idx((r0[k], r1[k], r1[j], r0[j]), mat, True, uvs=[(0, 0.5)] * 4)

    def add_profile_extrusion(self, profile: Sequence[tuple[float, float]], x0: float, x1: float, mat: int = 0,
                              smooth: bool = True, close: bool = False, uv_tile: float = 1.0,
                              cap_start: bool = False, cap_end: bool = False, cap_mat: int | None = None) -> None:
        """Extrude a (y, z) profile polyline along +X from x0 to x1 (arches, kerbs, berms).

        Faces are oriented so that normals point away from the profile's interior when
        the profile runs left-to-right over the top (y increasing).
        """
        n = len(profile)
        r0 = [self.add_vertex((x0, y, z)) for y, z in profile]
        r1 = [self.add_vertex((x1, y, z)) for y, z in profile]
        acc = 0.0
        count = n if close else n - 1
        for i in range(count):
            j = (i + 1) % n
            seg = math.hypot(profile[j][0] - profile[i][0], profile[j][1] - profile[i][1])
            v0, v1 = acc / uv_tile, (acc + seg) / uv_tile
            self.add_face_idx((r0[i], r1[i], r1[j], r0[j]), mat, smooth,
                              uvs=[(x0 / uv_tile, v0), (x1 / uv_tile, v0), (x1 / uv_tile, v1), (x0 / uv_tile, v1)])
            acc += seg
        cm = mat if cap_mat is None else cap_mat
        if cap_start:
            self.add_face_idx(tuple(reversed(r0)), cm, False, uvs=[(y / uv_tile, z / uv_tile) for y, z in reversed(profile)])
        if cap_end:
            self.add_face_idx(tuple(r1), cm, False, uvs=[(y / uv_tile, z / uv_tile) for y, z in profile])

    # ------------------------------------------------------------------ composition
    def append(self, other: "MeshBuilder", matrix: Matrix | None = None, mat_offset: int = 0) -> None:
        """Merge another builder (optionally transformed) into this one."""
        base = len(self.verts)
        if matrix is None:
            self.verts.extend(other.verts)
        else:
            for v in other.verts:
                w = matrix @ Vector(v)
                self.verts.append((w.x, w.y, w.z))
        for f, m, s, uv, col in zip(other.faces, other.face_mat, other.face_smooth, other.loop_uv, other.loop_col):
            self.faces.append(tuple(i + base for i in f))
            self.face_mat.append(m + mat_offset)
            self.face_smooth.append(s)
            self.loop_uv.append(uv)
            self.loop_col.append(col)
            if col is not None:
                self.has_colors = True

    def translate(self, dx: float, dy: float, dz: float) -> None:
        self.verts = [(x + dx, y + dy, z + dz) for x, y, z in self.verts]

    def transform(self, matrix: Matrix) -> None:
        out = []
        for v in self.verts:
            w = matrix @ Vector(v)
            out.append((w.x, w.y, w.z))
        self.verts = out

    def is_empty(self) -> bool:
        return not self.faces

    def bounds(self) -> tuple[Vec3, Vec3]:
        if not self.verts:
            return (0, 0, 0), (0, 0, 0)
        xs = [v[0] for v in self.verts]
        ys = [v[1] for v in self.verts]
        zs = [v[2] for v in self.verts]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    # ------------------------------------------------------------------ output
    def build(self, name: str, materials: Sequence[bpy.types.Material | None] = (),
              smooth_angle: float | None = None, validate: bool = True) -> bpy.types.Mesh:
        """Create the Blender mesh datablock."""
        me = bpy.data.meshes.new(name)
        me.from_pydata(self.verts, [], [list(f) for f in self.faces])
        n_faces = len(me.polygons)
        if n_faces:
            me.polygons.foreach_set("material_index", [max(0, m) for m in self.face_mat[:n_faces]])
            me.polygons.foreach_set("use_smooth", self.face_smooth[:n_faces])
            uv = me.uv_layers.new(name=UV_NAME)
            flat = []
            for f_uv in self.loop_uv[:n_faces]:
                for u, v in f_uv:
                    flat.append(u)
                    flat.append(v)
            if len(flat) == 2 * len(me.loops):
                uv.data.foreach_set("uv", flat)
            if self.has_colors:
                ca = me.color_attributes.new(name=COLOR_ATTR, type='FLOAT_COLOR', domain='CORNER')
                flat_c = []
                fallback = self.default_color or (0.0, 0.0, 0.0, 1.0)
                for f, col in zip(self.faces[:n_faces], self.loop_col[:n_faces]):
                    if col is None:
                        col = [fallback] * len(f)
                    for c in col:
                        flat_c.extend((c[0], c[1], c[2], c[3] if len(c) > 3 else 1.0))
                if len(flat_c) == 4 * len(me.loops):
                    ca.data.foreach_set("color", flat_c)
        for m in materials:
            me.materials.append(m)
        if validate:
            me.validate(verbose=False)
        me.update()
        if smooth_angle is not None and n_faces:
            mark_sharp_by_angle(me, smooth_angle)
        return me


def mark_sharp_by_angle(me: bpy.types.Mesh, angle: float) -> None:
    """Emulate 'Auto Smooth' (removed in Blender 4.1): smooth faces + sharp edges over ``angle``."""
    import numpy as np
    n_poly = len(me.polygons)
    if n_poly == 0:
        return
    me.polygons.foreach_set("use_smooth", [True] * n_poly)
    normals = np.empty(n_poly * 3, dtype=np.float32)
    me.polygons.foreach_get("normal", normals)
    normals = normals.reshape(-1, 3)
    edge_faces: dict[tuple[int, int], list[int]] = defaultdict(list)
    for p in me.polygons:
        for ek in p.edge_keys:
            edge_faces[ek].append(p.index)
    edge_index = {e.key: e.index for e in me.edges}
    sharp = np.zeros(len(me.edges), dtype=bool)
    cos_lim = math.cos(angle)
    for ek, fs in edge_faces.items():
        if len(fs) == 2:
            d = float(np.dot(normals[fs[0]], normals[fs[1]]))
            if d < cos_lim:
                sharp[edge_index[ek]] = True
        elif len(fs) == 1:
            pass
    attr = me.attributes.get("sharp_edge") or me.attributes.new("sharp_edge", 'BOOLEAN', 'EDGE')
    attr.data.foreach_set("value", sharp.tolist())
    me.update()


from collections import defaultdict  # noqa: E402  (used by mark_sharp_by_angle)
