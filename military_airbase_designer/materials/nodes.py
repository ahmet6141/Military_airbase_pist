"""Node-tree construction for ``materials.library.MaterialSpec``.

Contract (used by ``library.get_material`` / ``library.rebuild_all``):
    ``build_material(spec, settings=None, existing=None) -> bpy.types.Material``
    named ``library.material_name(spec.name)``; it never raises because of missing
    textures (it falls back to a flat-colour tree) and it refills an *existing*
    datablock in place so meshes keep their material slots.

Tree families
-------------
* **Textured** (``spec.texture_set`` and the key map exists on disk): Image Texture
  nodes fed by the mesh ``UVMap`` through a neutral Mapping node (tiling is baked into
  the UVs by ``core.meshbuild.planar_uv``, so the Mapping node only offers per-material
  offset / rotation tweaks).  BaseColor (sRGB) x tint x AO (0.6) -> ``MAD_WearBlend``
  (vertex colour ``Wear``) -> Principled; Normal (Non-Color, optional DirectX green flip)
  -> Normal Map -> optional low Bump from Height; Roughness (Non-Color) -> WearBlend;
  Opacity -> Alpha for MASKED sets.
* **Paint** (tag ``paint``): flat ``spec.color`` modulated by ``MAD_PaintMask`` (PaintWear
  Opacity mask: R chip, G dirt, B bead sparkle) and greyed by rubber through WearBlend;
  Alpha from PaintMask (dithered / hashed) so worn paint reveals the pavement below.
* **Flat PBR** (no texture set or textures missing): Principled + cheap Noise-driven
  roughness (+/-0.05) and, on pavement / terrain, brightness variation; pavement and
  terrain still go through WearBlend so rubber / dirt vertex masks show without textures.
* **Emissive** (tags ``light`` / ``sign``): emission colour from the spec, strength from
  ``settings.lighting`` only in night mode (UFC 3-535-01 fixtures are off by day).

Node names are stable (``MAD_UV``, ``MAD_Mapping``, ``MAD_BaseColor``, ``MAD_Normal``,
``MAD_Roughness``, ``MAD_AO``, ``MAD_Height``, ``MAD_Opacity``, ``MAD_Metallic``,
``MAD_Tint``, ``MAD_AOMix``, ``MAD_NormalMap``, ``MAD_Bump``, ``MAD_Wear``, ``MAD_WearBlend``,
``MAD_PaintMask``, ``MAD_BSDF``, ``MAD_Output``) so ``ops.export`` and users can find them.
Custom properties: ``mat["mad_spec"]``, ``mat["mad_texture_set"]``, ``mat["mad_maps"]``
(``;``-joined map names found on disk), ``mat["mad_textured"]``.

Texture files: ``textures.io.texture_dir(settings)`` + ``textures.io.map_path(dir, set, map)``
(``T_MAD_<Set>_<Map>.png``).  When the key map is missing and
``settings.materials.generate_textures`` is on, ``textures.synth.generate_set`` is tried
once per set (guarded: the synthesiser may be absent), then the flat path is used and a
``MAD_MissingTexture`` frame documents it.  ``refresh_textures(settings)`` reloads all
images of the texture folder and rebuilds materials whose file set changed.

Blender compatibility: Principled 4.x socket names; EEVEE Next ``surface_render_method``
(``blend_method`` / ``shadow_method`` only when the attribute exists); ``Material.use_nodes``
is only touched when ``node_tree`` is None (deprecated in 5.0 where new materials already
carry a tree); Bump ``Distance`` is set explicitly (default changed in 4.5).
"""
from __future__ import annotations

import math
import os
import time
import traceback
from typing import Any

import bpy

from ..core import constants as C
from . import library
from . import nodegroups as NG

__all__ = ["build_material", "refresh_textures", "texture_maps", "material_signature", "AO_FACTOR",
           "SIGN_EMISSION_SCALE", "NODE_VERSION", "MAP_ORDER", "MISSING_FRAME"]

NODE_VERSION = 4
AO_FACTOR = 0.6                 # how much of the baked AO map is multiplied into the albedo (engines use the packed map)
BUMP_STRENGTH = 0.12            # Height -> Bump on top of the normal map (low: the normal already encodes the same relief)
BUMP_DISTANCE = 0.01            # m of relief for a full 0..1 Height (16-bit map, 6 mm joints / grooves)
SIGN_EMISSION_SCALE = 0.15      # internally lit sign faces are far dimmer than fixtures: 25 * 0.3 * 0.15 = ~1.1
FLAT_ROUGH_VARIATION = 0.05     # +/- roughness noise on flat props
FLAT_COLOR_VARIATION = 0.08     # +/- brightness noise on flat pavement / terrain
MISSING_FRAME = "MAD_MissingTexture"
PAINT_MASK_MAP = library.MAP_OPACITY    # PaintWear map carrying the chip / dirt / bead channels (textures.synth may override)

MAP_ORDER = (library.MAP_BASECOLOR, library.MAP_NORMAL, library.MAP_ROUGHNESS, library.MAP_AO, library.MAP_HEIGHT,
             library.MAP_METALLIC, library.MAP_OPACITY)

# columns of the node layout (x) - frames: Textures | Wear | Output
_X_UV, _X_TEX, _X_PROC, _X_GROUP, _X_BSDF, _X_OUT = -1500, -1100, -650, -250, 150, 500

_GEN_ATTEMPTED: set[str] = set()    # texture sets whose synthesis already failed this session (avoid retrying per material)


# --------------------------------------------------------------------------- settings access (settings may be None / a fake)
def _g(settings: Any, path: str, default):
    ob = settings
    for part in path.split("."):
        if ob is None:
            return default
        ob = getattr(ob, part, None)
    return default if ob is None else ob


def _night(settings) -> bool:
    return bool(_g(settings, "lighting.night_mode", False))


def _emission_strength(spec: library.MaterialSpec, settings) -> float:
    if not spec.emission or not _night(settings):
        return 0.0
    base = float(_g(settings, "lighting.emission_strength", 25.0)) * spec.emission_strength
    if 'sign' in spec.tags:
        return base * SIGN_EMISSION_SCALE
    return base


def _tint(spec: library.MaterialSpec, settings) -> tuple[float, float, float, float]:
    """spec.color relative to its own brightness, times the user concrete / asphalt tint (linear)."""
    r, g, b, _ = C.hex_to_linear(spec.color)
    mean = max((r + g + b) / 3.0, 1e-4)
    t = [r / mean, g / mean, b / mean]
    user = None
    if 'concrete' in spec.tags:
        user = _g(settings, "materials.concrete_tint", None)
    elif 'asphalt' in spec.tags:
        user = _g(settings, "materials.asphalt_tint", None)
    if user is not None:
        try:
            t = [t[i] * float(user[i]) for i in range(3)]
        except (TypeError, IndexError, ValueError):
            pass
    return (t[0], t[1], t[2], 1.0)


def _base_color(spec: library.MaterialSpec, settings) -> tuple[float, float, float, float]:
    col = list(C.hex_to_linear(spec.color))
    user = None
    if 'concrete' in spec.tags:
        user = _g(settings, "materials.concrete_tint", None)
    elif 'asphalt' in spec.tags:
        user = _g(settings, "materials.asphalt_tint", None)
    if user is not None:
        try:
            for i in range(3):
                col[i] *= float(user[i])
        except (TypeError, IndexError, ValueError):
            pass
    return (col[0], col[1], col[2], 1.0)


def _is_pavement_like(spec: library.MaterialSpec) -> bool:
    return bool({'pavement', 'terrain'} & set(spec.tags))


# --------------------------------------------------------------------------- texture discovery
def _io():
    from ..textures import io
    return io


def texture_maps(texture_set: str | None, settings=None) -> tuple[str, dict[str, str]]:
    """(texture folder, {map name: absolute path}) for the files of a set that exist on disk."""
    if not texture_set:
        return "", {}
    io = _io()
    try:
        directory = io.texture_dir(settings, create=False)
    except Exception:  # noqa: BLE001 - never fail a build over a path problem
        return "", {}
    maps = {}
    for m in MAP_ORDER:
        p = io.map_path(directory, texture_set, m)
        if os.path.isfile(p):
            maps[m] = p
    return directory, maps


def _key_map(spec: library.MaterialSpec) -> str:
    return library.MAP_OPACITY if spec.alpha == 'MASKED' else library.MAP_BASECOLOR


def _try_generate(texture_set: str, settings, directory: str) -> bool:
    """Synthesize a missing set through textures.synth (optional module); True when something was written."""
    if texture_set in _GEN_ATTEMPTED or settings is None or not _g(settings, "materials.generate_textures", False):
        return False
    _GEN_ATTEMPTED.add(texture_set)
    try:
        from ..textures import synth
    except Exception:  # noqa: BLE001
        return False
    gen = getattr(synth, "generate_set", None)
    if gen is None:
        return False
    sets = getattr(synth, "TEXTURE_SETS", None)
    if isinstance(sets, dict) and texture_set not in sets:
        return False
    try:
        if directory:
            os.makedirs(directory, exist_ok=True)
        res_fn = getattr(synth, "preview_resolution", None)
        kwargs = {}
        if callable(res_fn):
            try:
                kwargs["resolution"] = int(res_fn(settings))
            except Exception:  # noqa: BLE001
                pass
        t0 = time.time()
        gen(texture_set, settings, directory, **kwargs)
        print(f"MAD materials: generated texture set {texture_set} in {time.time() - t0:.1f}s")
        return True
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return False


def _resolve_maps(spec: library.MaterialSpec, settings) -> tuple[str, dict[str, str]]:
    directory, maps = texture_maps(spec.texture_set, settings)
    if spec.texture_set and _key_map(spec) not in maps:
        if _try_generate(spec.texture_set, settings, directory):
            directory, maps = texture_maps(spec.texture_set, settings)
    return directory, maps


def _load(path: str, is_data: bool) -> bpy.types.Image | None:
    try:
        return _io().load_image(path, is_data)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return None


def material_signature(spec: library.MaterialSpec, maps: dict[str, str]) -> str:
    return ";".join(sorted(maps.keys()))


# --------------------------------------------------------------------------- node helpers
def _node(nt, idname: str, name: str, x: float, y: float, parent=None, label: str | None = None):
    n = nt.nodes.new(idname)
    n.name = name
    n.label = label if label is not None else name.replace("MAD_", "")
    n.location = (x, y)
    if parent is not None:
        n.parent = parent
    return n


def _frame(nt, name: str, label: str, x: float, y: float):
    f = nt.nodes.new("NodeFrame")
    f.name = name
    f.label = label
    f.location = (x, y)
    f.shrink = True
    return f


def _image_node(nt, name: str, image, x: float, y: float, parent, uv_out, label: str | None = None):
    tex = _node(nt, "ShaderNodeTexImage", name, x, y, parent, label)
    tex.image = image
    tex.interpolation = 'Linear'
    tex.extension = 'REPEAT'
    tex.projection = 'FLAT'
    if uv_out is not None:
        nt.links.new(uv_out, tex.inputs["Vector"])
    return tex


def _ensure_tree(mat: bpy.types.Material):
    if getattr(mat, "node_tree", None) is None:
        mat.use_nodes = True         # 4.x: materials start without a tree; 5.0 creates one (use_nodes deprecated there)
    nt = mat.node_tree
    nt.nodes.clear()
    return nt


def _set_input(node, name: str, value) -> None:
    sock = node.inputs.get(name)
    if sock is not None:
        try:
            sock.default_value = value
        except (TypeError, ValueError):
            pass


def _wear_group(nt, x, y, parent, spec, settings, rubber_only: bool = False):
    grp = _node(nt, "ShaderNodeGroup", "MAD_WearBlend", x, y, parent, "Wear blend")
    grp.node_tree = NG.wear_blend_group()
    vc = _node(nt, "ShaderNodeVertexColor", "MAD_Wear", x - 300, y - 250, parent, "Wear (vertex colour)")
    vc.layer_name = "Wear"
    nt.links.new(vc.outputs["Color"], grp.inputs["Wear"])
    nt.links.new(vc.outputs["Alpha"], grp.inputs["Wear Alpha"])
    rubber = float(_g(settings, "materials.rubber", 0.7))
    dirt = float(_g(settings, "materials.dirt", 0.5))
    wet = float(_g(settings, "materials.wet", 0.0))
    ch = spec.wear_channel
    if rubber_only:
        _set_input(grp, "Rubber", rubber)
        _set_input(grp, "Dirt", 0.0)
        _set_input(grp, "Macro", 0.0)
        _set_input(grp, "Wet", 0.0)
    else:
        # R (rubber) only where the spec asks for it (runway); dirt on every pavement edge; macro on all pavement / terrain
        _set_input(grp, "Rubber", rubber if ch == 'R' else rubber * 0.35)
        _set_input(grp, "Dirt", dirt)
        _set_input(grp, "Macro", 1.0 if _is_pavement_like(spec) else 0.5)
        _set_input(grp, "Wet", wet if _is_pavement_like(spec) else 0.0)
    return grp, vc


def _flat_variation(nt, x, y, parent, base_rough: float, with_color: bool):
    """Object-space Noise -> roughness (+/-0.05) and optional brightness variation for flat materials."""
    coord = _node(nt, "ShaderNodeTexCoord", "MAD_Coord", x - 250, y, parent, "Object coords")
    noise = _node(nt, "ShaderNodeTexNoise", "MAD_Noise", x, y, parent, "Micro variation")
    noise.noise_dimensions = '3D'
    _set_input(noise, "Scale", 40.0 if not with_color else 0.6)
    _set_input(noise, "Detail", 2.0)
    _set_input(noise, "Roughness", 0.5)
    nt.links.new(coord.outputs["Object"], noise.inputs["Vector"])
    mr = _node(nt, "ShaderNodeMapRange", "MAD_RoughVar", x + 250, y, parent, "Roughness variation")
    mr.data_type = 'FLOAT'
    mr.clamp = True
    _set_input(mr, "From Min", 0.3)
    _set_input(mr, "From Max", 0.7)
    _set_input(mr, "To Min", max(0.02, base_rough - FLAT_ROUGH_VARIATION))
    _set_input(mr, "To Max", min(1.0, base_rough + FLAT_ROUGH_VARIATION))
    nt.links.new(noise.outputs["Fac"], mr.inputs["Value"])
    color_fac = None
    if with_color:
        cv = _node(nt, "ShaderNodeMapRange", "MAD_ColorVar", x + 250, y - 250, parent, "Brightness variation")
        cv.data_type = 'FLOAT'
        cv.clamp = True
        _set_input(cv, "From Min", 0.3)
        _set_input(cv, "From Max", 0.7)
        _set_input(cv, "To Min", 1.0 - FLAT_COLOR_VARIATION)
        _set_input(cv, "To Max", 1.0 + FLAT_COLOR_VARIATION)
        nt.links.new(noise.outputs["Fac"], cv.inputs["Value"])
        color_fac = cv.outputs["Result"]
    return mr.outputs["Result"], color_fac


# --------------------------------------------------------------------------- material settings shared by all trees
def _apply_material_settings(mat: bpy.types.Material, spec: library.MaterialSpec, settings, view_color) -> None:
    mat.diffuse_color = view_color
    mat.roughness = spec.roughness
    mat.metallic = spec.metallic
    mat.use_backface_culling = not spec.two_sided
    if hasattr(mat, "use_backface_culling_shadow"):
        try:
            mat.use_backface_culling_shadow = False
        except (AttributeError, TypeError):
            pass
    masked = spec.alpha == 'MASKED'
    blend = spec.alpha == 'BLEND'
    if hasattr(mat, "surface_render_method"):
        try:
            mat.surface_render_method = 'BLENDED' if blend else 'DITHERED'
        except (AttributeError, TypeError):
            pass
    if hasattr(mat, "blend_method"):
        try:
            mat.blend_method = 'BLEND' if blend else ('HASHED' if masked else 'OPAQUE')
        except (AttributeError, TypeError, RuntimeError):
            pass
    if hasattr(mat, "shadow_method"):
        try:
            mat.shadow_method = 'NONE' if blend else ('HASHED' if masked else 'OPAQUE')
        except (AttributeError, TypeError, RuntimeError):
            pass
    if hasattr(mat, "use_transparent_shadow"):
        try:
            mat.use_transparent_shadow = masked or blend
        except (AttributeError, TypeError):
            pass
    if blend:
        for attr in ("show_transparent_back", "use_transparency_overlap"):
            if hasattr(mat, attr):
                try:
                    setattr(mat, attr, False)
                except (AttributeError, TypeError):
                    pass
    if hasattr(mat, "displacement_method"):
        try:
            mat.displacement_method = 'BUMP'
        except (AttributeError, TypeError):
            pass
    mat.pass_index = 0


def _finish(mat, nt, bsdf, out, spec, settings, texture_set: str, maps: dict[str, str], textured: bool) -> None:
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    if not bsdf.inputs["Metallic"].is_linked:
        _set_input(bsdf, "Metallic", spec.metallic)
    if "Specular IOR Level" in bsdf.inputs:
        _set_input(bsdf, "Specular IOR Level", spec.specular)
    strength = _emission_strength(spec, settings)
    if spec.emission:
        _set_input(bsdf, "Emission Color", C.hex_to_linear(spec.emission))
    _set_input(bsdf, "Emission Strength", strength)
    view = C.hex_to_linear(spec.emission) if (spec.emission and strength > 0.0) else _base_color(spec, settings)
    _apply_material_settings(mat, spec, settings, view)
    mat["mad_spec"] = spec.name
    mat["mad_texture_set"] = texture_set or ""
    mat["mad_maps"] = material_signature(spec, maps)
    mat["mad_textured"] = 1 if textured else 0
    mat["mad_node_version"] = NODE_VERSION
    try:
        nt.nodes.active = bsdf
    except (AttributeError, RuntimeError):
        pass


# --------------------------------------------------------------------------- tree builders
def _build_textured(mat, nt, spec, settings, directory: str, maps: dict[str, str]) -> bool:
    """Image-texture PBR tree. Returns False (after clearing) when the key image cannot be loaded."""
    key = _key_map(spec)
    is_paint = 'paint' in spec.tags
    directx = bool(_g(settings, "materials.normal_directx", True))
    f_tex = _frame(nt, "MAD_Frame_Textures", "Textures", _X_TEX, 300)
    f_wear = _frame(nt, "MAD_Frame_Wear", "Wear", _X_PROC, 300)
    f_out = _frame(nt, "MAD_Frame_Output", "Output", _X_BSDF, 300)

    uv = _node(nt, "ShaderNodeUVMap", "MAD_UV", _X_UV, 0, f_tex, "UVMap (world planar, tiling baked in)")
    uv.uv_map = "UVMap"
    mapping = _node(nt, "ShaderNodeMapping", "MAD_Mapping", _X_UV + 200, 0, f_tex, "Offset / rotation tweak")
    mapping.vector_type = 'POINT'
    nt.links.new(uv.outputs["UV"], mapping.inputs["Vector"])
    uv_out = mapping.outputs["Vector"]

    bsdf = _node(nt, "ShaderNodeBsdfPrincipled", "MAD_BSDF", _X_BSDF, 0, f_out, "Principled")
    out = _node(nt, "ShaderNodeOutputMaterial", "MAD_Output", _X_OUT, 0, f_out, "Output")

    images: dict[str, bpy.types.Image] = {}
    for m, p in maps.items():
        img = _load(p, is_data=(m != library.MAP_BASECOLOR))
        if img is not None:
            images[m] = img
    if key not in images:
        nt.nodes.clear()
        return False
    if 'decal' in spec.tags:
        _build_decal(mat, nt, spec, settings, images, maps, uv, bsdf, out, f_tex, f_wear)
        return True

    y = 250
    tex_nodes: dict[str, bpy.types.Node] = {}
    for m in MAP_ORDER:
        if m not in images:
            continue
        if is_paint and m == library.MAP_BASECOLOR:
            continue      # paint colour is the flat FS-595 chip; the PaintWear set only supplies masks
        if m == library.MAP_METALLIC and spec.metallic <= 0.0:
            continue
        tex_nodes[m] = _image_node(nt, f"MAD_{m}", images[m], _X_TEX, y, f_tex, uv_out, m)
        y -= 290

    # ---- base colour -------------------------------------------------------------
    if is_paint:
        paint = _node(nt, "ShaderNodeGroup", "MAD_PaintMask", _X_PROC + 50, 100, f_wear, "Paint mask")
        paint.node_tree = NG.paint_mask_group()
        mask_tex = tex_nodes.get(PAINT_MASK_MAP) or tex_nodes.get(library.MAP_OPACITY)
        sep = _node(nt, "ShaderNodeSeparateColor", "MAD_MaskSplit", _X_PROC - 200, 100, f_wear, "Mask channels")
        sep.mode = 'RGB'
        nt.links.new(mask_tex.outputs["Color"], sep.inputs["Color"])
        nt.links.new(sep.outputs["Red"], paint.inputs["Mask R"])
        nt.links.new(sep.outputs["Green"], paint.inputs["Mask G"])
        nt.links.new(sep.outputs["Blue"], paint.inputs["Mask B"])
        _set_input(paint, "Paint Wear", float(_g(settings, "markings.paint_wear", 0.35)))
        _set_input(paint, "Dirt", float(_g(settings, "materials.dirt", 0.5)))
        _set_input(paint, "Roughness", spec.roughness)
        tint = _node(nt, "ShaderNodeMix", "MAD_Tint", _X_PROC + 50, -200, f_wear, "Paint colour x dirt")
        tint.data_type = 'RGBA'
        tint.blend_type = 'MULTIPLY'
        NG.mix_socket(tint, "Factor_Float").default_value = 1.0
        NG.mix_socket(tint, "A_Color").default_value = C.hex_to_linear(spec.color)
        nt.links.new(paint.outputs["Color Multiplier"], NG.mix_socket(tint, "B_Color"))
        grp, vc = _wear_group(nt, _X_GROUP, 0, f_wear, spec, settings, rubber_only=True)
        nt.links.new(vc.outputs["Alpha"], paint.inputs["Wear Alpha"])
        nt.links.new(NG.mix_result(tint, "Result_Color"), grp.inputs["BaseColor"])
        nt.links.new(paint.outputs["Roughness"], grp.inputs["Roughness"])
        nt.links.new(grp.outputs["BaseColor"], bsdf.inputs["Base Color"])
        nt.links.new(grp.outputs["Roughness"], bsdf.inputs["Roughness"])
        nt.links.new(paint.outputs["Alpha"], bsdf.inputs["Alpha"])
    else:
        base = tex_nodes.get(library.MAP_BASECOLOR)
        color_src = base.outputs["Color"] if base is not None else None
        if base is not None and 'terrain' in spec.tags:
            # anti-tiling for large ground areas: a second, rotated/scaled sample blended by a ~60 m noise
            map2 = _node(nt, "ShaderNodeMapping", "MAD_DetileMapping", _X_UV + 200, -1200, f_tex, "Detile sample (x0.31, 41 deg)")
            map2.vector_type = 'POINT'
            map2.inputs["Rotation"].default_value = (0.0, 0.0, 0.715)
            map2.inputs["Scale"].default_value = (0.31, 0.31, 1.0)
            nt.links.new(uv.outputs["UV"], map2.inputs["Vector"])
            tex2 = _image_node(nt, "MAD_BaseColor_Detile", images[library.MAP_BASECOLOR], _X_TEX, -1200, f_tex, map2.outputs["Vector"], "BaseColor (detile)")
            noise = _node(nt, "ShaderNodeTexNoise", "MAD_DetileNoise", _X_TEX, -1500, f_tex, "Blend noise (~60 m)")
            noise.noise_dimensions = '2D'
            _set_input(noise, "Scale", 0.13)       # UV units are texture tiles (8 m): 0.13 -> ~60 m period
            _set_input(noise, "Detail", 2.0)
            _set_input(noise, "Roughness", 0.5)
            nt.links.new(uv.outputs["UV"], noise.inputs["Vector"])
            rng = _node(nt, "ShaderNodeMapRange", "MAD_DetileRange", _X_TEX + 200, -1500, f_tex, "0.35..0.65 -> 0..1")
            _set_input(rng, "From Min", 0.35)
            _set_input(rng, "From Max", 0.65)
            nt.links.new(noise.outputs["Fac"], rng.inputs["Value"])
            dmix = _node(nt, "ShaderNodeMix", "MAD_DetileMix", _X_PROC - 200, 250, f_wear, "Detile blend")
            dmix.data_type = 'RGBA'
            dmix.blend_type = 'MIX'
            nt.links.new(rng.outputs["Result"], NG.mix_socket(dmix, "Factor_Float"))
            nt.links.new(base.outputs["Color"], NG.mix_socket(dmix, "A_Color"))
            nt.links.new(tex2.outputs["Color"], NG.mix_socket(dmix, "B_Color"))
            color_src = NG.mix_result(dmix, "Result_Color")
            # large-scale, non-repeating variation: dry straw patches (~150-300 m) and a soft brightness drift
            mnoise = _node(nt, "ShaderNodeTexNoise", "MAD_MacroNoise", _X_TEX, -1800, f_tex, "Macro patches (~200 m)")
            mnoise.noise_dimensions = '2D'
            _set_input(mnoise, "Scale", 0.035)
            _set_input(mnoise, "Detail", 4.0)
            _set_input(mnoise, "Roughness", 0.6)
            _set_input(mnoise, "Distortion", 0.4)
            nt.links.new(uv.outputs["UV"], mnoise.inputs["Vector"])
            mrng = _node(nt, "ShaderNodeMapRange", "MAD_MacroRange", _X_TEX + 200, -1800, f_tex, "0.42..0.62 -> 0..0.75")
            _set_input(mrng, "From Min", 0.42)
            _set_input(mrng, "From Max", 0.62)
            _set_input(mrng, "To Max", 0.75)
            nt.links.new(mnoise.outputs["Fac"], mrng.inputs["Value"])
            mmix = _node(nt, "ShaderNodeMix", "MAD_MacroMix", _X_PROC - 200, 0, f_wear, "Dry patches")
            mmix.data_type = 'RGBA'
            mmix.blend_type = 'MULTIPLY'
            nt.links.new(mrng.outputs["Result"], NG.mix_socket(mmix, "Factor_Float"))
            nt.links.new(color_src, NG.mix_socket(mmix, "A_Color"))
            NG.mix_socket(mmix, "B_Color").default_value = (1.16, 1.09, 0.80, 1.0)
            bnoise = _node(nt, "ShaderNodeTexNoise", "MAD_MacroBright", _X_TEX, -2100, f_tex, "Brightness drift (~80 m)")
            bnoise.noise_dimensions = '2D'
            _set_input(bnoise, "Scale", 0.1)
            _set_input(bnoise, "Detail", 3.0)
            _set_input(bnoise, "Roughness", 0.55)
            nt.links.new(uv.outputs["UV"], bnoise.inputs["Vector"])
            brng = _node(nt, "ShaderNodeMapRange", "MAD_MacroBrightRange", _X_TEX + 200, -2100, f_tex, "0.3..0.7 -> 0.88..1.08")
            _set_input(brng, "From Min", 0.3)
            _set_input(brng, "From Max", 0.7)
            _set_input(brng, "To Min", 0.88)
            _set_input(brng, "To Max", 1.08)
            nt.links.new(bnoise.outputs["Fac"], brng.inputs["Value"])
            bmix = _node(nt, "ShaderNodeMix", "MAD_MacroBrightMix", _X_PROC - 200, -250, f_wear, "Brightness drift")
            bmix.data_type = 'RGBA'
            bmix.blend_type = 'MULTIPLY'
            NG.mix_socket(bmix, "Factor_Float").default_value = 1.0
            nt.links.new(NG.mix_result(mmix, "Result_Color"), NG.mix_socket(bmix, "A_Color"))
            nt.links.new(brng.outputs["Result"], NG.mix_socket(bmix, "B_Color"))
            color_src = NG.mix_result(bmix, "Result_Color")
        tint = _node(nt, "ShaderNodeMix", "MAD_Tint", _X_PROC, 250, f_wear, "Tint (spec colour / user tint)")
        tint.data_type = 'RGBA'
        tint.blend_type = 'MULTIPLY'
        NG.mix_socket(tint, "Factor_Float").default_value = 1.0
        NG.mix_socket(tint, "B_Color").default_value = _tint(spec, settings)
        if color_src is not None:
            nt.links.new(color_src, NG.mix_socket(tint, "A_Color"))
        else:
            NG.mix_socket(tint, "A_Color").default_value = (1.0, 1.0, 1.0, 1.0)
        color_src = NG.mix_result(tint, "Result_Color")
        if library.MAP_AO in tex_nodes:
            ao = _node(nt, "ShaderNodeMix", "MAD_AOMix", _X_PROC + 220, 250, f_wear, f"AO x {AO_FACTOR:g}")
            ao.data_type = 'RGBA'
            ao.blend_type = 'MULTIPLY'
            NG.mix_socket(ao, "Factor_Float").default_value = AO_FACTOR
            nt.links.new(color_src, NG.mix_socket(ao, "A_Color"))
            nt.links.new(tex_nodes[library.MAP_AO].outputs["Color"], NG.mix_socket(ao, "B_Color"))
            color_src = NG.mix_result(ao, "Result_Color")
        grp, vc = _wear_group(nt, _X_GROUP, 0, f_wear, spec, settings)
        nt.links.new(color_src, grp.inputs["BaseColor"])
        if library.MAP_ROUGHNESS in tex_nodes:
            nt.links.new(tex_nodes[library.MAP_ROUGHNESS].outputs["Color"], grp.inputs["Roughness"])
        else:
            _set_input(grp, "Roughness", spec.roughness)
        nt.links.new(grp.outputs["BaseColor"], bsdf.inputs["Base Color"])
        nt.links.new(grp.outputs["Roughness"], bsdf.inputs["Roughness"])
        if spec.alpha == 'MASKED' and library.MAP_OPACITY in tex_nodes:
            nt.links.new(tex_nodes[library.MAP_OPACITY].outputs["Color"], bsdf.inputs["Alpha"])
        if library.MAP_METALLIC in tex_nodes:
            nt.links.new(tex_nodes[library.MAP_METALLIC].outputs["Color"], bsdf.inputs["Metallic"])

    # ---- normal / bump ------------------------------------------------------------
    normal_out = None
    if library.MAP_NORMAL in tex_nodes:
        ncol = tex_nodes[library.MAP_NORMAL].outputs["Color"]
        if directx:
            # the file is DirectX (green = -Y): flip green so Blender (OpenGL) shades it correctly
            sep = _node(nt, "ShaderNodeSeparateColor", "MAD_NormalFlip_Sep", _X_PROC - 150, -450, f_wear, "DX -> GL")
            inv = _node(nt, "ShaderNodeMath", "MAD_NormalFlip_Inv", _X_PROC + 30, -450, f_wear, "1 - G")
            inv.operation = 'SUBTRACT'
            inv.inputs[0].default_value = 1.0
            comb = _node(nt, "ShaderNodeCombineColor", "MAD_NormalFlip_Comb", _X_PROC + 210, -450, f_wear, "GL normal")
            nt.links.new(ncol, sep.inputs["Color"])
            nt.links.new(sep.outputs["Red"], comb.inputs["Red"])
            nt.links.new(sep.outputs["Green"], inv.inputs[1])
            nt.links.new(inv.outputs[0], comb.inputs["Green"])
            nt.links.new(sep.outputs["Blue"], comb.inputs["Blue"])
            ncol = comb.outputs["Color"]
        nmap = _node(nt, "ShaderNodeNormalMap", "MAD_NormalMap", _X_GROUP, -450, f_wear, "Normal map")
        nmap.space = 'TANGENT'
        nmap.uv_map = "UVMap"
        _set_input(nmap, "Strength", spec.normal_strength * (0.5 if is_paint else 1.0))
        nt.links.new(ncol, nmap.inputs["Color"])
        normal_out = nmap.outputs["Normal"]
    if library.MAP_HEIGHT in tex_nodes and not is_paint:
        bump = _node(nt, "ShaderNodeBump", "MAD_Bump", _X_GROUP + 180, -650, f_wear, "Bump (height, low)")
        _set_input(bump, "Strength", BUMP_STRENGTH)
        _set_input(bump, "Distance", BUMP_DISTANCE)
        nt.links.new(tex_nodes[library.MAP_HEIGHT].outputs["Color"], bump.inputs["Height"])
        if normal_out is not None:
            nt.links.new(normal_out, bump.inputs["Normal"])
        normal_out = bump.outputs["Normal"]
    if normal_out is not None:
        nt.links.new(normal_out, bsdf.inputs["Normal"])

    _finish(mat, nt, bsdf, out, spec, settings, spec.texture_set or "", maps, textured=True)
    return True


def _build_flat(mat, nt, spec, settings, missing_set: str | None) -> None:
    f_wear = _frame(nt, "MAD_Frame_Wear", "Wear", _X_PROC, 300)
    f_out = _frame(nt, "MAD_Frame_Output", "Output", _X_BSDF, 300)
    bsdf = _node(nt, "ShaderNodeBsdfPrincipled", "MAD_BSDF", _X_BSDF, 0, f_out, "Principled")
    out = _node(nt, "ShaderNodeOutputMaterial", "MAD_Output", _X_OUT, 0, f_out, "Output")
    base = _base_color(spec, settings)
    pavement = _is_pavement_like(spec)
    is_paint = 'paint' in spec.tags
    is_light = 'light' in spec.tags

    if missing_set:
        note = _frame(nt, MISSING_FRAME, f"Missing texture set '{missing_set}' - flat colour fallback (Generate Textures)", _X_TEX, 300)
        note.use_custom_color = True
        note.color = (0.5, 0.2, 0.15)

    rough_out, color_fac = (None, None)
    if not is_light:
        rough_out, color_fac = _flat_variation(nt, _X_PROC, -350, f_wear, spec.roughness, with_color=pavement)

    if pavement or is_paint:
        grp, vc = _wear_group(nt, _X_GROUP, 0, f_wear, spec, settings, rubber_only=is_paint)
        if color_fac is not None:
            sc = _node(nt, "ShaderNodeVectorMath", "MAD_ColorScale", _X_PROC + 500, 150, f_wear, "Brightness x")
            sc.operation = 'SCALE'
            sc.inputs[0].default_value = base[:3]
            nt.links.new(color_fac, sc.inputs["Scale"])
            nt.links.new(sc.outputs["Vector"], grp.inputs["BaseColor"])
        else:
            _set_input(grp, "BaseColor", base)
        if rough_out is not None:
            nt.links.new(rough_out, grp.inputs["Roughness"])
        else:
            _set_input(grp, "Roughness", spec.roughness)
        nt.links.new(grp.outputs["BaseColor"], bsdf.inputs["Base Color"])
        nt.links.new(grp.outputs["Roughness"], bsdf.inputs["Roughness"])
    else:
        _set_input(bsdf, "Base Color", base)
        if rough_out is not None:
            nt.links.new(rough_out, bsdf.inputs["Roughness"])
        else:
            _set_input(bsdf, "Roughness", spec.roughness)
    if is_paint and spec.alpha == 'MASKED':
        # without the PaintWear chip mask there is nothing to erode with: paint stays fully opaque
        _set_input(bsdf, "Alpha", 1.0)
    if 'decal' in spec.tags:
        _set_input(bsdf, "Alpha", 0.0)       # no atlas: the decal sheet stays invisible instead of white
    _finish(mat, nt, bsdf, out, spec, settings, missing_set or "", {}, textured=False)


def _build_decal(mat, nt, spec, settings, images, maps, uv, bsdf, out, f_tex, f_wear) -> None:
    """Alpha-blended decal sheet: atlas colour / roughness, alpha = atlas opacity x per-decal vertex alpha."""
    uv_out = uv.outputs["UV"]

    def img(name: str, key: str, y: float, label: str):
        n = _image_node(nt, name, images[key], _X_TEX, y, f_tex, uv_out, label)
        n.extension = 'EXTEND'
        return n

    base = img("MAD_BaseColor", library.MAP_BASECOLOR, 250, "Decal colour")
    nt.links.new(base.outputs["Color"], bsdf.inputs["Base Color"])
    if library.MAP_ROUGHNESS in images:
        rough = img("MAD_Roughness", library.MAP_ROUGHNESS, -40, "Roughness")
        nt.links.new(rough.outputs["Color"], bsdf.inputs["Roughness"])
    else:
        _set_input(bsdf, "Roughness", spec.roughness)
    vc = _node(nt, "ShaderNodeVertexColor", "MAD_Wear", _X_TEX, -620, f_wear, "Per-decal opacity (vertex alpha)")
    vc.layer_name = "Wear"
    mul = _node(nt, "ShaderNodeMath", "MAD_DecalAlpha", _X_PROC, -200, f_wear, "Atlas opacity x decal opacity")
    mul.operation = 'MULTIPLY'
    mul.use_clamp = True
    if library.MAP_OPACITY in images:
        op = img("MAD_Opacity", library.MAP_OPACITY, -330, "Opacity (atlas)")
        nt.links.new(op.outputs["Color"], mul.inputs[0])
    else:
        mul.inputs[0].default_value = 1.0
    nt.links.new(vc.outputs["Alpha"], mul.inputs[1])
    gain = _node(nt, "ShaderNodeMath", "MAD_DecalGain", _X_PROC + 220, -200, f_wear, "Global decal opacity")
    gain.operation = 'MULTIPLY'
    gain.use_clamp = True
    gain.inputs[1].default_value = float(_g(settings, "decals.opacity", 1.0))
    nt.links.new(mul.outputs["Value"], gain.inputs[0])
    nt.links.new(gain.outputs["Value"], bsdf.inputs["Alpha"])
    _finish(mat, nt, bsdf, out, spec, settings, spec.texture_set or "", maps, textured=True)


# --------------------------------------------------------------------------- public API
def build_material(spec: library.MaterialSpec, settings=None, existing: bpy.types.Material | None = None) -> bpy.types.Material:
    """Build (or refill in place) the Blender material for ``spec``. Never raises for missing textures."""
    name = library.material_name(spec.name)
    mat = existing if existing is not None else bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    nt = _ensure_tree(mat)
    directory, maps = "", {}
    built = False
    if spec.texture_set:
        try:
            directory, maps = _resolve_maps(spec, settings)
            if _key_map(spec) in maps:
                built = _build_textured(mat, nt, spec, settings, directory, maps)
        except Exception:  # noqa: BLE001 - fall back to flat colour, keep the base building
            traceback.print_exc()
            nt.nodes.clear()
            built = False
    if not built:
        try:
            _build_flat(mat, nt, spec, settings, spec.texture_set if spec.texture_set else None)
        except Exception:  # noqa: BLE001 - last resort: bare principled so the mesh has *something*
            traceback.print_exc()
            nt.nodes.clear()
            bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled"); bsdf.name = "MAD_BSDF"
            out = nt.nodes.new("ShaderNodeOutputMaterial"); out.name = "MAD_Output"; out.location = (300, 0)
            bsdf.inputs["Base Color"].default_value = C.hex_to_linear(spec.color)
            bsdf.inputs["Roughness"].default_value = spec.roughness
            bsdf.inputs["Metallic"].default_value = spec.metallic
            nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
            mat["mad_spec"] = spec.name
    return mat


def refresh_textures(settings=None, force: bool = False) -> list[str]:
    """Reload every image of the texture folder and rebuild the materials whose files changed.

    Called by the UI after "Generate Textures". Returns the spec names that were rebuilt.
    """
    _GEN_ATTEMPTED.clear()
    io = _io()
    try:
        directory = io.texture_dir(settings, create=False)
    except Exception:  # noqa: BLE001
        directory = ""
    if directory:
        try:
            io.reload_all(directory)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    rebuilt: list[str] = []
    for mat in list(bpy.data.materials):
        key = mat.get("mad_spec")
        spec = library.SPECS.get(key) if key else None
        if spec is None or not spec.texture_set:
            continue
        if mat.name != library.material_name(spec.name):
            continue          # '.001' duplicates are left alone
        _, maps = texture_maps(spec.texture_set, settings)
        want_textured = _key_map(spec) in maps
        changed = (force or material_signature(spec, maps) != mat.get("mad_maps", "")
                   or bool(mat.get("mad_textured", 0)) != want_textured
                   or mat.get("mad_node_version") != NODE_VERSION)
        if changed:
            build_material(spec, settings, existing=mat)
            rebuilt.append(spec.name)
    return rebuilt


def register():
    pass


def unregister():
    pass
