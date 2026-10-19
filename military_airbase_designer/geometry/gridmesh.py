"""Quad-grid topology generators for the heliport surfaces (pure Python, no ``bpy``).

Everything is built in a local (u, v) frame into a :class:`QuadSoup`, a welded vertex pool plus quads:

* ``axis_lines`` - grid lines along one axis. Every rectangle takes its lines from one *shared* breakpoint set
  (``refine`` between consecutive breakpoints is deterministic), so two grids that touch always carry the same
  vertices on the shared edge: no T-junctions, no cracks.
* ``QuadSoup.grid`` - tensor-product grid of a rectangle.
* ``pad_surface`` - FATO / TLOF pad: the TLOF is a tensor grid (square) or an O-grid (a square core plus four
  radial sectors, no poles, only quads) and a loft ring carries it out to the square FATO boundary. The ring and the
  TLOF share their boundary loop vertex for vertex.
"""
from __future__ import annotations

import math
from typing import Sequence

Point = tuple[float, float]
TOL = 1e-6


def axis_lines(breaks: Sequence[float], lo: float, hi: float, step: float) -> list[float]:
    """Sorted grid coordinates on [lo, hi]: every breakpoint inside, each interval refined to <= ``step``."""
    pts = sorted({round(lo, 6), round(hi, 6)} | {round(b, 6) for b in breaks if lo - TOL <= b <= hi + TOL})
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(math.ceil((b - a) / step - 1e-9)))
        out.extend(a + (b - a) * k / n for k in range(1, n + 1))
        out[-1] = b
    return out


class QuadSoup:
    """Welded vertices + quads in a local frame. Quads are stored counter-clockwise (+Z normal)."""

    def __init__(self) -> None:
        self.verts: list[Point] = []
        self._index: dict[tuple[int, int], int] = {}
        self.quads: list[tuple[tuple[int, int, int, int], int]] = []   # (vertex ids, material slot)

    def vert(self, u: float, v: float) -> int:
        key = (int(round(u / TOL)), int(round(v / TOL)))
        i = self._index.get(key)
        if i is None:
            i = len(self.verts)
            self.verts.append((u, v))
            self._index[key] = i
        return i

    def quad(self, a: int, b: int, c: int, d: int, mat: int = 0) -> None:
        """A quad; when two neighbouring corners coincide (the cusp of a fillet) it degrades to a triangle."""
        ids = [a, b, c, d]
        out = [i for k, i in enumerate(ids) if i != ids[k - 1]]
        if len(out) < 3 or len(set(out)) < len(out):
            return
        pts = [self.verts[i] for i in out]
        area = 0.0
        for k in range(len(pts)):
            x0, y0 = pts[k]
            x1, y1 = pts[(k + 1) % len(pts)]
            area += x0 * y1 - x1 * y0
        if abs(area) < 1e-10:
            return
        if area < 0.0:
            out = out[::-1]
        self.quads.append((tuple(out), mat))

    # ------------------------------------------------------------------ primitives
    def grid(self, us: Sequence[float], vs: Sequence[float], mat: int = 0) -> None:
        ids = [[self.vert(u, v) for u in us] for v in vs]
        for j in range(len(vs) - 1):
            for i in range(len(us) - 1):
                self.quad(ids[j][i], ids[j][i + 1], ids[j + 1][i + 1], ids[j + 1][i], mat)

    def loft(self, inner: Sequence[Point], outer: Sequence[Point], rows: Sequence[float], mat: int = 0) -> None:
        """Closed band between two loops of equal length; ``rows`` are the blend fractions 0 = inner .. 1 = outer."""
        n = len(inner)
        assert n == len(outer) and n >= 3
        rings = []
        for r in rows:
            rings.append([self.vert(inner[k][0] + (outer[k][0] - inner[k][0]) * r,
                                    inner[k][1] + (outer[k][1] - inner[k][1]) * r) for k in range(n)])
        for j in range(len(rows) - 1):
            for k in range(n):
                k2 = (k + 1) % n
                self.quad(rings[j][k], rings[j][k2], rings[j + 1][k2], rings[j + 1][k], mat)

    # ------------------------------------------------------------------ audit
    def stats(self) -> dict:
        edges: dict[tuple[int, int], int] = {}
        valence: dict[int, int] = {}
        area = 0.0
        worst_aspect = 1.0
        tris = 0
        for ids, _m in self.quads:
            pts = [self.verts[i] for i in ids]
            m = len(ids)
            tris += m == 3
            for k in range(m):
                a, b = ids[k], ids[(k + 1) % m]
                key = (a, b) if a < b else (b, a)
                edges[key] = edges.get(key, 0) + 1
            a2 = 0.0
            for k in range(m):
                x0, y0 = pts[k]
                x1, y1 = pts[(k + 1) % m]
                a2 += x0 * y1 - x1 * y0
            area += 0.5 * a2
            if m == 4:
                l = [math.dist(pts[k], pts[(k + 1) % 4]) for k in range(4)]
                if min(l) > 1e-9:
                    worst_aspect = max(worst_aspect, max(l) / min(l))
        for (a, b) in edges:
            valence[a] = valence.get(a, 0) + 1
            valence[b] = valence.get(b, 0) + 1
        return dict(verts=len(self.verts), quads=len(self.quads) - tris, tris=tris, area=area,
                    nonmanifold=sum(1 for c in edges.values() if c > 2),
                    boundary=[e for e, c in edges.items() if c == 1],
                    max_valence=max(valence.values()) if valence else 0, worst_aspect=worst_aspect)


# --------------------------------------------------------------------------- pad (FATO + TLOF)
def _side_params(lines: Sequence[float], centre: float, half: float) -> list[float]:
    return [max(-1.0, min(1.0, (x - centre) / half)) for x in lines]


def _loop_square(cu: float, cv: float, half: float, su: Sequence[float], sv: Sequence[float]) -> list[Point]:
    """CCW boundary loop of an axis-aligned square starting at the SW corner (corners appear once)."""
    pts: list[Point] = []
    for s in su[:-1]:
        pts.append((cu + s * half, cv - half))
    for t in sv[:-1]:
        pts.append((cu + half, cv + t * half))
    for s in reversed(su[1:]):
        pts.append((cu + s * half, cv + half))
    for t in reversed(sv[1:]):
        pts.append((cu - half, cv + t * half))
    return pts


def _loop_circle(cu: float, cv: float, radius: float, su: Sequence[float], sv: Sequence[float]) -> list[Point]:
    """Circle loop with exactly the same vertex count / order as ``_loop_square`` (each side spans 90 degrees)."""
    def pt(theta: float) -> Point:
        return (cu + radius * math.cos(theta), cv + radius * math.sin(theta))
    q = math.pi / 4.0
    pts: list[Point] = []
    for s in su[:-1]:
        pts.append(pt(-math.pi / 2 + q * s))
    for t in sv[:-1]:
        pts.append(pt(q * t))
    for s in reversed(su[1:]):
        pts.append(pt(math.pi / 2 - q * s))
    for t in reversed(sv[1:]):
        pts.append(pt(math.pi - q * t))
    return pts


def pad_surface(soup: QuadSoup, cu: float, cv: float, fato_half: float, tlof_half: float, round_tlof: bool,
                us: Sequence[float], vs: Sequence[float], mat_tlof: int, mat_fato: int, step: float) -> dict:
    """FATO square (grid lines ``us`` / ``vs`` on its boundary) with a square or round TLOF in the centre.

    Returns the TLOF boundary loop (for markings / lights) and the FATO boundary loop."""
    su = _side_params(us, cu, fato_half)
    sv = _side_params(vs, cv, fato_half)
    outer = _loop_square(cu, cv, fato_half, su, sv)
    if round_tlof:
        inner = _loop_circle(cu, cv, tlof_half, su, sv)
        c = 0.5 * tlof_half
        # core tensor grid
        soup.grid([cu + s * c for s in su], [cv + t * c for t in sv], mat_tlof)
        n_rad = max(2, int(math.ceil((tlof_half - c * 1.1) / step)))
        rr = [k / n_rad for k in range(n_rad + 1)]
        # four sectors: core edge -> circle
        sides = (
            ([(cu + s * c, cv - c) for s in su], [(cu + tlof_half * math.cos(-math.pi / 2 + math.pi / 4 * s),
                                                   cv + tlof_half * math.sin(-math.pi / 2 + math.pi / 4 * s)) for s in su]),
            ([(cu + c, cv + t * c) for t in sv], [(cu + tlof_half * math.cos(math.pi / 4 * t),
                                                   cv + tlof_half * math.sin(math.pi / 4 * t)) for t in sv]),
            ([(cu + s * c, cv + c) for s in su], [(cu + tlof_half * math.cos(math.pi / 2 - math.pi / 4 * s),
                                                   cv + tlof_half * math.sin(math.pi / 2 - math.pi / 4 * s)) for s in su]),
            ([(cu - c, cv + t * c) for t in sv], [(cu + tlof_half * math.cos(math.pi - math.pi / 4 * t),
                                                   cv + tlof_half * math.sin(math.pi - math.pi / 4 * t)) for t in sv]),
        )
        for core_pts, circ_pts in sides:
            ids = [[soup.vert(cp[0] + (qp[0] - cp[0]) * r, cp[1] + (qp[1] - cp[1]) * r) for cp, qp in zip(core_pts, circ_pts)]
                   for r in rr]
            for j in range(len(rr) - 1):
                for i in range(len(core_pts) - 1):
                    soup.quad(ids[j][i], ids[j][i + 1], ids[j + 1][i + 1], ids[j + 1][i], mat_tlof)
    else:
        inner = _loop_square(cu, cv, tlof_half, su, sv)
        soup.grid([cu + s * tlof_half for s in su], [cv + t * tlof_half for t in sv], mat_tlof)
    gap = fato_half - tlof_half
    corner = (fato_half * math.sqrt(2.0) - tlof_half) if round_tlof else gap * math.sqrt(2.0)
    n_ring = max(1, int(math.ceil(0.5 * (gap + corner) / step)))      # balances the quads at mid-side and at the corners
    soup.loft(inner, outer, [k / n_ring for k in range(n_ring + 1)], mat_fato)
    return dict(tlof_loop=inner, fato_loop=outer)


def fillet_patch(soup: QuadSoup, fillet, mat: int = 0) -> None:
    """Quad patch of a 90 degree junction fillet (``layout.heliport.HeliFillet``): one row of quads between the arc and
    the two straight edges (vertices on the neighbouring grids' edge lines). The two cusps at the tangent points
    degrade to triangles - the only non-quads of a heliport."""
    path = [soup.vert(*p) for p in fillet.edge_points()]
    arc = [soup.vert(*p) for p in fillet.arc()]
    for k in range(len(path) - 1):
        soup.quad(arc[k], arc[k + 1], path[k + 1], path[k], mat)


# --------------------------------------------------------------------------- whole heliport
SLOT_NAMES = ('PadConcrete', 'ApronConcrete', 'TaxiwayAsphalt')


def heliport_soup(hp, step: float = 3.0) -> tuple[QuadSoup, list[str], dict]:
    """Quad mesh (local frame) of every paved heliport piece: rectangles share the plan's global breakpoints, the
    FATO pad is built by :func:`pad_surface` on the access-route breakpoints. Returns (soup, material names, info)."""
    soup = QuadSoup()
    names: list[str] = []

    def slot(name: str) -> int:
        if name not in names:
            names.append(name)
        return names.index(name)

    for pc in hp.pieces:
        soup.grid(axis_lines(hp.breaks_u, pc.u0, pc.u1, step), axis_lines(hp.breaks_v, pc.v0, pc.v1, step), slot(pc.mat))
    for f in hp.fillets:
        fillet_patch(soup, f, slot('TaxiwayAsphalt'))
    info: dict = {}
    if hp.fato_paved:
        fh = hp.fato * 0.5
        hw = hp.w_t * 0.5
        us = axis_lines([-hw, hw], -fh, fh, step)
        vs = axis_lines([], hp.v_f0, hp.v_f0 + hp.fato, step)
        info = pad_surface(soup, 0.0, hp.v_fc, fh, hp.tlof * 0.5, hp.tlof_shape == 'ROUND', us, vs,
                           slot('PadConcrete'), slot('ApronConcrete'), step)
    return soup, names, info
