"""Node-tree construction for MaterialSpec (placeholder implementation).

The full PBR implementation (image textures, vertex-colour wear, engine packing)
replaces ``build_material``; the contract is: return a Material named
``library.material_name(spec.name)`` and never raise on missing textures.
"""
from __future__ import annotations

import bpy

from ..core import constants as C
from . import library


def _ensure_nodes(mat: bpy.types.Material):
    if getattr(mat, "node_tree", None) is None:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    return nt


def build_material(spec: library.MaterialSpec, settings=None, existing=None) -> bpy.types.Material:
    mat = existing or bpy.data.materials.new(library.material_name(spec.name))
    nt = _ensure_nodes(mat)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (300, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)
    bsdf.inputs["Base Color"].default_value = C.hex_to_linear(spec.color)
    bsdf.inputs["Roughness"].default_value = spec.roughness
    bsdf.inputs["Metallic"].default_value = spec.metallic
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = spec.specular
    if spec.emission:
        night = bool(getattr(getattr(settings, "lighting", None), "night_mode", False)) if settings is not None else False
        strength = getattr(getattr(settings, "lighting", None), "emission_strength", 25.0) if settings is not None else 25.0
        bsdf.inputs["Emission Color"].default_value = C.hex_to_linear(spec.emission)
        bsdf.inputs["Emission Strength"].default_value = (strength * spec.emission_strength) if night else (spec.emission_strength * 0.5 if 'sign' in spec.tags else 0.0)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    mat.diffuse_color = C.hex_to_linear(spec.color)
    mat.roughness = spec.roughness
    mat.metallic = spec.metallic
    if spec.two_sided:
        mat.use_backface_culling = False
    else:
        mat.use_backface_culling = True
    if spec.alpha == 'MASKED' and hasattr(mat, "surface_render_method"):
        mat.surface_render_method = 'DITHERED'
    mat["mad_spec"] = spec.name
    return mat
