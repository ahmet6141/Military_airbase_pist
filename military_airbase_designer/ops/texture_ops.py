"""Texture generation operator."""
from __future__ import annotations

import time

import bpy

from .build_ops import active_root


class MAD_OT_generate_textures(bpy.types.Operator):
    bl_idname = "mad.generate_textures"
    bl_label = "Generate Textures"
    bl_description = "Synthesize the full-resolution PBR texture sets into the texture folder and refresh the materials"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return active_root(context) is not None

    def execute(self, context):
        root = active_root(context)
        s = root.mad
        try:
            from ..textures import io as tio
            from ..textures import synth
            from ..materials import nodes
        except ImportError as exc:
            self.report({'ERROR'}, f"Texture module not available: {exc}")
            return {'CANCELLED'}
        directory = tio.texture_dir(s)
        res = int(s.materials.resolution)
        t0 = time.time()
        wm = context.window_manager
        names = list(synth.TEXTURE_SETS.keys())
        wm.progress_begin(0, len(names))
        try:
            synth.generate_all(s, directory, resolution=res, force=True,
                               progress=lambda i, n, name: wm.progress_update(i))
        except Exception as exc:  # report, keep Blender alive
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"Texture generation failed: {exc}")
            return {'CANCELLED'}
        finally:
            wm.progress_end()
        s.materials.generate_textures = True
        try:
            nodes.refresh_textures(s, force=True)
        except Exception:
            from ..materials import library
            library.rebuild_all(s)
        self.report({'INFO'}, f"{len(names)} texture sets at {res}px written to {directory} in {time.time() - t0:.0f}s")
        return {'FINISHED'}


class MAD_OT_open_texture_folder(bpy.types.Operator):
    bl_idname = "mad.open_texture_folder"
    bl_label = "Open Texture Folder"
    bl_description = "Open the texture folder in the system file browser"

    @classmethod
    def poll(cls, context):
        return active_root(context) is not None

    def execute(self, context):
        from ..textures import io as tio
        directory = tio.texture_dir(active_root(context).mad)
        bpy.ops.wm.path_open(filepath=directory)
        return {'FINISHED'}


CLASSES = (MAD_OT_generate_textures, MAD_OT_open_texture_folder)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
