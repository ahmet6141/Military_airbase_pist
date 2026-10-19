"""UI lists for the taxiway, apron and heliport collections."""
import bpy

TAXIWAY_ICONS = {
    'PARALLEL': 'IPO_LINEAR',
    'CONNECTOR': 'IPO_CONSTANT',
    'HIGH_SPEED': 'IPO_EASE_OUT',
    'END': 'IPO_BACK',
}
APRON_ICONS = {
    'MAIN': 'MESH_PLANE',
    'ALERT': 'OUTLINER_OB_FORCE_FIELD',
    'HAS_LOOP': 'MESH_TORUS',
    'ARM_DEARM': 'ORIENTATION_GIMBAL',
    'HOT_CARGO': 'ERROR',
    'HANGAR_LINE': 'HOME',
    'HELIPAD': 'MESH_CIRCLE',
    'COMPASS': 'ORIENTATION_VIEW',
    'TRIM_PAD': 'FORCE_WIND',
}


def _enum_label(item, prop: str) -> str:
    try:
        return item.bl_rna.properties[prop].enum_items[getattr(item, prop)].name
    except (KeyError, AttributeError):
        return getattr(item, prop, "")


class MAD_UL_taxiways(bpy.types.UIList):
    bl_idname = "MAD_UL_taxiways"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.prop(item, "name", text="", emboss=False, icon=TAXIWAY_ICONS.get(item.kind, 'DOT'))
            row.label(text=_enum_label(item, "kind"))
            row.label(text="L" if item.side == 'LEFT' else "R")
            row.prop(item, "enabled", text="", icon='HIDE_OFF' if item.enabled else 'HIDE_ON', emboss=False)
        else:
            layout.alignment = 'CENTER'
            layout.label(text=item.name, icon=TAXIWAY_ICONS.get(item.kind, 'DOT'))


class MAD_UL_aprons(bpy.types.UIList):
    bl_idname = "MAD_UL_aprons"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.prop(item, "name", text="", emboss=False, icon=APRON_ICONS.get(item.kind, 'DOT'))
            row.label(text=_enum_label(item, "kind"))
            row.label(text="L" if item.side == 'LEFT' else "R")
            row.prop(item, "enabled", text="", icon='HIDE_OFF' if item.enabled else 'HIDE_ON', emboss=False)
        else:
            layout.alignment = 'CENTER'
            layout.label(text=item.name, icon=APRON_ICONS.get(item.kind, 'DOT'))


class MAD_UL_heliports(bpy.types.UIList):
    bl_idname = "MAD_UL_heliports"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.prop(item, "name", text="", emboss=False, icon='MESH_CIRCLE')
            row.label(text=_enum_label(item, "hclass"))
            row.label(text="L" if item.side == 'LEFT' else "R")
            row.prop(item, "enabled", text="", icon='HIDE_OFF' if item.enabled else 'HIDE_ON', emboss=False)
        else:
            layout.alignment = 'CENTER'
            layout.label(text=item.name, icon='MESH_CIRCLE')


CLASSES = (MAD_UL_taxiways, MAD_UL_aprons, MAD_UL_heliports)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
