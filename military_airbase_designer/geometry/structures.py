"""Structures builder: every ``StructurePlan`` in the plan plus the BAK-12 arresting gear on the runway and a
few apron-edge props.

Kinds (from ``layout.plan``): HAS, REVETMENT, HANGAR, TOWER, VAULT, FIRE_STATION, BUILDING, FUEL_FARM, MUNITIONS,
ARRESTING_HOUSING, WASH_RACK, BEACON, ECP, GUARD_TOWER, BLAST_DEFLECTOR, HUSH_HOUSE.

Conventions: ``StructurePlan.position`` is the footprint centre in the base frame, ``rotation`` the Z rotation;
every mesh is modelled in its local frame with local +X = forward (the apron / road side) except the HAS, whose
loop taxiway lies at local -X (doors on the -X face). Repeated shelters, tanks, igloos, sheds, donuts and bollards
are shared meshes instanced through ``ctx.add_instance``; the geometry for each kind lives in
``structures_shelters.py`` (hardened structures) and ``structures_buildings.py`` (buildings) with the primitives in
``structures_props.py``.
"""
from __future__ import annotations

import math
import traceback
from collections import defaultdict

from ..core import geom2d as g
from ..core.units import ft
from . import structures_buildings as B
from . import structures_props as K
from . import structures_shelters as S
from .structures_props import Part

CATEGORY = "Structures"


# --------------------------------------------------------------------------- helpers
def _key_size(size) -> str:
    return "x".join(f"{v:.1f}" for v in size)


def _front_depth(ctx, st, depth_local: float, default: float = 40.0, max_depth: float = 60.0) -> float:
    """Distance from a structure's +X face to the nearest pavement (access apron / ramp length)."""
    c, s = math.cos(st.rotation), math.sin(st.rotation)
    origin = (st.position[0] + c * (depth_local * 0.5 + 0.5), st.position[1] + s * (depth_local * 0.5 + 0.5))
    d = K.ray_to_pavement(ctx.plan, origin, (c, s), max_dist=max_depth + 10.0)
    if d is None:
        return default
    return max(4.0, min(max_depth, d + 1.5))


def _bollard(ctx):
    return ctx.shared_mesh("Structures_Bollard", lambda: K.bollard_mesh(ctx))


# --------------------------------------------------------------------------- per-kind builders
def _b_has(ctx, st, n, detail):
    style = st.params.get('style', getattr(ctx.settings.structures, 'has_style', 'NATO3'))
    if style not in ('NATO3', 'TABVEE', 'EARTH'):
        style = 'NATO3'
    index = int(st.params.get('index', n - 1))
    exterior = S.has_camo_material(ctx, index)
    key = f"Structures_HAS_{style}_{_key_size(st.size)}_{exterior}"
    me = ctx.shared_mesh(key, lambda: S.has_mesh(ctx, style, st.size, exterior, detail))
    ctx.add_instance(f"Structures_HAS_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)
    if detail >= 0.75:
        num = S.has_number_mesh(ctx, style, st.size, f"{index + 1:02d}")
        ctx.add_object(f"Structures_HASNumber_{n}", num, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


def _b_revetment(ctx, st, n, detail):
    key = f"Structures_Revetment_{_key_size(st.size)}"
    me = ctx.shared_mesh(key, lambda: S.revetment_mesh(ctx, st.size, detail))
    ctx.add_instance(f"Structures_Revetment_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


def _b_hangar(ctx, st, n, detail):
    large = bool(st.params.get('large', False))
    depth = _front_depth(ctx, st, st.size[1], default=40.0)
    key = f"Structures_Hangar_{_key_size(st.size)}_{int(large)}_{depth:.0f}"
    me = ctx.shared_mesh(key, lambda: B.hangar_mesh(ctx, st.size, large, detail, depth))
    ctx.add_instance(f"Structures_Hangar_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


def _b_tower(ctx, st, n, detail):
    me = B.tower_mesh(ctx, st.size, detail)
    ctx.add_object(f"Structures_Tower_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


def _b_vault(ctx, st, n, detail):
    me = B.vault_mesh(ctx, st.size, detail)
    ctx.add_object(f"Structures_Vault_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


def _b_fire_station(ctx, st, n, detail):
    depth = _front_depth(ctx, st, st.size[1], default=30.0)
    me = B.fire_station_mesh(ctx, st.size, detail, depth)
    ctx.add_object(f"Structures_FireStation_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


def _b_building(ctx, st, n, detail):
    floors = max(1, int(st.params.get('floors', max(1, round(st.size[2] / 3.6)))))
    key = f"Structures_Building_{_key_size(st.size)}_{floors}"
    me = ctx.shared_mesh(key, lambda: B.building_mesh(ctx, st.size, floors, detail))
    ctx.add_instance(f"Structures_Building_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


def _b_fuel_farm(ctx, st, n, detail):
    B.build_fuel_farm(ctx, st, n, detail, _bollard(ctx) if detail >= 0.75 else None)


def _b_munitions(ctx, st, n, detail):
    S.build_munitions(ctx, st, n, detail)


def _b_arresting_housing(ctx, st, n, detail):
    key = f"Structures_ArrestingShed_{_key_size(st.size)}"
    me = ctx.shared_mesh(key, lambda: B.arresting_shed_mesh(ctx, st.size, detail))
    ctx.add_instance(f"Structures_ArrestingHousing_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


def _clear_of_others(ctx, st, step: float = 20.0, max_shift: float = 240.0):
    """Footprint centre nudged along +X until it no longer overlaps another structure's footprint (bbox test).

    Only used for minor pads (wash rack): the plan currently places it on the fire station's position."""
    def bbox(s):
        h = max(s.size[0], s.size[1]) * 0.5 + 6.0
        return (s.position[0] - h, s.position[1] - h, s.position[0] + h, s.position[1] + h)

    others = [bbox(o) for o in ctx.plan.structures if o is not st and o.kind not in ('BLAST_DEFLECTOR',)]
    x, y = st.position
    hx = max(st.size[0], st.size[1]) * 0.5 + 2.0
    shift = 0.0
    while shift <= max_shift:
        bx = (x + shift - hx, y - hx, x + shift + hx, y + hx)
        if not any(bx[0] < o[2] and bx[2] > o[0] and bx[1] < o[3] and bx[3] > o[1] for o in others):
            break
        shift += step
    if shift > 0.0:
        ctx.warn(f"structures: {st.name} overlapped another structure; moved {shift:.0f} m along +X")
    return (x + shift, y)


def _b_wash_rack(ctx, st, n, detail):
    me = B.wash_rack_mesh(ctx, st.size, detail)
    pos = _clear_of_others(ctx, st)
    st = type(st)(st.kind, pos, st.rotation, st.size, st.params, st.name)
    ctx.add_object(f"Structures_WashRack_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)
    if detail >= 0.75:
        bol = _bollard(ctx)
        L, W = st.size[0], st.size[1]
        c, s = math.cos(st.rotation), math.sin(st.rotation)
        k = 0
        for lx, ly in ((-L / 2 - 1.2, -W / 2 - 1.2), (L / 2 + 1.2, -W / 2 - 1.2), (L / 2 + 1.2, W / 2 + 1.2), (-L / 2 - 1.2, W / 2 + 1.2)):
            wx = st.position[0] + lx * c - ly * s
            wy = st.position[1] + lx * s + ly * c
            k += 1
            ctx.add_instance(f"Structures_Bollard_W{n}_{k}", bol, CATEGORY, (wx, wy, 0.0), 0.0)


def _b_beacon(ctx, st, n, detail):
    me = B.beacon_tower_mesh(ctx, st.size, detail)
    ctx.add_object(f"Structures_BeaconTower_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


def _b_ecp(ctx, st, n, detail):
    me = B.ecp_mesh(ctx, st.size, detail)
    ctx.add_object(f"Structures_ECP_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


def _b_guard_tower(ctx, st, n, detail):
    key = f"Structures_GuardTower_{_key_size(st.size)}"
    me = ctx.shared_mesh(key, lambda: B.guard_tower_mesh(ctx, st.size, detail))
    ctx.add_instance(f"Structures_GuardTower_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


def _b_blast_deflector(ctx, st, n, detail):
    length = round(st.size[0] / 2.4) * 2.4
    key = f"Structures_BlastDeflector_{length:.1f}_{st.size[2]:.1f}"
    me = ctx.shared_mesh(key, lambda: S.blast_deflector_mesh(ctx, st.size, detail, length))
    ctx.add_instance(f"Structures_BlastDeflector_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


def _b_hush_house(ctx, st, n, detail):
    me = S.hush_house_mesh(ctx, st.size, detail)
    ctx.add_object(f"Structures_HushHouse_{n}", me, CATEGORY, (st.position[0], st.position[1], 0.0), st.rotation)


DISPATCH = {
    'HAS': _b_has,
    'REVETMENT': _b_revetment,
    'HANGAR': _b_hangar,
    'TOWER': _b_tower,
    'VAULT': _b_vault,
    'FIRE_STATION': _b_fire_station,
    'BUILDING': _b_building,
    'FUEL_FARM': _b_fuel_farm,
    'MUNITIONS': _b_munitions,
    'ARRESTING_HOUSING': _b_arresting_housing,
    'WASH_RACK': _b_wash_rack,
    'BEACON': _b_beacon,
    'ECP': _b_ecp,
    'GUARD_TOWER': _b_guard_tower,
    'BLAST_DEFLECTOR': _b_blast_deflector,
    'HUSH_HOUSE': _b_hush_house,
}


# --------------------------------------------------------------------------- BAK-12 arresting gear on the runway
def build_arresting_gear(ctx, detail: float) -> None:
    """Pendant cable on rubber donuts across the runway, deck sheaves at the paved-shoulder edges and the purchase
    tapes running out to the energy-absorber shelters (AC 150/5220-9B; UFC 3-260-01 B13-2.21.1.1).

    Cable 1.25 in (32 mm) at 60 mm above the crown; donuts 150 mm dia / 60 mm thick every 1.8 m (instanced).
    """
    rw = ctx.plan.runway
    if not rw.arresting or rw.arresting_type == 'NONE':
        return
    hw, phw = rw.half_w, rw.paved_half_w
    donut = ctx.shared_mesh("Structures_CableDonut", lambda: K.donut_mesh(ctx)) if detail >= 0.75 else None
    housings = {(round(s.params.get('station', 0.0), 2), s.params.get('side', 0)): s
                for s in ctx.plan.structures if s.kind == 'ARRESTING_HOUSING'}
    for k, xs in enumerate(rw.arresting):
        p = Part(ctx, uv_tile=1.0)
        # pendant cable following the crown, out to the sheaves beyond the paved shoulder
        y_sheave = phw + 0.6
        n_pts = max(9, int((2 * y_sheave) / 2.5))
        pts = []
        for i in range(n_pts + 1):
            y = -y_sheave + 2 * y_sheave * i / n_pts
            pts.append((xs, y, rw.crown(xs, y) + 0.06))
        p.tube(pts, 0.016, 'SteelCable', 6 if detail < 0.75 else 8, caps=False)
        for s in (1, -1):
            ys = s * y_sheave
            # deck sheave / fairlead beam: steel box with a pulley on a flush concrete foundation (30H:1V sloped)
            p.prism(K.rxy(xs - 0.9, xs + 0.9, ys - s * 0.2, ys + s * 1.3), -0.2, 0.02, 'ConcreteBase', uv_tile=2.0)
            p.box((xs, ys + s * 0.4, 0.17), (0.6, 0.4, 0.3), 'MetalDark')
            p.tube([(xs - 0.14, ys + s * 0.4, 0.3), (xs + 0.14, ys + s * 0.4, 0.3)], 0.18, 'SteelCable', 12)
            p.tube([(xs, ys, 0.06), (xs, ys + s * 0.4, 0.28)], 0.016, 'SteelCable', 6, caps=False)
            # purchase tape (216 mm nylon, drawn 0.2 m wide) from the sheave to the absorber shelter
            h = housings.get((round(xs, 2), s))
            y_end = h.position[1] - s * (h.size[1] * 0.5 + 0.1) if h is not None else s * (phw + ft(40) - 1.6)
            y0 = ys + s * 0.9
            a, b = (xs - 0.1, y0, 0.02), (xs + 0.1, y0, 0.02)
            c_, d_ = (xs + 0.1, y_end, 0.02), (xs - 0.1, y_end, 0.02)
            if s > 0:
                p.quad(a, b, c_, d_, 'RubberBlack')
            else:
                p.quad(b, a, d_, c_, 'RubberBlack')
        me = p.build(f"Structures_ArrestingGear_{k + 1}", smooth_angle=math.radians(40))
        ctx.add_object(f"Structures_ArrestingGear_{k + 1}", me, CATEGORY)
        if donut is not None:
            n_d = int((2 * hw - 1.0) / 1.8)
            for i in range(n_d + 1):
                y = -hw + 0.5 + 1.8 * i
                if y > hw - 0.4:
                    break
                ctx.add_instance(f"Structures_CableDonut_{k + 1}_{i + 1}", donut, CATEGORY, (xs, y, rw.crown(xs, y) + 0.075), 0.0)


# --------------------------------------------------------------------------- apron-edge props
def build_apron_props(ctx, detail: float) -> None:
    """Light dressing at STANDARD/HIGH: safety bollards at apron rear corners, one flightline extinguisher cart."""
    if detail < 0.75:
        return
    bol = _bollard(ctx)
    cart = ctx.shared_mesh("Structures_ExtinguisherCart", lambda: K.extinguisher_cart_mesh(ctx))
    k = 0
    for ap in ctx.plan.aprons:
        if ap.kind not in ('MAIN', 'HANGAR_LINE', 'HOT_CARGO', 'ALERT', 'ARM_DEARM'):
            continue
        x0, y0, x1, y1 = g.bbox(ap.poly)
        side = ap.side
        far_y = y1 if side > 0 else y0
        for cx in (x0 + 2.0, x1 - 3.5):
            for j in range(2):
                k += 1
                ctx.add_instance(f"Structures_Bollard_A{k}", bol, CATEGORY, (cx + j * 1.5, far_y + side * 1.5, 0.0), 0.0)
        if ap.kind in ('MAIN', 'HANGAR_LINE', 'ALERT'):
            k += 1
            ctx.add_instance(f"Structures_ExtinguisherCart_{k}", cart, CATEGORY, ((x0 + x1) * 0.5 + 8.0, far_y - side * 3.0, 0.0),
                             math.atan2(-side, 0.0))


# --------------------------------------------------------------------------- entry point
def build(ctx) -> None:
    detail = ctx.detail
    counters: dict[str, int] = defaultdict(int)
    for st in ctx.plan.structures:
        counters[st.kind] += 1
        n = counters[st.kind]
        fn = DISPATCH.get(st.kind)
        if fn is None:
            ctx.warn(f"structures: unknown structure kind {st.kind!r} ({st.name})")
            continue
        try:
            fn(ctx, st, n, detail)
        except Exception as exc:      # one bad structure must not abort the module
            traceback.print_exc()
            ctx.warn(f"structures: {st.kind} {n} ({st.name}): {type(exc).__name__}: {exc}")
    try:
        build_arresting_gear(ctx, detail)
    except Exception as exc:
        traceback.print_exc()
        ctx.warn(f"structures: arresting gear: {type(exc).__name__}: {exc}")
    try:
        build_apron_props(ctx, detail)
    except Exception as exc:
        traceback.print_exc()
        ctx.warn(f"structures: apron props: {type(exc).__name__}: {exc}")
