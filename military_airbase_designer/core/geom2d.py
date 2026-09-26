"""2D geometry kernel used by every pavement / marking / layout builder.

Conventions
-----------
* A ``Point`` is an ``(x, y)`` tuple in metres, in the *runway frame*:
  +X runs along the runway from the low-numbered end towards the high-numbered
  end, +Y is to the left of that direction, origin at the runway centre.
* A ``Poly`` is a closed simple polygon given as a list of points with
  counter-clockwise (CCW) orientation. Functions accept any orientation and
  normalise it where it matters.
* Polygon union is done with Blender's constrained Delaunay triangulation
  (``mathutils.geometry.delaunay_2d_cdt``).  The ``output_type`` argument is
  not honoured by every Blender build (4.2.0 ignores it), so the union is
  derived from the ``orig_faces`` mapping instead, which is reliable.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Callable, Iterable, Sequence

from mathutils import Vector
from mathutils.geometry import delaunay_2d_cdt, tessellate_polygon

Point = tuple[float, float]
Poly = list[Point]

TWO_PI = math.tau
EPS = 1e-7


# --------------------------------------------------------------------------- basics
def signed_area(poly: Sequence[Point]) -> float:
    a = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return 0.5 * a


def area(poly: Sequence[Point]) -> float:
    return abs(signed_area(poly))


def ensure_ccw(poly: Sequence[Point]) -> Poly:
    pts = [(float(p[0]), float(p[1])) for p in poly]
    return pts if signed_area(pts) >= 0.0 else pts[::-1]


def centroid(poly: Sequence[Point]) -> Point:
    a = signed_area(poly)
    if abs(a) < EPS:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    cx = cy = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    f = 1.0 / (6.0 * a)
    return (cx * f, cy * f)


def bbox(points: Iterable[Point]) -> tuple[float, float, float, float]:
    """(min_x, min_y, max_x, max_y) of a point cloud / polygon list."""
    xs, ys = [], []
    for p in points:
        xs.append(p[0])
        ys.append(p[1])
    return (min(xs), min(ys), max(xs), max(ys))


def bbox_polys(polys: Iterable[Sequence[Point]]) -> tuple[float, float, float, float]:
    return bbox(p for poly in polys for p in poly)


def rotate_point(p: Point, angle: float, origin: Point = (0.0, 0.0)) -> Point:
    c, s = math.cos(angle), math.sin(angle)
    x, y = p[0] - origin[0], p[1] - origin[1]
    return (origin[0] + x * c - y * s, origin[1] + x * s + y * c)


def transform_poly(poly: Sequence[Point], angle: float = 0.0, offset: Point = (0.0, 0.0),
                   origin: Point = (0.0, 0.0)) -> Poly:
    """Rotate about ``origin`` then translate by ``offset``."""
    if abs(angle) < EPS:
        return [(p[0] + offset[0], p[1] + offset[1]) for p in poly]
    c, s = math.cos(angle), math.sin(angle)
    out = []
    for p in poly:
        x, y = p[0] - origin[0], p[1] - origin[1]
        out.append((origin[0] + x * c - y * s + offset[0], origin[1] + x * s + y * c + offset[1]))
    return out


def mirror_y(poly: Sequence[Point]) -> Poly:
    """Mirror across the X axis (runway centreline) keeping CCW orientation."""
    return ensure_ccw([(p[0], -p[1]) for p in poly])


def mirror_x(poly: Sequence[Point]) -> Poly:
    """Mirror across the Y axis (runway midpoint) keeping CCW orientation."""
    return ensure_ccw([(-p[0], p[1]) for p in poly])


def length(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def normalize(v: Point) -> Point:
    l = math.hypot(v[0], v[1])
    if l < EPS:
        return (0.0, 0.0)
    return (v[0] / l, v[1] / l)


def perp_left(v: Point) -> Point:
    """Left-hand perpendicular of a direction."""
    return (-v[1], v[0])


def lerp(a: Point, b: Point, t: float) -> Point:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


# --------------------------------------------------------------------------- primitives
def rect(cx: float, cy: float, w: float, h: float, angle: float = 0.0) -> Poly:
    """Rectangle centred at (cx, cy), size w (along local X) x h, rotated by angle."""
    hw, hh = w * 0.5, h * 0.5
    pts = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    return transform_poly(pts, angle, (cx, cy))


def rect_xy(x0: float, y0: float, x1: float, y1: float) -> Poly:
    """Axis-aligned rectangle from two corners (any order)."""
    xa, xb = (x0, x1) if x0 <= x1 else (x1, x0)
    ya, yb = (y0, y1) if y0 <= y1 else (y1, y0)
    return [(xa, ya), (xb, ya), (xb, yb), (xa, yb)]


def strip(p0: Point, p1: Point, width: float, extend0: float = 0.0, extend1: float = 0.0) -> Poly:
    """Rectangle of ``width`` along the segment p0->p1, optionally extended past the ends."""
    d = normalize((p1[0] - p0[0], p1[1] - p0[1]))
    n = perp_left(d)
    hw = width * 0.5
    a = (p0[0] - d[0] * extend0, p0[1] - d[1] * extend0)
    b = (p1[0] + d[0] * extend1, p1[1] + d[1] * extend1)
    return ensure_ccw([
        (a[0] - n[0] * hw, a[1] - n[1] * hw),
        (b[0] - n[0] * hw, b[1] - n[1] * hw),
        (b[0] + n[0] * hw, b[1] + n[1] * hw),
        (a[0] + n[0] * hw, a[1] + n[1] * hw),
    ])


def arc_points(center: Point, radius: float, a0: float, a1: float,
               segments: int | None = None, max_seg_len: float = 1.5, min_segments: int = 4) -> list[Point]:
    """Points along an arc from angle a0 to a1 (radians, signed sweep), ends inclusive."""
    sweep = a1 - a0
    if segments is None:
        arc_len = abs(sweep) * radius
        segments = max(min_segments, int(math.ceil(arc_len / max(max_seg_len, 1e-3))))
    out = []
    for i in range(segments + 1):
        a = a0 + sweep * i / segments
        out.append((center[0] + radius * math.cos(a), center[1] + radius * math.sin(a)))
    return out


def circle(center: Point, radius: float, segments: int = 32) -> Poly:
    return [(center[0] + radius * math.cos(TWO_PI * i / segments),
             center[1] + radius * math.sin(TWO_PI * i / segments)) for i in range(segments)]


def ring_sector(center: Point, r_inner: float, r_outer: float, a0: float, a1: float,
                segments: int | None = None) -> Poly:
    """Annular sector (used for curved marking lines and curved taxiway edges)."""
    outer = arc_points(center, r_outer, a0, a1, segments)
    inner = arc_points(center, r_inner, a0, a1, len(outer) - 1)
    return ensure_ccw(outer + inner[::-1])


def regular_polygon(center: Point, radius: float, sides: int, angle0: float = 0.0) -> Poly:
    return [(center[0] + radius * math.cos(angle0 + TWO_PI * i / sides),
             center[1] + radius * math.sin(angle0 + TWO_PI * i / sides)) for i in range(sides)]


def fillet_corner(vertex: Point, dir_a: Point, dir_b: Point, radius: float,
                  max_seg_len: float = 1.5) -> Poly | None:
    """Fillet patch between two pavement edges meeting at ``vertex``.

    ``dir_a`` / ``dir_b`` are unit directions *away* from the vertex along the two
    edges. The returned polygon is bounded by the two edges (from the vertex to
    the tangent points) and the arc tangent to both. Returns None for
    degenerate (collinear) input.
    """
    da, db = normalize(dir_a), normalize(dir_b)
    cos_t = max(-1.0, min(1.0, dot(da, db)))
    theta = math.acos(cos_t)             # angle between the two edges
    if theta < 1e-4 or abs(math.pi - theta) < 1e-4:
        return None
    half = theta * 0.5
    t = radius / math.tan(half)           # distance from vertex to tangent points
    bis = normalize((da[0] + db[0], da[1] + db[1]))
    cdist = radius / math.sin(half)
    center = (vertex[0] + bis[0] * cdist, vertex[1] + bis[1] * cdist)
    ta = (vertex[0] + da[0] * t, vertex[1] + da[1] * t)
    tb = (vertex[0] + db[0] * t, vertex[1] + db[1] * t)
    ang_a = math.atan2(ta[1] - center[1], ta[0] - center[0])
    ang_b = math.atan2(tb[1] - center[1], tb[0] - center[0])
    sweep = ang_b - ang_a
    while sweep > math.pi:
        sweep -= TWO_PI
    while sweep < -math.pi:
        sweep += TWO_PI
    arc = arc_points(center, radius, ang_a, ang_a + sweep, max_seg_len=max_seg_len)
    return ensure_ccw([vertex] + arc)


def fillet_tangent_length(theta: float, radius: float) -> float:
    """Distance from a corner vertex to the fillet tangent point for edge angle theta."""
    return radius / math.tan(theta * 0.5)


# --------------------------------------------------------------------------- polylines
def round_polyline(points: Sequence[Point], radius: float, closed: bool = False,
                   max_seg_len: float = 2.0) -> list[Point]:
    """Replace every corner of a polyline by a tangent arc of ``radius`` (clamped to fit)."""
    n = len(points)
    if n < 3 or radius <= 0.0:
        return list(points)
    out: list[Point] = []
    rng = range(n) if closed else range(1, n - 1)
    if not closed:
        out.append(points[0])
    for i in rng:
        p_prev = points[(i - 1) % n]
        p = points[i]
        p_next = points[(i + 1) % n]
        da = normalize((p_prev[0] - p[0], p_prev[1] - p[1]))
        db = normalize((p_next[0] - p[0], p_next[1] - p[1]))
        cos_t = max(-1.0, min(1.0, dot(da, db)))
        theta = math.acos(cos_t)
        if theta < 1e-3 or abs(math.pi - theta) < 1e-3:
            out.append(p)
            continue
        half = theta * 0.5
        max_t = 0.5 * min(length(p, p_prev), length(p, p_next))
        r = min(radius, max_t * math.tan(half))
        t = r / math.tan(half)
        bis = normalize((da[0] + db[0], da[1] + db[1]))
        center = (p[0] + bis[0] * r / math.sin(half), p[1] + bis[1] * r / math.sin(half))
        ta = (p[0] + da[0] * t, p[1] + da[1] * t)
        tb = (p[0] + db[0] * t, p[1] + db[1] * t)
        ang_a = math.atan2(ta[1] - center[1], ta[0] - center[0])
        ang_b = math.atan2(tb[1] - center[1], tb[0] - center[0])
        sweep = ang_b - ang_a
        while sweep > math.pi:
            sweep -= TWO_PI
        while sweep < -math.pi:
            sweep += TWO_PI
        out.extend(arc_points(center, r, ang_a, ang_a + sweep, max_seg_len=max_seg_len))
    if not closed:
        out.append(points[-1])
    return out


def offset_polyline(points: Sequence[Point], dist: float, closed: bool = False) -> list[Point]:
    """Offset a polyline to its left by ``dist`` (negative = right) using mitre joins."""
    n = len(points)
    if n == 0:
        return []
    if n == 1:
        return [points[0]]
    out: list[Point] = []
    for i in range(n):
        if closed:
            p_prev, p, p_next = points[(i - 1) % n], points[i], points[(i + 1) % n]
            d0 = normalize((p[0] - p_prev[0], p[1] - p_prev[1]))
            d1 = normalize((p_next[0] - p[0], p_next[1] - p[1]))
        else:
            p = points[i]
            d0 = normalize((p[0] - points[i - 1][0], p[1] - points[i - 1][1])) if i > 0 else None
            d1 = normalize((points[i + 1][0] - p[0], points[i + 1][1] - p[1])) if i < n - 1 else None
            if d0 is None:
                d0 = d1
            if d1 is None:
                d1 = d0
        n0, n1 = perp_left(d0), perp_left(d1)
        sx, sy = n0[0] + n1[0], n0[1] + n1[1]
        denom = 1.0 + dot(n0, n1)
        if denom < 0.1:                  # very sharp corner: fall back to bevel-ish average
            m = normalize((sx, sy))
            out.append((p[0] + m[0] * dist * 3.0, p[1] + m[1] * dist * 3.0))
        else:
            f = dist / denom
            out.append((p[0] + sx * f, p[1] + sy * f))
    return out


def polyline_strip(points: Sequence[Point], width: float, closed: bool = False) -> Poly | list[Poly]:
    """Closed polygon(s) covering a polyline with the given width.

    For an open polyline a single polygon is returned; for a closed loop the
    result is a *pair* [outer, inner] describing a ring (use with union_triangulate
    as polygon + hole, or with ring quads).
    """
    hw = width * 0.5
    left = offset_polyline(points, hw, closed)
    right = offset_polyline(points, -hw, closed)
    if closed:
        outer = ensure_ccw(left if signed_area(left) > signed_area(right) else right)
        inner = ensure_ccw(right if signed_area(left) > signed_area(right) else left)
        return [outer, inner]
    return ensure_ccw(left + right[::-1])


def resample_polyline(points: Sequence[Point], step: float, closed: bool = False) -> list[Point]:
    """Points at (approximately) even spacing along a polyline, including both ends."""
    pts = list(points)
    if closed and len(pts) > 1:
        pts.append(pts[0])
    if len(pts) < 2:
        return pts
    total = sum(length(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    count = max(1, int(round(total / max(step, 1e-6))))
    real_step = total / count
    out = [pts[0]]
    seg = 0
    acc = 0.0
    target = real_step
    while seg < len(pts) - 1 and len(out) < count:
        seg_len = length(pts[seg], pts[seg + 1])
        if acc + seg_len >= target - 1e-9:
            t = (target - acc) / seg_len if seg_len > 0 else 0.0
            out.append(lerp(pts[seg], pts[seg + 1], t))
            target += real_step
        else:
            acc += seg_len
            seg += 1
    if not closed:
        out.append(pts[-1])
    return out


def polyline_length(points: Sequence[Point], closed: bool = False) -> float:
    total = sum(length(points[i], points[i + 1]) for i in range(len(points) - 1))
    if closed and len(points) > 1:
        total += length(points[-1], points[0])
    return total


def point_along(points: Sequence[Point], dist: float) -> tuple[Point, Point]:
    """Point and unit tangent at arc-length ``dist`` along an open polyline (clamped)."""
    acc = 0.0
    for i in range(len(points) - 1):
        seg = length(points[i], points[i + 1])
        if acc + seg >= dist or i == len(points) - 2:
            t = 0.0 if seg < EPS else max(0.0, min(1.0, (dist - acc) / seg))
            d = normalize((points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1]))
            return lerp(points[i], points[i + 1], t), d
        acc += seg
    return points[0], (1.0, 0.0)


# --------------------------------------------------------------------------- loop offsets
def offset_loop(loop: Sequence[Point], dist: float, miter_limit: float = 4.0) -> Poly:
    """Offset a closed loop keeping the vertex count (mitre joins, clamped).

    Positive ``dist`` moves a CCW loop outwards (and a CW hole loop into the
    hole), i.e. always *away* from the pavement region.
    """
    n = len(loop)
    out: list[Point] = []
    for i in range(n):
        p_prev, p, p_next = loop[(i - 1) % n], loop[i], loop[(i + 1) % n]
        d0 = normalize((p[0] - p_prev[0], p[1] - p_prev[1]))
        d1 = normalize((p_next[0] - p[0], p_next[1] - p[1]))
        # outward normal of a CCW loop is the right-hand perpendicular
        n0 = (d0[1], -d0[0])
        n1 = (d1[1], -d1[0])
        sx, sy = n0[0] + n1[0], n0[1] + n1[1]
        denom = 1.0 + dot(n0, n1)
        if denom < 1e-6:
            m = normalize((sx, sy)) if (abs(sx) + abs(sy)) > EPS else n0
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


# --------------------------------------------------------------------------- predicates
def point_in_poly(pt: Point, poly: Sequence[Point]) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / ((yj - yi) or 1e-30) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


def dist_point_segment(p: Point, a: Point, b: Point) -> float:
    abx, aby = b[0] - a[0], b[1] - a[1]
    l2 = abx * abx + aby * aby
    if l2 < EPS:
        return length(p, a)
    t = max(0.0, min(1.0, ((p[0] - a[0]) * abx + (p[1] - a[1]) * aby) / l2))
    return length(p, (a[0] + abx * t, a[1] + aby * t))


def dist_point_poly_edge(p: Point, poly: Sequence[Point]) -> float:
    n = len(poly)
    return min(dist_point_segment(p, poly[i], poly[(i + 1) % n]) for i in range(n))


def line_intersection(p1: Point, p2: Point, p3: Point, p4: Point) -> Point | None:
    """Intersection of infinite lines p1p2 and p3p4 (None if parallel)."""
    d = (p1[0] - p2[0]) * (p3[1] - p4[1]) - (p1[1] - p2[1]) * (p3[0] - p4[0])
    if abs(d) < 1e-12:
        return None
    t = ((p1[0] - p3[0]) * (p3[1] - p4[1]) - (p1[1] - p3[1]) * (p3[0] - p4[0])) / d
    return (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))


# --------------------------------------------------------------------------- triangulation
def triangulate(poly: Sequence[Point], holes: Sequence[Sequence[Point]] = ()) -> tuple[list[Point], list[tuple[int, int, int]]]:
    """Ear-clip style triangulation of a simple polygon (optionally with holes).

    Returns (points, triangles) where triangles index into points.
    """
    loops = [ensure_ccw(poly)] + [ensure_ccw(h)[::-1] for h in holes]
    vec_loops = [[Vector(p) for p in lp] for lp in loops]
    tris = tessellate_polygon(vec_loops)
    pts: list[Point] = [p for lp in loops for p in lp]
    out = []
    for t in tris:
        a, b, c = t
        # keep CCW
        if signed_area([pts[a], pts[b], pts[c]]) < 0:
            out.append((a, c, b))
        else:
            out.append((a, b, c))
    return pts, out


def union_triangulate(polys: Sequence[Sequence[Point]], holes: Sequence[Sequence[Point]] = (),
                      extra_points: Sequence[Point] = (), epsilon: float = 1e-4
                      ) -> tuple[list[Point], list[tuple[int, int, int]], list[frozenset[int]]]:
    """Triangulate the union of ``polys`` minus ``holes``.

    Returns (points, triangles, origins). ``origins[i]`` is the set of input
    polygon indices covering triangle ``i`` (used to pick materials / zones by
    priority). ``extra_points`` are optional interior refinement points; the ones
    that fall inside the union become mesh vertices, others are discarded.
    """
    verts: list[Vector] = []
    faces: list[list[int]] = []
    for poly in polys:
        p = ensure_ccw(poly)
        if len(p) < 3 or area(p) < 1e-9:
            faces.append([])   # keep index alignment
            continue
        base = len(verts)
        verts.extend(Vector(q) for q in p)
        faces.append(list(range(base, base + len(p))))
    n_polys = len(polys)
    for hole in holes:
        p = ensure_ccw(hole)
        if len(p) < 3:
            faces.append([])
            continue
        base = len(verts)
        verts.extend(Vector(q) for q in p)
        faces.append(list(range(base, base + len(p))))
    faces_in = [f for f in faces if f]
    index_map = [i for i, f in enumerate(faces) if f]   # cdt face index -> our index
    verts.extend(Vector(q) for q in extra_points)
    if not faces_in:
        return [], [], []
    out_v, _out_e, out_f, _ov, _oe, out_of = delaunay_2d_cdt(verts, [], faces_in, 1, epsilon, True)

    pts = [(v.x, v.y) for v in out_v]
    tris: list[tuple[int, int, int]] = []
    origins: list[frozenset[int]] = []
    for f, orig in zip(out_f, out_of):
        if not orig:
            continue
        ids = {index_map[o] for o in orig}
        if any(i >= n_polys for i in ids):      # covered by a hole
            continue
        if len(f) == 3:
            tri_list = [tuple(f)]
        else:   # fan (should not happen with type 1, but be safe)
            tri_list = [(f[0], f[i], f[i + 1]) for i in range(1, len(f) - 1)]
        for t in tri_list:
            if signed_area([pts[t[0]], pts[t[1]], pts[t[2]]]) < 0:
                t = (t[0], t[2], t[1])
            tris.append(t)
            origins.append(frozenset(ids))
    # compact unused vertices
    used = sorted({i for t in tris for i in t})
    remap = {old: new for new, old in enumerate(used)}
    pts2 = [pts[i] for i in used]
    tris2 = [(remap[a], remap[b], remap[c]) for a, b, c in tris]
    return pts2, tris2, origins


def boundary_loops(tris: Sequence[tuple[int, int, int]]) -> list[list[int]]:
    """Closed boundary loops (as vertex index lists) of a triangle soup.

    Loops inherit the triangle winding: outer boundaries come out CCW, hole
    boundaries CW, which is exactly what ``offset_loop`` expects.
    """
    counter: Counter = Counter()
    directed: dict[tuple[int, int], int] = {}
    for a, b, c in tris:
        for u, v in ((a, b), (b, c), (c, a)):
            counter[(min(u, v), max(u, v))] += 1
            directed[(u, v)] = 1
    nxt: dict[int, list[int]] = defaultdict(list)
    for (u, v) in directed:
        if counter[(min(u, v), max(u, v))] == 1:
            nxt[u].append(v)
    loops: list[list[int]] = []
    visited: set[tuple[int, int]] = set()
    for start in list(nxt.keys()):
        for first in nxt[start]:
            if (start, first) in visited:
                continue
            loop = [start]
            u, v = start, first
            visited.add((u, v))
            guard = 0
            while v != start and guard < 1_000_000:
                loop.append(v)
                cands = [w for w in nxt.get(v, []) if (v, w) not in visited]
                if not cands:
                    break
                w = cands[0]
                visited.add((v, w))
                u, v = v, w
                guard += 1
            if len(loop) >= 3:
                loops.append(loop)
    return loops


def lattice_points(x0: float, y0: float, x1: float, y1: float, step: float,
                   angle: float = 0.0, origin: Point = (0.0, 0.0), jitter: float = 0.0,
                   seed: int = 0) -> list[Point]:
    """Regular lattice (optionally rotated / jittered) used to refine large pavement meshes."""
    import random
    rng = random.Random(seed)
    pts: list[Point] = []
    nx = int((x1 - x0) / step) + 1
    ny = int((y1 - y0) / step) + 1
    for i in range(nx):
        for j in range(ny):
            x = x0 + i * step
            y = y0 + j * step
            if jitter:
                x += rng.uniform(-jitter, jitter) * step
                y += rng.uniform(-jitter, jitter) * step
            pts.append(rotate_point((x, y), angle, origin) if angle else (x, y))
    return pts


def dedupe_points(points: Sequence[Point], tol: float = 1e-3) -> list[Point]:
    seen = set()
    out = []
    inv = 1.0 / tol
    for p in points:
        key = (round(p[0] * inv), round(p[1] * inv))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out
