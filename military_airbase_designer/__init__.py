"""Military Airbase Designer — parametric, game-ready military airbase generator.

Works as a Blender 4.2+ extension (blender_manifest.toml) and as a legacy add-on
(bl_info) for 4.0/4.1.
"""
bl_info = {
    "name": "Military Airbase Designer",
    "author": "Ahmet Faruk Sahin",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar (N) > Airbase",
    "description": "Parametric military airbase: runway, taxiways, aprons, markings, lighting, signs, structures, PBR textures, game export",
    "category": "Add Mesh",
    "doc_url": "https://github.com/ahmet6141/Military_airbase_pist",
}

import importlib
import sys

import bpy

_MODULE_NAMES = (
    "core.units", "core.constants", "core.geom2d", "core.meshbuild", "core.scene_utils", "core.props",
    "core.rebuild", "core.presets", "layout.plan", "layout.builder",
    "materials.library", "materials.nodes",
    "textures.noise", "textures.synth", "textures.io",
    "geometry.glyphs", "geometry.pavement", "geometry.terrain", "geometry.markings", "geometry.lighting",
    "geometry.signage", "geometry.structures", "geometry.perimeter", "geometry.navaids",
    "ops.build_ops", "ops.list_ops", "ops.preset_ops", "ops.texture_ops", "ops.export_ops",
    "ui.lists", "ui.panels", "i18n.translations", "prefs",
)


def _import_all():
    mods = []
    for name in _MODULE_NAMES:
        full = f"{__package__}.{name}"
        if full in sys.modules:
            mods.append(importlib.reload(sys.modules[full]))
        else:
            mods.append(importlib.import_module(full))
    return mods


_modules = []


def register():
    global _modules
    _modules = _import_all()
    for m in _modules:
        if hasattr(m, "register"):
            m.register()


def unregister():
    for m in reversed(_modules):
        if hasattr(m, "unregister"):
            try:
                m.unregister()
            except Exception:  # pragma: no cover
                import traceback
                traceback.print_exc()
