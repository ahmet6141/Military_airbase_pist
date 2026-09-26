"""Scene-level helpers: collections, object creation, tagging, cleanup, instancing."""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import bpy
from mathutils import Euler, Matrix, Vector

TAG_PROP = "mad_generated"      # custom property set on every generated object
ROOT_PROP = "mad_base_root"     # custom property marking a base root empty
CATEGORY_PROP = "mad_category"

# Sub-collection names (also the categories used by export filters)
CATEGORIES = (
    "Pavement", "Markings", "Lighting", "Signage", "Structures", "Perimeter", "Navaids", "Terrain",
)


def addon_prefs(context: bpy.types.Context | None = None):
    """Addon preferences or None (e.g. when running under the pip 'bpy' module in tests)."""
    context = context or bpy.context
    pkg = __package__.split(".")[0] if __package__ else None
    try:
        return context.preferences.addons[pkg].preferences
    except (KeyError, AttributeError, TypeError):
        return None


# --------------------------------------------------------------------------- collections
def ensure_collection(name: str, parent: bpy.types.Collection | None = None) -> bpy.types.Collection:
    parent = parent or bpy.context.scene.collection
    for child in parent.children:
        if child.name == name or child.name.startswith(name + "."):
            return child
    col = bpy.data.collections.get(name)
    if col is None or col.users == 0 or _collection_in_other_parent(col, parent):
        col = bpy.data.collections.new(name)
    parent.children.link(col)
    return col


def _collection_in_other_parent(col: bpy.types.Collection, parent: bpy.types.Collection) -> bool:
    for c in bpy.data.collections:
        if col.name in c.children and c != parent:
            return True
    if col.name in bpy.context.scene.collection.children and parent != bpy.context.scene.collection:
        return True
    return False


def remove_collection_recursive(col: bpy.types.Collection, remove_data: bool = True) -> None:
    for child in list(col.children):
        remove_collection_recursive(child, remove_data)
    for ob in list(col.objects):
        remove_object(ob, remove_data)
    try:
        bpy.data.collections.remove(col)
    except ReferenceError:
        pass


# --------------------------------------------------------------------------- objects
def new_object(name: str, data, collection: bpy.types.Collection, parent: bpy.types.Object | None = None,
               location: Sequence[float] | None = None, rotation_z: float = 0.0, category: str = "",
               scale: Sequence[float] | None = None) -> bpy.types.Object:
    ob = bpy.data.objects.new(name, data)
    collection.objects.link(ob)
    if parent is not None:
        ob.parent = parent
    if location is not None:
        ob.location = Vector(location)
    if rotation_z:
        ob.rotation_euler = Euler((0.0, 0.0, rotation_z))
    if scale is not None:
        ob.scale = Vector(scale)
    ob[TAG_PROP] = True
    if category:
        ob[CATEGORY_PROP] = category
    return ob


def new_empty(name: str, collection: bpy.types.Collection, parent: bpy.types.Object | None = None,
              location: Sequence[float] | None = None, display: str = 'PLAIN_AXES', size: float = 1.0) -> bpy.types.Object:
    ob = new_object(name, None, collection, parent, location)
    ob.empty_display_type = display
    ob.empty_display_size = size
    return ob


def instance(name: str, mesh: bpy.types.Mesh, collection: bpy.types.Collection, parent: bpy.types.Object | None,
             location: Sequence[float], rotation_z: float = 0.0, category: str = "",
             scale: Sequence[float] | None = None) -> bpy.types.Object:
    """Linked duplicate sharing ``mesh`` (light fixtures, signs, fence posts...)."""
    return new_object(name, mesh, collection, parent, location, rotation_z, category, scale)


def remove_object(ob: bpy.types.Object, remove_data: bool = True) -> None:
    data = ob.data
    try:
        bpy.data.objects.remove(ob, do_unlink=True)
    except ReferenceError:
        return
    if remove_data and data is not None and data.users == 0:
        try:
            if isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data, bpy.types.Curve):
                bpy.data.curves.remove(data)
            elif isinstance(data, bpy.types.Light):
                bpy.data.lights.remove(data)
            elif isinstance(data, bpy.types.Camera):
                bpy.data.cameras.remove(data)
        except ReferenceError:
            pass


def children_recursive(ob: bpy.types.Object) -> list[bpy.types.Object]:
    out = []
    stack = list(ob.children)
    while stack:
        c = stack.pop()
        out.append(c)
        stack.extend(c.children)
    return out


def find_base_root(ob: bpy.types.Object | None) -> bpy.types.Object | None:
    """Walk up the parent chain to the base root empty (or None)."""
    while ob is not None:
        if ob.get(ROOT_PROP):
            return ob
        ob = ob.parent
    return None


def all_base_roots(scene: bpy.types.Scene | None = None) -> list[bpy.types.Object]:
    scene = scene or bpy.context.scene
    return [o for o in scene.objects if o.get(ROOT_PROP)]


def clear_generated(root: bpy.types.Object, keep_categories: Iterable[str] = ()) -> int:
    """Delete every generated child of ``root`` (optionally keeping some categories)."""
    keep = set(keep_categories)
    count = 0
    for ob in children_recursive(root):
        if not ob.get(TAG_PROP):
            continue
        if ob.get(CATEGORY_PROP, "") in keep:
            continue
        remove_object(ob, remove_data=True)
        count += 1
    purge_orphan_meshes()
    return count


def purge_orphan_meshes() -> None:
    for me in list(bpy.data.meshes):
        if me.users == 0 and me.name.startswith("MAD_"):
            try:
                bpy.data.meshes.remove(me)
            except ReferenceError:
                pass
    for cu in list(bpy.data.curves):
        if cu.users == 0 and cu.name.startswith("MAD_"):
            try:
                bpy.data.curves.remove(cu)
            except ReferenceError:
                pass


def set_active(ob: bpy.types.Object, select: bool = True) -> None:
    try:
        bpy.context.view_layer.objects.active = ob
        if select:
            ob.select_set(True)
    except (RuntimeError, AttributeError):
        pass


def deselect_all() -> None:
    for ob in bpy.context.view_layer.objects:
        try:
            ob.select_set(False)
        except RuntimeError:
            pass


def compass_to_math(heading_deg: float) -> float:
    """Compass bearing (degrees clockwise from +Y/north) -> Blender Z rotation (radians)."""
    return math.radians(90.0 - heading_deg)


def text_mesh(name: str, text: str, size: float, extrude: float = 0.0, align_x: str = 'CENTER',
              align_y: str = 'CENTER', font: bpy.types.VectorFont | None = None, bold: bool = False) -> bpy.types.Mesh:
    """Convert a text string into a mesh datablock (sign legends, spot numbers)."""
    cu = bpy.data.curves.new("MAD_txt_" + name, 'FONT')
    cu.body = text
    cu.size = size
    cu.extrude = extrude
    cu.align_x = align_x
    cu.align_y = align_y
    cu.fill_mode = 'BOTH' if extrude > 0 else 'FRONT'
    if font is not None:
        cu.font = font
    if bold and cu.font_bold is not None:
        cu.font_bold = cu.font
    tmp = bpy.data.objects.new("MAD_tmp_txt", cu)
    bpy.context.scene.collection.objects.link(tmp)
    try:
        dg = bpy.context.evaluated_depsgraph_get()
        ev = tmp.evaluated_get(dg)
        me_eval = ev.to_mesh()
        me = bpy.data.meshes.new("MAD_" + name)
        verts = [v.co.copy() for v in me_eval.vertices]
        faces = [list(p.vertices) for p in me_eval.polygons]
        me.from_pydata(verts, [], faces)
        me.update()
        ev.to_mesh_clear()
    finally:
        bpy.data.objects.remove(tmp, do_unlink=True)
        bpy.data.curves.remove(cu)
    return me
