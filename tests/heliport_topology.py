"""Pure topology audit of the heliport quad meshes over a parameter matrix (class x TLOF x FATO surface x stands x side).

run: python3 tests/heliport_topology.py   (needs the pip `bpy` module for mathutils; exits 1 on any defect)"""
import sys, math, itertools, types
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bpy  # mathutils for geom2d
from military_airbase_designer.layout import heliport as H
from military_airbase_designer.geometry import gridmesh as GM
from military_airbase_designer.core import geom2d as g

def item(**kw):
    d = dict(name="H1", hclass="MEDIUM", tlof_shape="ROUND", fato_surface="PAVED", stands=0, route_length=0.0, heading=0.0,
             edge_lights=True, centerline_lights=False, perimeter_lights=True)
    d.update(kw); return types.SimpleNamespace(**d)

def audit(hp):
    soup, names, info = GM.heliport_soup(hp)
    st = soup.stats()
    polys = [pc.poly() for pc in hp.pieces] + [f.poly() for f in hp.fillets] + ([hp.fato_poly()] if hp.fato_paved else [])
    want = sum(g.area(p) for p in polys)
    errs = []
    if abs(st['area'] - want) > 1e-6 * max(1, want): errs.append(f"area {st['area']:.4f} != {want:.4f}")
    if st['nonmanifold']: errs.append(f"nonmanifold {st['nonmanifold']}")
    # boundary edges must lie on the outline of the union of polys (not interior)
    def inside(pt): return any(g.point_in_poly(pt, p) for p in polys)
    cracks = 0
    for a, b in st['boundary']:
        pa, pb = soup.verts[a], soup.verts[b]
        mid = ((pa[0]+pb[0])/2, (pa[1]+pb[1])/2)
        d = (pb[0]-pa[0], pb[1]-pa[1]); L = math.hypot(*d); n = (-d[1]/L, d[0]/L)
        e = 0.02
        if inside((mid[0]+n[0]*e, mid[1]+n[1]*e)) and inside((mid[0]-n[0]*e, mid[1]-n[1]*e)): cracks += 1
    if cracks: errs.append(f"{cracks} crack edges")
    if st['tris'] != 2 * len(hp.fillets): errs.append(f"{st['tris']} triangles, expected {2*len(hp.fillets)} (fillet cusps)")
    if st['max_valence'] > 5: errs.append(f"valence {st['max_valence']}")
    return st, errs

bad = 0; n = 0
for cls, shape, surf, stands, side in itertools.product(H.HELI_CLASSES, ('SQUARE','ROUND'), ('PAVED','TURF'), (0,1,2,3,6,8), (1,-1)):
    hp = H.plan_heliport(item(hclass=cls, tlof_shape=shape, fato_surface=surf, stands=stands), 0, 100.0, side*150.0, side, True)
    st, errs = audit(hp); n += 1
    if errs:
        bad += 1; print("BAD", cls, shape, surf, stands, side, errs)
    elif (cls, shape, surf, stands, side) in (('MEDIUM','ROUND','PAVED',0,1), ('MEDIUM','ROUND','PAVED',6,1), ('HEAVY','SQUARE','TURF',3,-1)):
        print("ok", cls, shape, surf, stands, side, f"verts {st['verts']} quads {st['quads']} tris {st['tris']} valence {st['max_valence']} aspect {st['worst_aspect']:.2f}")
print("cases", n, "bad", bad)
sys.exit(1 if bad else 0)
