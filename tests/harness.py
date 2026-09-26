"""Headless test harness for the pip ``bpy`` module (Blender 4.2+ / 5.x).

Usage:
    python3 tests/harness.py [--preset NAME] [--render out.png] [--blend out.blend] [--quality PREVIEW]
                             [--modules pavement,markings] [--textures] [--export DIR]

It installs the package into a temporary Blender script directory, enables the
add-on the way Blender would (so preferences and translations work), builds a
base from a preset and reports statistics / warnings. Exit code 1 on any
exception or on warnings that contain 'Error'.
"""
from __future__ import annotations

import argparse
import math
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "military_airbase_designer"


def install_addon():
    import bpy
    tmp = tempfile.mkdtemp(prefix="mad_scripts_")
    addons = os.path.join(tmp, "addons")
    os.makedirs(addons)
    shutil.copytree(os.path.join(ROOT, PKG), os.path.join(addons, PKG),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # test-only: stub modules that are not written yet so the package still registers
    init_src = open(os.path.join(addons, PKG, "__init__.py")).read()
    import re
    for name in re.findall(r'"([a-z_]+\.[a-z_]+)"', init_src.split("_MODULE_NAMES")[1].split(")")[0]):
        path = os.path.join(addons, PKG, *name.split(".")) + ".py"
        if not os.path.exists(path):
            print("STUBBING missing module", name)
            with open(path, "w") as f:
                f.write("def build(ctx):\n    ctx.warn('%s: missing')\ndef register():\n    pass\ndef unregister():\n    pass\n" % name)
    sd = bpy.context.preferences.filepaths.script_directories.new()
    sd.name = "mad_test"
    sd.directory = tmp
    bpy.utils.refresh_script_paths()
    r = bpy.ops.preferences.addon_enable(module=PKG)
    assert r == {'FINISHED'}, r
    return tmp


def setup_camera(scene, bounds, mode="overview"):
    import bpy
    x0, y0, x1, y1 = bounds
    cam = bpy.data.cameras.new("TestCam")
    co = bpy.data.objects.new("TestCam", cam)
    scene.collection.objects.link(co)
    if mode == "overview":
        cam.type = 'ORTHO'
        cam.clip_end = 20000
        cam.ortho_scale = max(x1 - x0, (y1 - y0) * 16 / 9) * 1.02
        co.location = ((x0 + x1) / 2, (y0 + y1) / 2, 3000)
    else:   # threshold perspective
        cam.lens = 35
        cam.clip_end = 20000
        co.location = (x0 * 0.55, -260, 90)
        co.rotation_euler = (math.radians(70), 0, math.radians(-35))
    scene.camera = co
    return co


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="USAF_CLASS_B_FIGHTER")
    ap.add_argument("--render", default="")
    ap.add_argument("--camera", default="overview", choices=["overview", "threshold"])
    ap.add_argument("--blend", default="")
    ap.add_argument("--quality", default="STANDARD")
    ap.add_argument("--modules", default="")
    ap.add_argument("--textures", action="store_true", help="generate texture sets (slow)")
    ap.add_argument("--texdir", default="")
    ap.add_argument("--export", default="")
    ap.add_argument("--samples", type=int, default=24)
    ap.add_argument("--res", default="1600x900")
    args = ap.parse_args(argv)

    import bpy
    bpy.ops.wm.read_factory_settings(use_empty=True)
    install_addon()
    import importlib
    pkg = importlib.import_module(PKG)
    builder = importlib.import_module(f"{PKG}.layout.builder")
    su = importlib.import_module(f"{PKG}.core.scene_utils")
    scene = bpy.context.scene
    if args.texdir:
        os.makedirs(args.texdir, exist_ok=True)

    t = time.time()
    root = builder.create_base(bpy.context, "Test", args.preset)
    root.mad.quality = args.quality
    if args.texdir:
        root.mad.materials.texture_dir = args.texdir
    root.mad.materials.generate_textures = bool(args.textures)
    mods = [m for m in args.modules.split(",") if m] or None
    ctx = builder.build_base(root, mods)
    dt = time.time() - t
    print("BUILD_TIME", round(dt, 1), "s")
    print("STATS", root.mad.build_stats)
    for k, v in ctx.timings.items():
        print(f"TIMING {k}: {v:.2f}s")
    for w in ctx.warnings:
        print("WARNING", w)
    n_obj = len(su.children_recursive(root))
    n_tris = 0
    for ob in su.children_recursive(root):
        if ob.type == 'MESH':
            n_tris += sum(len(p.vertices) - 2 for p in ob.data.polygons)
    print("OBJECTS", n_obj, "TRIS", n_tris)
    print("BOUNDS", [round(v, 1) for v in ctx.plan.bounds])
    if args.export:
        exp = importlib.import_module(f"{PKG}.ops.export_ops")
        if hasattr(exp, "export_base"):
            exp.export_base(bpy.context, root, args.export)
            print("EXPORTED", os.listdir(args.export))
    if args.blend:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.blend))
        print("BLEND", args.blend)
    if args.render:
        w, h = (int(v) for v in args.res.split("x"))
        setup_camera(scene, ctx.plan.site_bounds, args.camera)
        sun = bpy.data.lights.new("Sun", 'SUN')
        sun.energy = 4.0
        so = bpy.data.objects.new("Sun", sun)
        scene.collection.objects.link(so)
        so.rotation_euler = (math.radians(50), math.radians(10), math.radians(35))
        world = bpy.data.worlds.new("World")
        world.use_nodes = True
        world.node_tree.nodes["Background"].inputs[0].default_value = (0.55, 0.65, 0.85, 1)
        world.node_tree.nodes["Background"].inputs[1].default_value = 0.8
        scene.world = world
        scene.render.engine = 'CYCLES'
        scene.cycles.samples = args.samples
        scene.cycles.use_denoising = False
        scene.render.resolution_x = w
        scene.render.resolution_y = h
        scene.render.filepath = os.path.abspath(args.render)
        t = time.time()
        bpy.ops.render.render(write_still=True)
        print("RENDER", args.render, round(time.time() - t, 1), "s")
    errors = [w for w in ctx.warnings if "Error" in w or "error" in w]
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
