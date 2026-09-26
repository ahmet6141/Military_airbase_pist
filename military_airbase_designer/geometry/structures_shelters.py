"""Hardened / protective structures: HAS (NATO 3rd gen, TAB-VEE, earth-covered), open revetments,
earth-covered munitions magazines, jet blast deflectors and the hush house.

Dimensions follow research report 04 (nukecompendium TAB VEE generations, AFMAN 32-1084 CATCODE 141182,
UFC 4-420-01 earth cover rules, AFMAN 32-1084 CATCODE 116945 blast deflectors, Emerald hush-house data).
All meshes are built in the structure's local frame: centre at the origin, local +X = "forward".
For a HAS the loop taxiway lies at local -X, so the blast doors are on the -X face.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import bpy
from mathutils import Matrix

from ..core import geom2d as g
from . import structures_props as K
from .structures_props import Part, arch_profile, arch_y, arch_z

Point = tuple[float, float]


# --------------------------------------------------------------------------- HAS geometry
@dataclass
class HasGeom:
    style: str
    L: float            # length along X (front face at -L/2)
    a: float            # outer half span
    b: float            # outer crown height
    t: float            # arch wall thickness
    exp: float          # super-ellipse exponent of the arch section
    d_w: float          # door opening width
    d_h: float          # door opening height
    leaf_w: float
    leaf_h: float
    leaf_t: float
    W: float            # headwall width incl. door-parking wings
    Hw: float           # wing height
    th: float = 0.8     # headwall thickness
    tr: float = 0.6     # rear wall thickness
    z_ground: float = -0.3

    @property
    def a_in(self) -> float:
        return self.a - self.t

    @property
    def b_in(self) -> float:
        return self.b - self.t

    @property
    def x_front(self) -> float:
        return -self.L * 0.5

    @property
    def track_x(self) -> tuple[float, float]:
        """Centre x of the inner and outer door tracks (in front of the headwall)."""
        x1 = self.x_front - 0.4 - self.leaf_t * 0.5       # 15 cm clear of the lintel / pilasters
        x2 = x1 - self.leaf_t - 0.2
        return x1, x2

    @property
    def rail_top(self) -> float:
        return 0.085

    def leaf_positions(self) -> list[tuple[float, float]]:
        """(track x, centre y) of the two leaves, stacked half-open toward -Y so the interior is visible."""
        x1, x2 = self.track_x
        right = (x1, -self.d_w * 0.25)                     # right leaf slid left: covers [-d_w/2-0.3, +0.3]
        left = (x2, -self.d_w * 0.75 - 0.6)                # left leaf parked on the -Y wing
        return [right, left]

    def number_anchor(self) -> tuple[float, float, float]:
        x1, y1 = self.leaf_positions()[0]
        return (x1 - self.leaf_t * 0.5, y1, 3.4)


def has_geometry(style: str, size: tuple[float, float, float]) -> HasGeom:
    L, w, h = size
    a, b = w * 0.5, h
    if style == 'TABVEE':
        t, exp = 0.25, 2.0                     # corrugated steel arch (semicircular family)
    else:
        t, exp = 0.5, 2.3                      # 460 mm concrete + liner -> 0.5 m, double-radius section
    a_in, b_in = a - t, b - t
    d_w = 0.72 * 2.0 * a_in
    d_h = min(0.66 * b_in, arch_z(a_in, b_in, d_w * 0.5 + 0.6, exp) - 0.5)
    leaf_w = d_w * 0.5 + 0.6
    leaf_h = d_h + 0.5
    leaf_t = 0.6 if style != 'TABVEE' else 0.4
    W = d_w + 2.0 * leaf_w + 1.2
    Hw = leaf_h + 0.9
    return HasGeom(style, L, a, b, t, exp, d_w, d_h, leaf_w, leaf_h, leaf_t, W, Hw)


def has_camo_material(ctx, index: int) -> str:
    if getattr(ctx.settings.structures, "camouflage", False):
        return ('CamoGreen', 'CamoGrey', 'CamoTan')[index % 3]
    return 'ConcreteHAS'


def has_mesh(ctx, style: str, size: tuple[float, float, float], exterior: str, detail: float) -> bpy.types.Mesh:
    """Shared HAS mesh (one per style / size / camouflage variant)."""
    G = has_geometry(style, size)
    p = Part(ctx, uv_tile=4.0)
    n_arc = 16 if detail < 0.75 else (36 if detail > 1.25 else 24)
    zg = G.z_ground
    arch_mat = exterior if style != 'TABVEE' or getattr(ctx.settings.structures, "camouflage", False) else 'CorrugatedSteel'
    liner = 'CorrugatedSteel'
    front_mat = exterior if style != 'TABVEE' else 'ConcreteWall'

    # ---- arch ring (outer skin + inner liner as one closed profile)
    outer = arch_profile(G.a, G.b - zg, n_arc, G.exp, zg)
    inner = arch_profile(G.a_in, G.b_in - zg, n_arc, G.exp, zg)
    x_ring0 = G.x_front + G.th - 0.1
    x_ring1 = G.L * 0.5 - G.tr + 0.1
    # outer skin (left->right over the top => normals outward)
    p.profile_x(outer, x_ring0, x_ring1, arch_mat, smooth=True)
    # inner liner (right->left => normals into the cavity)
    p.profile_x(inner[::-1], x_ring0, x_ring1, liner, smooth=True, uv_tile=2.0)

    # ---- front headwall with door-parking wings and the door notch
    hw_pts = [(-G.W / 2, zg), (-G.W / 2, G.Hw)]
    ya = arch_y(G.a, G.b - zg, G.Hw, G.exp, zg)
    hw_pts.append((-ya, G.Hw))
    hw_pts.extend([pt for pt in outer if pt[1] > G.Hw + 1e-6])
    hw_pts.extend([(ya, G.Hw), (G.W / 2, G.Hw), (G.W / 2, zg),
                   (G.d_w / 2, zg), (G.d_w / 2, G.d_h), (-G.d_w / 2, G.d_h), (-G.d_w / 2, zg)])
    p.yz_prism(hw_pts, G.x_front, G.x_front + G.th, front_mat, mat_cap='ConcreteWall', cap_mat_start=front_mat)
    # lintel over the door and pilasters at the jambs (heavy concrete frame)
    p.box_xyz(G.x_front - 0.25, G.x_front + 0.05, -G.d_w / 2 - 0.9, G.d_w / 2 + 0.9, G.d_h, G.d_h + 0.9, front_mat)
    for s in (1, -1):
        p.box_xyz(G.x_front - 0.25, G.x_front + 0.05, s * (G.d_w / 2 + 0.05), s * (G.d_w / 2 + 0.9), zg, G.d_h, front_mat)
    # buttresses behind the wing ends
    for s in (1, -1):
        y = s * (G.W / 2 - 0.6)
        p.box_xyz(G.x_front + G.th - 0.05, G.x_front + G.th + 2.2, y - 0.5, y + 0.5, zg, G.Hw * 0.8, 'ConcreteWall')
        p.plate((G.x_front + G.th + 1.1, y, G.Hw * 0.8 + 0.1), (2.3, 1.0, 0.25), 'ConcreteWall', rot_y=math.radians(12))

    # ---- rear wall with exhaust port, cheek walls and angled blast deflector
    rear_poly = list(outer)
    port = [(-1.6, 1.4), (1.6, 1.4), (1.6, 4.0), (-1.6, 4.0)]
    p.yz_prism(rear_poly, G.L / 2 - G.tr, G.L / 2, arch_mat, mat_cap=arch_mat, holes=[port], cap_mat_start='ConcreteWall')
    xr = G.L / 2
    for s in (1, -1):
        p.box_xyz(xr - 0.05, xr + 3.2, s * 3.0, s * 3.5, zg, 3.8, 'ConcreteWall')
    tilt = math.radians(28)
    pc = (xr + 3.0 + 2.6 * math.sin(tilt), 0.0, 2.6 * math.cos(tilt) - 0.1)
    p.plate(pc, (0.5, 7.0, 5.2), 'ConcreteWall', rot_y=tilt)
    # soot fan on the deflector face and around the port
    nx, nz = -math.cos(tilt), math.sin(tilt)
    p.plate((pc[0] + nx * 0.262, 0.0, pc[2] + nz * 0.262), (0.02, 3.6, 3.0), 'RubberBlack', rot_y=tilt)
    p.plate((xr + 0.011, 0.0, 4.3), (0.02, 4.2, 0.6), 'RubberBlack')

    # ---- doors: two sliding leaves on two ground rails, top guide beam
    rail_y = G.W / 2 - 0.3
    for tx in G.track_x:
        p.box_xyz(tx - 0.15, tx + 0.15, -rail_y, rail_y, K.PAD_TOP, G.rail_top, 'MetalDark')
    for (tx, cy) in G.leaf_positions():
        p.box((tx, cy, G.rail_top + G.leaf_h / 2), (G.leaf_t, G.leaf_w, G.leaf_h), 'BlastDoor', uv_tile=2.0)
        # horizontal stiffener ribs on the leaf face
        if detail >= 0.75:
            for k in range(3):
                zr = G.rail_top + G.leaf_h * (0.25 + 0.25 * k)
                p.box_xyz(tx - G.leaf_t / 2 - 0.06, tx - G.leaf_t / 2 + 0.02, cy - G.leaf_w / 2 + 0.2, cy + G.leaf_w / 2 - 0.2, zr - 0.12, zr + 0.12, 'MetalDark')
    x1, x2 = G.track_x
    zb = G.rail_top + G.leaf_h + 0.12
    p.box_xyz(x2 - G.leaf_t / 2 - 0.1, G.x_front + 0.05, -rail_y, rail_y, zb, zb + 0.35, 'MetalDark')
    # hazard stripes at the rail ends
    for s in (1, -1):
        p.box_xyz(x2 - 0.2, x1 + 0.2, s * (rail_y - 0.6), s * rail_y, K.PAD_TOP, K.PAD_TOP + 0.02, 'PaintYellow')

    # ---- personnel door with blast barricade (3rd gen), vent stack, lightning rods
    py = G.d_w / 2 + 2.6
    p.panel((G.x_front, py, zg + 0.3 + 1.05), 1.0, 2.1, 'MetalDark', '-X', proud=0.03)
    if style != 'TABVEE':
        p.box_xyz(G.x_front - 3.0, G.x_front - 2.5, py - 1.6, py + 1.6, K.PAD_TOP, 2.3, 'ConcreteWall')
    p.box((G.L * 0.25, 0.0, G.b + 0.45), (1.0, 1.0, 1.3), 'MetalDark')
    p.box((G.L * 0.25, 0.0, G.b + 1.15), (1.3, 1.3, 0.12), 'MetalDark')
    if detail > 1.25:
        for xf in (-0.3, 0.0, 0.3):
            p.tube([(G.L * xf, 0.0, G.b - 0.1), (G.L * xf, 0.0, G.b + 1.6)], 0.02, 'Galvanized', 4)

    # ---- earth berm hugging the flanks (EARTH style)
    if style == 'EARTH':
        zc = 0.62 * G.b
        zb = -0.05
        m_arc = 6

        def flank(sign: int, f: float) -> list[Point]:
            """Closed berm section: toe -> contact point on the arch -> down the arch (inside the wall) -> ground."""
            cz = zc * f
            cy = arch_y(G.a, G.b, cz, G.exp)
            toe = cy + 2.0 * cz + 0.6          # 2H:1V earth slope (UFC 4-420-01 cover rule)
            prof: list[Point] = [(-toe, zb), (-cy, cz)]
            for k in range(m_arc):
                z = cz * (1.0 - (k + 1) / (m_arc + 1))
                prof.append((-arch_y(G.a, G.b, z, G.exp) + 0.15, z))
            prof.append((-(G.a - 0.15), zb))
            if sign > 0:
                prof = [(-y, z) for y, z in prof][::-1]
            return prof

        xs = [x_ring0 + 0.6, x_ring0 + 6.0, x_ring1 - 4.0, x_ring1 + 0.4]
        fs = [0.08, 1.0, 1.0, 0.08]
        for sign in (-1, 1):
            secs = [(x, flank(sign, f)) for x, f in zip(xs, fs)]
            p.loft(secs, 'EarthBerm', smooth=True, uv_tile=6.0, close=True)

    # ---- slab under the shelter and hardstand in front (flush with pavement, +5 mm)
    x_slab0 = G.x_front - 1.0
    p.prism(g.rect_xy(x_slab0, G.L / 2 + 1.0, -G.W / 2 - 1.0, G.W / 2 + 1.0), K.PAD_TOP - 0.35, K.PAD_TOP, 'PadConcrete', uv_tile=12.19)
    p.prism(g.rect_xy(x_slab0 - 30.0, x_slab0, -15.0, 15.0), K.PAD_TOP - 0.35, K.PAD_TOP, 'PadConcrete', uv_tile=12.19)
    return p.build(f"Structures_HAS_{style}_{exterior}", smooth_angle=math.radians(40))


def has_number_mesh(ctx, style: str, size: tuple[float, float, float], number: str) -> bpy.types.Mesh:
    """Painted shelter number on the exposed door leaf (per shelter object, parented at the shelter position)."""
    G = has_geometry(style, size)
    p = Part(ctx)
    x, y, z = G.number_anchor()
    p.text(number, 1.2, 'PaintWhite', (x, y, z), '-X', offset=0.006)
    return p.build(f"Structures_HASNumber_{number}")


# --------------------------------------------------------------------------- revetment
def revetment_mesh(ctx, size: tuple[float, float, float], detail: float) -> bpy.types.Mesh:
    """Three-sided concrete revetment (U in plan) open toward the taxilane at local -X.

    plan.py passes size = (span + 6, length + 6, 3.6): the aircraft length runs along local X, so the U is
    size[1] deep (X) and size[0] wide (Y). The enclosure is shifted +4 m along X so the airframe (nose-wheel stop
    at the origin) sits inside the walls.
    """
    w_y, depth, h = size[0], size[1], size[2] if size[2] > 1.0 else 3.6
    t = 0.6
    p = Part(ctx, uv_tile=3.0)
    shift = 4.0
    x0, x1 = -depth / 2 + shift, depth / 2 + shift
    hy = w_y / 2
    p.wall((x1 - t / 2, -hy), (x1 - t / 2, hy), 0.0, h, t, 'ConcreteWall', chamfer=0.12)
    p.wall((x0, hy - t / 2), (x1 - t, hy - t / 2), 0.0, h, t, 'ConcreteWall', chamfer=0.12)
    p.wall((x0, -hy + t / 2), (x1 - t, -hy + t / 2), 0.0, h, t, 'ConcreteWall', chamfer=0.12)
    # end pilasters at the open side and a tie-down / grounding block
    for s in (1, -1):
        p.box_xyz(x0 - 0.3, x0 + 0.5, s * (hy - t - 0.3), s * (hy + 0.2), -0.1, h + 0.15, 'ConcreteWall')
    if detail >= 0.75:
        p.box((x1 - 1.6, 0.0, 0.3), (0.8, 0.8, 0.6), 'ConcreteBase')
    p.prism(g.rect_xy(x0 - 1.0, x1 + 1.0, -hy - 1.0, hy + 1.0), K.PAD_TOP - 0.35, K.PAD_TOP, 'PadConcrete', uv_tile=12.19)
    return p.build("Structures_Revetment")


# --------------------------------------------------------------------------- munitions igloos
IGLOO = dict(L=24.0, w=8.0, h=4.0, mound_L=30.0, mound_w=14.0, mound_h=5.0)   # research 04 §9 / brief


def igloo_mesh(ctx, detail: float) -> bpy.types.Mesh:
    """Earth-covered magazine: turf mound loft with a concrete headwall, double steel door and loading apron.

    Local frame: headwall at -X (faces the access road), mound runs toward +X.
    """
    p = Part(ctx, uv_tile=4.0)
    mL, mw, mh = IGLOO['mound_L'], IGLOO['mound_w'], IGLOO['mound_h']
    n = 12 if detail < 0.75 else 20
    zg = -0.06
    x_head = -mL * 0.4          # headwall plane
    hw_w, hw_h = 9.0, 5.0

    def sec(half: float, height: float) -> list[Point]:
        return arch_profile(half, height - zg, n, 1.6, zg)

    secs = [
        (x_head, sec(hw_w * 0.5 - 0.35, hw_h - 0.15)),
        (x_head + 1.2, sec(5.8, mh)),
        (x_head + 3.5, sec(mw * 0.5, mh)),
        (x_head + mL - 6.0, sec(mw * 0.5, mh)),
        (x_head + mL - 3.0, sec(5.6, 3.6)),
        (x_head + mL - 1.0, sec(3.2, 1.6)),
        (x_head + mL, sec(0.8, 0.05)),
    ]
    p.loft(secs, 'EarthBerm', smooth=True, uv_tile=6.0)
    # headwall (rectangular concrete face, embedded 10 cm into the mound) + wing walls along the toe
    p.box_xyz(x_head - 0.6, x_head + 0.1, -hw_w / 2, hw_w / 2, zg, hw_h, 'ConcreteWall')
    for s in (1, -1):
        ang = s * math.radians(38)
        p.plate((x_head + 1.4, s * (hw_w / 2 + 1.0), 1.4 + zg), (3.6, 0.35, 2.8), 'ConcreteWall', rot_z=ang)
    # double steel door (2.4 x 2.4), hazard placard, magazine number stripe
    door_w, door_h = 2.4, 2.4
    for s in (1, -1):
        p.panel((x_head - 0.6, s * door_w / 4, zg + 0.3 + door_h / 2), door_w / 2 - 0.03, door_h, 'BlastDoor', '-X', proud=0.05)
    p.panel((x_head - 0.6, hw_w / 2 - 1.2, 3.6), 0.6, 0.6, 'PaintOrange', '-X', proud=0.012)
    # loading apron in front, vent hood and lightning rod on the mound
    p.prism(g.rect_xy(x_head - 12.6, x_head - 0.6, -5.0, 5.0), K.PAD_TOP - 0.35, K.PAD_TOP, 'PadConcrete', uv_tile=12.19)
    p.box((x_head + mL * 0.45, 0.0, mh + 0.15), (0.8, 0.8, 0.7), 'MetalDark')
    p.box((x_head + mL * 0.45, 0.0, mh + 0.55), (1.1, 1.1, 0.1), 'MetalDark')
    if detail > 1.25:
        p.tube([(x_head + mL * 0.75, 0.0, mh - 0.6), (x_head + mL * 0.75, 0.0, mh + 2.5)], 0.02, 'Galvanized', 4)
    return p.build("Structures_Igloo", smooth_angle=math.radians(45))


def build_munitions(ctx, st, idx: int, detail: float) -> None:
    """Munitions storage area: igloos in two rows facing an access road stub, all inside one chain-link fence."""
    n = max(1, int(st.params.get('igloos', 6)))
    per_row = (n + 1) // 2
    pitch = 40.0
    cx, cy = st.position
    rot = st.rotation
    c, s = math.cos(rot), math.sin(rot)

    def world(lx: float, ly: float) -> Point:
        return (cx + lx * c - ly * s, cy + lx * s + ly * c)

    igloo = ctx.shared_mesh("Structures_Igloo", lambda: igloo_mesh(ctx, detail))
    front = 14.0                         # headwall distance from the road centreline (loading apron reaches the road)
    mL = IGLOO['mound_L']
    centre_off = front + mL * 0.4        # igloo local origin (mound at -0.4 L .. +0.6 L)
    k = 0
    row_len = (per_row - 1) * pitch
    for i in range(n):
        row = i % 2
        col = i // 2
        lx = -row_len / 2 + col * pitch
        ly = (1 if row == 0 else -1) * centre_off
        face = math.pi / 2 if row == 0 else -math.pi / 2      # local +X of the igloo points away from the road
        wx, wy = world(lx, ly)
        ctx.add_instance(f"Structures_Igloo_{idx}_{k + 1}", igloo, 'Structures', (wx, wy, 0.0), rot + face)
        k += 1
    # compound mesh: road stub, fence, gate, lightning masts
    p = Part(ctx, uv_tile=4.0)
    half_x = row_len / 2 + 45.0
    half_y = centre_off + mL * 0.6 + 25.0
    road_w = 6.0
    p.prism(g.rect_xy(-half_x - 40.0, half_x - 10.0, -road_w / 2, road_w / 2), -0.06, 0.02, 'Road', uv_tile=5.0)
    _fence_rect(p, -half_x, half_x, -half_y, half_y, height=2.4, post_pitch=3.0, detail=detail,
                gate=(-half_x, 0.0, road_w + 2.0))
    if detail >= 0.75:
        for sx_, sy_ in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            p.lattice_tower(sx_ * (half_x - 8.0), sy_ * (half_y - 8.0), 0.0, 15.0, 0.6, 0.25, 0.05, 0.03, 2.5, 'Galvanized', detail)
    me = p.build(f"Structures_Munitions_{idx}")
    ctx.add_object(f"Structures_Munitions_{idx}", me, 'Structures', (cx, cy, 0.0), rot)


def _fence_rect(p: Part, x0: float, x1: float, y0: float, y1: float, height: float, post_pitch: float,
                detail: float, gate: tuple[float, float, float] | None = None) -> None:
    """Chain-link fence around a rectangle as one mesh: posts, fabric panels (two-sided masked), top rail.

    ``gate`` = (x, y, opening width) on the -X side: leaves the opening with two heavier gate posts.
    """
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        d = g.normalize((b[0] - a[0], b[1] - a[1]))
        length = g.length(a, b)
        n_seg = max(1, int(round(length / post_pitch)))
        step = length / n_seg
        gate_range = None
        if gate is not None and abs(a[0] - gate[0]) < 1e-6 and abs(b[0] - gate[0]) < 1e-6:
            gate_range = (gate[1] - gate[2] / 2, gate[1] + gate[2] / 2)
        for k in range(n_seg):
            pa = (a[0] + d[0] * step * k, a[1] + d[1] * step * k)
            pb = (a[0] + d[0] * step * (k + 1), a[1] + d[1] * step * (k + 1))
            mid = ((pa[1] + pb[1]) / 2)
            in_gate = gate_range is not None and gate_range[0] - 0.1 < mid < gate_range[1] + 0.1
            if detail >= 0.75 or k % 2 == 0:
                p.cylinder(pa, 0.04, -0.05, height + 0.1, 'Galvanized', segments=5, smooth=False)
            if in_gate:
                continue
            # fabric panel (single quad, two-sided material) + top rail
            p.mb.add_face(((pa[0], pa[1], 0.02), (pb[0], pb[1], 0.02), (pb[0], pb[1], height), (pa[0], pa[1], height)),
                          p.m('ChainLink'), False, uvs=[(k, 0.0), (k + 1, 0.0), (k + 1, height), (k, height)])
            if detail >= 0.75:
                p.tube([(pa[0], pa[1], height), (pb[0], pb[1], height)], 0.03, 'Galvanized', 4, caps=False)
    if gate is not None:
        for s in (1, -1):
            p.cylinder((gate[0], gate[1] + s * gate[2] / 2), 0.09, -0.1, height + 0.3, 'Galvanized', segments=8)
        p.box_xyz(gate[0] - 0.3, gate[0] + 0.3, gate[1] - gate[2] / 2 - 0.15, gate[1] + gate[2] / 2 + 0.15, height + 0.1, height + 0.35, 'Galvanized')


# --------------------------------------------------------------------------- jet blast deflector
def blast_deflector_mesh(ctx, size: tuple[float, float, float], detail: float, key_len: float) -> bpy.types.Mesh:
    """Louvred jet blast fence: inclined vane panels between posts on a concrete footing beam.

    The deflector runs along local Y; the jet blast arrives from -X and is turned upward by vanes leaning 20 deg
    toward +X (AFMAN 32-1084 CATCODE 116945 type 2: "rectangular metal frame anchored at an angle with multiple
    horizontal curved vanes"). Height 3.5-4 m for fighters.
    """
    length = key_len
    h = max(2.4, min(size[2] if size[2] > 1.0 else 3.6, 4.2))
    p = Part(ctx, uv_tile=2.0)
    tilt = math.radians(20)
    bay = 2.4
    n_bay = max(1, int(round(length / bay)))
    bay_w = length / n_bay
    hy = length / 2
    # footing beam
    p.box_xyz(-0.45, 0.75, -hy - 0.3, hy + 0.3, -0.35, 0.3, 'ConcreteBase')
    # posts (leaning with the frame) and top rail
    for i in range(n_bay + 1):
        y = -hy + i * bay_w
        p.plate((h * 0.5 * math.tan(tilt) + 0.15, y, 0.3 + h * 0.5), (0.2, 0.2, h / math.cos(tilt)), 'Galvanized', rot_y=tilt)
    top_x = h * math.tan(tilt) + 0.15
    p.box_xyz(top_x - 0.12, top_x + 0.12, -hy - 0.1, hy + 0.1, 0.3 + h - 0.1, 0.3 + h + 0.12, 'Galvanized')
    if detail < 0.75:
        p.plate((h * 0.5 * math.tan(tilt), 0.0, 0.3 + h * 0.5), (0.06, length, h / math.cos(tilt)), 'BlastDeflector', rot_y=tilt)
    else:
        n_vane = max(3, int(h / 0.5))
        vane_ang = math.radians(40)
        for i in range(n_bay):
            yc = -hy + (i + 0.5) * bay_w
            for k in range(n_vane):
                z = 0.3 + h * (k + 0.5) / n_vane
                x = (z - 0.3) * math.tan(tilt)
                p.plate((x, yc, z), (0.05, bay_w - 0.22, h / n_vane * 1.15), 'BlastDeflector', rot_y=tilt + vane_ang)
    return p.build(f"Structures_BlastDeflector_{int(round(length))}")


# --------------------------------------------------------------------------- hush house
def hush_house_mesh(ctx, size: tuple[float, float, float], detail: float) -> bpy.types.Mesh:
    """Engine test enclosure: metal-clad test bay, acoustic intake baffles on the front (-X), bi-parting
    noise-lock doors, control room annex, 3.5 m augmenter tube with a silencer box out the back (+X)."""
    total_L, W, H = size
    tube_L = min(15.0, total_L * 0.34)
    bay_L = total_L - tube_L
    p = Part(ctx, uv_tile=2.0)
    x0, x1 = -total_L / 2, -total_L / 2 + bay_L
    cx = (x0 + x1) / 2
    hy = W / 2
    # slab, walls (MetalPainted over a Trim plinth), roof with parapet
    p.slab(cx, 0.0, bay_L, W, top=0.02)
    p.box_xyz(x0, x1, -hy, hy, 0.02, 1.2, 'Trim')
    p.box_xyz(x0 + 0.05, x1 - 0.05, -hy + 0.05, hy - 0.05, 1.2, H, 'MetalPainted', mat_top='RoofMembrane')
    p.parapet(cx, 0.0, bay_L - 0.1, W - 0.1, H, 0.6, 0.3, 'MetalPainted')
    # front: bi-parting noise-lock doors + intake baffle bank above
    door_w, door_h = W * 0.7, H * 0.72
    for s in (1, -1):
        p.panel((x0, s * door_w / 4, 0.02 + door_h / 2), door_w / 2 - 0.05, door_h, 'BlastDoor', '-X', proud=0.25, thick=0.5)
    p.box_xyz(x0 - 0.35, x0 + 0.05, -door_w / 2 - 0.6, door_w / 2 + 0.6, door_h, door_h + 0.5, 'MetalDark')
    n_fin = 6 if detail < 0.75 else 14
    fin_z0, fin_z1 = door_h + 0.9, H - 0.4
    span = W - 3.0
    p.box_xyz(x0 - 1.6, x0 + 0.05, -span / 2 - 0.3, span / 2 + 0.3, fin_z0 - 0.3, fin_z0, 'MetalDark')
    p.box_xyz(x0 - 1.6, x0 + 0.05, -span / 2 - 0.3, span / 2 + 0.3, fin_z1, fin_z1 + 0.3, 'MetalDark')
    for i in range(n_fin):
        y = -span / 2 + span * (i + 0.5) / n_fin
        p.plate((x0 - 0.8, y, (fin_z0 + fin_z1) / 2), (1.5, 0.12, fin_z1 - fin_z0), 'MetalDark', rot_z=math.radians(25))
    # control room annex on -Y
    ax, ay = cx - bay_L * 0.15, -hy - 3.0
    p.slab(ax, ay, 8.0, 6.0, top=0.15)
    p.box_xyz(ax - 4.0, ax + 4.0, ay - 3.0, -hy + 0.05, 0.15, 3.8, 'MetalPainted', mat_top='RoofMembrane')
    p.window_band(ax, ay, 8.0, 6.0, 1.3, 2.5, faces='Y', inset_ends=0.8)
    p.panel((ax - 4.0, ay, 0.15 + 1.05), 1.0, 2.1, 'MetalDark', '-X')
    # augmenter tube + saddles + silencer box with louvred top
    r = 1.75
    zt = 0.02 + r + 0.8
    seg = 12 if detail < 0.75 else 24
    p.tube([(x1 - 0.5, 0.0, zt), (x1 + tube_L - 3.0, 0.0, zt)], r, 'MetalPainted', seg, caps=False)
    p.tube([(x1 + 1.5, 0.0, zt), (x1 + 1.8, 0.0, zt)], r + 0.12, 'MetalDark', seg, caps=True)     # flange ring
    for xs in (x1 + 3.0, x1 + tube_L - 6.0):
        p.box_xyz(xs - 0.5, xs + 0.5, -r - 0.3, r + 0.3, 0.02, zt - r * 0.55, 'ConcreteWall')
    bx0, bx1 = x1 + tube_L - 3.4, x1 + tube_L
    p.box_xyz(bx0, bx1, -2.6, 2.6, 0.02, zt + r + 1.6, 'MetalPainted')
    p.box_xyz(bx1 - 0.02, bx1 + 0.02, -2.0, 2.0, zt - 1.6, zt + 1.6, 'RubberBlack')      # dark exhaust grille
    for i in range(4):
        p.plate((bx0 + 3.4 * (i + 0.5) / 4, 0.0, zt + r + 1.6 + 0.2), (0.7, 5.0, 0.06), 'MetalDark', rot_y=math.radians(35))
    return p.build("Structures_HushHouse", smooth_angle=math.radians(35))
