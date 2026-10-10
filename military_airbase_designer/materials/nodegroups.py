"""Reusable shader node groups shared by every generated material.

Two groups keep the per-material trees small and give users / exporters a single
place to tweak the weathering logic:

``MAD_WearBlend``
    Vertex-colour driven pavement weathering (research report 05 §B16, meshbuild
    ``Wear`` attribute convention: R rubber, G edge dirt, B macro tone, A unused/1).
    Inputs  : BaseColor, Roughness, Wear (RGB), Wear Alpha, Rubber, Dirt, Macro, Wet
    Outputs : BaseColor, Roughness
    * R * Rubber   -> base colour mixed toward rubber black ``#1A1A1A``; roughness -0.15 * R
      (rubber is glossier than concrete, AC 150/5320-12C: friction loss = polished deposit)
    * G * Dirt*0.6 -> base colour mixed toward dusty tan ``#8C7E66``; roughness +0.12 * G
    * (B - 0.5)    -> +/- 8 % brightness (macro variation that breaks texture tiling)
    * Wet          -> albedo * (1 - 0.15 Wet), roughness mixed toward 0.15
    ``Wear Alpha`` is the *presence* flag: Blender's Color Attribute node returns 0 for a
    missing attribute (verified on 4.2 and 5.0), so meshes without ``Wear`` get a neutral
    macro value (0.5) instead of a -8 % darkening.

``MAD_PaintMask``
    Erosion of painted markings (UFC 3-260-04 paint is 0.4 mm and chips / is rubbed off by
    tyres; the ``PaintWear`` texture set carries a per-texel mask).
    Inputs  : Wear Alpha (vertex A, 1 = intact paint, 0 = fully exposed), Mask R (chip mask),
              Mask G (dirt), Mask B (glass-bead sparkle), Paint Wear (settings slider),
              Dirt (settings slider), Roughness (paint base roughness)
    Outputs : Alpha, Roughness, Color Multiplier
    erosion  = (1 - A) * lerp(0.35, 1.0, Paint Wear)
    Alpha    = smoothstep(erosion - 0.06, erosion + 0.06, Mask R)   (hard chipped edges)
    Colour   = mix(white, dusty tan, Mask G * Dirt)
    Roughness= base + 0.2 * dirt - 0.3 * Mask B (beads are glossy: AC 150/5370-10H P-620)

Groups are built with the 4.x ``NodeTree.interface`` API, cached by name in
``bpy.data.node_groups`` (fake user) and rebuilt when missing or when their stored
``mad_version`` differs from ``GROUP_VERSION``.
"""
from __future__ import annotations

import bpy

from ..core import constants as C

GROUP_VERSION = 4
WEARBLEND = "MAD_WearBlend"
PAINTMASK = "MAD_PaintMask"

RUBBER_COLOR = C.hex_to_linear("#1A1A1A")
DIRT_COLOR = C.hex_to_linear("#8C7E66")
PAINT_DIRT_MULT = (0.62, 0.56, 0.47, 1.0)   # multiplier that turns white paint dusty tan

__all__ = ["GROUP_VERSION", "WEARBLEND", "PAINTMASK", "wear_blend_group", "paint_mask_group",
           "ensure_all", "rebuild_all_groups", "mix_socket", "mix_result"]


# --------------------------------------------------------------------------- socket helpers (ShaderNodeMix shares socket names)
def mix_socket(node: bpy.types.Node, identifier: str) -> bpy.types.NodeSocket:
    """Input socket of a ShaderNodeMix by identifier (``Factor_Float``, ``A_Color``...)."""
    for s in node.inputs:
        if s.identifier == identifier:
            return s
    raise KeyError(identifier)


def mix_result(node: bpy.types.Node, identifier: str) -> bpy.types.NodeSocket:
    for s in node.outputs:
        if s.identifier == identifier:
            return s
    raise KeyError(identifier)


def _math(nt, op: str, loc, a=None, b=None, c=None, clamp: bool = False, name: str = "") -> bpy.types.Node:
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    n.use_clamp = clamp
    n.location = loc
    if name:
        n.name = n.label = name
    for i, v in enumerate((a, b, c)):
        if v is not None:
            n.inputs[i].default_value = v
    return n


def _mix(nt, data_type: str, loc, blend: str = 'MIX', factor=None, a=None, b=None, name: str = "") -> bpy.types.Node:
    n = nt.nodes.new("ShaderNodeMix")
    n.data_type = data_type
    if data_type == 'RGBA':
        n.blend_type = blend
    n.clamp_factor = True
    n.location = loc
    if name:
        n.name = n.label = name
    if factor is not None:
        mix_socket(n, "Factor_Float").default_value = factor
    suffix = "Color" if data_type == 'RGBA' else "Float"
    if a is not None:
        mix_socket(n, f"A_{suffix}").default_value = a
    if b is not None:
        mix_socket(n, f"B_{suffix}").default_value = b
    return n


def _socket(ng, name: str, in_out: str, socket_type: str, default=None, lo=None, hi=None, description: str = ""):
    s = ng.interface.new_socket(name=name, in_out=in_out, socket_type=socket_type, description=description)
    if default is not None:
        try:
            s.default_value = default
        except (AttributeError, TypeError):
            pass
    if lo is not None and hasattr(s, "min_value"):
        s.min_value = lo
    if hi is not None and hasattr(s, "max_value"):
        s.max_value = hi
    return s


def _fresh_group(name: str) -> bpy.types.ShaderNodeTree:
    ng = bpy.data.node_groups.get(name)
    if ng is not None and ng.bl_idname != 'ShaderNodeTree':
        ng.name = name + ".old"
        ng = None
    if ng is None:
        ng = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    else:
        ng.nodes.clear()
        # remove every interface item (sockets and panels) so the layout below is exact
        for item in list(ng.interface.items_tree):
            try:
                ng.interface.remove(item)
            except (RuntimeError, ReferenceError):
                pass
    ng.use_fake_user = True
    return ng


def _is_current(ng: bpy.types.NodeTree | None, inputs: tuple[str, ...], outputs: tuple[str, ...]) -> bool:
    if ng is None or ng.get("mad_version") != GROUP_VERSION:
        return False
    have_in = {i.name for i in ng.interface.items_tree if i.item_type == 'SOCKET' and i.in_out == 'INPUT'}
    have_out = {i.name for i in ng.interface.items_tree if i.item_type == 'SOCKET' and i.in_out == 'OUTPUT'}
    return set(inputs) <= have_in and set(outputs) <= have_out and len(ng.nodes) > 2


# --------------------------------------------------------------------------- MAD_WearBlend
WEAR_INPUTS = ("BaseColor", "Roughness", "Wear", "Wear Alpha", "Rubber", "Dirt", "Macro", "Wet")
WEAR_OUTPUTS = ("BaseColor", "Roughness")


def wear_blend_group(force: bool = False) -> bpy.types.ShaderNodeTree:
    ng = bpy.data.node_groups.get(WEARBLEND)
    if not force and _is_current(ng, WEAR_INPUTS, WEAR_OUTPUTS):
        return ng
    ng = _fresh_group(WEARBLEND)
    _socket(ng, "BaseColor", 'INPUT', 'NodeSocketColor', (0.8, 0.8, 0.8, 1.0), description="Albedo before weathering")
    _socket(ng, "Roughness", 'INPUT', 'NodeSocketFloat', 0.85, 0.0, 1.0)
    _socket(ng, "Wear", 'INPUT', 'NodeSocketColor', (0.0, 0.0, 0.5, 1.0), description="Vertex colour 'Wear': R rubber, G dirt, B macro")
    _socket(ng, "Wear Alpha", 'INPUT', 'NodeSocketFloat', 0.0, 0.0, 1.0, description="Alpha of the Wear attribute (0 when the mesh has none)")
    _socket(ng, "Rubber", 'INPUT', 'NodeSocketFloat', 0.7, 0.0, 1.0, description="Rubber deposit amount (settings)")
    _socket(ng, "Dirt", 'INPUT', 'NodeSocketFloat', 0.5, 0.0, 1.0, description="Edge dirt amount (settings)")
    _socket(ng, "Macro", 'INPUT', 'NodeSocketFloat', 1.0, 0.0, 2.0, description="Strength of the +/-8 % macro brightness variation")
    _socket(ng, "Wet", 'INPUT', 'NodeSocketFloat', 0.0, 0.0, 1.0, description="Wetness: darker and glossier")
    _socket(ng, "BaseColor", 'OUTPUT', 'NodeSocketColor')
    _socket(ng, "Roughness", 'OUTPUT', 'NodeSocketFloat')

    nodes, links = ng.nodes, ng.links
    gi = nodes.new("NodeGroupInput"); gi.location = (-900, 0)
    go = nodes.new("NodeGroupOutput"); go.location = (900, 0)

    sep = nodes.new("ShaderNodeSeparateColor"); sep.mode = 'RGB'; sep.location = (-650, -150); sep.name = sep.label = "SeparateWear"
    links.new(gi.outputs["Wear"], sep.inputs["Color"])

    # rubber factor r = R * Rubber
    r = _math(ng, 'MULTIPLY', (-400, -100), clamp=True, name="RubberFactor")
    links.new(sep.outputs["Red"], r.inputs[0]); links.new(gi.outputs["Rubber"], r.inputs[1])
    # dirt factor g = G * Dirt * 0.6
    g1 = _math(ng, 'MULTIPLY', (-400, -280), name="DirtFactor")
    links.new(sep.outputs["Green"], g1.inputs[0]); links.new(gi.outputs["Dirt"], g1.inputs[1])
    g = _math(ng, 'MULTIPLY', (-200, -280), b=0.6, clamp=True, name="DirtScale")
    links.new(g1.outputs[0], g.inputs[0])
    # macro: b_eff = mix(0.5, B, presence); f = 1 + (b_eff - 0.5) * 0.16 * Macro
    bsel = _mix(ng, 'FLOAT', (-400, -460), a=0.5, name="MacroPresence")
    links.new(gi.outputs["Wear Alpha"], mix_socket(bsel, "Factor_Float"))
    links.new(sep.outputs["Blue"], mix_socket(bsel, "B_Float"))
    msub = _math(ng, 'SUBTRACT', (-200, -460), b=0.5, name="MacroCentre")
    links.new(mix_result(bsel, "Result_Float"), msub.inputs[0])
    mstr = _math(ng, 'MULTIPLY', (-200, -620), b=0.16, name="MacroStrength")
    links.new(gi.outputs["Macro"], mstr.inputs[0])
    mfac = _math(ng, 'MULTIPLY_ADD', (0, -460), c=1.0, name="MacroFactor")
    links.new(msub.outputs[0], mfac.inputs[0]); links.new(mstr.outputs[0], mfac.inputs[1])

    # base colour chain: macro scale -> rubber mix -> dirt mix -> wet darkening
    sc1 = nodes.new("ShaderNodeVectorMath"); sc1.operation = 'SCALE'; sc1.location = (0, 150); sc1.name = sc1.label = "MacroScale"
    links.new(gi.outputs["BaseColor"], sc1.inputs[0]); links.new(mfac.outputs[0], sc1.inputs["Scale"])
    mixr = _mix(ng, 'RGBA', (200, 150), 'MIX', b=RUBBER_COLOR, name="RubberMix")
    links.new(r.outputs[0], mix_socket(mixr, "Factor_Float")); links.new(sc1.outputs[0], mix_socket(mixr, "A_Color"))
    mixg = _mix(ng, 'RGBA', (400, 150), 'MIX', b=DIRT_COLOR, name="DirtMix")
    links.new(g.outputs[0], mix_socket(mixg, "Factor_Float")); links.new(mix_result(mixr, "Result_Color"), mix_socket(mixg, "A_Color"))
    wetf = _math(ng, 'MULTIPLY_ADD', (400, -50), b=-0.15, c=1.0, name="WetDarken")
    links.new(gi.outputs["Wet"], wetf.inputs[0])
    sc2 = nodes.new("ShaderNodeVectorMath"); sc2.operation = 'SCALE'; sc2.location = (600, 150); sc2.name = sc2.label = "WetScale"
    links.new(mix_result(mixg, "Result_Color"), sc2.inputs[0]); links.new(wetf.outputs[0], sc2.inputs["Scale"])
    links.new(sc2.outputs[0], go.inputs["BaseColor"])

    # roughness chain: -0.15 r, +0.12 g, wet -> 0.15, clamp
    ro1 = _math(ng, 'MULTIPLY_ADD', (200, -250), b=-0.15, name="RubberRough")
    links.new(r.outputs[0], ro1.inputs[0]); links.new(gi.outputs["Roughness"], ro1.inputs[2])
    ro2 = _math(ng, 'MULTIPLY_ADD', (400, -250), b=0.12, name="DirtRough")
    links.new(g.outputs[0], ro2.inputs[0]); links.new(ro1.outputs[0], ro2.inputs[2])
    row = _mix(ng, 'FLOAT', (600, -250), b=0.15, name="WetRough")
    links.new(gi.outputs["Wet"], mix_socket(row, "Factor_Float")); links.new(ro2.outputs[0], mix_socket(row, "A_Float"))
    roc = _math(ng, 'MAXIMUM', (750, -250), b=0.03, clamp=True, name="RoughClamp")
    links.new(mix_result(row, "Result_Float"), roc.inputs[0])
    links.new(roc.outputs[0], go.inputs["Roughness"])

    ng["mad_version"] = GROUP_VERSION
    return ng


# --------------------------------------------------------------------------- MAD_PaintMask
PAINT_INPUTS = ("Wear Alpha", "Mask R", "Mask G", "Mask B", "Paint Wear", "Dirt", "Roughness")
PAINT_OUTPUTS = ("Alpha", "Roughness", "Color Multiplier")
PAINT_EDGE = 0.06     # half width of the smoothstep that turns the chip mask into hard paint edges


def paint_mask_group(force: bool = False) -> bpy.types.ShaderNodeTree:
    ng = bpy.data.node_groups.get(PAINTMASK)
    if not force and _is_current(ng, PAINT_INPUTS, PAINT_OUTPUTS):
        return ng
    ng = _fresh_group(PAINTMASK)
    _socket(ng, "Wear Alpha", 'INPUT', 'NodeSocketFloat', 1.0, 0.0, 1.0, description="Vertex 'Wear' alpha: 1 intact paint, 0 fully exposed")
    _socket(ng, "Mask R", 'INPUT', 'NodeSocketFloat', 1.0, 0.0, 1.0, description="PaintWear chip mask (high = paint stays)")
    _socket(ng, "Mask G", 'INPUT', 'NodeSocketFloat', 0.0, 0.0, 1.0, description="PaintWear dirt")
    _socket(ng, "Mask B", 'INPUT', 'NodeSocketFloat', 0.0, 0.0, 1.0, description="PaintWear glass-bead sparkle")
    _socket(ng, "Paint Wear", 'INPUT', 'NodeSocketFloat', 0.35, 0.0, 1.0, description="settings.markings.paint_wear")
    _socket(ng, "Dirt", 'INPUT', 'NodeSocketFloat', 0.5, 0.0, 1.0, description="settings.materials.dirt")
    _socket(ng, "Roughness", 'INPUT', 'NodeSocketFloat', 0.6, 0.0, 1.0, description="Fresh paint roughness")
    _socket(ng, "Alpha", 'OUTPUT', 'NodeSocketFloat')
    _socket(ng, "Roughness", 'OUTPUT', 'NodeSocketFloat')
    _socket(ng, "Color Multiplier", 'OUTPUT', 'NodeSocketColor')

    nodes, links = ng.nodes, ng.links
    gi = nodes.new("NodeGroupInput"); gi.location = (-800, 0)
    go = nodes.new("NodeGroupOutput"); go.location = (700, 0)

    inv = _math(ng, 'SUBTRACT', (-500, 200), a=1.0, name="Exposure")          # 1 - A
    links.new(gi.outputs["Wear Alpha"], inv.inputs[1])
    scale = _math(ng, 'MULTIPLY_ADD', (-500, 40), b=0.65, c=0.35, name="WearScale")   # lerp(0.35, 1, paint_wear)
    links.new(gi.outputs["Paint Wear"], scale.inputs[0])
    ero = _math(ng, 'MULTIPLY', (-300, 120), clamp=True, name="Erosion")
    links.new(inv.outputs[0], ero.inputs[0]); links.new(scale.outputs[0], ero.inputs[1])
    lo = _math(ng, 'SUBTRACT', (-100, 200), b=PAINT_EDGE, name="EdgeLow")
    hi = _math(ng, 'ADD', (-100, 40), b=PAINT_EDGE, name="EdgeHigh")
    links.new(ero.outputs[0], lo.inputs[0]); links.new(ero.outputs[0], hi.inputs[0])
    mr = nodes.new("ShaderNodeMapRange"); mr.data_type = 'FLOAT'; mr.interpolation_type = 'SMOOTHSTEP'; mr.clamp = True
    mr.location = (150, 150); mr.name = mr.label = "ChipAlpha"
    links.new(gi.outputs["Mask R"], mr.inputs["Value"])
    links.new(lo.outputs[0], mr.inputs["From Min"]); links.new(hi.outputs[0], mr.inputs["From Max"])
    mr.inputs["To Min"].default_value = 0.0; mr.inputs["To Max"].default_value = 1.0
    links.new(mr.outputs["Result"], go.inputs["Alpha"])

    d = _math(ng, 'MULTIPLY', (-300, -150), clamp=True, name="DirtFactor")
    links.new(gi.outputs["Mask G"], d.inputs[0]); links.new(gi.outputs["Dirt"], d.inputs[1])
    cm = _mix(ng, 'RGBA', (150, -150), 'MIX', a=(1.0, 1.0, 1.0, 1.0), b=PAINT_DIRT_MULT, name="DirtTint")
    links.new(d.outputs[0], mix_socket(cm, "Factor_Float"))
    links.new(mix_result(cm, "Result_Color"), go.inputs["Color Multiplier"])

    r1 = _math(ng, 'MULTIPLY_ADD', (150, -350), b=0.2, name="DirtRough")
    links.new(d.outputs[0], r1.inputs[0]); links.new(gi.outputs["Roughness"], r1.inputs[2])
    r2 = _math(ng, 'MULTIPLY_ADD', (400, -350), b=-0.3, clamp=True, name="BeadRough")
    links.new(gi.outputs["Mask B"], r2.inputs[0]); links.new(r1.outputs[0], r2.inputs[2])
    links.new(r2.outputs[0], go.inputs["Roughness"])

    ng["mad_version"] = GROUP_VERSION
    return ng


# --------------------------------------------------------------------------- housekeeping
def ensure_all() -> dict[str, bpy.types.ShaderNodeTree]:
    return {WEARBLEND: wear_blend_group(), PAINTMASK: paint_mask_group()}


def rebuild_all_groups() -> None:
    """Force-rebuild both groups in place (existing group nodes keep pointing at them)."""
    wear_blend_group(force=True)
    paint_mask_group(force=True)


def register():
    pass


def unregister():
    pass
