"""Build orchestration: settings -> plan -> geometry modules -> Blender objects.

Every geometry module exposes ``build(ctx: BuildContext) -> None`` and adds its
objects through ``ctx.add_object`` / ``ctx.add_instance``. A module that raises
does not abort the build: the error is recorded in ``ctx.warnings`` and shown in
the UI, so a bug in one feature never leaves the user with nothing.
"""
from __future__ import annotations

import math
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import bpy
from mathutils import Vector

from ..core import constants as C
from ..core import scene_utils as su
from ..core.props import resume_updates, suspend_updates
from ..materials.library import MaterialGetter
from . import plan as planmod

MODULE_ORDER = ("pavement", "terrain", "markings", "lighting", "signage", "structures", "perimeter", "navaids")


@dataclass
class BuildContext:
    plan: planmod.Plan
    root: bpy.types.Object
    settings: Any
    collections: dict[str, bpy.types.Collection]
    mats: MaterialGetter
    quality: str = 'STANDARD'
    stats: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    meshes: dict[str, bpy.types.Mesh] = field(default_factory=dict)      # shared instance meshes by key
    timings: dict[str, float] = field(default_factory=dict)

    # ------------------------------------------------------------------ helpers
    @property
    def scene(self) -> bpy.types.Scene:
        return bpy.context.scene

    def collection(self, category: str) -> bpy.types.Collection:
        col = self.collections.get(category)
        if col is None:
            col = su.ensure_collection(f"{self.root.name}_{category}", self.collections["_root"])
            self.collections[category] = col
        return col

    def add_object(self, name: str, mesh: bpy.types.Mesh | None, category: str, location=None,
                   rotation_z: float = 0.0, scale=None) -> bpy.types.Object:
        ob = su.new_object(C.PREFIX + name, mesh, self.collection(category), self.root, location, rotation_z, category, scale)
        self.stats[category] = self.stats.get(category, 0) + 1
        return ob

    def add_instance(self, name: str, mesh: bpy.types.Mesh, category: str, location, rotation_z: float = 0.0,
                     scale=None) -> bpy.types.Object:
        ob = su.instance(C.PREFIX + name, mesh, self.collection(category), self.root, location, rotation_z, category, scale)
        self.stats[category] = self.stats.get(category, 0) + 1
        return ob

    def shared_mesh(self, key: str, factory: Callable[[], bpy.types.Mesh]) -> bpy.types.Mesh:
        """Create-once meshes for instanced props (light fixtures, posts...)."""
        me = self.meshes.get(key)
        if me is None:
            me = factory()
            if not me.name.startswith(C.PREFIX):
                me.name = C.PREFIX + me.name
            self.meshes[key] = me
        return me

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def detail(self) -> float:
        """0.5 preview, 1.0 standard, 1.5 high — builders scale tessellation / prop density with it."""
        return {'PREVIEW': 0.5, 'STANDARD': 1.0, 'HIGH': 1.5}.get(self.quality, 1.0)

    @property
    def z_at(self) -> Callable[[float, float], float]:
        return self.plan.runway.crown


def _import_modules():
    from ..geometry import lighting, markings, navaids, pavement, perimeter, signage, structures, terrain
    return {"pavement": pavement, "terrain": terrain, "markings": markings, "lighting": lighting,
            "signage": signage, "structures": structures, "perimeter": perimeter, "navaids": navaids}


def ensure_root_transform(root: bpy.types.Object) -> None:
    """The root Empty carries the magnetic heading; children are built in the runway frame."""
    s = root.mad
    root.rotation_euler = (0.0, 0.0, su.compass_to_math(s.runway.heading))


def build_base(root: bpy.types.Object, modules: Sequence[str] | None = None) -> BuildContext:
    """(Re)generate everything under ``root``."""
    s = root.mad
    t0 = time.time()
    suspend_updates()
    try:
        plan = planmod.build_plan(s)
        # collections
        root_col = _root_collection(root)
        cols = {"_root": root_col}
        for cat in su.CATEGORIES:
            cols[cat] = su.ensure_collection(f"{root.name}_{cat}", root_col)
        su.clear_generated(root)
        ensure_root_transform(root)
        mats = MaterialGetter(s)
        ctx = BuildContext(plan=plan, root=root, settings=s, collections=cols, mats=mats, quality=s.quality)
        ctx.warnings.extend(plan.warnings)
        mods = _import_modules()
        for name in (modules or MODULE_ORDER):
            mod = mods[name]
            enabled = _module_enabled(name, s)
            if not enabled:
                continue
            t1 = time.time()
            try:
                mod.build(ctx)
            except Exception as exc:  # keep building the rest
                traceback.print_exc()
                ctx.warn(f"{name}: {type(exc).__name__}: {exc}")
            ctx.timings[name] = time.time() - t1
        s.warnings = "\n".join(ctx.warnings)
        total = sum(ctx.stats.values())
        s.build_stats = f"{total} objects in {time.time() - t0:.1f}s | " + ", ".join(f"{k} {v}" for k, v in sorted(ctx.stats.items()))
        root["mad_last_build"] = time.time()
        return ctx
    finally:
        resume_updates()


def _module_enabled(name: str, s) -> bool:
    return {
        "pavement": True,
        "terrain": s.terrain.enabled,
        "markings": s.markings.enabled,
        "lighting": s.lighting.enabled,
        "signage": s.signage.enabled,
        "structures": s.structures.enabled,
        "perimeter": s.perimeter.enabled,
        "navaids": s.structures.enabled and s.structures.navaids,
    }.get(name, True)


def _root_collection(root: bpy.types.Object) -> bpy.types.Collection:
    name = f"Airbase_{root.mad.base_name}" if root.mad.base_name else root.name
    for col in root.users_collection:
        if col.name.startswith("Airbase_"):
            return col
    col = su.ensure_collection(name, bpy.context.scene.collection)
    if root.name not in col.objects:
        for c in list(root.users_collection):
            c.objects.unlink(root)
        col.objects.link(root)
    return col


def update_materials(root: bpy.types.Object) -> None:
    """Material-only refresh (textures / weathering / night mode)."""
    from ..materials import library
    s = root.mad
    library.rebuild_all(s)


def create_base(context: bpy.types.Context, name: str = "Airbase", preset: str | None = None,
                location=None) -> bpy.types.Object:
    """Create a new base root Empty with default (or preset) settings and build it."""
    from ..core import presets as presetmod
    scene = context.scene
    col = su.ensure_collection(f"Airbase_{name}", scene.collection)
    root = su.new_empty(f"{C.PREFIX}Base_{name}", col, None, location or (0.0, 0.0, 0.0), 'ARROWS', 50.0)
    root[su.ROOT_PROP] = True
    root[su.TAG_PROP] = False
    root.mad.is_root = True
    root.mad.base_name = name
    suspend_updates()
    try:
        presetmod.apply_preset(root, preset or "USAF_CLASS_B_FIGHTER")
    finally:
        resume_updates()
    build_base(root)
    return root
