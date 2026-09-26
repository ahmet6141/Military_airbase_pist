"""Build / rebuild / clear operators."""
from __future__ import annotations

import bpy
from bpy.props import StringProperty

from ..core import scene_utils as su
from ..layout import builder


def active_root(context) -> bpy.types.Object | None:
    root = su.find_base_root(context.active_object)
    if root is None:
        roots = su.all_base_roots(context.scene)
        root = roots[0] if roots else None
    return root


class MAD_OT_create_base(bpy.types.Operator):
    bl_idname = "mad.create_base"
    bl_label = "Build Full Base"
    bl_description = "Create a new airbase from the selected preset at the 3D cursor"
    bl_options = {'REGISTER', 'UNDO'}

    preset: StringProperty(name="Preset", default="USAF_CLASS_B_FIGHTER")
    name: StringProperty(name="Name", default="Airbase")

    def execute(self, context):
        preset = self.preset or context.scene.mad_ui.new_base_preset or "USAF_CLASS_B_FIGHTER"
        root = builder.create_base(context, self.name, preset, context.scene.cursor.location.copy())
        su.deselect_all()
        su.set_active(root)
        self.report({'INFO'}, root.mad.build_stats)
        return {'FINISHED'}


class MAD_OT_rebuild(bpy.types.Operator):
    bl_idname = "mad.rebuild"
    bl_label = "Rebuild"
    bl_description = "Regenerate the active airbase from its parameters"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return active_root(context) is not None

    def execute(self, context):
        root = active_root(context)
        ctx = builder.build_base(root)
        su.set_active(root)
        if ctx.warnings:
            self.report({'WARNING'}, f"Built with {len(ctx.warnings)} warning(s) — see panel")
        else:
            self.report({'INFO'}, root.mad.build_stats)
        return {'FINISHED'}


class MAD_OT_clear(bpy.types.Operator):
    bl_idname = "mad.clear"
    bl_label = "Clear Generated"
    bl_description = "Delete every generated object of the active airbase (parameters are kept on the root)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return active_root(context) is not None

    def execute(self, context):
        root = active_root(context)
        n = su.clear_generated(root)
        self.report({'INFO'}, f"Removed {n} objects")
        return {'FINISHED'}


class MAD_OT_delete_base(bpy.types.Operator):
    bl_idname = "mad.delete_base"
    bl_label = "Delete Base"
    bl_description = "Delete the active airbase including its root and collections"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return active_root(context) is not None

    def execute(self, context):
        root = active_root(context)
        su.clear_generated(root)
        cols = list(root.users_collection)
        su.remove_object(root)
        for c in cols:
            if c.name.startswith("Airbase_"):
                su.remove_collection_recursive(c)
        return {'FINISHED'}


class MAD_OT_update_materials(bpy.types.Operator):
    bl_idname = "mad.update_materials"
    bl_label = "Update Materials"
    bl_description = "Rebuild materials (weathering, night mode, texture set) without regenerating geometry"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return active_root(context) is not None

    def execute(self, context):
        builder.update_materials(active_root(context))
        return {'FINISHED'}


CLASSES = (MAD_OT_create_base, MAD_OT_rebuild, MAD_OT_clear, MAD_OT_delete_base, MAD_OT_update_materials)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
