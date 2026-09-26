"""Property groups: the complete parameter model of a base.

The root Empty of every generated base carries ``Object.mad`` (MAD_BaseSettings).
Every property has an ``update`` callback that asks the rebuild scheduler for a
(debounced) regeneration when *live update* is enabled.

Defaults follow UFC 3-260-01 (geometry), UFC 3-260-04 / FAA AC 150/5340-1M
(markings) and UFC 3-535-01 (lighting & signs) for a USAF Class B fighter base.
"""
from __future__ import annotations

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)

from .units import ft

# --------------------------------------------------------------------------- update plumbing
_SUSPEND = 0


def suspend_updates():
    global _SUSPEND
    _SUSPEND += 1


def resume_updates():
    global _SUSPEND
    _SUSPEND = max(0, _SUSPEND - 1)


def updates_suspended() -> bool:
    return _SUSPEND > 0


def _root_of(pg) -> bpy.types.Object | None:
    ob = pg.id_data if isinstance(pg.id_data, bpy.types.Object) else None
    return ob


def _upd(self, context):
    """Geometry-affecting change."""
    if updates_suspended():
        return
    from . import rebuild
    rebuild.on_param_change(_root_of(self), kind='GEOMETRY')


def _upd_mat(self, context):
    """Material / texture-only change (no geometry rebuild needed)."""
    if updates_suspended():
        return
    from . import rebuild
    rebuild.on_param_change(_root_of(self), kind='MATERIAL')


def _upd_light(self, context):
    if updates_suspended():
        return
    from . import rebuild
    rebuild.on_param_change(_root_of(self), kind='LIGHTING')


# --------------------------------------------------------------------------- enums
SURFACE_ITEMS = [
    ('PCC', "Concrete (PCC)", "Portland cement concrete slabs with sealed joints (typical USAF Class B runway)"),
    ('ASPHALT', "Asphalt (AC)", "Asphaltic concrete with longitudinal paving lanes"),
    ('HYBRID', "Hybrid (PCC ends / asphalt centre)", "Concrete at both ends and touchdown zones, asphalt in the middle (common on older bases)"),
]
STANDARD_ITEMS = [
    ('UFC', "UFC 3-260-04 (USAF/DoD)", "US military marking standard (FAA compatible)"),
    ('FAA', "FAA AC 150/5340-1M", "US civil standard (no leading zero, 12-in hold lines)"),
    ('ICAO', "ICAO Annex 14 / NATO STANAG 3158", "Metric ICAO geometry (9 m numerals, 30 m threshold stripes)"),
]
RUNWAY_CLASS_ITEMS = [
    ('B', "Class B (fixed-wing jet)", "Fighter / bomber / transport runway: 150-200 ft wide, 25 ft shoulders, 1,000 ft overruns"),
    ('A', "Class A (light aircraft)", "Light aircraft runway: 75-100 ft wide, 10 ft shoulders, 300 ft overruns"),
]
SUFFIX_ITEMS = [('NONE', "None", ""), ('L', "L", "Left"), ('C', "C", "Centre"), ('R', "R", "Right")]
OVERRUN_ITEMS = [
    ('PAVED', "Paved", "Paved overrun with yellow chevrons"),
    ('STABILIZED', "Stabilised", "Stabilised turf / gravel overrun (no chevrons)"),
    ('NONE', "None", "No overrun"),
]
SHOULDER_ITEMS = [
    ('PAVED', "Paved (asphalt)", "Paved shoulder, marked with yellow deceptive-surface stripes"),
    ('TURF', "Turf / stabilised", "Unpaved graded shoulder"),
]
ARRESTING_ITEMS = [
    ('NONE', "None", ""),
    ('BAK12', "BAK-12 (pendant cable)", "Pendant cable on rubber donuts with energy absorbers at both sides"),
    ('BAK14', "BAK-14 (retractable)", "Retractable pendant cable, flush when not in use"),
    ('E28', "E-28 (Navy)", "E-28 rotary friction brake system"),
]
TAXIWAY_KIND_ITEMS = [
    ('PARALLEL', "Parallel", "Full-length parallel taxiway"),
    ('CONNECTOR', "Connector", "Right-angle connector between runway and parallel taxiway"),
    ('HIGH_SPEED', "High-speed exit", "Acute-angle (30°) exit taxiway"),
    ('END', "End connector", "Turn-around connector at the runway end"),
]
SIDE_ITEMS = [('LEFT', "Left (+Y)", "Left of the low-to-high runway direction"), ('RIGHT', "Right (-Y)", "Right side")]
EXIT_DIR_ITEMS = [('HIGH', "Toward high end", "Serves landings from the low-numbered threshold"),
                  ('LOW', "Toward low end", "Serves landings from the high-numbered threshold")]
HOLD_ITEMS = [('AUTO', "Auto", "Pattern A at the standard distance (+ Pattern B if ILS)"),
              ('PATTERN_A', "Pattern A", "VFR runway holding position"),
              ('PATTERN_B', "Pattern A + B (ILS)", "Adds the ILS critical-area ladder"),
              ('NONE', "None", "")]
APRON_KIND_ITEMS = [
    ('MAIN', "Main parking apron", "Flow-through parking apron with lead-in lines and spot numbers"),
    ('ALERT', "Alert apron (Christmas tree)", "Quick-reaction alert pad with angled stubs off a spine taxilane"),
    ('HAS_LOOP', "HAS dispersal loop", "Loop taxiway with hardened aircraft shelters both sides"),
    ('ARM_DEARM', "Arm / de-arm pad", "End-of-runway pad with angled parking spots"),
    ('HOT_CARGO', "Hot cargo pad", "Isolated explosives-loading pad"),
    ('HANGAR_LINE', "Hangar line", "Apron in front of maintenance hangars"),
    ('HELIPAD', "Helipad", "Rotary-wing pad with H marking"),
    ('COMPASS', "Compass calibration pad", "Compass rose pad"),
    ('TRIM_PAD', "Engine run-up / trim pad", "Run-up pad with jet blast deflector"),
]
AIRCRAFT_CLASS_ITEMS = [
    ('FIGHTER', "Fighter (F-16 / F-35)", "Wingspan ~10-11 m"),
    ('TRAINER', "Trainer (T-38 / Hürjet)", "Wingspan ~8-10 m"),
    ('TRANSPORT', "Transport (C-130)", "Wingspan ~40 m"),
    ('HEAVY', "Heavy (C-17 / KC-135 / A400M)", "Wingspan ~40-52 m"),
    ('HELICOPTER', "Helicopter", "Rotor diameter ~15-20 m"),
]
ALS_ITEMS = [
    ('NONE', "None", ""),
    ('ALSF2', "ALSF-2 (CAT II/III)", "2,400 ft high-intensity system with red side rows and sequenced flashers"),
    ('ALSF1', "ALSF-1 (CAT I)", "2,400 ft with 1,000 ft crossbar and terminating bar"),
    ('MALSR', "MALSR", "1,400 ft medium-intensity system with RAIL flashers"),
    ('SSALR', "SSALR", "Simplified short system with RAIL"),
    ('ODALS', "ODALS", "Omnidirectional flashers (non-precision)"),
]
EDGE_LIGHT_ITEMS = [('HIRL', "HIRL", "High-intensity runway lights"), ('MIRL', "MIRL", "Medium intensity"), ('NONE', "None", "")]
HAS_STYLE_ITEMS = [
    ('NATO3', "NATO 3rd generation", "Semi-circular concrete arch with sliding blast doors"),
    ('TABVEE', "TAB-VEE (2nd gen)", "Corrugated steel arch with concrete cover"),
    ('EARTH', "Earth-covered", "Arch shelter under an earth berm"),
]
HAS_LAYOUT_ITEMS = [
    ('LOOP', "Dispersal loop", "Shelters on both sides of a loop taxiway"),
    ('ROW', "Row", "Single row of shelters along a taxilane"),
    ('HERRINGBONE', "Herringbone", "Angled shelters along a spine taxiway"),
]
ENGINE_ITEMS = [
    ('UNREAL', "Unreal Engine (ORM, DirectX normal)", ""),
    ('UNITY_URP', "Unity URP (Metallic-Smoothness)", ""),
    ('UNITY_HDRP', "Unity HDRP (Mask map)", ""),
    ('GLTF', "glTF / Godot (MetallicRoughness)", ""),
    ('SEPARATE', "Separate maps", "Unpacked BaseColor / Normal / Roughness / AO / Height"),
]
RES_ITEMS = [('512', "512 (preview)", ""), ('1024', "1K", ""), ('2048', "2K", ""), ('4096', "4K", ""), ('8192', "8K", "")]
EXPORT_FORMAT_ITEMS = [('FBX', "FBX", ""), ('GLTF', "glTF (.glb)", ""), ('GLTF_SEP', "glTF separate", ""), ('OBJ', "OBJ", "")]
AXIS_ITEMS = [('UNREAL', "Unreal (Z up, X fwd, cm)", ""), ('UNITY', "Unity (Y up, -Z fwd)", ""),
              ('GODOT', "Godot (Y up)", ""), ('BLENDER', "Blender (Z up, -Y fwd)", "")]
QUALITY_ITEMS = [('PREVIEW', "Preview", "Coarse tessellation, no small props"),
                 ('STANDARD', "Standard", "Balanced"),
                 ('HIGH', "High", "Fine tessellation, all props and details")]


# --------------------------------------------------------------------------- sub groups
class MAD_RunwaySettings(bpy.types.PropertyGroup):
    length: FloatProperty(name="Length", description="Runway length between thresholds (USAF Class B typical 8,000-12,000 ft)",
                          default=ft(10000), min=ft(1500), max=ft(20000), unit='LENGTH', update=_upd)
    width: FloatProperty(name="Width", description="Full-strength runway width (150 ft fighter / 200 ft bomber-tanker)",
                         default=ft(150), min=ft(50), max=ft(300), unit='LENGTH', update=_upd)
    heading: FloatProperty(name="Magnetic heading", description="Magnetic heading of the low-numbered runway (degrees)",
                           default=50.0, min=0.0, max=359.99, update=_upd)
    suffix: EnumProperty(name="Parallel suffix", items=SUFFIX_ITEMS, default='NONE', update=_upd)
    runway_class: EnumProperty(name="Class", items=RUNWAY_CLASS_ITEMS, default='B', update=_upd)
    surface: EnumProperty(name="Surface", items=SURFACE_ITEMS, default='PCC', update=_upd)
    hybrid_end_length: FloatProperty(name="Concrete end length", description="Length of the concrete sections at each end for hybrid runways",
                                     default=ft(3000), min=ft(500), max=ft(6000), unit='LENGTH', update=_upd)
    slab_size: FloatProperty(name="Slab size", description="PCC slab joint spacing (20 ft military typical, 25 ft heavy)",
                             default=ft(20), min=ft(10), max=ft(30), unit='LENGTH', update=_upd_mat)
    crown_percent: FloatProperty(name="Crown slope %", description="Transverse crown slope from centreline to edge (1.0-1.5 % typical)",
                                 default=1.5, min=0.0, max=2.0, precision=2, update=_upd)
    shoulder_width: FloatProperty(name="Shoulder width", description="Paved shoulder each side (25 ft Class B, 10 ft Class A)",
                                  default=ft(25), min=0.0, max=ft(100), unit='LENGTH', update=_upd)
    shoulder_type: EnumProperty(name="Shoulder", items=SHOULDER_ITEMS, default='PAVED', update=_upd)
    overrun_type: EnumProperty(name="Overrun", items=OVERRUN_ITEMS, default='PAVED', update=_upd)
    overrun_length: FloatProperty(name="Overrun length", description="Paved overrun at each end (1,000 ft USAF Class B)",
                                  default=ft(1000), min=0.0, max=ft(2000), unit='LENGTH', update=_upd)
    overrun_low: BoolProperty(name="Overrun at low end", default=True, update=_upd)
    overrun_high: BoolProperty(name="Overrun at high end", default=True, update=_upd)
    displaced_low: FloatProperty(name="Displaced threshold (low)", description="Displacement of the low-numbered threshold",
                                 default=0.0, min=0.0, max=ft(3000), unit='LENGTH', update=_upd)
    displaced_high: FloatProperty(name="Displaced threshold (high)", default=0.0, min=0.0, max=ft(3000), unit='LENGTH', update=_upd)
    arresting_type: EnumProperty(name="Arresting gear", items=ARRESTING_ITEMS, default='BAK12', update=_upd)
    arresting_distance: FloatProperty(name="Cable distance from threshold", description="Pendant cable station measured from each threshold (1,200-1,500 ft typical)",
                                      default=ft(1500), min=ft(500), max=ft(4000), unit='LENGTH', update=_upd)
    arresting_low: BoolProperty(name="Cable at low end", default=True, update=_upd)
    arresting_high: BoolProperty(name="Cable at high end", default=True, update=_upd)
    arresting_mid: BoolProperty(name="Mid-field cable", default=False, update=_upd)
    grooved: BoolProperty(name="Transverse grooving", description="Saw-cut transverse grooves (1/4 in at 1.5 in centres) on the runway surface",
                         default=True, update=_upd_mat)
    ils_low: BoolProperty(name="ILS approach (low end)", description="Precision approach to the low-numbered end (adds ILS hold lines, TDZ lights, localizer/glide slope)", default=True, update=_upd)
    ils_high: BoolProperty(name="ILS approach (high end)", default=False, update=_upd)
    elevation: FloatProperty(name="Field elevation", description="Field elevation (affects hold-line distance: +1 ft per 100 ft)",
                             default=ft(1000), min=-ft(500), max=ft(10000), unit='LENGTH', update=_upd)


class MAD_MarkingSettings(bpy.types.PropertyGroup):
    standard: EnumProperty(name="Standard", items=STANDARD_ITEMS, default='UFC', update=_upd)
    enabled: BoolProperty(name="Markings", default=True, update=_upd)
    designators: BoolProperty(name="Designator numerals", default=True, update=_upd)
    threshold: BoolProperty(name="Threshold stripes & bar", default=True, update=_upd)
    aiming_point: BoolProperty(name="Aiming point", default=True, update=_upd)
    touchdown_zone: BoolProperty(name="Touchdown zone", default=True, update=_upd)
    centerline: BoolProperty(name="Centreline", default=True, update=_upd)
    side_stripes: BoolProperty(name="Side stripes", default=True, update=_upd)
    shoulder_stripes: BoolProperty(name="Shoulder deceptive-surface stripes", default=True, update=_upd)
    overrun_chevrons: BoolProperty(name="Overrun chevrons", default=True, update=_upd)
    arresting_discs: BoolProperty(name="Arresting-gear discs", default=True, update=_upd)
    taxiway_centerline: BoolProperty(name="Taxiway centrelines", default=True, update=_upd)
    taxiway_edges: BoolProperty(name="Taxiway edge lines", default=True, update=_upd)
    hold_lines: BoolProperty(name="Holding positions", default=True, update=_upd)
    enhanced_centerline: BoolProperty(name="Enhanced centreline", default=True, update=_upd)
    surface_signs: BoolProperty(name="Surface painted signs", default=True, update=_upd)
    apron_markings: BoolProperty(name="Apron markings", default=True, update=_upd)
    road_markings: BoolProperty(name="Road markings", default=True, update=_upd)
    black_border: EnumProperty(name="Black border", items=[('AUTO', "Auto (concrete)", "Border on light pavement only"),
                                                          ('ALWAYS', "Always", ""), ('NEVER', "Never", "")],
                               default='AUTO', update=_upd)
    taxi_line_width: EnumProperty(name="Taxiway line width", items=[('6', "6 in (DoD)", ""), ('12', "12 in (FAA)", "")],
                                  default='6', update=_upd)
    hold_line_width: EnumProperty(name="Hold line width", items=[('6', "6 in (DoD)", ""), ('12', "12 in (FAA)", "")],
                                  default='6', update=_upd)
    leading_zero: BoolProperty(name="Leading zero (05 vs 5)", default=True, update=_upd)
    paint_wear: FloatProperty(name="Paint wear", description="Amount of chipping / tyre-rubbed erosion on paint", default=0.35, min=0.0, max=1.0, update=_upd_mat)
    striated: BoolProperty(name="Striated wide markings", description="Paint wide markings as 6-in stripes (frost-heave practice)", default=False, update=_upd)


class MAD_TaxiwayItem(bpy.types.PropertyGroup):
    name: StringProperty(name="Designator", default="A", update=_upd)
    kind: EnumProperty(name="Kind", items=TAXIWAY_KIND_ITEMS, default='CONNECTOR', update=_upd)
    side: EnumProperty(name="Side", items=SIDE_ITEMS, default='LEFT', update=_upd)
    width: FloatProperty(name="Width", default=ft(75), min=ft(25), max=ft(150), unit='LENGTH', update=_upd)
    shoulder_width: FloatProperty(name="Shoulder", default=ft(25), min=0.0, max=ft(50), unit='LENGTH', update=_upd)
    offset: FloatProperty(name="Offset from runway CL", description="Parallel taxiway centreline separation (1,000 ft Class B, 500 ft Class A)",
                          default=ft(1037.5), min=ft(200), max=ft(3000), unit='LENGTH', update=_upd)
    station: FloatProperty(name="Station", description="Position along the runway from its midpoint (negative = toward the low end)",
                           default=0.0, min=-ft(10000), max=ft(10000), unit='LENGTH', update=_upd)
    angle: FloatProperty(name="Exit angle", description="High-speed exit angle to the runway", default=30.0, min=25.0, max=60.0, update=_upd)
    exit_dir: EnumProperty(name="Exit direction", items=EXIT_DIR_ITEMS, default='HIGH', update=_upd)
    fillet_radius: FloatProperty(name="Fillet radius", default=ft(125), min=ft(25), max=ft(400), unit='LENGTH', update=_upd)
    hold: EnumProperty(name="Hold position", items=HOLD_ITEMS, default='AUTO', update=_upd)
    hold_distance: FloatProperty(name="Hold distance override", description="Distance from runway edge (0 = standard)", default=0.0, min=0.0, max=ft(600), unit='LENGTH', update=_upd)
    edge_lights: BoolProperty(name="Edge lights", default=True, update=_upd_light)
    centerline_lights: BoolProperty(name="Centreline lights", default=False, update=_upd_light)
    signs: BoolProperty(name="Signs", default=True, update=_upd)
    enabled: BoolProperty(name="Enabled", default=True, update=_upd)


class MAD_ApronItem(bpy.types.PropertyGroup):
    name: StringProperty(name="Name", default="Main Apron", update=_upd)
    kind: EnumProperty(name="Kind", items=APRON_KIND_ITEMS, default='MAIN', update=_upd)
    side: EnumProperty(name="Side", items=SIDE_ITEMS, default='LEFT', update=_upd)
    station: FloatProperty(name="Station", description="Centre position along the runway from its midpoint", default=0.0,
                           min=-ft(12000), max=ft(12000), unit='LENGTH', update=_upd)
    offset: FloatProperty(name="Offset", description="Distance of the apron's near edge beyond the parallel taxiway centreline (0 = auto)",
                          default=0.0, min=0.0, max=ft(3000), unit='LENGTH', update=_upd)
    length: FloatProperty(name="Length (along runway)", default=ft(1200), min=ft(100), max=ft(6000), unit='LENGTH', update=_upd)
    depth: FloatProperty(name="Depth", default=ft(500), min=ft(60), max=ft(3000), unit='LENGTH', update=_upd)
    aircraft: EnumProperty(name="Aircraft class", items=AIRCRAFT_CLASS_ITEMS, default='FIGHTER', update=_upd)
    spots: IntProperty(name="Parking spots", description="0 = automatic from apron size", default=0, min=0, max=200, update=_upd)
    rows: IntProperty(name="Rows", default=1, min=1, max=4, update=_upd)
    shelters: IntProperty(name="Shelters", description="Hardened aircraft shelters on a HAS loop", default=8, min=1, max=48, update=_upd)
    lead_in_radius: FloatProperty(name="Lead-in radius", default=ft(60), min=ft(25), max=ft(150), unit='LENGTH', update=_upd)
    floodlights: BoolProperty(name="Floodlight masts", default=True, update=_upd)
    hydrants: BoolProperty(name="Hydrant fuel pits", default=True, update=_upd)
    tie_downs: BoolProperty(name="Tie-downs & grounding points", default=True, update=_upd)
    blast_deflector: BoolProperty(name="Jet blast deflector", default=False, update=_upd)
    enabled: BoolProperty(name="Enabled", default=True, update=_upd)


class MAD_LightingSettings(bpy.types.PropertyGroup):
    enabled: BoolProperty(name="Lighting", default=True, update=_upd_light)
    edge: EnumProperty(name="Runway edge lights", items=EDGE_LIGHT_ITEMS, default='HIRL', update=_upd_light)
    edge_spacing: FloatProperty(name="Edge light spacing", default=ft(200), min=ft(50), max=ft(400), unit='LENGTH', update=_upd_light)
    threshold: BoolProperty(name="Threshold / end lights", default=True, update=_upd_light)
    centerline: BoolProperty(name="Centreline lights", default=True, update=_upd_light)
    tdz: BoolProperty(name="Touchdown zone lights", default=True, update=_upd_light)
    papi: BoolProperty(name="PAPI", default=True, update=_upd_light)
    papi_side: EnumProperty(name="PAPI side", items=SIDE_ITEMS, default='LEFT', update=_upd_light)
    papi_distance: FloatProperty(name="PAPI distance from threshold", default=ft(1000), min=ft(500), max=ft(2000), unit='LENGTH', update=_upd_light)
    reil: BoolProperty(name="REIL", default=True, update=_upd_light)
    als_low: EnumProperty(name="Approach lights (low end)", items=ALS_ITEMS, default='ALSF2', update=_upd_light)
    als_high: EnumProperty(name="Approach lights (high end)", items=ALS_ITEMS, default='MALSR', update=_upd_light)
    taxiway_edge: BoolProperty(name="Taxiway edge lights (blue)", default=True, update=_upd_light)
    taxiway_centerline: BoolProperty(name="Taxiway centreline lights (green)", default=False, update=_upd_light)
    taxiway_spacing: FloatProperty(name="Taxiway light spacing", default=ft(200), min=ft(25), max=ft(300), unit='LENGTH', update=_upd_light)
    guard_lights: BoolProperty(name="Runway guard lights (wig-wags)", default=True, update=_upd_light)
    stop_bars: BoolProperty(name="Stop bars", default=False, update=_upd_light)
    apron_floods: BoolProperty(name="Apron floodlights", default=True, update=_upd_light)
    beacon: BoolProperty(name="Rotating beacon", default=True, update=_upd_light)
    obstruction: BoolProperty(name="Obstruction lights", default=True, update=_upd_light)
    night_mode: BoolProperty(name="Night mode (emissive)", description="Turn on light emission in materials", default=False, update=_upd_mat)
    emission_strength: FloatProperty(name="Emission strength", default=25.0, min=0.0, max=200.0, update=_upd_mat)
    fixture_detail: BoolProperty(name="Detailed fixtures", description="Concrete bases and frangible couplings on elevated fixtures", default=True, update=_upd_light)


class MAD_SignageSettings(bpy.types.PropertyGroup):
    enabled: BoolProperty(name="Signage", default=True, update=_upd)
    hold_signs: BoolProperty(name="Runway holding position signs", default=True, update=_upd)
    location_signs: BoolProperty(name="Taxiway location signs", default=True, update=_upd)
    direction_signs: BoolProperty(name="Direction signs", default=True, update=_upd)
    rdr_signs: BoolProperty(name="Runway distance remaining signs", default=True, update=_upd)
    agm_signs: BoolProperty(name="Arresting gear markers", default=True, update=_upd)
    wind_cones: BoolProperty(name="Wind cones", default=True, update=_upd)
    sign_size: IntProperty(name="Sign size", description="FAA sign size class (1-5)", default=3, min=1, max=5, update=_upd)
    sign_offset: FloatProperty(name="Sign offset from taxiway edge", default=ft(20), min=ft(10), max=ft(35), unit='LENGTH', update=_upd)


class MAD_StructureSettings(bpy.types.PropertyGroup):
    enabled: BoolProperty(name="Structures", default=True, update=_upd)
    has_style: EnumProperty(name="HAS style", items=HAS_STYLE_ITEMS, default='NATO3', update=_upd)
    has_layout: EnumProperty(name="HAS layout", items=HAS_LAYOUT_ITEMS, default='LOOP', update=_upd)
    hangars: IntProperty(name="Maintenance hangars", default=2, min=0, max=8, update=_upd)
    large_hangar: BoolProperty(name="Large airlift hangar", default=False, update=_upd)
    control_tower: BoolProperty(name="Control tower", default=True, update=_upd)
    tower_height: FloatProperty(name="Tower height", default=32.0, min=15.0, max=60.0, unit='LENGTH', update=_upd)
    fire_station: BoolProperty(name="Fire / crash rescue station", default=True, update=_upd)
    ops_buildings: BoolProperty(name="Squadron ops & base ops", default=True, update=_upd)
    fuel_farm: BoolProperty(name="Fuel farm (POL)", default=True, update=_upd)
    fuel_tanks: IntProperty(name="Fuel tanks", default=4, min=1, max=12, update=_upd)
    munitions: BoolProperty(name="Munitions storage area", default=True, update=_upd)
    igloos: IntProperty(name="Earth-covered magazines", default=6, min=1, max=24, update=_upd)
    arresting_housings: BoolProperty(name="Arresting gear housings", default=True, update=_upd)
    lighting_vault: BoolProperty(name="Lighting vault", default=True, update=_upd)
    hush_house: BoolProperty(name="Hush house", default=True, update=_upd)
    wash_rack: BoolProperty(name="Wash rack", default=True, update=_upd)
    navaids: BoolProperty(name="Navaids (ILS, TACAN, radar, weather)", default=True, update=_upd)
    revetments: BoolProperty(name="Revetments on alert apron", default=True, update=_upd)
    camouflage: BoolProperty(name="Camouflage colours on shelters", default=True, update=_upd_mat)


class MAD_PerimeterSettings(bpy.types.PropertyGroup):
    enabled: BoolProperty(name="Perimeter", default=True, update=_upd)
    fence: BoolProperty(name="Security fence", default=True, update=_upd)
    double_fence: BoolProperty(name="Double fence", default=True, update=_upd)
    fence_height: FloatProperty(name="Fence height", default=2.4, min=1.8, max=4.0, unit='LENGTH', update=_upd)
    fence_gap: FloatProperty(name="Gap between fences", default=8.0, min=3.0, max=20.0, unit='LENGTH', update=_upd)
    margin: FloatProperty(name="Fence margin from pavement", description="Clear distance from the outermost pavement/approach lights to the fence",
                          default=ft(500), min=ft(150), max=ft(3000), unit='LENGTH', update=_upd)
    patrol_road: BoolProperty(name="Patrol road", default=True, update=_upd)
    road_width: FloatProperty(name="Road width", default=5.0, min=3.0, max=8.0, unit='LENGTH', update=_upd)
    entry_control_point: BoolProperty(name="Entry control point", default=True, update=_upd)
    guard_towers: BoolProperty(name="Guard towers", default=True, update=_upd)
    service_roads: BoolProperty(name="Service roads", default=True, update=_upd)


class MAD_TerrainSettings(bpy.types.PropertyGroup):
    enabled: BoolProperty(name="Terrain", default=True, update=_upd)
    margin: FloatProperty(name="Terrain margin beyond fence", default=200.0, min=0.0, max=2000.0, unit='LENGTH', update=_upd)
    dirt_strip: FloatProperty(name="Dirt strip at pavement edge", default=0.8, min=0.0, max=3.0, unit='LENGTH', update=_upd)
    ditches: BoolProperty(name="Drainage ditches", default=True, update=_upd)
    ditch_offset: FloatProperty(name="Ditch offset from runway CL", default=ft(600), min=ft(100), max=ft(1200), unit='LENGTH', update=_upd)
    mowing_stripes: BoolProperty(name="Mowing stripes", default=True, update=_upd_mat)
    subdivision: FloatProperty(name="Grid spacing", default=25.0, min=5.0, max=100.0, unit='LENGTH', update=_upd)
    undulation: FloatProperty(name="Ground undulation", description="Gentle terrain height noise outside the airfield strip", default=0.4, min=0.0, max=3.0, unit='LENGTH', update=_upd)


class MAD_MaterialSettings(bpy.types.PropertyGroup):
    resolution: EnumProperty(name="Texture resolution", items=RES_ITEMS, default='2048', update=_upd_mat)
    slabs_per_tile: IntProperty(name="Slabs per tile", description="Concrete slabs across one texture tile (1 = best texel density, 2-4 = less repetition)", default=2, min=1, max=4, update=_upd_mat)
    seed: IntProperty(name="Seed", default=1234, min=0, max=999999, update=_upd_mat)
    engine: EnumProperty(name="Engine preset", items=ENGINE_ITEMS, default='UNREAL', update=_upd_mat)
    normal_directx: BoolProperty(name="DirectX normal (Y-)", description="Flip green channel (Unreal); off = OpenGL (Unity/glTF/Blender)", default=True, update=_upd_mat)
    texture_dir: StringProperty(name="Texture folder", subtype='DIR_PATH', default="//textures/", update=_upd_mat)
    generate_textures: BoolProperty(name="Generate image textures", description="Synthesize PNG texture sets (off = flat placeholder materials)", default=True, update=_upd_mat)
    age: FloatProperty(name="Pavement age", description="0 = new pavement, 1 = heavily weathered", default=0.55, min=0.0, max=1.0, update=_upd_mat)
    rubber: FloatProperty(name="Rubber deposits", description="Touchdown-zone rubber build-up", default=0.7, min=0.0, max=1.0, update=_upd_mat)
    dirt: FloatProperty(name="Edge dirt", default=0.5, min=0.0, max=1.0, update=_upd_mat)
    concrete_tint: FloatVectorProperty(name="Concrete tint", subtype='COLOR', size=3, default=(1.0, 1.0, 1.0), min=0.0, max=1.0, update=_upd_mat)
    asphalt_tint: FloatVectorProperty(name="Asphalt tint", subtype='COLOR', size=3, default=(1.0, 1.0, 1.0), min=0.0, max=1.0, update=_upd_mat)
    wet: FloatProperty(name="Wetness", default=0.0, min=0.0, max=1.0, update=_upd_mat)


class MAD_ExportSettings(bpy.types.PropertyGroup):
    format: EnumProperty(name="Format", items=EXPORT_FORMAT_ITEMS, default='FBX')
    axis: EnumProperty(name="Axis preset", items=AXIS_ITEMS, default='UNREAL')
    directory: StringProperty(name="Export folder", subtype='DIR_PATH', default="//export/")
    join_by_category: BoolProperty(name="Join instances by category", description="Merge light fixtures / fence posts etc. into one mesh per category", default=True)
    lods: BoolProperty(name="Generate LODs", default=True)
    lod_count: IntProperty(name="LOD levels", default=3, min=1, max=4)
    collision: BoolProperty(name="Collision meshes (UCX_)", default=True)
    copy_textures: BoolProperty(name="Copy textures", default=True)
    export_terrain: BoolProperty(name="Include terrain", default=True)
    export_markings_as_decals: BoolProperty(name="Markings as separate meshes", default=True)
    scale_cm: BoolProperty(name="Scale to centimetres (Unreal)", default=False)


class MAD_BaseSettings(bpy.types.PropertyGroup):
    """Lives on the base root Empty (``Object.mad``)."""
    is_root: BoolProperty(name="Is base root", default=False)
    base_name: StringProperty(name="Base name", default="Airbase", update=_upd)
    icao_code: StringProperty(name="ICAO code", default="LTAG", maxlen=4)
    preset_name: StringProperty(name="Preset", default="")
    quality: EnumProperty(name="Quality", items=QUALITY_ITEMS, default='STANDARD', update=_upd)
    runway: PointerProperty(type=MAD_RunwaySettings)
    markings: PointerProperty(type=MAD_MarkingSettings)
    taxiways: CollectionProperty(type=MAD_TaxiwayItem)
    taxiway_index: IntProperty(default=0)
    aprons: CollectionProperty(type=MAD_ApronItem)
    apron_index: IntProperty(default=0)
    lighting: PointerProperty(type=MAD_LightingSettings)
    signage: PointerProperty(type=MAD_SignageSettings)
    structures: PointerProperty(type=MAD_StructureSettings)
    perimeter: PointerProperty(type=MAD_PerimeterSettings)
    terrain: PointerProperty(type=MAD_TerrainSettings)
    materials: PointerProperty(type=MAD_MaterialSettings)
    export: PointerProperty(type=MAD_ExportSettings)
    warnings: StringProperty(name="Warnings", default="")
    build_stats: StringProperty(name="Stats", default="")


class MAD_UISettings(bpy.types.PropertyGroup):
    """Scene-level UI state (``Scene.mad_ui``)."""
    live_update: BoolProperty(name="Live update", description="Rebuild automatically when a parameter changes", default=False)
    show_advanced: BoolProperty(name="Show advanced", default=False)
    imperial: BoolProperty(name="Show feet", description="Display lengths in feet in labels", default=False)
    new_base_preset: StringProperty(name="Preset", default="USAF_CLASS_B_FIGHTER")


CLASSES = (
    MAD_RunwaySettings, MAD_MarkingSettings, MAD_TaxiwayItem, MAD_ApronItem, MAD_LightingSettings,
    MAD_SignageSettings, MAD_StructureSettings, MAD_PerimeterSettings, MAD_TerrainSettings,
    MAD_MaterialSettings, MAD_ExportSettings, MAD_BaseSettings, MAD_UISettings,
)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Object.mad = PointerProperty(type=MAD_BaseSettings)
    bpy.types.Scene.mad_ui = PointerProperty(type=MAD_UISettings)


def unregister():
    if hasattr(bpy.types.Scene, "mad_ui"):
        del bpy.types.Scene.mad_ui
    if hasattr(bpy.types.Object, "mad"):
        del bpy.types.Object.mad
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
