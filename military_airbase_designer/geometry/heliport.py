"""Heliport surfaces: FATO / TLOF pad, helitaxiways and stands as one clean quad mesh per heliport.

The CDT slab (``geometry.pavement``) keeps the heliport pieces in its footprint - terrain holes, shoulders and the
edge skirt need them - but skips their triangles (zone ``HELI``); this module meshes exactly the same polygons as
quads: tensor grids for rectangles, an O-grid for a round TLOF and a loft ring out to the FATO boundary
(``geometry.gridmesh``). Neighbouring grids share their boundary vertices, so there are no T-junctions and no cracks
inside the heliport; only the junction fillets (concave corners) remain in the CDT slab.

UVs are world-planar like the slab (texture continuity across the seams), vertex colour ``Wear`` carries the edge-dirt
and macro masks.
"""
from __future__ import annotations

import re

import numpy as np

from ..core import geom2d as g
from ..core.meshbuild import MeshBuilder, planar_uv
from ..layout import heliport as H
from ..materials.library import SPECS
from . import gridmesh as GM
from . import pavement as pav

CAT = "Pavement"


def mesh_step(detail: float) -> float:
    return H.MESH_STEP / min(max(detail, 0.6), 1.6)


def base_mesh(hp: H.HeliportPlan, step: float):
    """(vertices [(x, y)], quads [(ids, slot)], material names, info) of one heliport in the base frame."""
    soup, names, info = GM.heliport_soup(hp, step)
    verts = [hp.to_base(p) for p in soup.verts]
    quads = []
    for ids, slot in soup.quads:
        pts = [verts[i] for i in ids]
        if g.signed_area(pts) < 0.0:           # the right-hand side mirrors the local frame: keep +Z normals
            ids = tuple(reversed(ids))
        quads.append((ids, slot))
    return verts, quads, names, info


def _build_one(ctx, hp: H.HeliportPlan, fp, wear) -> None:
    verts, quads, names, _info = base_mesh(hp, mesh_step(ctx.detail))
    if not quads:
        return
    xy = np.asarray(verts, dtype=np.float64).reshape(-1, 2)
    edge = np.asarray([fp.edge_field.dist(p[0], p[1]) for p in verts], dtype=np.float64)
    gdirt = wear.dirt_np(edge, xy[:, 0], xy[:, 1])
    macro = wear.macro_np(xy[:, 0], xy[:, 1])
    cols = [(0.0, float(a), float(b), 1.0) for a, b in zip(gdirt, macro)]
    mb = MeshBuilder()
    for p in verts:
        mb.add_vertex((p[0], p[1], 0.0))
    uv_fns = [planar_uv(SPECS[n].tile) for n in names]
    for ids, slot in quads:
        mb.add_face_idx(ids, slot, False, uv_fn=uv_fns[slot], colors=[cols[i] for i in ids])
    safe = re.sub(r"[^A-Za-z0-9]+", "_", hp.name).strip("_") or "H"
    me = mb.build(f"MAD_Pavement_Heliport_{safe}", [ctx.mats(n) for n in names])
    ctx.add_object(f"Pavement_Heliport_{safe}", me, CAT)


def build(ctx) -> None:
    plan = ctx.plan
    if not plan.heliports:
        return
    fp = pav.get_footprint(ctx)
    wear = pav.WearModel(plan, ctx.settings, pav._seed(ctx))
    for hp in plan.heliports:
        _build_one(ctx, hp, fp, wear)
