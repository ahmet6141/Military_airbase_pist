"""Add / remove / move / duplicate operators for the taxiway, apron and heliport lists."""
from __future__ import annotations

import bpy
from bpy.props import EnumProperty

from ..core import rebuild
from ..core.props import APRON_KIND_ITEMS, TAXIWAY_KIND_ITEMS, suspend_updates, resume_updates
from ..core.units import ft
from .build_ops import active_root

_LETTERS = "ABCDEFGHJKLMNPQRSTUVWYZ"   # ICAO taxiway letters (I, O, X are not used)


def _next_taxiway_name(items) -> str:
    used = {t.name for t in items}
    for ch in _LETTERS:
        if ch not in used:
            return ch
    for ch in _LETTERS:
        for n in range(1, 20):
            if f"{ch}{n}" not in used:
                return f"{ch}{n}"
    return "T"


def _after_change(context, root):
    rebuild.on_param_change(root, 'GEOMETRY')


class MAD_OT_taxiway_add(bpy.types.Operator):
    bl_idname = "mad.taxiway_add"
    bl_label = "Add Taxiway"
    bl_description = "Add a taxiway of the chosen kind with standard Class B dimensions"
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name="Kind", items=TAXIWAY_KIND_ITEMS, default='CONNECTOR')

    @classmethod
    def poll(cls, context):
        return active_root(context) is not None

    def execute(self, context):
        root = active_root(context)
        s = root.mad
        suspend_updates()
        try:
            # read everything from the existing list BEFORE adding (the new item would otherwise count as a used
            # name / as the parallel taxiway itself); plain Python values only, adding can reallocate the collection
            name = _next_taxiway_name(s.taxiways)
            par = next((x for x in s.taxiways if x.kind == 'PARALLEL'), None)
            par_side = par.side if par else 'LEFT'
            par_offset = par.offset if par else None
            t = s.taxiways.add()
            t.name = name
            t.kind = self.kind
            t.side = par_side
            if self.kind == 'PARALLEL':
                t.offset = ft(1037.5) if par_offset is None else par_offset + ft(237.5)
            elif self.kind == 'HIGH_SPEED':
                t.station = ft(1500)
                t.exit_dir = 'HIGH'
            elif self.kind == 'END':
                t.station = 1.0
            else:
                # place the connector halfway between the existing ones' largest gap
                stations = sorted(x.station for x in s.taxiways if x.kind in ('CONNECTOR', 'HIGH_SPEED'))
                t.station = 0.0 if not stations else (stations[0] - ft(1500) if len(stations) == 1 else (stations[-1] + ft(1500)))
            s.taxiway_index = len(s.taxiways) - 1
        finally:
            resume_updates()
        _after_change(context, root)
        return {'FINISHED'}


class MAD_OT_taxiway_remove(bpy.types.Operator):
    bl_idname = "mad.taxiway_remove"
    bl_label = "Remove Taxiway"
    bl_description = "Remove the selected taxiway"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        root = active_root(context)
        return root is not None and 0 <= root.mad.taxiway_index < len(root.mad.taxiways)

    def execute(self, context):
        root = active_root(context)
        s = root.mad
        suspend_updates()
        try:
            s.taxiways.remove(s.taxiway_index)
            s.taxiway_index = max(0, min(s.taxiway_index, len(s.taxiways) - 1))
        finally:
            resume_updates()
        _after_change(context, root)
        return {'FINISHED'}


class MAD_OT_taxiway_move(bpy.types.Operator):
    bl_idname = "mad.taxiway_move"
    bl_label = "Move Taxiway"
    bl_description = "Move the selected taxiway up or down in the list"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")], default='UP')

    @classmethod
    def poll(cls, context):
        root = active_root(context)
        return root is not None and len(root.mad.taxiways) > 1

    def execute(self, context):
        s = active_root(context).mad
        i = s.taxiway_index
        j = i - 1 if self.direction == 'UP' else i + 1
        if 0 <= j < len(s.taxiways):
            s.taxiways.move(i, j)
            s.taxiway_index = j
        return {'FINISHED'}


class MAD_OT_taxiway_duplicate(bpy.types.Operator):
    bl_idname = "mad.taxiway_duplicate"
    bl_label = "Duplicate Taxiway"
    bl_description = "Duplicate the selected taxiway (shifted 1,000 ft along the runway)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        root = active_root(context)
        return root is not None and 0 <= root.mad.taxiway_index < len(root.mad.taxiways)

    def execute(self, context):
        root = active_root(context)
        s = root.mad
        src = s.taxiways[s.taxiway_index]
        suspend_updates()
        try:
            t = s.taxiways.add()
            for prop in src.bl_rna.properties:
                if prop.identifier in ('rna_type',) or prop.is_readonly:
                    continue
                setattr(t, prop.identifier, getattr(src, prop.identifier))
            t.name = _next_taxiway_name(s.taxiways)
            if t.kind in ('CONNECTOR', 'HIGH_SPEED'):
                t.station = src.station + ft(1000)
            elif t.kind == 'PARALLEL':
                t.offset = src.offset + ft(237.5)
            s.taxiway_index = len(s.taxiways) - 1
        finally:
            resume_updates()
        _after_change(context, root)
        return {'FINISHED'}


# --------------------------------------------------------------------------- aprons
class MAD_OT_apron_add(bpy.types.Operator):
    bl_idname = "mad.apron_add"
    bl_label = "Add Apron"
    bl_description = "Add an apron or pad of the chosen kind"
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(name="Kind", items=APRON_KIND_ITEMS, default='MAIN')

    @classmethod
    def poll(cls, context):
        return active_root(context) is not None

    def execute(self, context):
        root = active_root(context)
        s = root.mad
        suspend_updates()
        try:
            a = s.aprons.add()
            labels = {k: n for k, n, _ in APRON_KIND_ITEMS}
            a.name = labels.get(self.kind, "Apron")
            a.kind = self.kind
            par = next((x for x in s.taxiways if x.kind == 'PARALLEL'), None)
            a.side = (par.side if par else 'LEFT') if self.kind != 'ALERT' else ('RIGHT' if (par is None or par.side == 'LEFT') else 'LEFT')
            defaults = {
                'MAIN': (ft(1200), ft(500), 'FIGHTER', 2, 0),
                'ALERT': (ft(700), ft(700), 'FIGHTER', 1, 8),
                'HAS_LOOP': (ft(1700), ft(1250), 'FIGHTER', 1, 12),
                'ARM_DEARM': (ft(250), ft(300), 'FIGHTER', 1, 4),
                'HOT_CARGO': (ft(300), ft(300), 'TRANSPORT', 1, 1),
                'HANGAR_LINE': (ft(850), ft(300), 'FIGHTER', 1, 0),
                'HELIPAD': (ft(200), ft(200), 'HELICOPTER', 1, 0),
                'COMPASS': (ft(200), ft(200), 'FIGHTER', 1, 0),
                'TRIM_PAD': (ft(300), ft(300), 'FIGHTER', 1, 1),
            }
            length, depth, ac, rows, spots = defaults.get(self.kind, defaults['MAIN'])
            a.length, a.depth, a.aircraft, a.rows, a.spots = length, depth, ac, rows, spots
            # station: next free slot along the parallel taxiway
            taken = sorted(x.station for x in s.aprons if x is not a and x.side == a.side)
            a.station = 0.0 if not taken else taken[-1] + ft(1800)
            if self.kind == 'ARM_DEARM':
                a.station = -s.runway.length / 2 if not any(x.kind == 'ARM_DEARM' and x.station < 0 for x in s.aprons) else s.runway.length / 2
            s.apron_index = len(s.aprons) - 1
        finally:
            resume_updates()
        _after_change(context, root)
        return {'FINISHED'}


class MAD_OT_apron_remove(bpy.types.Operator):
    bl_idname = "mad.apron_remove"
    bl_label = "Remove Apron"
    bl_description = "Remove the selected apron"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        root = active_root(context)
        return root is not None and len(root.mad.aprons) > 0

    def execute(self, context):
        root = active_root(context)
        s = root.mad
        suspend_updates()
        try:
            s.aprons.remove(s.apron_index)
            s.apron_index = max(0, min(s.apron_index, len(s.aprons) - 1))
        finally:
            resume_updates()
        _after_change(context, root)
        return {'FINISHED'}


class MAD_OT_apron_move(bpy.types.Operator):
    bl_idname = "mad.apron_move"
    bl_label = "Move Apron"
    bl_description = "Move the selected apron up or down in the list"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")], default='UP')

    @classmethod
    def poll(cls, context):
        root = active_root(context)
        return root is not None and len(root.mad.aprons) > 1

    def execute(self, context):
        s = active_root(context).mad
        i = s.apron_index
        j = i - 1 if self.direction == 'UP' else i + 1
        if 0 <= j < len(s.aprons):
            s.aprons.move(i, j)
            s.apron_index = j
        return {'FINISHED'}


class MAD_OT_apron_duplicate(bpy.types.Operator):
    bl_idname = "mad.apron_duplicate"
    bl_label = "Duplicate Apron"
    bl_description = "Duplicate the selected apron further along the taxiway"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        root = active_root(context)
        return root is not None and 0 <= root.mad.apron_index < len(root.mad.aprons)

    def execute(self, context):
        root = active_root(context)
        s = root.mad
        src = s.aprons[s.apron_index]
        suspend_updates()
        try:
            a = s.aprons.add()
            for prop in src.bl_rna.properties:
                if prop.identifier in ('rna_type',) or prop.is_readonly:
                    continue
                setattr(a, prop.identifier, getattr(src, prop.identifier))
            a.name = src.name + " 2"
            a.station = src.station + src.length + ft(600)
            s.apron_index = len(s.aprons) - 1
        finally:
            resume_updates()
        _after_change(context, root)
        return {'FINISHED'}


# --------------------------------------------------------------------------- heliports
class MAD_OT_heliport_add(bpy.types.Operator):
    bl_idname = "mad.heliport_add"
    bl_label = "Add Heliport"
    bl_description = "Add a heliport: FATO / TLOF pad, helitaxiway and parking stands"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return active_root(context) is not None

    def execute(self, context):
        root = active_root(context)
        s = root.mad
        suspend_updates()
        try:
            h = s.heliports.add()
            h.name = f"H{len(s.heliports)}"
            par = next((x for x in s.taxiways if x.kind == 'PARALLEL'), None)
            h.side = par.side if par else 'LEFT'
            taken = sorted(x.station for x in s.heliports if x is not h and x.side == h.side)
            h.station = ft(2500) if not taken else taken[-1] + ft(500)
            s.heliport_index = len(s.heliports) - 1
        finally:
            resume_updates()
        _after_change(context, root)
        return {'FINISHED'}


class MAD_OT_heliport_remove(bpy.types.Operator):
    bl_idname = "mad.heliport_remove"
    bl_label = "Remove Heliport"
    bl_description = "Remove the selected heliport"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        root = active_root(context)
        return root is not None and 0 <= root.mad.heliport_index < len(root.mad.heliports)

    def execute(self, context):
        root = active_root(context)
        s = root.mad
        suspend_updates()
        try:
            s.heliports.remove(s.heliport_index)
            s.heliport_index = max(0, min(s.heliport_index, len(s.heliports) - 1))
        finally:
            resume_updates()
        _after_change(context, root)
        return {'FINISHED'}


class MAD_OT_heliport_duplicate(bpy.types.Operator):
    bl_idname = "mad.heliport_duplicate"
    bl_label = "Duplicate Heliport"
    bl_description = "Duplicate the selected heliport further along the runway"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        root = active_root(context)
        return root is not None and 0 <= root.mad.heliport_index < len(root.mad.heliports)

    def execute(self, context):
        root = active_root(context)
        s = root.mad
        src = s.heliports[s.heliport_index]
        suspend_updates()
        try:
            h = s.heliports.add()
            for prop in src.bl_rna.properties:
                if prop.identifier in ('rna_type',) or prop.is_readonly:
                    continue
                setattr(h, prop.identifier, getattr(src, prop.identifier))
            h.name = f"H{len(s.heliports)}"
            h.station = src.station + ft(600) + (src.stands + 1) * 40.0
            s.heliport_index = len(s.heliports) - 1
        finally:
            resume_updates()
        _after_change(context, root)
        return {'FINISHED'}


class MAD_OT_heliport_move(bpy.types.Operator):
    bl_idname = "mad.heliport_move"
    bl_label = "Move Heliport"
    bl_description = "Move the selected heliport up or down in the list"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")], default='UP')

    @classmethod
    def poll(cls, context):
        root = active_root(context)
        return root is not None and len(root.mad.heliports) > 1

    def execute(self, context):
        s = active_root(context).mad
        i = s.heliport_index
        j = i - 1 if self.direction == 'UP' else i + 1
        if 0 <= j < len(s.heliports):
            s.heliports.move(i, j)
            s.heliport_index = j
        return {'FINISHED'}


CLASSES = (MAD_OT_taxiway_add, MAD_OT_taxiway_remove, MAD_OT_taxiway_move, MAD_OT_taxiway_duplicate,
           MAD_OT_apron_add, MAD_OT_apron_remove, MAD_OT_apron_move, MAD_OT_apron_duplicate,
           MAD_OT_heliport_add, MAD_OT_heliport_remove, MAD_OT_heliport_move, MAD_OT_heliport_duplicate)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
