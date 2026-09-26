"""Material library: one place that defines every material the generator uses.

Geometry builders only ever call ``get_material(name)``; they never build node
trees themselves. Each entry in ``SPECS`` describes a material in engine-neutral
terms (albedo, roughness, metallic, texture set, tile size, emission, alpha mode).

``build_material`` (in ``materials/nodes.py``) turns a spec into a Blender node
tree; when a texture set exists on disk (see ``textures/synth.py``) the material
uses image textures with world-planar UVs baked into the mesh, otherwise a flat
placeholder colour so the base always builds.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import bpy

from ..core import constants as C

# --------------------------------------------------------------------------- spec
@dataclass(frozen=True)
class MaterialSpec:
    name: str
    color: str                       # sRGB hex albedo (used as tint when textured)
    roughness: float = 0.8
    metallic: float = 0.0
    texture_set: str | None = None   # key into textures.synth.TEXTURE_SETS, or None for flat colour
    tile: float = 4.0                # metres per texture repeat (UVs are baked as xy / tile)
    emission: str | None = None      # emissive colour (lights); strength from settings.lighting
    emission_strength: float = 0.0
    alpha: str = 'OPAQUE'            # OPAQUE | MASKED (paint wear / chain link) 
    wear_channel: str | None = None  # vertex colour channel driving darkening: 'R' rubber, 'G' dirt, 'A' paint wear
    normal_strength: float = 1.0
    specular: float = 0.5
    two_sided: bool = False
    tags: tuple[str, ...] = ()


def _s(name, color, **kw) -> MaterialSpec:
    return MaterialSpec(name=name, color=color, **kw)


SPECS: dict[str, MaterialSpec] = {s.name: s for s in [
    # ---- pavement (textured, world planar UV)
    _s('RunwayConcrete', "#B5B4AE", roughness=0.86, texture_set='RunwayConcrete', tile=12.19, wear_channel='R', tags=('pavement', 'concrete')),
    _s('RunwayAsphalt', "#4A4A48", roughness=0.9, texture_set='RunwayAsphalt', tile=6.0, wear_channel='R', tags=('pavement', 'asphalt')),
    _s('TaxiwayAsphalt', "#535351", roughness=0.9, texture_set='TaxiwayAsphalt', tile=6.0, wear_channel='G', tags=('pavement', 'asphalt')),
    _s('TaxiwayConcrete', "#AEADA7", roughness=0.86, texture_set='ApronConcrete', tile=12.19, wear_channel='G', tags=('pavement', 'concrete')),
    _s('ApronConcrete', "#ADACA5", roughness=0.85, texture_set='ApronConcrete', tile=12.19, wear_channel='G', tags=('pavement', 'concrete')),
    _s('PadConcrete', "#B0AFA8", roughness=0.85, texture_set='ApronConcrete', tile=12.19, wear_channel='G', tags=('pavement', 'concrete')),
    _s('OverrunAsphalt', "#5A5955", roughness=0.92, texture_set='TaxiwayAsphalt', tile=6.0, wear_channel='G', tags=('pavement', 'asphalt')),
    _s('ShoulderAsphalt', "#5E5C57", roughness=0.93, texture_set='ShoulderAsphalt', tile=5.0, wear_channel='G', tags=('pavement', 'asphalt')),
    _s('Road', "#4E4D4A", roughness=0.9, texture_set='TaxiwayAsphalt', tile=5.0, tags=('pavement', 'asphalt')),
    _s('Gravel', "#8F8A80", roughness=0.95, texture_set='Gravel', tile=3.0, tags=('terrain',)),
    # ---- terrain
    _s('Grass', "#5E6E38", roughness=0.95, texture_set='Grass', tile=8.0, wear_channel='B', tags=('terrain',)),
    _s('Dirt', "#7C6B52", roughness=0.95, texture_set='Dirt', tile=4.0, tags=('terrain',)),
    _s('EarthBerm', "#6F6A4E", roughness=0.95, texture_set='Grass', tile=6.0, tags=('terrain',)),
    # ---- paint (masked by wear alpha)
    _s('PaintWhite', C.PAINT_WHITE, roughness=0.6, texture_set='PaintWear', tile=2.0, alpha='MASKED', wear_channel='A', tags=('paint',)),
    _s('PaintYellow', C.PAINT_YELLOW, roughness=0.62, texture_set='PaintWear', tile=2.0, alpha='MASKED', wear_channel='A', tags=('paint',)),
    _s('PaintRed', C.PAINT_RED, roughness=0.65, texture_set='PaintWear', tile=2.0, alpha='MASKED', wear_channel='A', tags=('paint',)),
    _s('PaintBlack', C.PAINT_BLACK, roughness=0.75, texture_set='PaintWear', tile=2.0, alpha='MASKED', wear_channel='A', tags=('paint',)),
    _s('PaintOrange', C.PAINT_ORANGE, roughness=0.65, texture_set='PaintWear', tile=2.0, alpha='MASKED', wear_channel='A', tags=('paint',)),
    _s('PaintGreen', C.PAINT_GREEN, roughness=0.7, texture_set='PaintWear', tile=2.0, alpha='MASKED', wear_channel='A', tags=('paint',)),
    _s('RoadPaintWhite', C.ROAD_WHITE, roughness=0.65, texture_set='PaintWear', tile=2.0, alpha='MASKED', wear_channel='A', tags=('paint',)),
    _s('RoadPaintYellow', C.ROAD_YELLOW, roughness=0.65, texture_set='PaintWear', tile=2.0, alpha='MASKED', wear_channel='A', tags=('paint',)),
    # ---- fixtures / props (flat PBR)
    _s('FixtureYellow', C.FIXTURE_YELLOW, roughness=0.45, metallic=0.0, tags=('prop',)),
    _s('FixtureAluminum', C.FIXTURE_ALUMINUM, roughness=0.4, metallic=0.9, tags=('prop',)),
    _s('FixtureCast', C.FIXTURE_CAST, roughness=0.55, metallic=0.8, tags=('prop',)),
    _s('Galvanized', C.GALVANIZED, roughness=0.5, metallic=0.85, tags=('prop',)),
    _s('ConcreteBase', C.CONCRETE_BASE, roughness=0.9, texture_set='ConcreteWall', tile=2.0, tags=('prop',)),
    _s('RubberBlack', "#1E1E1E", roughness=0.85, tags=('prop',)),
    _s('SteelCable', "#5A5D60", roughness=0.45, metallic=0.9, tags=('prop',)),
    _s('MetalDark', "#3A3D40", roughness=0.5, metallic=0.8, tags=('prop',)),
    _s("MetalPainted", C.NATO_GREY, roughness=0.55, metallic=0.1, texture_set='MetalPainted', tile=2.0, tags=('prop',)),
    _s('Glass', "#8FB4C8", roughness=0.05, metallic=0.0, specular=0.8, tags=('prop',)),
    _s('WindConeOrange', C.WIND_CONE_ORANGE, roughness=0.7, two_sided=True, tags=('prop',)),
    _s('ObstructionOrange', C.OBSTRUCTION_ORANGE, roughness=0.5, tags=('prop',)),
    _s('ObstructionWhite', C.OBSTRUCTION_WHITE, roughness=0.5, tags=('prop',)),
    _s('Bollard', "#F0B400", roughness=0.5, tags=('prop',)),
    # ---- lights (emissive lenses)
    _s('LightWhite', "#FFFFFF", roughness=0.2, emission=C.LIGHT_WHITE, emission_strength=1.0, tags=('light',)),
    _s('LightRed', "#FF4020", roughness=0.2, emission=C.LIGHT_RED, emission_strength=1.0, tags=('light',)),
    _s('LightGreen', "#40FFA0", roughness=0.2, emission=C.LIGHT_GREEN, emission_strength=1.0, tags=('light',)),
    _s('LightBlue', "#4070FF", roughness=0.2, emission=C.LIGHT_BLUE, emission_strength=1.0, tags=('light',)),
    _s('LightYellow', "#FFC040", roughness=0.2, emission=C.LIGHT_YELLOW, emission_strength=1.0, tags=('light',)),
    _s('LightFlasher', "#E8F0FF", roughness=0.2, emission=C.LIGHT_FLASHER, emission_strength=1.5, tags=('light',)),
    _s('LightFlood', "#FFF6E0", roughness=0.2, emission=C.LIGHT_FLOOD, emission_strength=2.0, tags=('light',)),
    # ---- signs
    _s('SignRed', C.SIGN_RED, roughness=0.4, emission=C.SIGN_RED, emission_strength=0.3, tags=('sign',)),
    _s('SignYellow', C.SIGN_YELLOW, roughness=0.4, emission=C.SIGN_YELLOW, emission_strength=0.3, tags=('sign',)),
    _s('SignBlack', C.SIGN_BLACK, roughness=0.45, tags=('sign',)),
    _s('SignWhite', C.SIGN_WHITE, roughness=0.4, emission=C.SIGN_WHITE, emission_strength=0.3, tags=('sign',)),
    _s('AGMYellow', C.AGM_YELLOW, roughness=0.4, emission=C.AGM_YELLOW, emission_strength=0.3, tags=('sign',)),
    _s('SignFrame', "#4A4A4A", roughness=0.5, metallic=0.6, tags=('sign',)),
    # ---- structures
    _s('ConcreteWall', "#A8A6A0", roughness=0.9, texture_set='ConcreteWall', tile=3.0, tags=('structure',)),
    _s('ConcreteHAS', "#8E8F88", roughness=0.92, texture_set='ConcreteWall', tile=4.0, tags=('structure',)),
    _s('CamoGreen', C.NATO_GREEN, roughness=0.85, texture_set='ConcreteWall', tile=4.0, tags=('structure',)),
    _s('CamoGrey', C.NATO_GREY, roughness=0.85, texture_set='ConcreteWall', tile=4.0, tags=('structure',)),
    _s('CamoTan', C.NATO_TAN, roughness=0.85, texture_set='ConcreteWall', tile=4.0, tags=('structure',)),
    _s('CorrugatedSteel', "#8C9094", roughness=0.55, metallic=0.6, texture_set='CorrugatedSteel', tile=2.0, tags=('structure',)),
    _s('RoofMembrane', "#6E6E6A", roughness=0.8, texture_set='Roof', tile=3.0, tags=('structure',)),
    _s('DoorRed', C.DOOR_RED, roughness=0.5, tags=('structure',)),
    _s('BlastDoor', "#5E6367", roughness=0.6, metallic=0.5, tags=('structure',)),
    _s('BlastDeflector', "#6B6F72", roughness=0.6, metallic=0.5, tags=('structure',)),
    _s('TankWhite', "#DADAD6", roughness=0.45, metallic=0.2, tags=('structure',)),
    _s('ChainLink', "#8A8E92", roughness=0.5, metallic=0.7, texture_set='ChainLink', tile=1.0, alpha='MASKED', two_sided=True, tags=('structure',)),
    _s('BarbedWire', "#7C8084", roughness=0.5, metallic=0.8, tags=('structure',)),
    _s('Trim', "#DCDCD8", roughness=0.6, tags=('structure',)),
]}

# texture map naming used by textures.synth / textures.io / materials.nodes
MAP_BASECOLOR = "BaseColor"
MAP_NORMAL = "Normal"
MAP_ROUGHNESS = "Roughness"
MAP_AO = "AO"
MAP_HEIGHT = "Height"
MAP_METALLIC = "Metallic"
MAP_OPACITY = "Opacity"
MAP_ORM = "ORM"              # Unreal: R=AO G=Roughness B=Metallic
MAP_MASK = "MaskMap"         # Unity HDRP: R=Metallic G=AO B=Detail A=Smoothness
MAP_METALSMOOTH = "MetallicSmoothness"   # Unity URP: RGB=Metallic A=Smoothness
MAP_METALROUGH = "MetallicRoughness"     # glTF: G=Roughness B=Metallic


def texture_filename(texture_set: str, map_name: str, ext: str = "png") -> str:
    return f"{C.TEX_PREFIX}{texture_set}_{map_name}.{ext}"


def material_name(spec_name: str) -> str:
    return C.MAT_PREFIX + spec_name


# --------------------------------------------------------------------------- cache / access
def get_material(name: str, settings=None, rebuild: bool = False) -> bpy.types.Material:
    """Return (building if needed) the Blender material for a spec name."""
    spec = SPECS[name]
    mname = material_name(name)
    mat = bpy.data.materials.get(mname)
    if mat is not None and not rebuild:
        return mat
    from . import nodes
    return nodes.build_material(spec, settings, existing=mat)


def rebuild_all(settings=None, names=None) -> None:
    for n in (names or SPECS.keys()):
        if bpy.data.materials.get(material_name(n)) is not None or names:
            get_material(n, settings, rebuild=True)


class MaterialGetter:
    """Callable handed to builders: ``mats('RunwayConcrete')`` -> Material (cached per build)."""

    def __init__(self, settings=None):
        self.settings = settings
        self._cache: dict[str, bpy.types.Material] = {}

    def __call__(self, name: str) -> bpy.types.Material:
        m = self._cache.get(name)
        if m is None:
            m = get_material(name, self.settings)
            self._cache[name] = m
        return m

    def index_list(self, names: list[str]) -> list[bpy.types.Material]:
        return [self(n) for n in names]
