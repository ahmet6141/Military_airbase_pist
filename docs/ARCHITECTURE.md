# Military Airbase Designer — Architecture

Parametric Blender 4.2+ / 5.x add-on that generates a complete, game-ready, PBR-textured
military airbase: runway, shoulders, overruns, taxiways with fillets, aprons, paint markings,
airfield lighting, signage, navaids, perimeter (fence/road/gates), structures (HAS, hangars,
tower, fuel farm, munitions…) and infield terrain, with procedural texture synthesis and
engine-oriented export.

## 1. Frames, units, naming

* Units: metres (Blender units). Standards tables are written in feet through `core.units.ft()`.
* **Base frame**: +X runs along the runway from the low-numbered end to the high-numbered end,
  +Y is left of that direction, Z up, origin at the runway centre. Every builder works in this
  frame. The base root Empty carries the world transform (location + heading rotation
  `compass_to_math(heading)`), so children never need to know the heading.
* Object names: `MAD_<Category>_<Name>` (MAD = Military Airbase Designer). Mesh datablocks:
  `MAD_…`. Materials `M_MAD_<Name>`; textures `T_MAD_<Name>_<Map>.png`.
* Collections: `Airbase_<name>` root with children `Pavement, Markings, Lighting, Signage,
  Structures, Perimeter, Navaids, Terrain` (see `scene_utils.CATEGORIES`).
* Every generated object gets custom props `mad_generated=True`, `mad_category=<cat>`; the root
  Empty gets `mad_base_root=True` and holds the parameters (`object.mad` PropertyGroup).

## 2. Data model (`core/props.py`)

`MAD_BaseSettings` (PointerProperty `Object.mad`) with sub-groups:

| Group | Key content |
|---|---|
| `runway` | length, width, heading, suffix (L/C/R, count of parallel runways), surface (PCC/ASPHALT/HYBRID), slab size, crown %, shoulder width/type, overrun length/width per end, blast pad, displaced threshold per end, arresting gear stations, class (A/B), rubber/wear amount, grooving |
| `markings` | standard (UFC/FAA/ICAO), toggles for every marking family, line width choices (6/12 in), black borders (auto), paint wear, striated option |
| `taxiways` | CollectionProperty of `MAD_TaxiwayItem`: kind (PARALLEL, CONNECTOR, HIGH_SPEED, END_LOOP, APRON_LINK, CUSTOM), designator, width, side, position (station along runway), angle, fillet radius, hold line type/distance, lights |
| `aprons` | CollectionProperty of `MAD_ApronItem`: kind (MAIN, ALERT, HOT_CARGO, ARM_DEARM, HANGAR, HELIPAD, COMPASS_PAD), station, side, size, spot count / aircraft class, lead-in radius |
| `lighting` | HIRL/MIRL, threshold/end, centreline, TDZ, PAPI (side, distance), approach system per end (NONE/ALSF2/ALSF1/MALSR/SSALR), REIL, taxiway edge/centreline, guard lights, floodlights, beacon, night mode & emission strength |
| `signage` | hold signs, location/direction signs, RDR signs, AGM markers, wind cones, sign size |
| `structures` | HAS (count, style, layout, earth cover), hangars, tower, fire station, ops/ATC buildings, fuel farm, munitions area, blast deflectors, arresting gear housings, lighting vault, navaids toggles |
| `perimeter` | fence (double, offset), patrol road, entry control point, guard towers |
| `terrain` | infield margin, dirt strip width, ditches, mowing stripes, subdivision |
| `materials` | texture resolution, slabs per tile, seed, engine preset, normal convention, texture folder, weathering sliders |
| `export` | format, scope, join by category, LODs, collision, axis preset |

Scene-level `Scene.mad_ui`: live update toggle, active tab, quality (PREVIEW/STANDARD/HIGH),
last warnings text.

Update callbacks call `core.rebuild.request_rebuild(root)` which debounces through
`bpy.app.timers` when live update is on.

## 3. Pipeline

```
props  ──►  layout.plan.build_plan(settings) ──► Plan (pure data, base frame)
                                                   │
        ┌──────────────┬──────────────┬────────────┼──────────────┬─────────────┬──────────┐
   geometry.pavement  geometry.markings  geometry.lighting  geometry.signage  geometry.structures  geometry.terrain
        │ (MeshBuilder → meshes → objects in collections, materials from materials.library)
   materials.library.get(name) ─► node materials referencing textures from textures.synth (generated lazily, cached on disk)
   ops.export ─► FBX/glTF/OBJ + texture copy + material index JSON
```

### 3.1 `layout.plan.Plan`
Single source of truth for *where things are*. Pure Python dataclasses, no bpy:
* `runway`: rect, threshold stations (x of each threshold incl. displacement), overruns, shoulders,
  designators, arresting stations, crown function.
* `taxiways[]`: centreline polyline, width, edge polygons, fillet polygons, hold line station &
  orientation, designator, kind, lights flags.
* `aprons[]`: polygon, spots (position + heading + class), lead-in lines, edge line loops.
* `roads[]`: centreline polylines + width (perimeter road, service roads).
* `fence`: loop polyline; `gates[]`.
* `structures[]`: (kind, position, rotation, size, params).
* `navaids`, `lights` (computed by geometry.lighting from plan), `signs`.
* `site_bounds`: bbox of everything + margin.

### 3.2 `geometry.pavement`
1. Collect pavement polygons from the plan with zone ids (RUNWAY, RUNWAY_END_PCC, OVERRUN,
   TAXIWAY, APRON, ROAD, SHOULDER…). 2. `geom2d.union_triangulate` with lattice refinement
   (15–25 m). 3. z from crown function. 4. Material index by zone priority. 5. Vertex colour
   `Wear` (R rubber, G edge dirt, B macro variation). 6. Shoulder ring via `boundary_loops` +
   `offset_loop`. 7. Dirt strip ring beyond shoulders (Terrain category).
Roads are separate meshes (polyline strips), 20 mm below apron level where they cross? No —
roads never overlap aircraft pavement; crossings are marked with stop bars.

### 3.3 `geometry.markings`
Flat meshes 4 mm above pavement (`MARKING_Z = 0.004`), one object per colour family
(white, yellow, red, black border) so materials stay simple and export light. Glyphs from
`geometry.glyphs`. All geometry is 2D polygons unioned per object with `union_triangulate`.
Black borders: offset polygons (`offset_loop`) 6 in around white/yellow markings on concrete,
built as a separate object 1 mm lower.

### 3.4 Instanced props
Light fixtures, signs, fence posts, tie-downs, bollards, approach masts: one mesh datablock per
type (`materials`-assigned), many linked-duplicate objects (`scene_utils.instance`). Optional
`join_instances` at export.

## 4. Materials & textures
* `materials.library.MaterialSpec` table: name, base colour (hex sRGB), roughness, metallic,
  texture set (or None), tile size, emission. `get_material(name, settings)` builds/caches.
* `textures.synth` builds texture sets with numpy (`textures.noise`) and saves PNGs through
  `textures.io` into `settings.materials.texture_dir`; a manifest JSON records params → regen
  only when changed. Sets: `RunwayConcrete`, `RunwayAsphalt`, `TaxiwayAsphalt`, `ApronConcrete`,
  `Grass`, `Dirt`, `Gravel`, `PaintWear` (mask), `ChainLink` (mask), `MetalPainted`,
  `ConcreteWall`, `CorrugatedSteel`, `Roof`.
* Maps: BaseColor (sRGB), Normal (OpenGL by default, DirectX toggle), Roughness, AO, Height
  (16-bit), Metallic (constant, optional) + packed map per engine preset (ORM / Unity mask /
  glTF metallicRoughness).
* Node graph: Image textures with world-planar UV from the mesh (tiling baked into UVs) →
  Principled BSDF; vertex colour `Wear` drives rubber darkening / dirt / macro variation;
  markings use BaseColor RGB + alpha wear mask (masked/dithered).

## 5. UI (`ui/panels.py`)
N-panel tab **Airbase**: main panel with preset menu, "Build Full Base", "Rebuild", "Clear",
live-update toggle, warnings box; sub-panels (DEFAULT_CLOSED): Runway, Markings, Taxiways
(UIList), Aprons (UIList), Lighting, Signage, Structures, Perimeter & Terrain, Materials &
Textures, Export. Labels/tooltips English with Turkish translation dictionary in `i18n/`.

## 6. Testing
`tests/test_headless.py` runs under the pip `bpy` module (4.2 and 5.0): registers the addon
through a temporary script directory, builds presets, asserts object counts/dimensions,
generates 512 px textures, exports FBX/glTF, renders a Cycles preview.
