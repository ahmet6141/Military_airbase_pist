"""Flight-line and support buildings: hangars, control tower, lighting vault, fire station, ops buildings,
POL fuel farm, wash rack, beacon tower, entry control point, guard towers and the BAK-12 absorber shelter.

Proportions follow research report 04: UFC 4-211-01 hangar modules and door types, UFC 4-133-01 tower cab
(15 deg outward glass, catwalk), UFC 4-730-10 fire station bays (5.5 m ARFF doors, drive-through),
UFC 3-460-01 tank spacing (>= 1 diameter shell to shell) and earth dikes, UFC 4-022-01 ECP canopy (5.3 m clear)
and ID island, AC 150/5220-9B / UFC 3-260-01 B13 arresting-gear absorber shelters.
Local frame: structure centre at the origin; local +X faces the apron / road (doors on the +X face).
"""
from __future__ import annotations

import math

import bpy

from ..core import geom2d as g
from . import structures_props as K
from .structures_props import Part

Point = tuple[float, float]


# --------------------------------------------------------------------------- generic office block
def office_block(p: Part, cx: float, cy: float, sx: float, sy: float, floors: int, detail: float,
                 wall: str = 'ConcreteWall', trim: str = 'Trim', roof: str = 'RoofMembrane', z0: float = K.SLAB_TOP,
                 floor_h: float = 3.6, entrance: str | None = '-Y', hvac: bool = True, parapet: bool = True,
                 windows: bool = True, band: tuple[float, float] = (1.1, 2.6), slab: bool = True, mast: bool = False) -> float:
    """Flat-roofed building with a Trim plinth, glazing band per floor, parapet, entrance canopy, roof HVAC.

    Returns the roof height (z of the parapet top)."""
    h = floors * floor_h
    x0, x1, y0, y1 = cx - sx / 2, cx + sx / 2, cy - sy / 2, cy + sy / 2
    if slab:
        p.slab(cx, cy, sx, sy, top=z0)
    p.box_xyz(x0, x1, y0, y1, z0, z0 + 0.9, trim)
    p.box_xyz(x0 + 0.04, x1 - 0.04, y0 + 0.04, y1 - 0.04, z0 + 0.9, z0 + h, wall, mat_top=roof)
    top = z0 + h
    if parapet:
        p.parapet(cx, cy, sx - 0.08, sy - 0.08, z0 + h, 0.5, 0.3, wall)
        top += 0.5
    if windows:
        for f in range(floors):
            zb0 = z0 + f * floor_h + band[0]
            zb1 = z0 + f * floor_h + band[1]
            p.window_band(cx, cy, sx - 0.08, sy - 0.08, zb0, zb1, faces='XY', inset_ends=min(1.5, sx * 0.1, sy * 0.1))
    if entrance:
        s = 1.0 if entrance in ('+X', '+Y') else -1.0
        if entrance in ('+X', '-X'):
            ex, ey = (x1 if s > 0 else x0), cy
            p.panel((ex, ey, z0 + 1.2), 2.4, 2.4, 'MetalDark', entrance, proud=0.05)
            p.canopy(ex + s * 1.6, ey, 3.2, 6.0, z0 + 3.0, thick=0.25, col=0.18, col_inset=0.4)
        else:
            ex, ey = cx, (y1 if s > 0 else y0)
            p.panel((ex, ey, z0 + 1.2), 2.4, 2.4, 'MetalDark', entrance, proud=0.05)
            p.canopy(ex, ey + s * 1.6, 6.0, 3.2, z0 + 3.0, thick=0.25, col=0.18, col_inset=0.4)
    if hvac and detail >= 0.75:
        n = max(1, min(4, int(sx * sy / 250)))
        for i in range(n):
            hx = x0 + sx * (i + 0.5) / n
            hy = cy + (sy * 0.18 if i % 2 == 0 else -sy * 0.18)
            p.box((hx, hy, z0 + h + 0.6), (2.2, 1.4, 1.2), 'MetalDark')
        p.tube([(x1 - 2.0, y1 - 2.0, z0 + h - 0.2), (x1 - 2.0, y1 - 2.0, z0 + h + 1.8)], 0.15, 'Galvanized', 6)
    if mast and detail >= 0.75:
        p.tube([(x0 + 2.5, y0 + 2.5, z0 + h - 0.2), (x0 + 2.5, y0 + 2.5, z0 + h + 7.0)], 0.05, 'Galvanized', 4)
        p.tube([(x0 + 2.5, y0 + 1.7, z0 + h + 6.2), (x0 + 2.5, y0 + 3.3, z0 + h + 6.2)], 0.02, 'Galvanized', 4)
    return top


# --------------------------------------------------------------------------- hangar
def hangar_mesh(ctx, size: tuple[float, float, float], large: bool, detail: float, apron_depth: float) -> bpy.types.Mesh:
    """Portal-frame maintenance hangar. size = (door-wall width along Y, depth along X, height).

    Six horizontal-sliding insulated steel leaves on three tracks (UFC 4-211-01 door types), centre pair open;
    access apron in front (UFC 3-260-01 Table 6-6: hangar door -> apron edge)."""
    W, D, H = size[0], size[1], size[2]
    p = Part(ctx, uv_tile=2.0)
    x0, x1, hy = -D / 2, D / 2, W / 2
    eave, ridge = H * 0.85, H
    # slab + access apron (flush, +2 cm)
    z_fl = 0.02
    p.prism(g.rect_xy(x0 - 1.0, x1 + 1.0, -hy - 1.0, hy + 1.0), z_fl - 0.35, z_fl, 'PadConcrete', uv_tile=12.19)
    p.prism(g.rect_xy(x1 + 1.0, x1 + 1.0 + apron_depth, -hy - 1.0, hy + 1.0), z_fl - 0.35, z_fl, 'PadConcrete', uv_tile=12.19)
    # plinth band (Trim) on the three closed sides and beside the door
    door_w, door_h = W - 5.0, eave - 1.2
    p.box_xyz(x0, x0 + 0.35, -hy, hy, z_fl, 1.2, 'Trim')
    for s in (1, -1):
        p.box_xyz(x0, x1, s * (hy - 0.35), s * hy, z_fl, 1.2, 'Trim')
        p.box_xyz(x1 - 0.35, x1, s * (door_w / 2 + 0.3), s * hy, z_fl, 1.2, 'Trim')
    # side walls and gable end walls (front one with the door opening)
    for s in (1, -1):
        p.box_xyz(x0 + 0.05, x1 - 0.05, s * (hy - 0.3), s * (hy - 0.05), 1.2, eave + 0.05, 'MetalPainted')
    hyi = hy - 0.05
    rear = [(-hyi, 1.15), (hyi, 1.15), (hyi, eave), (0.0, ridge), (-hyi, eave)]
    p.yz_prism(rear, x0 + 0.05, x0 + 0.3, 'MetalPainted')
    front = [(-hyi, z_fl), (-hyi, eave), (0.0, ridge), (hyi, eave), (hyi, z_fl),
             (door_w / 2, z_fl), (door_w / 2, door_h), (-door_w / 2, door_h), (-door_w / 2, z_fl)]
    p.yz_prism(front, x1 - 0.3, x1 - 0.05, 'MetalPainted')
    p.box_xyz(x1 - 0.32, x1 + 0.12, -door_w / 2 - 0.6, door_w / 2 + 0.6, door_h - 0.05, door_h + 0.8, 'Trim')
    # roof
    p.gable_roof(0.0, 0.0, D, W, eave, ridge, 'X', overhang=0.5, thick=0.3)
    # door leaves on three tracks; centre pair open 4 m
    n_leaf = 6
    lw = door_w / n_leaf + 0.15
    lh = door_h + 0.3
    lt = 0.3
    tracks = [x1 + 0.2 + lt / 2 + k * (lt + 0.15) for k in range(3)]
    track_of = {0: 2, 5: 2, 1: 1, 4: 1, 2: 0, 3: 0}
    for i in range(n_leaf):
        yc = -door_w / 2 + door_w * (i + 0.5) / n_leaf
        if i == 2:
            yc -= min(4.0, door_w / 6)
        elif i == 3:
            yc += min(4.0, door_w / 6)
        tx = tracks[track_of[i]]
        p.box((tx, yc, z_fl + 0.06 + lh / 2), (lt, lw, lh), 'BlastDoor')
        if detail >= 0.75:
            p.box_xyz(tx + lt / 2 - 0.02, tx + lt / 2 + 0.05, yc - lw / 2 + 0.15, yc + lw / 2 - 0.15, z_fl + 0.06 + lh * 0.5 - 0.1, z_fl + 0.06 + lh * 0.5 + 0.1, 'MetalDark')
    for tx in tracks:
        p.box_xyz(tx - 0.12, tx + 0.12, -hy + 0.5, hy - 0.5, z_fl, z_fl + 0.06, 'MetalDark')
    p.box_xyz(x1 + 0.05, tracks[-1] + lt / 2 + 0.1, -hy + 0.5, hy - 0.5, z_fl + 0.06 + lh + 0.1, z_fl + 0.06 + lh + 0.5, 'MetalDark')
    # glazing band on the +Y wall, roll-up personnel doors on the -Y wall, annex on -Y
    p.panel((0.0, hy, 3.7), D - 6.0, 1.4, 'Glass', '+Y', proud=0.02)
    for k in range(2):
        p.panel((x1 - 5.0 - k * 6.0, -hy, z_fl + 2.0), 3.5, 4.0, 'MetalDark', '-Y', proud=0.03)
    ax, ay = x0 + D * 0.3, -hy - 4.0
    asx, asy = min(16.0, D * 0.4), 8.0
    office_block(p, ax, ay, asx, asy, 1, detail, wall='MetalPainted', z0=K.SLAB_TOP, floor_h=4.2, entrance='-X',
                 hvac=detail >= 0.75, band=(1.2, 2.8))
    # roof vents along the ridge
    if detail >= 0.75:
        n_v = max(2, int(D / 9))
        for i in range(n_v):
            vx = x0 + D * (i + 0.5) / n_v
            p.box((vx, 0.0, ridge + 0.5), (1.2, 1.2, 0.9), 'MetalDark')
            p.box((vx, 0.0, ridge + 1.0), (1.5, 1.5, 0.12), 'MetalDark')
    if large and detail >= 0.75:
        # obstruction marking: aviation orange/white checker patches on the ridge line of a tall airlift hangar
        for i in range(6):
            vx = x0 + D * (i + 0.5) / 6
            p.box((vx, 0.0, ridge + 0.15), (D / 6 - 0.4, 3.0, 0.02), 'ObstructionOrange' if i % 2 == 0 else 'ObstructionWhite')
    return p.build(f"Structures_Hangar_{int(W)}x{int(D)}")


# --------------------------------------------------------------------------- control tower
def tower_mesh(ctx, size: tuple[float, float, float], detail: float) -> bpy.types.Mesh:
    """ATCT: tapered octagonal concrete shaft, concrete catwalk slab, glass cab sloped 15 deg outward
    (UFC 4-133-01), roof with antennas, anemometer and obstruction light; 2-storey base building attached."""
    H = size[2]
    p = Part(ctx, uv_tile=3.0)
    cab_floor = H - 6.0
    r0 = 6.0 / math.cos(math.pi / 8)          # 12 m across flats at the base
    r1 = 4.0 / math.cos(math.pi / 8)          # 8 m across flats under the cab
    seg = 8
    a0 = math.pi / 8
    p.slab(0.0, 0.0, 14.0, 14.0, top=K.SLAB_TOP, margin=0.0)
    p.cylinder((0.0, 0.0), r0, K.SLAB_TOP, cab_floor, 'ConcreteWall', seg, cap_top=True, smooth=False, radius_top=r1, angle0=a0)
    # stair-tower slit windows on the -X flat (follow the taper)
    taper = math.atan2((r0 - r1) * math.cos(math.pi / 8), cab_floor - K.SLAB_TOP)
    for k in range(int((cab_floor - 6.0) / 5.0) + 1):
        z = 4.0 + 5.0 * k
        if z > cab_floor - 2.5:
            break
        apo = (r0 + (r1 - r0) * (z - K.SLAB_TOP) / (cab_floor - K.SLAB_TOP)) * math.cos(math.pi / 8)
        p.plate((-(apo + 0.03), 0.0, z), (0.04, 0.6, 2.0), 'Glass', rot_y=-taper)
    # entrance door at the shaft base (+X side) with a small canopy
    apo0 = r0 * math.cos(math.pi / 8)
    p.panel((apo0, 0.0, K.SLAB_TOP + 1.2), 2.0, 2.4, 'MetalDark', '+X', proud=0.05)
    p.canopy(apo0 + 1.5, 0.0, 3.0, 5.0, K.SLAB_TOP + 3.0, thick=0.25, col=0.18, col_inset=0.4)
    # catwalk slab (concrete, UFC 4-133-01) + handrail
    rc = 6.4 / math.cos(math.pi / 8)
    p.cylinder((0.0, 0.0), rc, cab_floor, cab_floor + 0.6, 'ConcreteWall', seg, cap_top=True, cap_bottom=True, smooth=False, angle0=a0)
    if detail >= 0.75:
        p.handrail([(rc * 0.97 * math.cos(a0 + 2 * math.pi * k / seg), rc * 0.97 * math.sin(a0 + 2 * math.pi * k / seg), cab_floor + 0.6) for k in range(seg)],
                   height=1.1, post_pitch=1.6, closed=True)
    # cab: sill band, sloped glass, mullions, roof
    rg0 = 5.0 / math.cos(math.pi / 8)
    glass_h = 3.0
    rg1 = rg0 + glass_h * math.tan(math.radians(15))
    z_sill0, z_sill1 = cab_floor + 0.6, cab_floor + 1.3
    p.cylinder((0.0, 0.0), rg0, z_sill0, z_sill1, 'MetalPainted', seg, cap_top=False, smooth=False, angle0=a0)
    p.cylinder((0.0, 0.0), rg0, z_sill1, z_sill1 + glass_h, 'Glass', seg, cap_top=False, smooth=False, radius_top=rg1, angle0=a0)
    for k in range(seg):
        ang = a0 + 2 * math.pi * k / seg
        p.tube([(rg0 * math.cos(ang), rg0 * math.sin(ang), z_sill1 - 0.1), (rg1 * math.cos(ang), rg1 * math.sin(ang), z_sill1 + glass_h + 0.1)], 0.07, 'MetalDark', 6)
    z_roof = z_sill1 + glass_h
    p.cylinder((0.0, 0.0), rg1 + 0.35, z_roof, z_roof + 0.45, 'MetalDark', seg, cap_top=True, cap_bottom=True, smooth=False, angle0=a0)
    p.cylinder((0.0, 0.0), rg1 - 0.4, z_roof + 0.45, z_roof + 0.7, 'MetalPainted', seg, cap_top=True, smooth=False, angle0=a0)
    # roof equipment: main mast with obstruction light, whip antennas, anemometer, HVAC
    zt = z_roof + 0.7
    p.tube([(1.5, 1.5, zt - 0.1), (1.5, 1.5, zt + 6.0)], 0.06, 'Galvanized', 6)
    p.sphere((1.5, 1.5, zt + 6.2), 0.2, 'LightRed', 10, 5)
    for (ax, ay, ah) in ((-2.5, 1.5, 3.0), (-1.0, -2.8, 3.5), (2.8, -1.5, 2.5)):
        p.tube([(ax, ay, zt - 0.1), (ax, ay, zt + ah)], 0.025, 'Galvanized', 4)
    p.tube([(-2.2, -2.2, zt - 0.1), (-2.2, -2.2, zt + 4.0)], 0.04, 'Galvanized', 4)
    p.tube([(-2.9, -2.2, zt + 3.9), (-1.5, -2.2, zt + 3.9)], 0.02, 'Galvanized', 4)
    if detail >= 0.75:
        for dx in (-0.7, 0.7):
            p.sphere((-2.2 + dx, -2.2, zt + 3.9), 0.07, 'MetalDark', 6, 3)
    p.box((0.0, 0.0, zt + 0.45), (1.6, 1.1, 0.9), 'MetalDark')
    # base building (2 storeys) attached on -Y
    office_block(p, 0.0, -(apo0 + 7.0), 25.0, 15.0, 2, detail, wall='ConcreteWall', entrance='-Y', mast=False)
    return p.build("Structures_Tower", smooth_angle=math.radians(35))


# --------------------------------------------------------------------------- lighting vault
def vault_mesh(ctx, size: tuple[float, float, float], detail: float) -> bpy.types.Mesh:
    L, W, H = size
    p = Part(ctx, uv_tile=3.0)
    z0 = K.SLAB_TOP
    p.slab(0.0, 0.0, L, W, top=z0)
    p.box_xyz(-L / 2, L / 2, -W / 2, W / 2, z0, z0 + H, 'ConcreteWall', mat_top='RoofMembrane')
    p.parapet(0.0, 0.0, L, W, z0 + H, 0.3, 0.25, 'ConcreteWall')
    p.panel((L / 2, -W / 4, z0 + 1.1), 1.2, 2.2, 'MetalDark', '+X', proud=0.04)
    p.panel((L / 2, W / 4, z0 + 1.6), 1.6, 1.0, 'MetalDark', '+X', proud=0.02)         # regulator room louvre
    p.panel((-L / 2, 0.0, z0 + 2.2), 2.0, 1.0, 'MetalDark', '-X', proud=0.02)          # exhaust louvre
    for s in (1, -1):
        p.box((s * L * 0.25, 0.0, z0 + H + 0.35), (0.7, 0.7, 0.7), 'MetalDark')
        p.box((s * L * 0.25, 0.0, z0 + H + 0.75), (1.0, 1.0, 0.1), 'MetalDark')
    if detail >= 0.75:
        # transformer pad beside the vault
        p.box_xyz(L / 2 + 1.5, L / 2 + 4.5, -W / 2, -W / 2 + 2.5, -0.1, 0.15, 'ConcreteBase')
        p.box((L / 2 + 3.0, -W / 2 + 1.25, 0.15 + 0.7), (1.4, 1.1, 1.4), 'MetalPainted')
    return p.build("Structures_Vault")


# --------------------------------------------------------------------------- fire station
def fire_station_mesh(ctx, size: tuple[float, float, float], detail: float, ramp_depth: float) -> bpy.types.Mesh:
    """ARFF station: drive-through apparatus bays with tall red roll-up doors on the apron (+X) face, a 2-storey
    admin / dorm wing and a hose tower (UFC 4-730-10)."""
    Wf, Df, Hf = size[0], size[1], size[2]
    p = Part(ctx, uv_tile=2.0)
    x0, x1 = -Df / 2, Df / 2
    bay_w = Wf * 0.6
    yb0, yb1 = -Wf / 2, -Wf / 2 + bay_w
    ya0, ya1 = yb1, Wf / 2
    z_fl = 0.02
    p.prism(g.rect_xy(x0 - 1.0, x1 + 1.0, -Wf / 2 - 1.0, Wf / 2 + 1.0), z_fl - 0.35, z_fl, 'PadConcrete', uv_tile=12.19)
    p.prism(g.rect_xy(x1 + 1.0, x1 + 1.0 + ramp_depth, yb0 - 1.0, yb1 + 1.0), z_fl - 0.35, z_fl, 'PadConcrete', uv_tile=12.19)
    p.prism(g.rect_xy(x0 - 1.0 - min(ramp_depth, 12.0), x0 - 1.0, yb0 - 1.0, yb1 + 1.0), z_fl - 0.35, z_fl, 'PadConcrete', uv_tile=12.19)
    # apparatus bay block
    p.box_xyz(x0, x1, yb0, yb1 + 0.3, z_fl, 1.0, 'Trim')
    p.box_xyz(x0 + 0.04, x1 - 0.04, yb0 + 0.04, yb1 + 0.26, 1.0, z_fl + Hf, 'MetalPainted', mat_top='RoofMembrane')
    p.parapet(0.0, (yb0 + yb1 + 0.3) / 2, Df - 0.08, bay_w + 0.22, z_fl + Hf, 0.5, 0.3, 'MetalPainted')
    n_bay = 3
    dw, dh = 5.5, 5.5                       # NFPA 403 recommended ARFF door 18 x 18 ft
    for i in range(n_bay):
        yc = yb0 + bay_w * (i + 0.5) / n_bay
        for face, xf in (('+X', x1), ('-X', x0)):
            p.panel((xf, yc, z_fl + dh / 2), dw, dh, 'DoorRed', face, proud=0.05)
            p.panel((xf, yc, z_fl + dh + 0.35), dw + 0.6, 0.5, 'Trim', face, proud=0.06)
    p.box_xyz(x1 - 0.05, x1 + 0.12, yb0 + 0.3, yb1 - 0.3, z_fl + dh + 0.7, z_fl + dh + 1.4, 'Trim')
    if detail >= 0.75:
        for i in range(2):
            p.box((x0 + Df * (i + 0.5) / 2, (yb0 + yb1) / 2, z_fl + Hf + 0.6), (2.5, 1.6, 1.2), 'MetalDark')
    # admin / dorm wing (2 storeys)
    office_block(p, 0.0, (ya0 + ya1) / 2, Df - 1.0, ya1 - ya0, 2, detail, wall='ConcreteWall', z0=z_fl, floor_h=3.5,
                 entrance='+X', slab=False)
    # hose tower at the rear corner of the admin wing
    tx, ty, tw = x0 + 2.5, ya1 - 2.5, 4.0
    p.box_xyz(tx - tw / 2, tx + tw / 2, ty - tw / 2, ty + tw / 2, z_fl, 12.0, 'ConcreteWall', mat_top='MetalDark')
    p.box_xyz(tx - tw / 2 - 0.25, tx + tw / 2 + 0.25, ty - tw / 2 - 0.25, ty + tw / 2 + 0.25, 12.0, 12.35, 'MetalDark')
    for zw in (5.0, 8.0, 11.0):
        p.panel((tx - tw / 2, ty, zw), 0.5, 1.6, 'Glass', '-X', proud=0.02)
    p.tube([(tx, ty, 12.3), (tx, ty, 17.0)], 0.05, 'Galvanized', 4)
    p.box((tx, ty, 16.4), (0.5, 0.5, 0.6), 'DoorRed')       # siren / warning beacon housing
    return p.build(f"Structures_FireStation_{int(Wf)}")


# --------------------------------------------------------------------------- generic building
def building_mesh(ctx, size: tuple[float, float, float], floors: int, detail: float) -> bpy.types.Mesh:
    L, W = size[0], size[1]
    p = Part(ctx, uv_tile=3.0)
    office_block(p, 0.0, 0.0, L, W, floors, detail, wall='ConcreteWall', entrance='-Y', mast=True)
    return p.build(f"Structures_Building_{int(L)}x{int(W)}x{floors}")


# --------------------------------------------------------------------------- fuel farm
TANK_D, TANK_H = 14.0, 12.0


def tank_mesh(ctx, detail: float) -> bpy.types.Mesh:
    """API 650 style vertical tank: shell, conical roof, wind girder, foundation ring, manway, stair (HIGH)."""
    p = Part(ctx, uv_tile=3.0)
    r = TANK_D / 2
    seg = 16 if detail < 0.75 else (32 if detail > 1.25 else 24)
    p.cylinder((0.0, 0.0), r + 0.7, -0.15, 0.3, 'ConcreteBase', seg, cap_top=True, smooth=False)
    p.cylinder((0.0, 0.0), r, 0.3, TANK_H, 'TankWhite', seg, cap_top=False, smooth=True, uv_tile=4.0)
    p.cylinder((0.0, 0.0), r + 0.15, TANK_H - 0.05, TANK_H + 1.3, 'TankWhite', seg, cap_top=True, smooth=True, radius_top=0.0)
    p.torus((0.0, 0.0, TANK_H - 0.6), r + 0.12, 0.12, 'MetalDark', seg, 6)
    p.box((r - 0.2, 0.0, 1.0), (0.9, 0.9, 1.0), 'MetalDark')
    p.box((r + 0.5, 0.0, 1.3), (0.5, 0.5, 0.5), 'MetalDark')     # nozzle / valve
    if detail >= 0.75:
        p.tube([(r * 0.7 * math.cos(a), r * 0.7 * math.sin(a), TANK_H + 1.3 - 0.7 * 1.3 / (r + 0.15) * 0) for a in (0.0, math.pi / 2, math.pi, 1.5 * math.pi, 0.0)],
               0.03, 'Galvanized', 4, caps=False)
        p.tube([(0.0, 0.0, TANK_H + 1.2), (0.0, 0.0, TANK_H + 2.6)], 0.07, 'MetalDark', 6)     # vent
    if detail > 1.25:
        pts = []
        turns = 1.15
        n = 40
        for i in range(n + 1):
            a = 2 * math.pi * turns * i / n
            pts.append(((r + 0.5) * math.cos(a), (r + 0.5) * math.sin(a), 0.5 + (TANK_H - 0.8) * i / n))
        p.tube(pts, 0.05, 'Galvanized', 4)
        p.tube([(x, y, z + 1.0) for x, y, z in pts], 0.03, 'Galvanized', 4)
    return p.build("Structures_FuelTank", smooth_angle=math.radians(35))


def build_fuel_farm(ctx, st, idx: int, detail: float, bollard: bpy.types.Mesh | None) -> None:
    n = max(1, int(st.params.get('tanks', 4)))
    cols = int(math.ceil(math.sqrt(n)))
    rows = int(math.ceil(n / cols))
    pitch = TANK_D + 16.0                                 # >= 1 diameter shell to shell (UFC 3-460-01 8-3.5)
    cx, cy = st.position
    rot = st.rotation
    c, s = math.cos(rot), math.sin(rot)

    def world(lx: float, ly: float) -> Point:
        return (cx + lx * c - ly * s, cy + lx * s + ly * c)

    tank = ctx.shared_mesh("Structures_FuelTank", lambda: tank_mesh(ctx, detail))
    fx, fy = cols * pitch, rows * pitch
    tank_pos: list[Point] = []
    k = 0
    for r_ in range(rows):
        for c_ in range(cols):
            if k >= n:
                break
            lx = -fx / 2 + pitch * (c_ + 0.5)
            ly = -fy / 2 + pitch * (r_ + 0.5) + 6.0
            tank_pos.append((lx, ly))
            wx, wy = world(lx, ly)
            ctx.add_instance(f"Structures_FuelTank_{idx}_{k + 1}", tank, 'Structures', (wx, wy, 0.0), rot)
            k += 1
    p = Part(ctx, uv_tile=4.0)
    # containment: gravel floor + earthen dike (crest 1.5 m, 2H:1V both faces)
    ix0, ix1 = -fx / 2 - 5.0, fx / 2 + 5.0
    iy0, iy1 = -fy / 2 + 1.0, fy / 2 + 11.0
    p.prism(g.rect_xy(ix0, ix1, iy0, iy1), -0.25, 0.0, 'Gravel', uv_tile=3.0)
    hb = 1.5
    crest = 1.2
    slope = 2.0 * hb
    zg = K.GROUND_Z - 0.02
    inner_g = [(ix0, iy0, zg), (ix1, iy0, zg), (ix1, iy1, zg), (ix0, iy1, zg)]
    inner_t = [(ix0 - slope, iy0 - slope, hb), (ix1 + slope, iy0 - slope, hb), (ix1 + slope, iy1 + slope, hb), (ix0 - slope, iy1 + slope, hb)]
    outer_t = [(ix0 - slope - crest, iy0 - slope - crest, hb), (ix1 + slope + crest, iy0 - slope - crest, hb),
               (ix1 + slope + crest, iy1 + slope + crest, hb), (ix0 - slope - crest, iy1 + slope + crest, hb)]
    outer_g = [(ix0 - 2 * slope - crest, iy0 - 2 * slope - crest, zg), (ix1 + 2 * slope + crest, iy0 - 2 * slope - crest, zg),
               (ix1 + 2 * slope + crest, iy1 + 2 * slope + crest, zg), (ix0 - 2 * slope - crest, iy1 + 2 * slope + crest, zg)]
    p.ring(inner_g, inner_t, 'EarthBerm', uv_tile=6.0)
    p.ring(inner_t, outer_t, 'EarthBerm', uv_tile=6.0)
    p.ring(outer_t, outer_g, 'EarthBerm', uv_tile=6.0)
    # dike crossing stair (concrete steps) on the -Y side
    sx_ = ix0 + 6.0
    p.box_xyz(sx_ - 0.8, sx_ + 0.8, iy0 - 2 * slope - crest - 0.3, iy0 + 0.3, hb, hb + 0.15, 'ConcreteBase')
    # pipework: manifold along each row + risers, header to the pump house
    py = iy0 - 2 * slope - crest - 4.0
    for r_ in range(rows):
        row = [pt for i, pt in enumerate(tank_pos) if i // cols == r_]
        if not row:
            continue
        ly = row[0][1]
        xs0, xs1 = row[0][0], row[-1][0]
        p.tube([(xs0 - TANK_D / 2 - 1.0, ly - TANK_D / 2 - 1.5, 0.9), (xs1 + TANK_D / 2 + 1.0, ly - TANK_D / 2 - 1.5, 0.9)], 0.15, 'MetalPainted', 8)
        for lx, ly_ in row:
            p.tube([(lx, ly_ - TANK_D / 2 - 1.5, 0.9), (lx, ly_ - TANK_D / 2 + 0.6, 0.9), (lx, ly_ - TANK_D / 2 + 0.6, 2.2)], 0.12, 'MetalPainted', 8)
        p.tube([(xs0 - TANK_D / 2 - 1.0, ly - TANK_D / 2 - 1.5, 0.9), (xs0 - TANK_D / 2 - 1.0, py + 2.0, 0.9)], 0.15, 'MetalPainted', 8)
    # pump house and truck fill stand outside the dike (-Y side)
    px = ix0 + 12.0
    office_block(p, px, py - 2.0, 6.0, 4.0, 1, detail, wall='ConcreteWall', floor_h=3.5, entrance=None, hvac=False, windows=False)
    p.panel((px + 3.0, py - 2.0, K.SLAB_TOP + 1.1), 1.2, 2.2, 'MetalDark', '+X', proud=0.04)
    fsx, fsy = px + 22.0, py - 3.0
    p.prism(g.rect_xy(fsx - 12.0, fsx + 12.0, fsy - 8.0, fsy + 8.0), 0.02 - 0.35, 0.02, 'PadConcrete', uv_tile=12.19)
    p.canopy(fsx, fsy, 15.0, 8.0, 5.5, thick=0.5, col=0.4, mat_roof='MetalDark', mat_fascia='Trim')
    p.box((fsx, fsy + 2.5, 0.02 + 0.6), (10.0, 1.2, 1.2), 'ConcreteBase')          # fill stand island
    for i in range(3):
        p.box((fsx - 3.0 + 3.0 * i, fsy + 2.5, 1.9), (0.6, 0.6, 1.4), 'MetalPainted')
        p.tube([(fsx - 3.0 + 3.0 * i + 0.5, fsy + 2.5, 2.4), (fsx - 3.0 + 3.0 * i + 1.6, fsy + 1.2, 1.6)], 0.06, 'RubberBlack', 6)
    p.tube([(fsx - 12.0, fsy + 3.6, 0.9), (px + 3.0, fsy + 3.6, 0.9), (px + 3.0, py, 0.9)], 0.15, 'MetalPainted', 8)
    # EFSO post (red mushroom button on a yellow post) and bollards
    p.cylinder((fsx + 9.0, fsy - 6.0), 0.05, 0.02, 1.3, 'Bollard', 6)
    p.box((fsx + 9.0, fsy - 6.0, 1.4), (0.25, 0.25, 0.25), 'DoorRed')
    me = p.build(f"Structures_FuelFarm_{idx}", smooth_angle=math.radians(35))
    ctx.add_object(f"Structures_FuelFarm_{idx}", me, 'Structures', (cx, cy, 0.0), rot)
    if bollard is not None and detail >= 0.75:
        k = 0
        for lx in (fsx - 9.0, fsx - 3.0, fsx + 3.0, fsx + 9.0):
            for ly in (fsy - 6.5, fsy + 6.5):
                wx, wy = world(lx, ly)
                ctx.add_instance(f"Structures_Bollard_F{idx}_{k + 1}", bollard, 'Structures', (wx, wy, 0.02), 0.0)
                k += 1


# --------------------------------------------------------------------------- wash rack
def wash_rack_mesh(ctx, size: tuple[float, float, float], detail: float) -> bpy.types.Mesh:
    L, W = size[0], size[1]
    p = Part(ctx, uv_tile=4.0)
    z = 0.02
    p.prism(g.rect_xy(-L / 2, L / 2, -W / 2, W / 2), z - 0.35, z, 'PadConcrete', uv_tile=12.19)
    outer = g.rect(0.0, 0.0, L + 0.4, W + 0.4)
    inner = g.rect(0.0, 0.0, L, W)
    p.prism(outer, z - 0.1, z + 0.15, 'ConcreteBase', holes=[inner])
    p.box_xyz(-L / 2 + 1.0, L / 2 - 1.0, -0.25, 0.25, z, z + 0.006, 'RubberBlack')        # trench drain grating
    p.box_xyz(-0.25, 0.25, -W / 2 + 1.0, W / 2 - 1.0, z, z + 0.006, 'RubberBlack')
    # equipment shed (pumps, separator) at one corner, hose reel posts along the edge
    sx, sy = -L / 2 - 3.5, W / 2 - 2.0
    office_block(p, sx, sy, 4.0, 3.0, 1, detail, wall='MetalPainted', floor_h=3.0, entrance=None, hvac=False, windows=False)
    p.panel((sx + 2.0, sy, K.SLAB_TOP + 1.05), 1.0, 2.1, 'MetalDark', '+X', proud=0.04)
    if detail >= 0.75:
        for yy in (-W / 4, W / 4):
            p.tube([(-L / 2 - 0.8, yy, z), (-L / 2 - 0.8, yy, z + 1.4)], 0.05, 'Galvanized', 6)
            p.box((-L / 2 - 0.8, yy, z + 1.2), (0.5, 0.35, 0.5), 'MetalPainted')
        p.box_xyz(-L / 2 - 4.0, -L / 2 - 1.0, -W / 2, -W / 2 + 2.5, -0.1, 0.1, 'ConcreteBase')      # separator vault lid
    return p.build(f"Structures_WashRack_{int(L)}")


# --------------------------------------------------------------------------- beacon tower
def beacon_tower_mesh(ctx, size: tuple[float, float, float], detail: float) -> bpy.types.Mesh:
    """18 m lattice tower with a top platform; the rotating beacon head (lighting module) sits at z = size[2]."""
    H = size[2]
    p = Part(ctx, uv_tile=2.0)
    base_half, top_half = 1.25, 0.6
    for sx_, sy_ in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        p.box((sx_ * base_half, sy_ * base_half, 0.15), (0.7, 0.7, 0.5), 'ConcreteBase')
    p.lattice_tower(0.0, 0.0, 0.4, H - 0.15, base_half, top_half, 0.075, 0.03, 2.0, 'Galvanized', detail)
    p.box((0.0, 0.0, H - 0.06), (2.2, 2.2, 0.12), 'MetalDark')
    if detail >= 0.75:
        pts = [(1.05, 1.05, H), (-1.05, 1.05, H), (-1.05, -1.05, H), (1.05, -1.05, H)]
        p.handrail(pts, 1.0, 1.1, closed=True)
        p.ladder(-top_half - 0.35, 0.0, 0.4, H - 0.2, '-X')
    return p.build("Structures_BeaconTower")


# --------------------------------------------------------------------------- entry control point
def ecp_mesh(ctx, size: tuple[float, float, float], detail: float) -> bpy.types.Mesh:
    """Entry control point on the ECP road (road runs along local X): gatehouse beside the road, canopy over the
    ID-check lanes (5.3 m clear, UFC 4-022-01 5-7.10), ID island with a guard booth, STOP / ID CHECK signs."""
    road_w = 7.0
    p = Part(ctx, uv_tile=2.0)
    # concrete pad under the check point
    p.prism(g.rect_xy(-8.0, 8.0, -road_w / 2 - 1.5, road_w / 2 + 1.5), 0.02 - 0.3, 0.02, 'PadConcrete', uv_tile=12.19)
    # gatehouse
    gy = road_w / 2 + 1.5 + 2.6
    office_block(p, 0.0, gy, 8.0, 5.0, 1, detail, wall='ConcreteWall', floor_h=3.6, entrance='-Y', band=(1.0, 2.4), hvac=detail >= 0.75)
    # canopy over the lanes
    p.canopy(0.0, 0.0, 12.0, 10.0, 5.3, thick=0.5, col=0.4, mat_roof='RoofMembrane', mat_fascia='Trim')
    # ID island with guard booth
    p.box_xyz(-7.5, 7.5, -0.6, 0.6, 0.02, 0.17, 'ConcreteBase')
    bx = 1.5
    p.box_xyz(bx - 1.5, bx + 1.5, -0.55, 0.55, 0.17, 1.1, 'MetalPainted')
    p.box_xyz(bx - 1.45, bx + 1.45, -0.5, 0.5, 1.1, 2.3, 'Glass')
    p.box_xyz(bx - 1.6, bx + 1.6, -0.65, 0.65, 2.3, 2.5, 'MetalDark')
    for s in (1, -1):
        p.box_xyz(bx - 1.5, bx + 1.5, s * 0.55 - 0.03, s * 0.55 + 0.03, 1.1, 2.3, 'MetalDark')
    # STOP sign (facing inbound traffic, which arrives from +X) and ID CHECK sign
    for s in (1, -1):
        sy = s * (road_w / 2 + 0.8)
        p.tube([(9.0, sy, 0.02), (9.0, sy, 2.6)], 0.035, 'Galvanized', 6)
        p.plate((9.0, sy, 2.2), (0.03, 0.8, 0.8), 'SignRed', rot_x=math.radians(45) if False else 0.0)
        p.text("STOP", 0.22, 'SignWhite', (9.0 + 0.02, sy, 2.2), '+X', offset=0.004)
        p.plate((9.0, sy, 1.5), (0.03, 1.0, 0.4), 'SignWhite')
        p.text("ID CHECK", 0.14, 'SignBlack', (9.0 + 0.02, sy, 1.5), '+X', offset=0.004)
    # passive barrier line: jersey-type kerb blocks along both road edges through the check point
    if detail >= 0.75:
        for s in (1, -1):
            for xx in (-6.0, -3.0, 3.0, 6.0):
                p.box((xx, s * (road_w / 2 + 0.6), 0.42), (2.4, 0.6, 0.8), 'ConcreteBase')
    return p.build("Structures_ECP")


# --------------------------------------------------------------------------- guard tower
def guard_tower_mesh(ctx, size: tuple[float, float, float], detail: float) -> bpy.types.Mesh:
    H = size[2]
    p = Part(ctx, uv_tile=2.0)
    cab = 3.0
    z_floor = H - 2.8
    for sx_, sy_ in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        p.box((sx_ * 1.5, sy_ * 1.5, 0.15), (0.6, 0.6, 0.5), 'ConcreteBase')
    p.lattice_tower(0.0, 0.0, 0.4, z_floor, 1.5, 1.2, 0.06, 0.03, 2.0, 'Galvanized', detail)
    p.box((0.0, 0.0, z_floor + 0.12), (cab + 0.4, cab + 0.4, 0.24), 'MetalDark')
    p.box_xyz(-cab / 2, cab / 2, -cab / 2, cab / 2, z_floor + 0.24, z_floor + 1.3, 'MetalPainted')
    p.box_xyz(-cab / 2 + 0.05, cab / 2 - 0.05, -cab / 2 + 0.05, cab / 2 - 0.05, z_floor + 1.3, z_floor + 2.3, 'Glass')
    for s in (1, -1):
        p.box_xyz(s * cab / 2 - 0.05, s * cab / 2 + 0.03, -cab / 2, cab / 2, z_floor + 1.3, z_floor + 2.3, 'MetalDark')
        p.box_xyz(-cab / 2, cab / 2, s * cab / 2 - 0.05, s * cab / 2 + 0.03, z_floor + 1.3, z_floor + 2.3, 'MetalDark')
    for s in (1, -1):
        for t in (1, -1):
            p.box_xyz(s * cab / 2 - 0.12, s * cab / 2 + 0.06, t * cab / 2 - 0.12, t * cab / 2 + 0.06, z_floor + 1.3, z_floor + 2.3, 'MetalDark')
    p.box((0.0, 0.0, z_floor + 2.42), (cab + 0.7, cab + 0.7, 0.24), 'MetalDark')
    p.plate((0.0, 0.0, z_floor + 2.7), (cab + 0.2, cab + 0.2, 0.3), 'MetalDark', rot_y=0.0)
    if detail >= 0.75:
        p.ladder(-1.4 - 0.35, 0.0, 0.4, z_floor + 0.2, '-X')
        p.box((0.0, cab / 2 + 0.5, z_floor + 2.9), (0.5, 0.5, 0.35), 'MetalDark')       # searchlight housing
        p.cylinder((0.0, cab / 2 + 0.78), 0.16, z_floor + 2.75, z_floor + 3.05, 'LightWhite', 10)
    return p.build("Structures_GuardTower")


# --------------------------------------------------------------------------- BAK-12 absorber shelter
def arresting_shed_mesh(ctx, size: tuple[float, float, float], detail: float) -> bpy.types.Mesh:
    """Energy-absorber shelter: 6 x 3 x 2.6 m frangible metal shed, window strip and tape slot facing the runway
    (local -Y), personnel door on +X, concrete pad (UFC 3-260-01 B13-2.21.1.1(b))."""
    L, W, H = size[0], size[1], size[2]
    p = Part(ctx, uv_tile=2.0)
    z0 = 0.02
    p.slab(0.0, 0.0, L, W, top=z0, thick=0.25, margin=1.0)
    eave = H - 0.45
    p.box_xyz(-L / 2, L / 2, -W / 2, W / 2, z0, 0.5, 'Trim')
    p.box_xyz(-L / 2 + 0.04, L / 2 - 0.04, -W / 2 + 0.04, W / 2 - 0.04, 0.5, eave, 'MetalPainted', mat_top='MetalDark')
    p.gable_roof(0.0, 0.0, L, W, eave, H, 'X', overhang=0.3, thick=0.12, mat='MetalDark')
    p.panel((0.0, -W / 2, 1.75), L - 1.6, 0.6, 'Glass', '-Y', proud=0.02)
    p.panel((0.0, -W / 2, 0.6), 0.5, 0.35, 'RubberBlack', '-Y', proud=0.012)
    p.panel((L / 2, W * 0.15, z0 + 1.0), 0.9, 2.0, 'MetalDark', '+X', proud=0.03)
    if detail >= 0.75:
        p.tube([(L * 0.3, W * 0.2, H - 0.3), (L * 0.3, W * 0.2, H + 0.5)], 0.08, 'MetalDark', 6)
    return p.build("Structures_ArrestingShed")
