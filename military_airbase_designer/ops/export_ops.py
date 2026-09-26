"""Game-engine export: FBX / glTF / OBJ with joined instances, LODs, UCX collision, textures and a material index.

Public API
----------
``export_base(context, root, directory=None, settings=None) -> dict``
    Exports every generated mesh of the base ``root`` and returns
    ``{"files": [...], "textures": [...], "report": str, "warnings": [...], "objects": int, "directory": str}``.
``bpy.ops.mad.export_base()``
    Operator wrapper ("Export Game Asset") using ``root.mad.export`` of the active base.

How the export is built (nothing in the scene is modified)
---------------------------------------------------------
* The generated objects (custom prop ``mad_generated``, type MESH) are collected below the root.  Every one of
  them is copied into a temporary collection; the copies are exported with ``use_selection`` and removed again
  in a ``finally`` block, so the user's scene, meshes and materials stay untouched.
* The root Empty carries the magnetic heading.  Its world matrix is applied to the copies, so the export is in
  **world space**: the runway comes out rotated to its heading and the base root is the world origin.
* Pavement, markings and terrain (large unique meshes) are baked to world space with an identity transform
  (pivot = world origin).  Structures / props keep their own transform (pivot at the object), which is what an
  engine wants for a placeable static mesh.
* ``join_by_category``: all linked duplicates that share one mesh datablock become ONE mesh per
  (category, mesh) - e.g. every runway edge light -> ``SM_Lighting_EdgeLight`` - built with bmesh (headless
  safe, no ``bpy.ops.object.join``); material slots are merged by material name.
* Naming: objects ``SM_<Category>_<Name>`` (Unreal static-mesh convention), collision ``UCX_<SM name>_00``,
  LODs ``SM_<Category>_<Name>_LOD0.._LODn``; materials keep ``M_MAD_*`` and textures ``T_MAD_*``.
* LODs (``lods``): props / structures with more than ``LOD_MIN_TRIS`` triangles get ``lod_count`` levels from a
  Decimate modifier (ratios 0.5 / 0.25 / 0.1) evaluated through the depsgraph.  Blender's FBX exporter cannot
  write FBX ``LodGroup`` nodes, so the widely used ``_LOD#`` naming convention is used instead (Unity builds a
  LODGroup from it automatically; Unreal: assign the LOD meshes in the Static Mesh editor or use the
  Interchange/Datasmith LOD naming import).
* Collision (``collision``): pavement / terrain get a decimated ``UCX_`` copy (ratio 0.2), structures a UCX
  bounding box; UCX meshes carry no materials.  Unreal treats UCX meshes as convex hulls, so for the (flat)
  pavement "Use Complex Collision As Simple" is the more accurate choice - documented in README_export.txt.
* Markings: ``export_markings_as_decals`` True keeps one mesh per paint colour (default); False joins every
  marking mesh into ``SM_Markings_All``.
* Axis / scale presets (FBX): UNREAL / UNITY / GODOT write the standard Y-up FBX (Blender's ``-Z`` forward /
  ``Y`` up); every engine converts that to its own frame (Unreal Z-up X-forward, Unity Y-up).  BLENDER writes
  the scene axes unchanged (``-Y`` forward / ``Z`` up).  Scale: UNREAL and BLENDER use Blender's default
  ``FBX_SCALE_NONE`` (file unit cm, a x100 scale on the root transforms - Unreal bakes that into the static
  mesh); UNITY / GODOT use ``FBX_SCALE_ALL`` (file unit metres, clean unit transforms - Unity shows
  File Scale 1).  ``scale_cm`` pre-scales the temporary geometry x100 and writes a pure centimetre file
  (unit factor 1, vertex data in cm, identity scales) for FBX and OBJ; glTF is metres by specification and
  ignores it.  glTF is always written +Y up (specification), whatever the axis preset.
* Textures (``copy_textures``): every ``T_MAD_*`` image referenced by the exported materials plus the whole
  texture set of each material (incl. the engine packed map) is copied into ``<dir>/Textures/``; image paths are
  retargeted there for the duration of the export (``path_mode='RELATIVE'``) so the FBX / MTL reference
  ``Textures/<file>`` and the folder is self-contained.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Sequence

import bpy
import bmesh
import numpy as np
from mathutils import Matrix, Vector

from ..core import constants as C
from ..core import scene_utils as su
from ..materials import library
from ..textures import io as texio

# --------------------------------------------------------------------------- conventions
SM_PREFIX = "SM_"                 # Unreal static-mesh prefix
UCX_PREFIX = "UCX_"               # Unreal convex collision prefix (UCX_<RenderMeshName>_##)
TEXTURE_SUBDIR = "Textures"
TEMP_COLLECTION = "MAD_export_tmp"
INDEX_FILE = "material_index.json"
README_FILE = "README_export.txt"

LOD_CATEGORIES = {"Structures", "Perimeter", "Navaids", "Signage", "Lighting"}
LOD_MIN_TRIS = 2000
LOD_RATIOS = (0.5, 0.25, 0.1)                 # LOD1, LOD2, LOD3
BAKE_CATEGORIES = {"Pavement", "Markings", "Terrain"}   # baked to world space, pivot = world origin
COLLISION_DECIMATE = {"Pavement", "Terrain"}
COLLISION_BOX = {"Structures"}
COLLISION_DECIMATE_RATIO = 0.2
COLLISION_MIN_TRIS = 500

# FBX axis_forward / axis_up per preset.  UNREAL/UNITY/GODOT: standard Y-up FBX (Blender default), the engines
# convert it themselves (Unreal: Z-up, Blender +Y -> Unreal -Y; Unity: Y-up).  BLENDER: scene axes unchanged.
FBX_AXIS = {'UNREAL': ('-Z', 'Y'), 'UNITY': ('-Z', 'Y'), 'GODOT': ('-Z', 'Y'), 'BLENDER': ('-Y', 'Z')}
OBJ_AXIS = {'UNREAL': ('NEGATIVE_Z', 'Y'), 'UNITY': ('NEGATIVE_Z', 'Y'), 'GODOT': ('NEGATIVE_Z', 'Y'),
            'BLENDER': ('Y', 'Z')}
FORMAT_EXT = {'FBX': ".fbx", 'GLTF': ".glb", 'GLTF_SEP': ".gltf", 'OBJ': ".obj"}

# channel layout of the packed map per engine preset (mirrors textures.io.pack_maps)
PACKED_CHANNELS = {
    'UNREAL': {"R": "AO", "G": "Roughness", "B": "Metallic"},
    'UNITY_URP': {"R": "Metallic", "G": "Metallic", "B": "Metallic", "A": "Smoothness (1 - roughness)"},
    'UNITY_HDRP': {"R": "Metallic", "G": "AO", "B": "Detail mask (0.5)", "A": "Smoothness (1 - roughness)"},
    'GLTF': {"R": "AO (occlusion)", "G": "Roughness", "B": "Metallic"},
    'SEPARATE': {},
}
VERTEX_COLOR_USAGE = {"attribute": "Wear", "R": "rubber deposits (touchdown zone darkening)",
                      "G": "edge dirt", "B": "macro colour variation", "A": "paint wear (markings alpha mask)"}
STANDARD_MAPS = (library.MAP_BASECOLOR, library.MAP_NORMAL, library.MAP_ROUGHNESS, library.MAP_AO, library.MAP_HEIGHT)
COLOR_MAPS = {library.MAP_BASECOLOR, "Emissive"}


# --------------------------------------------------------------------------- small helpers
def _get(settings, name: str, default):
    return getattr(settings, name, default) if settings is not None else default


def _tri_count(me: bpy.types.Mesh) -> int:
    n = len(me.polygons)
    if not n:
        return 0
    lt = np.empty(n, dtype=np.int32)
    me.polygons.foreach_get("loop_total", lt)
    return int(lt.sum()) - 2 * n


def _clean_name(raw: str) -> str:
    s = re.sub(r"\.\d{3}$", "", raw)
    if s.startswith(C.PREFIX):
        s = s[len(C.PREFIX):]
    return s


def _asset_name(category: str, raw: str) -> str:
    n = _clean_name(raw)
    if n.startswith(category + "_"):
        n = n[len(category) + 1:]
    n = re.sub(r"[^A-Za-z0-9_]+", "_", n).strip("_") or "Mesh"
    cat = re.sub(r"[^A-Za-z0-9_]+", "_", category) or "Misc"
    return f"{SM_PREFIX}{cat}_{n}"


def _scaled_matrix(m: Matrix, k: float) -> Matrix:
    """Same rotation / scale, translation multiplied by k (for centimetre export of transform-keeping objects)."""
    if k == 1.0:
        return m.copy()
    loc, rot, sca = m.decompose()
    return Matrix.LocRotScale(loc * k, rot, sca)


def _is_identity(m: Matrix, eps: float = 1e-9) -> bool:
    ident = Matrix.Identity(4)
    return all(abs(m[i][j] - ident[i][j]) < eps for i in range(4) for j in range(4))


def _op_kwargs(op, kwargs: dict) -> dict:
    """Drop keyword arguments the operator of this Blender version does not know (4.2 vs 5.x differences)."""
    props = {p.identifier for p in op.get_rna_type().properties}
    return {k: v for k, v in kwargs.items() if k in props}


def resolve_directory(raw: str | None, warnings: list[str]) -> str:
    """Absolute export folder. '//' paths need a saved .blend; otherwise a temp folder is used (with a warning)."""
    raw = raw or "//export/"
    if raw.startswith("//") and not bpy.data.filepath:
        path = os.path.join(tempfile.gettempdir(), "mad_export")
        warnings.append(f"Blend file is not saved: '{raw}' cannot be resolved, exporting to {path}")
    else:
        path = bpy.path.abspath(raw)
    path = os.path.normpath(os.path.abspath(path))
    os.makedirs(path, exist_ok=True)
    return path


def collect_objects(root: bpy.types.Object, include_terrain: bool = True) -> list[bpy.types.Object]:
    """Generated mesh objects of the base (optionally without the Terrain category)."""
    out = []
    for ob in su.children_recursive(root):
        if not ob.get(su.TAG_PROP) or ob.type != 'MESH' or ob.data is None:
            continue
        if not include_terrain and ob.get(su.CATEGORY_PROP, "") == "Terrain":
            continue
        out.append(ob)
    out.sort(key=lambda o: (o.get(su.CATEGORY_PROP, ""), o.name))
    return out


# --------------------------------------------------------------------------- temporary export set
@dataclass
class ExportItem:
    ob: bpy.types.Object
    category: str
    name: str
    kind: str = "mesh"                      # mesh | lod | ucx
    sources: int = 1                        # number of scene objects merged into this item
    tris: int = 0
    materials: list[str] = field(default_factory=list)


class _TempScene:
    """Owns every temporary datablock of one export and removes all of them in ``cleanup``."""

    def __init__(self, scene: bpy.types.Scene):
        self.scene = scene
        self.col = bpy.data.collections.new(TEMP_COLLECTION)
        scene.collection.children.link(self.col)
        self.objects: list[bpy.types.Object] = []
        self.meshes: list[bpy.types.Mesh] = []
        self.used_names: set[str] = set()

    def own(self, me: bpy.types.Mesh) -> bpy.types.Mesh:
        self.meshes.append(me)
        return me

    def new_mesh(self, name: str) -> bpy.types.Mesh:
        return self.own(bpy.data.meshes.new(name))

    def unique(self, name: str) -> str:
        base, n = name, 2
        while name in self.used_names or name in bpy.data.objects:
            name = f"{base}_{n}"
            n += 1
        self.used_names.add(name)
        return name

    def add(self, name: str, me: bpy.types.Mesh, matrix: Matrix | None = None) -> bpy.types.Object:
        ob = bpy.data.objects.new(name, me)
        self.col.objects.link(ob)
        if matrix is not None:
            ob.matrix_world = matrix
        self.objects.append(ob)
        return ob

    def cleanup(self) -> None:
        for ob in self.objects:
            try:
                bpy.data.objects.remove(ob, do_unlink=True)
            except ReferenceError:
                pass
        for me in self.meshes:
            try:
                if me.users == 0:
                    bpy.data.meshes.remove(me)
            except ReferenceError:
                pass
        try:
            bpy.data.collections.remove(self.col)
        except ReferenceError:
            pass
        self.objects, self.meshes = [], []


class _Selection:
    """Select exactly the temp objects for ``use_selection`` exporters; restores the previous selection."""

    def __init__(self, view_layer, objects: Sequence[bpy.types.Object]):
        self.vl = view_layer
        self.objects = list(objects)
        self.prev_sel: list[bpy.types.Object] = []
        self.prev_active = None

    def __enter__(self):
        try:
            self.prev_sel = [o for o in self.vl.objects if o.select_get(view_layer=self.vl)]
            self.prev_active = self.vl.objects.active
            for o in self.prev_sel:
                o.select_set(False, view_layer=self.vl)
            for o in self.objects:
                o.select_set(True, view_layer=self.vl)
            if self.objects:
                self.vl.objects.active = self.objects[0]
        except (RuntimeError, AttributeError):
            pass
        return self

    def __exit__(self, *exc):
        try:
            for o in self.objects:
                try:
                    o.select_set(False, view_layer=self.vl)
                except (RuntimeError, ReferenceError):
                    pass
            for o in self.prev_sel:
                try:
                    o.select_set(True, view_layer=self.vl)
                except (RuntimeError, ReferenceError):
                    pass
            try:
                self.vl.objects.active = self.prev_active
            except (RuntimeError, ReferenceError):
                pass
        except AttributeError:
            pass
        return False


class _ImageRetarget:
    """Temporarily point image datablocks at the copied texture files (restored afterwards)."""

    def __init__(self, mapping: dict[bpy.types.Image, str]):
        self.mapping = mapping
        self.saved: list[tuple[bpy.types.Image, str]] = []

    def __enter__(self):
        for img, new_path in self.mapping.items():
            try:
                self.saved.append((img, img.filepath_raw))
                img.filepath_raw = new_path
            except (AttributeError, ReferenceError):
                pass
        return self

    def __exit__(self, *exc):
        for img, old in self.saved:
            try:
                img.filepath_raw = old
            except (AttributeError, ReferenceError):
                pass
        return False


# --------------------------------------------------------------------------- mesh operations (headless safe)
def _source_mesh(context, ob: bpy.types.Object, tmp: _TempScene, cache: dict[str, bpy.types.Mesh]) -> bpy.types.Mesh:
    """Local-space copy of the object's (evaluated, when it has modifiers) mesh, one per source datablock."""
    key = ob.data.name
    me = cache.get(key)
    if me is not None:
        return me
    if ob.modifiers:
        dg = context.evaluated_depsgraph_get()
        ev = ob.evaluated_get(dg)
        me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
        if not me.materials and ob.data.materials:
            for m in ob.data.materials:
                me.materials.append(m)
    else:
        me = ob.data.copy()
    me.name = "MADexp_" + _clean_name(ob.data.name)
    tmp.own(me)
    cache[key] = me
    return me


def join_meshes(name: str, parts: Sequence[tuple[bpy.types.Mesh, Matrix | None]], tmp: _TempScene) -> bpy.types.Mesh:
    """Merge (mesh, matrix) parts into one mesh with bmesh; material slots merged by material name.

    UVs, colour attributes (``Wear``), smooth flags and sharp edges are preserved by bmesh.
    """
    bm = bmesh.new()
    materials: list[bpy.types.Material | None] = []
    slot_of: dict[str, int] = {}
    for me, m in parts:
        remap = []
        slots = list(me.materials) if len(me.materials) else [None]
        for mat in slots:
            key = mat.name if mat is not None else "__none__"
            if key not in slot_of:
                slot_of[key] = len(materials)
                materials.append(mat)
            remap.append(slot_of[key])
        cp = me.copy()
        try:
            n = len(cp.polygons)
            if n and any(r != i for i, r in enumerate(remap)):
                idx = np.empty(n, dtype=np.int32)
                cp.polygons.foreach_get("material_index", idx)
                idx = np.asarray(remap, dtype=np.int32)[np.clip(idx, 0, len(remap) - 1)]
                cp.polygons.foreach_set("material_index", idx)
            if m is not None and not _is_identity(m):
                cp.transform(m)
            bm.from_mesh(cp)
        finally:
            bpy.data.meshes.remove(cp)
    out = tmp.new_mesh(name)
    bm.to_mesh(out)
    bm.free()
    for mat in materials:
        out.materials.append(mat)
    out.update()
    return out


def _baked_copy(name: str, me: bpy.types.Mesh, m: Matrix, tmp: _TempScene) -> bpy.types.Mesh:
    cp = tmp.own(me.copy())
    cp.name = name
    if not _is_identity(m):
        cp.transform(m)
    cp.update()
    return cp


def _decimated_mesh(context, ob: bpy.types.Object, ratio: float, name: str, tmp: _TempScene) -> bpy.types.Mesh:
    """Evaluate a temporary Decimate modifier on ``ob`` (a temp object) into a new mesh datablock."""
    mod = ob.modifiers.new("MAD_decimate", 'DECIMATE')
    try:
        mod.decimate_type = 'COLLAPSE'
        mod.ratio = ratio
        mod.use_collapse_triangulate = True
        dg = context.evaluated_depsgraph_get()
        ev = ob.evaluated_get(dg)
        me = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=dg)
        if not me.materials and ob.data.materials:
            for m in ob.data.materials:
                me.materials.append(m)
    finally:
        ob.modifiers.remove(mod)
    me.name = name
    return tmp.own(me)


def _box_mesh(name: str, me: bpy.types.Mesh, tmp: _TempScene) -> bpy.types.Mesh:
    """Axis-aligned bounding box (in the mesh's local space) as a closed 6-quad mesh."""
    n = len(me.vertices)
    if n == 0:
        lo, hi = Vector((0, 0, 0)), Vector((0, 0, 0))
    else:
        co = np.empty(n * 3, dtype=np.float32)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        lo, hi = co.min(axis=0), co.max(axis=0)
    x0, y0, z0 = (float(v) for v in lo)
    x1, y1, z1 = (float(v) for v in hi)
    verts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    box = tmp.new_mesh(name)
    box.from_pydata(verts, [], faces)
    box.update()
    return box


# --------------------------------------------------------------------------- export set construction
def build_export_set(context, root: bpy.types.Object, objects: Sequence[bpy.types.Object], settings, tmp: _TempScene,
                     warnings: list[str], scale: float = 1.0) -> list[ExportItem]:
    """Create the temporary, engine-named objects (joined / baked / LOD / UCX) for the given scene objects."""
    join = bool(_get(settings, "join_by_category", True))
    lods = bool(_get(settings, "lods", True))
    lod_count = int(_get(settings, "lod_count", 3))
    collision = bool(_get(settings, "collision", True))
    decals = bool(_get(settings, "export_markings_as_decals", True))
    S = Matrix.Scale(scale, 4)
    cache: dict[str, bpy.types.Mesh] = {}
    items: list[ExportItem] = []

    groups: dict[tuple[str, str], list[bpy.types.Object]] = {}
    for ob in objects:
        cat = ob.get(su.CATEGORY_PROP, "") or "Misc"
        groups.setdefault((cat, ob.data.name), []).append(ob)

    def register(ob, cat, name, kind="mesh", sources=1):
        me = ob.data
        item = ExportItem(ob, cat, name, kind, sources, _tri_count(me), [m.name for m in me.materials if m])
        items.append(item)
        return item

    # ---- markings joined into one mesh when not exported as decals
    if not decals:
        mk_parts = []
        mk_sources = 0
        for (cat, _mname), obs in list(groups.items()):
            if cat != "Markings":
                continue
            for ob in obs:
                mk_parts.append((_source_mesh(context, ob, tmp, cache), S @ ob.matrix_world))
                mk_sources += 1
            del groups[(cat, _mname)]
        if mk_parts:
            name = tmp.unique(f"{SM_PREFIX}Markings_All")
            me = join_meshes(name, mk_parts, tmp)
            register(tmp.add(name, me), "Markings", name, sources=mk_sources)

    for (cat, _mname), obs in groups.items():
        first = obs[0]
        src = _source_mesh(context, first, tmp, cache)
        if len(src.polygons) == 0:
            warnings.append(f"{first.name}: empty mesh skipped")
            continue
        if len(obs) > 1 and join:
            # linked duplicates -> one static mesh in world space
            name = tmp.unique(_asset_name(cat, first.data.name))
            parts = [(_source_mesh(context, ob, tmp, cache), S @ ob.matrix_world) for ob in obs]
            me = join_meshes(name, parts, tmp)
            register(tmp.add(name, me), cat, name, sources=len(obs))
            continue
        if len(obs) > 1:
            # keep instancing: one shared (local-space) mesh, one object per placement
            if scale != 1.0 and "MADexp_scaled" not in src:
                src.transform(S)
                src["MADexp_scaled"] = True
            base = _asset_name(cat, first.data.name)
            for i, ob in enumerate(obs):
                name = tmp.unique(f"{base}_{i:03d}")
                register(tmp.add(name, src, _scaled_matrix(ob.matrix_world, scale)), cat, name)
            continue
        # unique mesh
        ob = first
        base = _asset_name(cat, ob.name)
        do_lod = lods and lod_count > 1 and cat in LOD_CATEGORIES and _tri_count(src) > LOD_MIN_TRIS
        name = tmp.unique(base + ("_LOD0" if do_lod else ""))
        if cat in BAKE_CATEGORIES:
            me = _baked_copy(name, src, S @ ob.matrix_world, tmp)
            matrix = None
        else:
            if scale != 1.0 and "MADexp_scaled" not in src:
                src.transform(S)
                src["MADexp_scaled"] = True
            me, matrix = src, _scaled_matrix(ob.matrix_world, scale)
        main = tmp.add(name, me, matrix)
        item = register(main, cat, name)
        # ---- LODs
        if do_lod:
            for level, ratio in enumerate(LOD_RATIOS[:max(0, lod_count - 1)], start=1):
                lname = tmp.unique(f"{base}_LOD{level}")
                lme = _decimated_mesh(context, main, ratio, lname, tmp)
                register(tmp.add(lname, lme, matrix), cat, lname, kind="lod")
        # ---- collision
        if collision and cat in COLLISION_DECIMATE:
            cname = tmp.unique(f"{UCX_PREFIX}{name}_00")
            if item.tris > COLLISION_MIN_TRIS:
                cme = _decimated_mesh(context, main, COLLISION_DECIMATE_RATIO, cname, tmp)
            else:
                cme = _baked_copy(cname, me, Matrix.Identity(4), tmp)
            cme.materials.clear()
            register(tmp.add(cname, cme, matrix), cat, cname, kind="ucx")
        elif collision and cat in COLLISION_BOX:
            cname = tmp.unique(f"{UCX_PREFIX}{name}_00")
            cme = _box_mesh(cname, me, tmp)
            register(tmp.add(cname, cme, matrix), cat, cname, kind="ucx")
    return items


# --------------------------------------------------------------------------- textures
def _image_path(img: bpy.types.Image) -> str | None:
    if img is None or img.source not in {'FILE', 'SEQUENCE'} or img.packed_file is not None:
        return None
    try:
        p = bpy.path.abspath(img.filepath, library=img.library)
    except (AttributeError, TypeError):
        p = bpy.path.abspath(img.filepath)
    p = os.path.normpath(p)
    return p if os.path.isfile(p) else None


def _material_images(mat: bpy.types.Material) -> list[bpy.types.Image]:
    out = []
    nt = getattr(mat, "node_tree", None)
    if nt is None:
        return out
    for node in nt.nodes:
        if node.bl_idname == 'ShaderNodeTexImage' and node.image is not None and node.image not in out:
            out.append(node.image)
    return out


def _spec_of(mat: bpy.types.Material) -> library.MaterialSpec | None:
    key = mat.get("mad_spec") if mat else None
    if not key and mat and mat.name.startswith(C.MAT_PREFIX):
        key = mat.name[len(C.MAT_PREFIX):]
    key = re.sub(r"\.\d{3}$", "", key or "")
    return library.SPECS.get(key)


def _copy_file(src: str, dst_dir: str, warnings: list[str]) -> str | None:
    dst = os.path.join(dst_dir, os.path.basename(src))
    try:
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
            return dst
        if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst) or os.path.getsize(src) != os.path.getsize(dst):
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copy2(src, dst)
        return dst
    except OSError as exc:
        warnings.append(f"texture copy failed for {os.path.basename(src)}: {exc}")
        return None


def copy_textures(out_dir: str, materials: Sequence[bpy.types.Material], tex_dir: str | None,
                  warnings: list[str]) -> tuple[list[str], dict[bpy.types.Image, str]]:
    """Copy referenced images + whole texture sets into <out_dir>/Textures. Returns (copied paths, image->new path)."""
    dst_dir = os.path.join(out_dir, TEXTURE_SUBDIR)
    copied: list[str] = []
    retarget: dict[bpy.types.Image, str] = {}
    for mat in materials:
        for img in _material_images(mat):
            src = _image_path(img)
            if src is None:
                continue
            dst = _copy_file(src, dst_dir, warnings)
            if dst:
                retarget[img] = dst
                if dst not in copied:
                    copied.append(dst)
    sets = {s.texture_set for s in (_spec_of(m) for m in materials) if s is not None and s.texture_set}
    if sets:
        if tex_dir and os.path.isdir(tex_dir):
            for ts in sorted(sets):
                prefix = f"{C.TEX_PREFIX}{ts}_"
                for fn in sorted(os.listdir(tex_dir)):
                    if fn.startswith(prefix) and fn.lower().endswith((".png", ".exr", ".tga", ".jpg")):
                        dst = _copy_file(os.path.join(tex_dir, fn), dst_dir, warnings)
                        if dst and dst not in copied:
                            copied.append(dst)
        else:
            warnings.append(f"texture folder not found ({tex_dir}); texture sets not copied - generate textures first")
    return copied, retarget


# --------------------------------------------------------------------------- exporters
def _fbx_kwargs(filepath: str, axis: str, scale_cm: bool, path_mode: str) -> dict:
    fwd, up = FBX_AXIS.get(axis, FBX_AXIS['UNREAL'])
    if scale_cm:
        # geometry already x100: file unit = cm (UnitScaleFactor 1), vertex data in cm, identity scales
        scale = dict(apply_unit_scale=False, apply_scale_options='FBX_SCALE_ALL', global_scale=0.01)
    elif axis in ('UNITY', 'GODOT'):
        # file unit = metres (UnitScaleFactor 100), no x100 on the transforms (Unity: File Scale 1)
        scale = dict(apply_unit_scale=True, apply_scale_options='FBX_SCALE_ALL', global_scale=1.0)
    else:
        # Blender default: file unit cm, x100 on the root transforms (Unreal bakes it into the static mesh)
        scale = dict(apply_unit_scale=True, apply_scale_options='FBX_SCALE_NONE', global_scale=1.0)
    kw = dict(filepath=filepath, use_selection=True, use_visible=False, use_active_collection=False,
              object_types={'MESH'}, use_mesh_modifiers=True, mesh_smooth_type='FACE', use_subsurf=False,
              use_mesh_edges=False, use_tspace=False, use_triangles=False, use_custom_props=False,
              colors_type='LINEAR', prioritize_active_color=True,
              axis_forward=fwd, axis_up=up, bake_space_transform=False, use_space_transform=True,
              add_leaf_bones=False, bake_anim=False, path_mode=path_mode, embed_textures=False, batch_mode='OFF',
              use_metadata=True)
    kw.update(scale)
    return kw


def _gltf_kwargs(filepath: str, fmt: str, axis: str) -> dict:
    # glTF is +Y up by specification and every importer (incl. Blender's) assumes it, so the axis preset has no
    # effect here; the engines convert to their own frame on import.
    kw = dict(filepath=filepath, export_format='GLTF_SEPARATE' if fmt == 'GLTF_SEP' else 'GLB',
              use_selection=True, use_visible=False, use_renderable=False, use_active_collection=False,
              export_apply=True, export_texcoords=True, export_normals=True, export_tangents=False,
              export_materials='EXPORT', export_image_format='AUTO', export_attributes=True,
              export_vertex_color='ACTIVE', export_all_vertex_colors=True, export_active_vertex_color_when_no_material=True,
              export_yup=True, export_extras=False, export_cameras=False, export_lights=False,
              export_animations=False, export_skins=False, export_morph=False, export_texture_dir=TEXTURE_SUBDIR,
              export_keep_originals=False, export_unused_images=False, will_save_settings=False)
    return kw


def _obj_kwargs(filepath: str, axis: str, path_mode: str) -> dict:
    fwd, up = OBJ_AXIS.get(axis, OBJ_AXIS['UNREAL'])
    return dict(filepath=filepath, export_selected_objects=True, export_materials=True, export_uv=True,
                export_normals=True, export_colors=True, export_pbr_extensions=True, export_triangulated_mesh=False,
                apply_modifiers=True, export_smooth_groups=False, export_object_groups=False, export_material_groups=False,
                export_vertex_groups=False, export_animation=False, path_mode=path_mode, forward_axis=fwd, up_axis=up,
                global_scale=1.0)


def run_exporter(context, fmt: str, filepath: str, objects: Sequence[bpy.types.Object], axis: str, scale_cm: bool,
                 path_mode: str) -> list[str]:
    """Call the Blender exporter for ``fmt`` on ``objects`` (must be linked to the view layer). Returns files written."""
    view_layer = context.view_layer
    view_layer.update()
    if fmt == 'FBX':
        op, kw = bpy.ops.export_scene.fbx, _fbx_kwargs(filepath, axis, scale_cm, path_mode)
    elif fmt in ('GLTF', 'GLTF_SEP'):
        op, kw = bpy.ops.export_scene.gltf, _gltf_kwargs(filepath, fmt, axis)
    elif fmt == 'OBJ':
        op, kw = bpy.ops.wm.obj_export, _obj_kwargs(filepath, axis, path_mode)
    else:
        raise ValueError(f"unknown export format {fmt!r}")
    kw = _op_kwargs(op, kw)
    before = set(os.listdir(os.path.dirname(filepath)))
    with _Selection(view_layer, objects):
        active = objects[0]
        try:
            with context.temp_override(selected_objects=list(objects), active_object=active, object=active):
                res = op(**kw)
        except (AttributeError, TypeError):
            res = op(**kw)
    if 'FINISHED' not in res:
        raise RuntimeError(f"{fmt} exporter returned {res}")
    files = [filepath] if os.path.exists(filepath) else []
    stem = os.path.splitext(os.path.basename(filepath))[0]
    for fn in sorted(set(os.listdir(os.path.dirname(filepath))) - before):
        p = os.path.join(os.path.dirname(filepath), fn)
        if p not in files and fn.startswith(stem) and os.path.isfile(p):
            files.append(p)
    if fmt == 'OBJ':
        mtl = os.path.splitext(filepath)[0] + ".mtl"
        if os.path.exists(mtl) and mtl not in files:
            files.append(mtl)
    return files


# --------------------------------------------------------------------------- material index + readme
def _packed_map_name(engine: str) -> str | None:
    try:
        z = np.zeros((1, 1), dtype=np.float32)
        keys = list(texio.pack_maps(engine, z, z).keys())
        return keys[0] if keys else None
    except Exception:  # pragma: no cover - never fail the export over the index
        return None


def _maps_of_set(texture_set: str, spec: library.MaterialSpec, tex_dir: str | None, packed: str | None,
                 tex_out: str | None) -> dict[str, dict]:
    names = list(STANDARD_MAPS)
    if spec.alpha == 'MASKED':
        names.append(library.MAP_OPACITY)
    if spec.metallic > 0.0:
        names.append(library.MAP_METALLIC)
    if packed:
        names.append(packed)
    if tex_dir and os.path.isdir(tex_dir):
        prefix = f"{C.TEX_PREFIX}{texture_set}_"
        for fn in os.listdir(tex_dir):
            if fn.startswith(prefix) and fn.lower().endswith(".png"):
                mp = fn[len(prefix):-4]
                if mp not in names:
                    names.append(mp)
    out = {}
    for mp in names:
        fn = library.texture_filename(texture_set, mp)
        exists = bool(tex_dir) and os.path.isfile(os.path.join(tex_dir, fn))
        entry = {"file": f"{TEXTURE_SUBDIR}/{fn}", "colorspace": "sRGB" if mp in COLOR_MAPS else "Non-Color (linear)",
                 "exists": exists}
        if tex_out is not None:
            entry["exported"] = os.path.isfile(os.path.join(tex_out, fn))
        if mp == packed:
            entry["channels"] = PACKED_CHANNELS.get(_engine_of(packed), {})
        if mp == library.MAP_HEIGHT:
            entry["bit_depth"] = 16
        out[mp] = entry
    return out


def _engine_of(packed: str) -> str:
    return {library.MAP_ORM: 'UNREAL', library.MAP_METALSMOOTH: 'UNITY_URP', library.MAP_MASK: 'UNITY_HDRP',
            library.MAP_METALROUGH: 'GLTF'}.get(packed, 'SEPARATE')


def write_material_index(out_dir: str, items: Sequence[ExportItem], mat_settings, export_settings, tex_dir: str | None,
                         files: Sequence[str], textures_copied: bool) -> str:
    engine = _get(mat_settings, "engine", 'UNREAL')
    directx = bool(_get(mat_settings, "normal_directx", True))
    packed = _packed_map_name(engine)
    tex_out = os.path.join(out_dir, TEXTURE_SUBDIR) if textures_copied else None
    used_by: dict[str, list[str]] = {}
    mats: dict[str, bpy.types.Material] = {}
    for it in items:
        for m in it.ob.data.materials:
            if m is None:
                continue
            mats[m.name] = m
            used_by.setdefault(m.name, []).append(it.name)
    materials = []
    for name in sorted(mats):
        m = mats[name]
        spec = _spec_of(m)
        entry: dict[str, Any] = {"name": name, "spec": spec.name if spec else None, "used_by": used_by[name]}
        if spec is not None:
            entry.update({
                "base_color": spec.color, "roughness": spec.roughness, "metallic": spec.metallic,
                "specular": spec.specular, "alpha": spec.alpha, "two_sided": spec.two_sided,
                "emission": spec.emission, "emission_strength": spec.emission_strength,
                "texture_set": spec.texture_set, "tile_m": spec.tile if spec.texture_set else None,
                "uv": "world-planar, metres / tile_m baked into UV0" if spec.texture_set else "flat colour",
                "vertex_color_channel": spec.wear_channel,
                "vertex_color_usage": VERTEX_COLOR_USAGE.get(spec.wear_channel) if spec.wear_channel else None,
                "normal_strength": spec.normal_strength, "tags": list(spec.tags),
                "maps": _maps_of_set(spec.texture_set, spec, tex_dir, packed, tex_out) if spec.texture_set else {},
            })
        else:
            try:
                entry.update({"base_color": "#%02X%02X%02X" % tuple(int(round(min(max(c, 0.0), 1.0) * 255)) for c in m.diffuse_color[:3]),
                              "roughness": m.roughness, "metallic": m.metallic, "texture_set": None, "maps": {}})
            except (AttributeError, TypeError):
                pass
        images = [os.path.basename(_image_path(i) or i.name) for i in _material_images(m)]
        if images:
            entry["image_nodes"] = images
        materials.append(entry)
    data = {
        "generator": "Military Airbase Designer", "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "format": _get(export_settings, "format", 'FBX'), "axis_preset": _get(export_settings, "axis", 'UNREAL'),
        "units": "centimetres" if (_get(export_settings, "scale_cm", False) and _get(export_settings, "format", 'FBX') in ('FBX', 'OBJ')) else "metres",
        "engine": engine,
        "normal_map": {"convention": "DirectX (Y-, green down)" if directx else "OpenGL (Y+, green up)",
                       "note": "Unreal expects DirectX; Unity, Godot and glTF expect OpenGL (flip green if it looks inverted)"},
        "packed_map": {"name": packed, "channels": PACKED_CHANNELS.get(engine, {})} if packed else None,
        "vertex_color": VERTEX_COLOR_USAGE,
        "texture_prefix": C.TEX_PREFIX, "material_prefix": C.MAT_PREFIX, "texture_dir": f"{TEXTURE_SUBDIR}/",
        "files": [os.path.basename(f) for f in files],
        "objects": [{"name": it.name, "category": it.category, "kind": it.kind, "instances_merged": it.sources,
                     "triangles": it.tris, "materials": it.materials} for it in items],
        "materials": materials,
    }
    path = os.path.join(out_dir, INDEX_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return path


def write_readme(out_dir: str, export_settings, mat_settings, base_file: str, n_lod: int, n_ucx: int) -> str:
    fmt = _get(export_settings, "format", 'FBX')
    axis = _get(export_settings, "axis", 'UNREAL')
    cm = bool(_get(export_settings, "scale_cm", False)) and fmt in ('FBX', 'OBJ')
    engine = _get(mat_settings, "engine", 'UNREAL')
    directx = bool(_get(mat_settings, "normal_directx", True))
    packed = _packed_map_name(engine)
    channels = ", ".join(f"{k}={v}" for k, v in PACKED_CHANNELS.get(engine, {}).items()) or "none (separate maps)"
    unit_note = ("vertex data in centimetres (file unit cm, identity scales)" if cm else
                 "metres; FBX unit header cm with x100 root scale (Unreal/Blender preset) or metres header (Unity/Godot preset)")
    lines = [
        f"Military Airbase Designer export - {os.path.basename(base_file)} ({fmt}, axis preset {axis}, {unit_note}).",
        f"Layout: {os.path.basename(base_file)} + {TEXTURE_SUBDIR}/ (T_MAD_*.png) + {INDEX_FILE} (every material, map file, channel packing, object list).",
        "Naming: SM_<Category>_<Name> static meshes (world space, base root = origin; pavement/markings/terrain pivots at the origin,",
        f"        structures/props keep their pivot), UCX_<SM name>_00 collision ({n_ucx}), SM_<Name>_LOD0.._LODn LOD chains ({n_lod} sets).",
        "Unreal: File > Import (FBX). Static Mesh: Combine Meshes OFF, Generate Missing Collision OFF (UCX_ hulls import automatically;",
        "        for the flat pavement/terrain prefer Collision Complexity = Use Complex Collision As Simple), Import Uniform Scale 1.0.",
        "        If meshes come in 100x too small enable Convert Scene Unit (or re-export with 'Scale to centimetres'). LODs: open the",
        "        SM_*_LOD0 asset > LOD Settings > Import LOD Level for _LOD1/_LOD2 (or use Interchange/Datasmith LOD naming import).",
        "        Textures: Normal = DirectX (Y-) expected; " + ("this export is DirectX." if directx else "this export is OpenGL - tick 'Flip Green Channel'."),
        "Unity:  drop the folder in Assets. Model tab: Scale Factor 1, Convert Units ON, Read/Write as needed; child meshes named _LOD0.._LODn",
        "        become a LODGroup automatically (Model > LODs). UCX_ meshes have no meaning in Unity: use them as MeshColliders or delete them.",
        "        Normal maps: Unity expects OpenGL (Y+); " + ("re-generate with 'DirectX normal' OFF or flip the green channel." if directx else "this export is OpenGL."),
        "Godot:  import the .glb/.gltf (recommended) or FBX (Godot 4 ufbx). Y-up, metres; textures import with 'Normal Map' = OpenGL.",
        f"Material recipe ({engine}): BaseColor (sRGB) -> Base Color; Normal (linear, {'DirectX' if directx else 'OpenGL'}) -> Normal;",
        f"        {packed or 'Roughness / AO / Metallic'} -> {channels}; Height (16-bit) -> displacement/parallax (optional);",
        "        vertex colour 'Wear': R rubber deposits, G edge dirt, B macro variation, A paint wear (markings alpha mask).",
        "        Pavement/terrain UVs are world-planar (tile size in material_index.json); paint materials use alpha mask (MASKED/dithered).",
    ]
    path = os.path.join(out_dir, README_FILE)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


# --------------------------------------------------------------------------- main entry point
def export_base(context, root: bpy.types.Object, directory: str | None = None, settings=None) -> dict:
    """Export the base under ``root`` as a self-contained game asset folder. Never modifies the scene."""
    t0 = time.time()
    settings = settings if settings is not None else root.mad.export
    mat_settings = getattr(root.mad, "materials", None)
    warnings: list[str] = []
    fmt = _get(settings, "format", 'FBX')
    axis = _get(settings, "axis", 'UNREAL')
    scale_cm = bool(_get(settings, "scale_cm", False)) and fmt in ('FBX', 'OBJ')
    if _get(settings, "scale_cm", False) and fmt not in ('FBX', 'OBJ'):
        warnings.append("scale_cm ignored: glTF is metres by specification (engines convert on import)")
    out_dir = resolve_directory(directory or _get(settings, "directory", "//export/"), warnings)
    result: dict[str, Any] = {"files": [], "textures": [], "report": "", "warnings": warnings, "objects": 0,
                              "directory": out_dir}

    objects = collect_objects(root, include_terrain=bool(_get(settings, "export_terrain", True)))
    if not objects:
        result["report"] = f"Nothing to export: base '{root.name}' has no generated meshes (build it first)"
        print("MAD export:", result["report"])
        return result

    base_name = re.sub(r"[^A-Za-z0-9_-]+", "_", getattr(root.mad, "base_name", "") or _clean_name(root.name)) or "Airbase"
    filepath = os.path.join(out_dir, base_name + FORMAT_EXT.get(fmt, ".fbx"))
    tex_dir = None
    try:
        tex_dir = texio.texture_dir(root.mad, create=False)
    except Exception:
        tex_dir = None

    scene = context.scene
    context.view_layer.update()
    tmp = _TempScene(scene)
    retarget: dict[bpy.types.Image, str] = {}
    try:
        items = build_export_set(context, root, objects, settings, tmp, warnings, scale=100.0 if scale_cm else 1.0)
        for it in items:
            if it.ob.name != it.name:
                warnings.append(f"name clash: '{it.name}' exported as '{it.ob.name}'")
                it.name = it.ob.name
        if not items:
            result["report"] = "Nothing to export: all meshes were empty"
            return result
        materials = []
        for it in items:
            for m in it.ob.data.materials:
                if m is not None and m not in materials:
                    materials.append(m)
        copied: list[str] = []
        copy_tex = bool(_get(settings, "copy_textures", True))
        if copy_tex:
            copied, retarget = copy_textures(out_dir, materials, tex_dir, warnings)
        path_mode = 'RELATIVE' if copy_tex else 'ABSOLUTE'
        with _ImageRetarget(retarget):
            files = run_exporter(context, fmt, filepath, [it.ob for it in items], axis, scale_cm, path_mode)
        if fmt == 'GLTF_SEP':
            tdir = os.path.join(out_dir, TEXTURE_SUBDIR)
            if os.path.isdir(tdir):
                for fn in sorted(os.listdir(tdir)):
                    p = os.path.join(tdir, fn)
                    if p not in copied:
                        copied.append(p)
        n_lod = sum(1 for it in items if it.kind == "lod" and it.name.endswith("_LOD1"))
        n_ucx = sum(1 for it in items if it.kind == "ucx")
        idx = write_material_index(out_dir, items, mat_settings, settings, tex_dir, files, bool(copied))
        readme = write_readme(out_dir, settings, mat_settings, filepath, n_lod, n_ucx)
        files = list(files) + [idx, readme]
        # ---- report
        by_cat: dict[str, list[ExportItem]] = {}
        for it in items:
            by_cat.setdefault(it.category, []).append(it)
        cats = ", ".join(f"{c} {sum(1 for i in v if i.kind == 'mesh')}"
                         + (f" (from {sum(i.sources for i in v if i.kind == 'mesh')} objects)"
                            if sum(i.sources for i in v if i.kind == 'mesh') > sum(1 for i in v if i.kind == 'mesh') else "")
                         for c, v in sorted(by_cat.items()))
        n_mesh = sum(1 for it in items if it.kind == "mesh")
        tris = sum(it.tris for it in items if it.kind == "mesh")
        report = (f"Exported {n_mesh} meshes ({tris:,} tris) from {len(objects)} objects as {fmt} [{axis}"
                  f"{', cm' if scale_cm else ''}] -> {files[0] if files else out_dir}; {cats}; "
                  f"{n_lod} LOD chains, {n_ucx} UCX, {len(copied)} textures, {time.time() - t0:.1f}s")
        if warnings:
            report += f"; {len(warnings)} warning(s): " + " | ".join(warnings[:3])
        result.update({"files": files, "textures": copied, "report": report, "objects": n_mesh, "items": [
            {"name": it.name, "category": it.category, "kind": it.kind, "sources": it.sources, "tris": it.tris} for it in items]})
        print("MAD export:", report)
        return result
    finally:
        tmp.cleanup()


# --------------------------------------------------------------------------- operator
def _active_root(context) -> bpy.types.Object | None:
    root = su.find_base_root(getattr(context, "active_object", None))
    if root is None:
        roots = su.all_base_roots(context.scene)
        root = roots[0] if roots else None
    return root


class MAD_OT_export_base(bpy.types.Operator):
    bl_idname = "mad.export_base"
    bl_label = "Export Game Asset"
    bl_description = ("Export the active airbase as FBX / glTF / OBJ with engine naming (SM_/UCX_/_LOD), joined "
                      "instances, collision, textures and a material index (settings: Export panel)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active_root(context) is not None

    def execute(self, context):
        root = _active_root(context)
        if root is None:
            self.report({'ERROR'}, "No airbase root found")
            return {'CANCELLED'}
        try:
            res = export_base(context, root)
        except Exception as exc:  # never leave the user without a message
            traceback.print_exc()
            self.report({'ERROR'}, f"Export failed: {type(exc).__name__}: {exc}")
            return {'CANCELLED'}
        if not res["files"]:
            self.report({'WARNING'}, res["report"])
            return {'CANCELLED'}
        self.report({'WARNING'} if res["warnings"] else {'INFO'}, res["report"])
        return {'FINISHED'}


CLASSES = (MAD_OT_export_base,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
