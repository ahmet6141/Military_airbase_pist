"""Add-on preferences."""
import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

_PATH_OPTS = {'PATH_SUPPORTS_BLEND_RELATIVE'} if bpy.app.version >= (4, 5, 0) else set()


class MAD_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    default_texture_dir: StringProperty(name="Default texture folder", subtype='DIR_PATH', default="//textures/", options=_PATH_OPTS)
    units: EnumProperty(name="Display units", items=[('METRIC', "Metric", ""), ('IMPERIAL', "Imperial (ft)", "")], default='METRIC')
    language: EnumProperty(name="Panel language", items=[('AUTO', "Follow Blender", ""), ('EN', "English", ""), ('TR', "Türkçe", "")], default='AUTO')
    debug: BoolProperty(name="Debug output", default=False)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "default_texture_dir")
        layout.prop(self, "units")
        layout.prop(self, "language")
        layout.prop(self, "debug")


def register():
    bpy.utils.register_class(MAD_Preferences)


def unregister():
    bpy.utils.unregister_class(MAD_Preferences)
