"""Sidebar panels (3D Viewport > N panel > Airbase)."""
from __future__ import annotations

import bpy

from ..core import scene_utils as su
from ..i18n.translations import tr, tr_lines
from ..ops.build_ops import active_root

CATEGORY = "Airbase"


def _root(context):
    return active_root(context)


# =========================================================================== main panel
class MAD_PT_main(bpy.types.Panel):
    bl_label = "Military Airbase Designer"
    bl_idname = "MAD_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY

    def draw(self, context):
        layout = self.layout
        ui = context.scene.mad_ui
        root = _root(context)
        if root is None:
            col = layout.column(align=True)
            col.label(text="No airbase in the scene", icon='INFO')
            col.prop(ui, "new_base_name")
            col.prop(ui, "new_base_preset", text="")
            col.separator()
            op = col.operator("mad.create_base", text="Build Full Base", icon='ADD')
            op.preset = ui.new_base_preset
            op.name = ui.new_base_name
            col.label(text="Builds at the 3D cursor; heading sets the rotation.", icon='DOT')
            return
        s = root.mad
        box = layout.box()
        row = box.row(align=True)
        row.label(text=s.base_name or root.name, icon='OUTLINER_OB_EMPTY')
        if s.preset_name:
            row.label(text=s.preset_name)
        row = box.row(align=True)
        row.scale_y = 1.4
        row.operator("mad.rebuild", icon='FILE_REFRESH')
        row.operator("mad.clear", text="", icon='X')
        row = box.row(align=True)
        row.prop(ui, "live_update", icon='AUTO' if ui.live_update else 'PAUSE', toggle=True)
        row.prop(s, "quality", text="")
        row = box.row(align=True)
        row.operator("mad.update_materials", icon='MATERIAL')
        row.operator("mad.generate_textures", icon='TEXTURE')
        row = box.row(align=True)
        row.menu("MAD_MT_presets", icon='PRESET')
        row.operator("mad.delete_base", text="", icon='TRASH')
        if s.build_stats:
            box.label(text=tr(s.build_stats), icon='INFO')
        if s.warnings:
            wb = layout.box()
            wb.label(text="Warnings", icon='ERROR')
            lines = [l for l in tr_lines(s.warnings).split("\n") if l.strip()]
            for line in lines[:8]:
                wb.label(text=line[:90])
            if len(lines) > 8:
                wb.label(text=f"... {len(lines) - 8} more")
        row = layout.row(align=True)
        row.operator("mad.create_base", text="New Base", icon='ADD').preset = ui.new_base_preset
        row.prop(ui, "new_base_preset", text="")


class _SubPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = CATEGORY
    bl_parent_id = "MAD_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return _root(context) is not None

    def settings(self, context):
        return _root(context).mad

    def prep(self):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        return layout


# =========================================================================== runway
class MAD_PT_runway(_SubPanel, bpy.types.Panel):
    bl_label = "Runway"
    bl_idname = "MAD_PT_runway"

    def draw(self, context):
        layout = self.prep()
        rw = self.settings(context).runway
        col = layout.column(align=True)
        col.prop(rw, "length")
        col.prop(rw, "width")
        col.prop(rw, "heading")
        col.prop(rw, "suffix")
        col.prop(rw, "runway_class")
        layout.separator()
        col = layout.column(align=True)
        col.prop(rw, "surface")
        if rw.surface == 'HYBRID':
            col.prop(rw, "hybrid_end_length")
        col.prop(rw, "slab_size")
        col.prop(rw, "grooved")
        col.prop(rw, "crown_percent")
        layout.separator()
        col = layout.column(align=True)
        col.prop(rw, "shoulder_type")
        col.prop(rw, "shoulder_width")
        col.prop(rw, "overrun_type")
        if rw.overrun_type != 'NONE':
            col.prop(rw, "overrun_length")
            row = col.row(align=True)
            row.prop(rw, "overrun_low", toggle=True)
            row.prop(rw, "overrun_high", toggle=True)
        layout.separator()
        col = layout.column(align=True)
        col.prop(rw, "displaced_low")
        col.prop(rw, "displaced_high")
        col.prop(rw, "ils_low")
        col.prop(rw, "ils_high")
        col.prop(rw, "elevation")
        layout.separator()
        col = layout.column(align=True)
        col.prop(rw, "arresting_type")
        if rw.arresting_type != 'NONE':
            col.prop(rw, "arresting_distance")
            row = col.row(align=True)
            row.prop(rw, "arresting_low", toggle=True)
            row.prop(rw, "arresting_high", toggle=True)
            row.prop(rw, "arresting_mid", toggle=True)


# =========================================================================== markings
class MAD_PT_markings(_SubPanel, bpy.types.Panel):
    bl_label = "Markings"
    bl_idname = "MAD_PT_markings"

    def draw_header(self, context):
        self.layout.prop(self.settings(context).markings, "enabled", text="")

    def draw(self, context):
        layout = self.prep()
        m = self.settings(context).markings
        layout.active = m.enabled
        col = layout.column(align=True)
        col.prop(m, "standard")
        col.prop(m, "black_border")
        col.prop(m, "taxi_line_width")
        col.prop(m, "hold_line_width")
        col.prop(m, "leading_zero")
        col.prop(m, "paint_wear")
        layout.separator()
        col = layout.column(heading="Runway", align=True)
        col.prop(m, "designators")
        col.prop(m, "threshold")
        col.prop(m, "aiming_point")
        col.prop(m, "touchdown_zone")
        col.prop(m, "centerline")
        col.prop(m, "side_stripes")
        col.prop(m, "shoulder_stripes")
        col.prop(m, "overrun_chevrons")
        col.prop(m, "arresting_discs")
        col = layout.column(heading="Taxiways & aprons", align=True)
        col.prop(m, "taxiway_centerline")
        col.prop(m, "taxiway_edges")
        col.prop(m, "hold_lines")
        col.prop(m, "enhanced_centerline")
        col.prop(m, "surface_signs")
        col.prop(m, "apron_markings")
        col.prop(m, "road_markings")


# =========================================================================== decals
class MAD_PT_decals(_SubPanel, bpy.types.Panel):
    bl_label = "Decals & Wear"
    bl_idname = "MAD_PT_decals"

    def draw_header(self, context):
        self.layout.prop(self.settings(context).decals, "enabled", text="")

    def draw(self, context):
        layout = self.prep()
        d = self.settings(context).decals
        layout.active = d.enabled
        col = layout.column(align=True)
        col.prop(d, "aircraft_mix")
        col.prop(d, "seed")
        layout.separator()
        col = layout.column(heading="Runway", align=True)
        col.prop(d, "skids")
        sub = col.column(align=True)
        sub.active = d.skids
        sub.prop(d, "skid_count")
        sub.prop(d, "skid_strength")
        sub.prop(d, "traffic_split")
        sub.prop(d, "touchdown_mean")
        sub.prop(d, "touchdown_spread")
        sub.prop(d, "lateral_spread")
        col.prop(d, "turn_scuffs")
        col = layout.column(heading="Aprons", align=True)
        col.prop(d, "apron_stains")
        col.prop(d, "exhaust_stains")
        col.prop(d, "stain_strength")


# =========================================================================== taxiways
class MAD_PT_taxiways(_SubPanel, bpy.types.Panel):
    bl_label = "Taxiways"
    bl_idname = "MAD_PT_taxiways"

    def draw(self, context):
        layout = self.layout
        s = self.settings(context)
        row = layout.row()
        row.template_list("MAD_UL_taxiways", "", s, "taxiways", s, "taxiway_index", rows=5)
        col = row.column(align=True)
        col.operator_menu_enum("mad.taxiway_add", "kind", text="", icon='ADD')
        col.operator("mad.taxiway_remove", text="", icon='REMOVE')
        col.separator()
        col.operator("mad.taxiway_duplicate", text="", icon='DUPLICATE')
        col.separator()
        col.operator("mad.taxiway_move", text="", icon='TRIA_UP').direction = 'UP'
        col.operator("mad.taxiway_move", text="", icon='TRIA_DOWN').direction = 'DOWN'
        if not s.taxiways or s.taxiway_index >= len(s.taxiways):
            return
        t = s.taxiways[s.taxiway_index]
        layout = self.prep()
        col = layout.column(align=True)
        col.prop(t, "name")
        col.prop(t, "kind")
        col.prop(t, "side")
        col.prop(t, "width")
        col.prop(t, "shoulder_width")
        if t.kind == 'PARALLEL':
            col.prop(t, "offset")
        elif t.kind in ('CONNECTOR', 'HIGH_SPEED'):
            col.prop(t, "station")
            if t.kind == 'HIGH_SPEED':
                col.prop(t, "angle")
                col.prop(t, "exit_dir")
        elif t.kind == 'END':
            col.prop(t, "station", text="End (sign: - low / + high)")
        col.prop(t, "fillet_radius")
        if t.kind != 'PARALLEL':
            col.prop(t, "hold")
            if t.hold != 'NONE':
                col.prop(t, "hold_distance")
        col = layout.column(align=True)
        col.prop(t, "edge_lights")
        col.prop(t, "centerline_lights")
        col.prop(t, "signs")


# =========================================================================== aprons
class MAD_PT_aprons(_SubPanel, bpy.types.Panel):
    bl_label = "Aprons & Pads"
    bl_idname = "MAD_PT_aprons"

    def draw(self, context):
        layout = self.layout
        s = self.settings(context)
        row = layout.row()
        row.template_list("MAD_UL_aprons", "", s, "aprons", s, "apron_index", rows=5)
        col = row.column(align=True)
        col.operator_menu_enum("mad.apron_add", "kind", text="", icon='ADD')
        col.operator("mad.apron_remove", text="", icon='REMOVE')
        col.separator()
        col.operator("mad.apron_duplicate", text="", icon='DUPLICATE')
        col.separator()
        col.operator("mad.apron_move", text="", icon='TRIA_UP').direction = 'UP'
        col.operator("mad.apron_move", text="", icon='TRIA_DOWN').direction = 'DOWN'
        if not s.aprons or s.apron_index >= len(s.aprons):
            return
        a = s.aprons[s.apron_index]
        layout = self.prep()
        col = layout.column(align=True)
        col.prop(a, "name")
        col.prop(a, "kind")
        col.prop(a, "side")
        col.prop(a, "station")
        col.prop(a, "offset")
        if a.kind not in ('HELIPAD', 'COMPASS'):
            col.prop(a, "length")
            col.prop(a, "depth")
        col = layout.column(align=True)
        if a.kind in ('MAIN', 'ALERT', 'ARM_DEARM', 'HOT_CARGO', 'HANGAR_LINE', 'TRIM_PAD', 'HAS_LOOP'):
            col.prop(a, "aircraft")
        if a.kind in ('MAIN', 'ALERT', 'ARM_DEARM', 'HOT_CARGO', 'HANGAR_LINE'):
            col.prop(a, "spots")
        if a.kind in ('MAIN', 'HANGAR_LINE'):
            col.prop(a, "rows")
            col.prop(a, "lead_in_radius")
        if a.kind == 'HAS_LOOP':
            col.prop(a, "shelters")
        col = layout.column(align=True)
        col.prop(a, "floodlights")
        col.prop(a, "hydrants")
        col.prop(a, "tie_downs")
        col.prop(a, "blast_deflector")


# =========================================================================== lighting
class MAD_PT_lighting(_SubPanel, bpy.types.Panel):
    bl_label = "Lighting"
    bl_idname = "MAD_PT_lighting"

    def draw_header(self, context):
        self.layout.prop(self.settings(context).lighting, "enabled", text="")

    def draw(self, context):
        layout = self.prep()
        l = self.settings(context).lighting
        layout.active = l.enabled
        col = layout.column(align=True)
        col.prop(l, "edge")
        col.prop(l, "edge_spacing")
        col.prop(l, "threshold")
        col.prop(l, "centerline")
        col.prop(l, "tdz")
        col.prop(l, "reil")
        layout.separator()
        col = layout.column(align=True)
        col.prop(l, "papi")
        if l.papi:
            col.prop(l, "papi_side")
            col.prop(l, "papi_distance")
        col.prop(l, "als_low")
        col.prop(l, "als_high")
        layout.separator()
        col = layout.column(align=True)
        col.prop(l, "taxiway_edge")
        col.prop(l, "taxiway_centerline")
        col.prop(l, "taxiway_spacing")
        col.prop(l, "guard_lights")
        col.prop(l, "stop_bars")
        col.prop(l, "apron_floods")
        col.prop(l, "beacon")
        col.prop(l, "obstruction")
        col.prop(l, "fixture_detail")
        layout.separator()
        col = layout.column(align=True)
        col.prop(l, "night_mode")
        col.prop(l, "emission_strength")


# =========================================================================== signage
class MAD_PT_signage(_SubPanel, bpy.types.Panel):
    bl_label = "Signage"
    bl_idname = "MAD_PT_signage"

    def draw_header(self, context):
        self.layout.prop(self.settings(context).signage, "enabled", text="")

    def draw(self, context):
        layout = self.prep()
        sg = self.settings(context).signage
        layout.active = sg.enabled
        col = layout.column(align=True)
        col.prop(sg, "hold_signs")
        col.prop(sg, "location_signs")
        col.prop(sg, "direction_signs")
        col.prop(sg, "rdr_signs")
        col.prop(sg, "agm_signs")
        col.prop(sg, "wind_cones")
        col.prop(sg, "sign_size")
        col.prop(sg, "sign_offset")


# =========================================================================== structures
class MAD_PT_structures(_SubPanel, bpy.types.Panel):
    bl_label = "Structures"
    bl_idname = "MAD_PT_structures"

    def draw_header(self, context):
        self.layout.prop(self.settings(context).structures, "enabled", text="")

    def draw(self, context):
        layout = self.prep()
        st = self.settings(context).structures
        layout.active = st.enabled
        col = layout.column(align=True)
        col.prop(st, "has_style")
        col.prop(st, "has_layout")
        col.prop(st, "camouflage")
        col.prop(st, "revetments")
        layout.separator()
        col = layout.column(align=True)
        col.prop(st, "hangars")
        col.prop(st, "large_hangar")
        col.prop(st, "control_tower")
        if st.control_tower:
            col.prop(st, "tower_height")
        col.prop(st, "fire_station")
        col.prop(st, "ops_buildings")
        col.prop(st, "lighting_vault")
        col.prop(st, "hush_house")
        col.prop(st, "wash_rack")
        layout.separator()
        col = layout.column(align=True)
        col.prop(st, "fuel_farm")
        if st.fuel_farm:
            col.prop(st, "fuel_tanks")
        col.prop(st, "munitions")
        if st.munitions:
            col.prop(st, "igloos")
        col.prop(st, "arresting_housings")
        col.prop(st, "navaids")


# =========================================================================== perimeter & terrain
class MAD_PT_perimeter(_SubPanel, bpy.types.Panel):
    bl_label = "Perimeter & Terrain"
    bl_idname = "MAD_PT_perimeter"

    def draw(self, context):
        layout = self.prep()
        s = self.settings(context)
        p, t = s.perimeter, s.terrain
        col = layout.column(heading="Perimeter", align=True)
        col.prop(p, "enabled")
        sub = col.column(align=True)
        sub.active = p.enabled
        sub.prop(p, "fence")
        sub.prop(p, "double_fence")
        sub.prop(p, "fence_height")
        sub.prop(p, "fence_gap")
        sub.prop(p, "margin")
        sub.prop(p, "patrol_road")
        sub.prop(p, "road_width")
        sub.prop(p, "entry_control_point")
        sub.prop(p, "guard_towers")
        sub.prop(p, "service_roads")
        layout.separator()
        col = layout.column(heading="Terrain", align=True)
        col.prop(t, "enabled")
        sub = col.column(align=True)
        sub.active = t.enabled
        sub.prop(t, "margin")
        sub.prop(t, "dirt_strip")
        sub.prop(t, "ditches")
        if t.ditches:
            sub.prop(t, "ditch_offset")
        sub.prop(t, "mowing_stripes")
        sub.prop(t, "subdivision")
        sub.prop(t, "undulation")


# =========================================================================== materials & textures
class MAD_PT_materials(_SubPanel, bpy.types.Panel):
    bl_label = "Materials & Textures"
    bl_idname = "MAD_PT_materials"

    def draw(self, context):
        layout = self.prep()
        m = self.settings(context).materials
        col = layout.column(align=True)
        col.prop(m, "generate_textures")
        col.prop(m, "resolution")
        col.prop(m, "slabs_per_tile")
        col.prop(m, "seed")
        col.prop(m, "texture_dir")
        row = col.row(align=True)
        row.operator("mad.generate_textures", icon='TEXTURE')
        row.operator("mad.open_texture_folder", text="", icon='FILE_FOLDER')
        if m.generate_textures and m.preview_only:
            layout.label(text="Textures are at preview resolution - press Generate Textures", icon='INFO')
        layout.separator()
        col = layout.column(align=True)
        col.prop(m, "engine")
        col.prop(m, "normal_directx")
        layout.separator()
        col = layout.column(heading="Weathering", align=True)
        col.prop(m, "age")
        col.prop(m, "rubber")
        col.prop(m, "dirt")
        col.prop(m, "wet")
        col.prop(m, "concrete_tint")
        col.prop(m, "asphalt_tint")
        layout.operator("mad.update_materials", icon='MATERIAL')


# =========================================================================== export
class MAD_PT_export(_SubPanel, bpy.types.Panel):
    bl_label = "Export"
    bl_idname = "MAD_PT_export"

    def draw(self, context):
        layout = self.prep()
        e = self.settings(context).export
        col = layout.column(align=True)
        col.prop(e, "format")
        col.prop(e, "axis")
        col.prop(e, "directory")
        col.prop(e, "scale_cm")
        layout.separator()
        col = layout.column(align=True)
        col.prop(e, "join_by_category")
        col.prop(e, "lods")
        if e.lods:
            col.prop(e, "lod_count")
        col.prop(e, "collision")
        col.prop(e, "copy_textures")
        col.prop(e, "export_terrain")
        col.prop(e, "export_markings_as_decals")
        layout.separator()
        row = layout.row()
        row.scale_y = 1.4
        if hasattr(bpy.ops.mad, "export_base"):
            row.operator("mad.export_base", icon='EXPORT')
        else:
            row.label(text="Export module not available", icon='ERROR')


# =========================================================================== help
class MAD_PT_help(_SubPanel, bpy.types.Panel):
    bl_label = "Help"
    bl_idname = "MAD_PT_help"

    @classmethod
    def poll(cls, context):
        return True

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="Runway frame: +X low end -> high end, origin at the centre.", icon='ORIENTATION_LOCAL')
        col.label(text="Standards: UFC 3-260-01/04, UFC 3-535-01, FAA, ICAO.", icon='BOOKMARKS')
        col.label(text="Lengths follow the scene unit system (metric / imperial).", icon='DRIVER_DISTANCE')
        col.label(text="Textures: Generate Textures writes PBR PNG sets.", icon='TEXTURE')
        col.operator("wm.url_open", text="Documentation", icon='URL').url = "https://github.com/ahmet6141/Military_airbase_pist"


# =========================================================================== Add menu
def _add_menu(self, context):
    self.layout.separator()
    self.layout.operator("mad.create_base", text="Military Airbase", icon='AUTO')


CLASSES = (MAD_PT_main, MAD_PT_runway, MAD_PT_markings, MAD_PT_decals, MAD_PT_taxiways, MAD_PT_aprons, MAD_PT_lighting,
           MAD_PT_signage, MAD_PT_structures, MAD_PT_perimeter, MAD_PT_materials, MAD_PT_export, MAD_PT_help)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_add.append(_add_menu)


def unregister():
    try:
        bpy.types.VIEW3D_MT_add.remove(_add_menu)
    except (AttributeError, ValueError):
        pass
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
