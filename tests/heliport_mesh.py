"""Blender-level audit of the generated heliport meshes (quads only except fillet cusps, no non-manifold edges,
all normals up, flat, no loose / degenerate geometry). run: python3 tests/heliport_mesh.py (exit 1 on a defect)"""
import os, sys, math, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import harness, bpy, importlib, bmesh
bpy.ops.wm.read_factory_settings(use_empty=True); harness.install_addon()
builder = importlib.import_module("military_airbase_designer.layout.builder")
root = builder.create_base(bpy.context, "V", "RUNWAY_FOCUS")
root.mad.materials.generate_textures = False; root.mad.quality = 'PREVIEW'
def add(st, side, cls, stands, shape, surf):
    h = root.mad.heliports.add(); h.name = f"H{len(root.mad.heliports)}"; h.station = st; h.side = side; h.hclass = cls; h.stands = stands; h.tlof_shape = shape; h.fato_surface = surf
add(700, 'LEFT', 'MEDIUM', 4, 'ROUND', 'PAVED'); add(-800, 'LEFT', 'ATTACK', 0, 'SQUARE', 'TURF'); add(1300, 'LEFT', 'HEAVY', 7, 'ROUND', 'PAVED'); add(-1000, 'RIGHT', 'MEDIUM', 3, 'ROUND', 'PAVED')
ctx = builder.build_base(root)
for w in ctx.warnings: print("WARN", w)
for ob in bpy.data.objects:
    if "Pavement_Heliport" not in ob.name: continue
    bm = bmesh.new(); bm.from_mesh(ob.data); bm.normal_update()
    sizes = collections.Counter(len(f.verts) for f in bm.faces)
    nonman = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    loose = sum(1 for v in bm.verts if not v.link_faces)
    down = sum(1 for f in bm.faces if f.normal.z < 0.99)
    zs = {round(v.co.z, 6) for v in bm.verts}
    degen = sum(1 for f in bm.faces if f.calc_area() < 1e-6)
    # vertex valence histogram
    val = collections.Counter(len(v.link_edges) for v in bm.verts)
    print(ob.name, "faces", dict(sizes), "nonmanifold", nonman, "loose", loose, "not-up", down, "z", zs, "degenerate", degen, "valence", dict(sorted(val.items())))
    bm.free()

bad = 0
for ob in bpy.data.objects:
    if "Pavement_Heliport" not in ob.name: continue
    bm = bmesh.new(); bm.from_mesh(ob.data); bm.normal_update()
    if (sum(1 for e in bm.edges if len(e.link_faces) > 2) or any(not v.link_faces for v in bm.verts)
            or any(f.normal.z < 0.99 or f.calc_area() < 1e-6 or len(f.verts) > 4 for f in bm.faces)):
        bad += 1; print("DEFECT", ob.name)
    bm.free()
print("heliport objects", sum(1 for o in bpy.data.objects if "Pavement_Heliport" in o.name), "defective", bad)
sys.exit(1 if bad else 0)
