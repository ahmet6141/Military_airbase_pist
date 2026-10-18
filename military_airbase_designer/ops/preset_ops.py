"""Preset menu and operators (bundled JSON presets + user presets)."""
from __future__ import annotations

import os

import bpy
from bpy.props import StringProperty

from ..core import presets as presetmod
from ..i18n.translations import tr
from ..layout import builder
from .build_ops import active_root


class MAD_OT_apply_preset(bpy.types.Operator):
    bl_idname = "mad.apply_preset"
    bl_label = "Apply Preset"
    bl_description = "Apply a preset to the active airbase and rebuild it"
    bl_options = {'REGISTER', 'UNDO'}

    identifier: StringProperty(name="Preset", default="")

    @classmethod
    def poll(cls, context):
        return active_root(context) is not None

    def execute(self, context):
        root = active_root(context)
        try:
            presetmod.apply_preset(root, self.identifier)
        except KeyError:
            self.report({'ERROR'}, tr("Preset not found: {name}").format(name=self.identifier))
            return {'CANCELLED'}
        except (ValueError, OSError) as exc:      # corrupt / unreadable user preset (JSONDecodeError is a ValueError)
            self.report({'ERROR'}, tr("Preset not found: {name}").format(name=self.identifier) + f" ({exc})")
            return {'CANCELLED'}
        builder.build_base(root)
        self.report({'INFO'}, tr("Preset applied: {name}").format(name=root.mad.preset_name))
        return {'FINISHED'}


class MAD_OT_save_preset(bpy.types.Operator):
    bl_idname = "mad.save_preset"
    bl_label = "Save Preset"
    bl_description = "Save the active airbase parameters as a user preset"
    bl_options = {'REGISTER'}

    name: StringProperty(name="Preset name", default="My Base")

    @classmethod
    def poll(cls, context):
        return active_root(context) is not None

    def invoke(self, context, event):
        root = active_root(context)
        self.name = root.mad.base_name or "My Base"
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        root = active_root(context)
        path = presetmod.save_user_preset(root, self.name)
        self.report({'INFO'}, tr("Preset saved: {path}").format(path=path))
        return {'FINISHED'}


class MAD_OT_delete_user_preset(bpy.types.Operator):
    bl_idname = "mad.delete_user_preset"
    bl_label = "Delete User Preset"
    bl_description = "Delete a user preset file (bundled presets cannot be deleted)"
    bl_options = {'REGISTER'}

    identifier: StringProperty(name="Preset", default="")

    def execute(self, context):
        user_dir = presetmod.user_preset_dir()
        for ident, _label, path in presetmod.list_presets():
            if ident == self.identifier and os.path.dirname(path) == os.path.normpath(user_dir):
                os.remove(path)
                self.report({'INFO'}, tr("Deleted preset {ident}").format(ident=ident))
                return {'FINISHED'}
        self.report({'WARNING'}, tr("Only user presets can be deleted"))
        return {'CANCELLED'}


class MAD_MT_presets(bpy.types.Menu):
    bl_idname = "MAD_MT_presets"
    bl_label = "Airbase Presets"

    def draw(self, context):
        layout = self.layout
        user_dir = os.path.normpath(presetmod.user_preset_dir())
        bundled = [p for p in presetmod.list_presets() if os.path.dirname(p[2]) != user_dir]
        user = [p for p in presetmod.list_presets() if os.path.dirname(p[2]) == user_dir]
        for ident, label, _path in bundled:
            layout.operator("mad.apply_preset", text=label, icon='PRESET').identifier = ident
        if user:
            layout.separator()
            for ident, label, _path in user:
                layout.operator("mad.apply_preset", text=label, icon='FILE').identifier = ident
        layout.separator()
        layout.operator("mad.save_preset", icon='ADD')
        if user:
            layout.menu("MAD_MT_delete_presets", icon='TRASH')


class MAD_MT_delete_presets(bpy.types.Menu):
    bl_idname = "MAD_MT_delete_presets"
    bl_label = "Delete User Preset"

    def draw(self, context):
        layout = self.layout
        user_dir = os.path.normpath(presetmod.user_preset_dir())
        user = [p for p in presetmod.list_presets() if os.path.dirname(p[2]) == user_dir]
        if not user:
            layout.label(text="No user presets", icon='INFO')
        for ident, label, _path in user:
            layout.operator("mad.delete_user_preset", text=label, icon='TRASH').identifier = ident


CLASSES = (MAD_OT_apply_preset, MAD_OT_save_preset, MAD_OT_delete_user_preset, MAD_MT_presets, MAD_MT_delete_presets)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
