# Implementer brief (shared by every module agent)

Repository: `/home/user/Military_airbase_pist`, package `military_airbase_designer/` (Blender 4.2+ / 5.x add-on,
Python 3.11, numpy available, no other third-party libs). Read `docs/ARCHITECTURE.md` first.

## Ground rules
* You own ONLY the files named in your task. Do not edit shared files (`core/*.py`, `layout/*.py`,
  `materials/library.py`, `__init__.py`, `tests/harness.py`, other agents' modules). If you need a change there,
  put it in your final report under `requested_shared_changes` (exact patch text) — do not apply it.
* Public contract of a geometry module: `def build(ctx: BuildContext) -> None` (see `layout/builder.py`).
  Everything you create goes through `ctx.add_object(name, mesh, category, location=None, rotation_z=0.0)`
  or `ctx.add_instance(name, shared_mesh, category, location, rotation_z)`; shared meshes for instanced props
  through `ctx.shared_mesh(key, factory)`. Categories: Pavement, Markings, Lighting, Signage, Structures,
  Perimeter, Navaids, Terrain.
* Build meshes with `core.meshbuild.MeshBuilder` (read it fully: add_polygon, add_triangles, add_ring, add_prism,
  add_box, add_cylinder, add_tube, add_torus, add_sphere, add_profile_extrusion, append, build). Materials come
  ONLY from `ctx.mats('SpecName')` (names in `materials/library.py` SPECS). Assign materials by index: build the
  mesh with `materials=[ctx.mats('A'), ctx.mats('B')]` and use `mat=0/1` per face.
* Geometry lives in the *base frame* (runway along +X from the low-numbered end, +Y left, origin at runway centre,
  metres). The root Empty applies the magnetic heading. Runway surface elevation at (x, y) is
  `ctx.plan.runway.crown(x, y)` — everything on the runway must use it; everything else sits at z = 0.
* Read `layout/plan.py` fully: `Plan`, `RunwayPlan`, `TaxiwayPlan` (kinds PARALLEL / CONNECTOR / HIGH_SPEED / END /
  ACCESS, `centerline`, `polys`, `fillet_polys`, `hold_lines`, `runway_junction`, `runway_tangents`),
  `ApronPlan` (spots, taxilanes, access_polys, edge_loop, loop_centerline, extra_markings), `RoadPlan`,
  `StructurePlan` (kind, position, rotation, size, params), `FencePlan`, `plan.navaids`, `plan.wind_cones`,
  `plan.pavement` (all pavement polygons with zone ids), `plan.airside`, `plan.bounds`, `plan.site_bounds`.
* 2D helpers in `core/geom2d.py` (rect, strip, polyline_strip, offset_polyline, round_polyline, resample_polyline,
  point_along, arc_points, circle, fillet_corner, union_triangulate, boundary_loops, offset_loop, triangulate,
  lattice_points). Union of overlapping polygons: `union_triangulate(polys, holes=..., extra_points=...)`.
* Standards constants: `core/constants.py` (MK = marking dims, LT = lighting dims, colours). Units via
  `core/units.py` (`ft()`, `inch()`). Keep standards traceable: comment the source (e.g. "UFC 3-535-01 §4-2").
* Quality/detail: `ctx.detail` (0.5 preview / 1.0 standard / 1.5 high) scales tessellation and small-prop density.
  A full base must build in a few seconds at STANDARD: use instancing (`ctx.add_instance`) for anything repeated
  (lights, signs, posts, tie-downs), and keep per-object Python loops vectorised where big.
* Game-ready: real-world scale, clean quads/tris, no overlapping coplanar faces (z-fight), UVs on every face
  (planar world UVs for pavement/terrain, sensible UVs on props), smooth shading only on curved surfaces
  (`smooth=True` per face or `smooth_angle=` in `build`). Object names `MAD_<Category>_<Thing>` via ctx.
* Never call `bpy.ops` inside build code except where unavoidable; never use EEVEE in headless tests (crashes).
* Blender API: Principled BSDF sockets are 4.x names; `mesh.use_auto_smooth` does not exist (use
  `smooth_angle` in MeshBuilder.build); `ShaderNodeTexMusgrave` does not exist.

## How to test
`python3 tests/harness.py --modules pavement,markings --render tests/_out/x.png --camera threshold --samples 16`
builds a base from the default preset with only the listed modules (others warn "not implemented"), prints
STATS / TIMING / WARNING lines, and renders with Cycles. Use `--camera overview` for a top-down ortho view,
`--blend out.blend` to save. Look at your render with the Read tool and iterate until it looks professional.
Also run under Blender 5.0: `/tmp/claude-0/-home-user-Military-airbase-pist/fa97b189-e7ce-5031-b35c-184639eba883/scratchpad/venv50/bin/python tests/harness.py ...`
(deprecation warnings are fine, exceptions are not). Put scratch scripts under the scratchpad directory
`/tmp/claude-0/-home-user-Military-airbase-pist/fa97b189-e7ce-5031-b35c-184639eba883/scratchpad/<yourmodule>/`, renders under `tests/_out/`.

## Research reports (read the ones your task names, fully)
`/tmp/claude-0/-home-user-Military-airbase-pist/fa97b189-e7ce-5031-b35c-184639eba883/scratchpad/research/`
01_geometry_standards.md, 02_markings.md, 03_lighting_signage.md, 04_structures_layout.md, 05_pbr_textures.md,
06_blender_addon_architecture.md, 07_visual_reference_and_polish.md (+ 08_gap_*.md when present).

## Final report (return value, structured)
files_written, public_api (functions/classes other modules may use), what_is_implemented (bullet list with the
standard each element follows), known_gaps, requested_shared_changes (exact text), renders (paths), test_log
(the STATS/TIMING lines from your last harness run).
