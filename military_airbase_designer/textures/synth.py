"""Procedural, seamless PBR texture-set synthesis (pure numpy + ``textures.noise``).

Every set is generated deterministically from ``settings.materials.seed`` at the resolution in
``settings.materials.resolution`` and written through ``textures.io.write_texture_set``:

* ``BaseColor``  sRGB 8-bit (authored in scene-linear, ``linear_to_srgb`` on save; absolute albedo for
  every set except ``PaintWear``, whose BaseColor is a neutral *multiplier* for the paint colour of the
  material spec, since white / yellow / red paint share the set).
* ``Normal``     8-bit Non-Color, OpenGL (+Y up) by default, green flipped when
  ``settings.materials.normal_directx``.
* ``Roughness``, ``AO`` 8-bit Non-Color (linear).  ``Height`` 16-bit Non-Color, 0..1 spans the
  ``height_range_mm`` of the set (recorded in the ``T_MAD_<Set>.json`` sidecar).
* ``Metallic``   8-bit, only for sets where it varies (chain link, corrugated / painted steel).
* packed map for ``settings.materials.engine`` via ``io.pack_maps`` (ORM / MetallicSmoothness / MaskMap /
  MetallicRoughness), ``Opacity`` for masked sets (PaintWear, ChainLink) and ``Mask`` (RGBA:
  R wear alpha, G dirt, B bead sparkle) for PaintWear.

Image convention (from ``textures.noise``): row 0 = top, column = u = base-frame X (along the runway),
row = v, all outputs tile seamlessly.  Physical scale: ``TEXTURE_SETS[name].tile`` metres per repeat,
matching ``materials.library.SPECS`` (``tile_metres`` returns it).

Physical reference: research report 05_pbr_textures.md (UFC 3-260-02 §12-6 slab jointing, AC 5370-10H
P-605 sealant recess, UFC 3-250-04 §8-8/8-9 burlap finish and grooving, AC 5320-12C grooves and rubber,
PASER-C distress catalogue, ACPA / Lagarde albedo tables).
"""
from __future__ import annotations

import json
import math
import os
import time
import zlib
from dataclasses import dataclass
from typing import Callable

import numpy as np

from ..core import constants as C
from . import io as texio
from . import noise as N

F32 = np.float32
VERSION = 13                      # bump when the look changes so cached sets regenerate
RESOLUTIONS = (512, 1024, 2048, 4096, 8192)
PREVIEW_MAX = 512
MAP_ORDER = ("BaseColor", "Normal", "Roughness", "AO", "Height", "Metallic", "Opacity", "Mask")

__all__ = [
    "TextureSetSpec", "TEXTURE_SETS", "Params", "read_params", "params_key", "generate_set", "generate_all",
    "preview_resolution", "tile_metres", "expected_maps", "sidecar_path", "outdated_sets", "below_resolution", "VERSION",
]


# =========================================================================== settings
@dataclass(frozen=True)
class Params:
    """Plain snapshot of the settings the synthesiser reads (defaults when ``settings`` is None)."""
    resolution: int = 2048
    seed: int = 1234
    age: float = 0.55
    rubber: float = 0.7
    dirt: float = 0.5
    wet: float = 0.0
    engine: str = 'UNREAL'
    normal_directx: bool = True
    slabs_per_tile: int = 2
    slab_size: float = 6.096
    grooved: bool = True
    mowing_stripes: bool = True
    concrete_tint: tuple = (1.0, 1.0, 1.0)
    asphalt_tint: tuple = (1.0, 1.0, 1.0)


def _get(settings, path: str, default):
    obj = settings
    for part in path.split("."):
        if obj is None:
            return default
        try:
            obj = getattr(obj, part)
        except AttributeError:
            return default
    return default if obj is None else obj


def _resolution_of(settings, resolution=None) -> int:
    if resolution is None:
        resolution = _get(settings, "materials.resolution", 2048)
    try:
        r = int(resolution)
    except (TypeError, ValueError):
        r = 2048
    return max(64, min(r, 8192))


def read_params(settings=None, resolution: int | None = None) -> Params:
    """Defensive read of ``settings`` (a ``MAD_BaseSettings`` or None) into a ``Params``."""
    def tint(path):
        v = _get(settings, path, (1.0, 1.0, 1.0))
        try:
            return tuple(round(float(x), 4) for x in tuple(v)[:3])
        except (TypeError, ValueError):
            return (1.0, 1.0, 1.0)
    return Params(
        resolution=_resolution_of(settings, resolution),
        seed=int(_get(settings, "materials.seed", 1234)),
        age=float(_get(settings, "materials.age", 0.55)),
        rubber=float(_get(settings, "materials.rubber", 0.7)),
        dirt=float(_get(settings, "materials.dirt", 0.5)),
        wet=float(_get(settings, "materials.wet", 0.0)),
        engine=str(_get(settings, "materials.engine", 'UNREAL')),
        normal_directx=bool(_get(settings, "materials.normal_directx", True)),
        slabs_per_tile=int(_get(settings, "materials.slabs_per_tile", 2)),
        slab_size=float(_get(settings, "runway.slab_size", 6.096)),
        grooved=bool(_get(settings, "runway.grooved", True)),
        mowing_stripes=bool(_get(settings, "terrain.mowing_stripes", True)),
        concrete_tint=tint("materials.concrete_tint"),
        asphalt_tint=tint("materials.asphalt_tint"),
    )


def preview_resolution(settings=None) -> int:
    """Resolution for the first (implicit) build: ``min(512, chosen)`` so the base appears quickly."""
    return min(PREVIEW_MAX, _resolution_of(settings))


# =========================================================================== small helpers
def _lin(hex_str: str) -> np.ndarray:
    return np.asarray(N.srgb_to_linear(np.array(N.hex_to_rgb(hex_str), dtype=np.float64)), dtype=F32)


def _grey(lum, col) -> np.ndarray:
    """(H,W) luminance factor x (3,) colour -> (H,W,3)."""
    return np.asarray(lum, F32)[..., None] * np.asarray(col, F32)[None, None, :]


def _mix(rgb: np.ndarray, col, t) -> np.ndarray:
    """Lerp an (H,W,3) image towards colour ``col`` (3,) by (H,W) weight ``t`` (in place semantics avoided)."""
    col = np.asarray(col, F32)
    t = np.asarray(t, F32)[..., None]
    return rgb + (col[None, None, :] - rgb) * t


def _mul(rgb: np.ndarray, f) -> np.ndarray:
    return rgb * np.asarray(f, F32)[..., None]


def _ss(e0, e1, x):
    return N.smoothstep(e0, e1, x)


def _signed(a) -> np.ndarray:
    """[0,1] noise -> [-1,1]."""
    return np.asarray(a, F32) * F32(2.0) - F32(1.0)


def _quantile_threshold(field: np.ndarray, fraction_above: float) -> float:
    """Threshold so that ``fraction_above`` of the pixels lie above it (deterministic, subsampled)."""
    fraction_above = float(np.clip(fraction_above, 0.0, 1.0))
    if fraction_above <= 0.0:
        return float(field.max()) + 1.0
    if fraction_above >= 1.0:
        return float(field.min()) - 1.0
    sub = field[::4, ::4] if field.shape[0] > 1024 else field
    return float(np.quantile(sub, 1.0 - fraction_above))


def _axis_m(n: int, tile: float) -> np.ndarray:
    """Pixel-centre coordinate in metres along one axis (float64)."""
    return (np.arange(n, dtype=np.float64) + 0.5) * (tile / n)


def _wrapped_delta(coord_m: np.ndarray, centre_m: float, tile: float) -> np.ndarray:
    """Signed wrapped distance (metres) from ``centre_m`` along a tileable axis."""
    return np.mod(coord_m - centre_m + 0.5 * tile, tile) - 0.5 * tile


def _box1d(n: int, tile: float, centre_m: float, length_m: float, soft_m: float) -> np.ndarray:
    """1-D wrapped soft box profile (1 inside, 0 outside)."""
    d = np.abs(_wrapped_delta(_axis_m(n, tile), centre_m, tile))
    s = max(float(soft_m), 0.5 * tile / n)
    return (F32(1.0) - N.smoothstep(0.5 * length_m - s, 0.5 * length_m + s, d)).astype(F32)


def _wavy_lines(g: "Gen", count: int, si: int, axis: str, width_m: float, wobble_m: float, gaps: float,
                res: int | None = None) -> np.ndarray:
    """Tileable wandering crack lines across the whole tile (thermal / reflective cracking).

    ``axis='y'``: lines of roughly constant x that wander with y (transverse to the paving
    direction X); ``'x'``: the opposite.  ``gaps`` (0-1) is the fraction of each line that is
    missing, so cracks start and stop like real ones."""
    res = res or g.res
    tile = g.tile
    rng = N.rng(g.s(si))
    coord = _axis_m(res, tile)
    w = max(float(width_m), 1.2 * tile / res)
    mask = np.zeros((res, res), F32)
    for _ in range(max(0, int(count))):
        x0 = rng.random() * tile
        wob = (N.fbm(1, res, 3, int(rng.integers(1 << 30)), octaves=3, normalize=False)[0] - F32(0.5)) * F32(2.0 * wobble_m)
        gate = N.fbm(1, res, 2, int(rng.integers(1 << 30)), octaves=2)[0]
        along = _ss(gaps - 0.08, gaps + 0.08, gate)
        if axis == 'y':
            d = _wrapped_delta(coord[None, :], x0, tile) - wob[:, None]
            prof = np.exp(-(d / w) ** 2).astype(F32) * along[:, None]
        else:
            d = _wrapped_delta(coord[:, None], x0, tile) - wob[None, :]
            prof = np.exp(-(d / w) ** 2).astype(F32) * along[None, :]
        mask = np.maximum(mask, prof)
    return mask


def _grid_dist_1d(n: int, tile: float, cells: int, phase: float = 0.0) -> np.ndarray:
    """Distance (metres) from pixel centres to the nearest of ``cells`` equally spaced lines."""
    pitch = tile / max(1, int(cells))
    f = np.mod(_axis_m(n, tile) - phase, pitch)
    return np.minimum(f, pitch - f).astype(F32)


def _groove_profile(n: int, tile: float, pitch_m: float, width_m: float, seed: int
                    ) -> tuple[np.ndarray, np.ndarray, int]:
    """Exact box-filtered coverage of periodic grooves along an axis + per-groove depth factor.

    ``pitch_m`` is snapped so a whole number of grooves fits the tile (AC 5320-12C 1-1/2 in c/c ->
    12.192 m = 320 grooves).  Coverage anti-aliasing means the pattern degrades to a flat grey at
    resolutions that cannot resolve the pitch instead of aliasing."""
    count = max(1, int(round(tile / pitch_m)))
    pitch = tile / count
    texel = tile / n
    phase = 0.5 * (pitch - width_m)                      # first groove centred in its slot

    def covered(x):                                      # groove length in [0, x)
        k = np.floor(x / pitch)
        return k * width_m + np.clip(x - k * pitch, 0.0, width_m)

    edges = np.arange(n + 1, dtype=np.float64) * texel - phase + pitch   # shift keeps x >= 0
    cov = (covered(edges[1:]) - covered(edges[:-1])) / texel
    centres = _axis_m(n, tile) - phase + pitch
    idx = np.floor(centres / pitch).astype(np.int64) % count
    depth = 0.75 + 0.5 * N.rng(seed).random(count)      # 60 % >= 1/4 in deep -> 4.8-7.9 mm
    return np.clip(cov, 0.0, 1.0).astype(F32), depth[idx].astype(F32), count


def _paint_discs(h: int, w: int, cx: np.ndarray, cy: np.ndarray, r_px: np.ndarray, value: np.ndarray,
                 soft: float = 0.5) -> np.ndarray:
    """Max-accumulated soft discs at explicit pixel positions (wrap-around)."""
    out = np.zeros((h, w), F32)
    for i in range(len(cx)):
        r = float(r_px[i])
        if r <= 0:
            continue
        rr = int(math.ceil(r)) + 1
        rows = np.arange(int(round(cy[i])) - rr, int(round(cy[i])) + rr + 1) % h
        cols = np.arange(int(round(cx[i])) - rr, int(round(cx[i])) + rr + 1) % w
        dy = (np.mod(rows + 0.5 - cy[i] + 0.5 * h, h) - 0.5 * h).astype(F32)[:, None]
        dx = (np.mod(cols + 0.5 - cx[i] + 0.5 * w, w) - 0.5 * w).astype(F32)[None, :]
        d = np.sqrt(dx * dx + dy * dy) / F32(r)
        s = max(float(soft), 1.0 / max(r, 1.0))
        disc = (F32(1.0) - N.smoothstep(1.0 - s, 1.0, d)) * F32(value[i])
        sub = out[np.ix_(rows, cols)]
        out[np.ix_(rows, cols)] = np.maximum(sub, disc)
    return out


# =========================================================================== generation context
class Gen:
    """Per-set generation context: resolution, physical tile, derived seeds and cached noise helpers.

    Coarse fields are generated at ``res // div`` and bilinearly upsampled (tileable), fine fields at
    full resolution; periods are given in metres and converted to lattice cells of the tile."""

    def __init__(self, spec: "TextureSetSpec", params: Params, res: int):
        self.spec = spec
        self.p = params
        self.res = int(res)
        self.tile = float(spec.tile)
        self.ppm = self.res / self.tile                       # pixels per metre
        self.texel = self.tile / self.res                     # metres per pixel
        self.seed = (int(params.seed) * 1000003 + zlib.crc32(spec.name.encode("utf-8"))) & 0x7FFFFFFF
        self._seeds = N.rng(self.seed).integers(1, (1 << 31) - 1, size=96, dtype=np.int64)

    # ------------------------------------------------------------------ seeds / units
    def s(self, i: int) -> int:
        return int(self._seeds[i % len(self._seeds)])

    def px(self, metres: float) -> float:
        return float(metres) * self.ppm

    def period(self, cell_m: float, r: int | None = None) -> int:
        """Lattice cells across the tile for a feature size in metres (at least 1, at most r/2)."""
        r = r or self.res
        return int(max(1, min(round(self.tile / max(float(cell_m), 1e-6)), max(1, r // 2))))

    def sub_res(self, div: int) -> int:
        return max(32, self.res // max(1, int(div)))

    def up(self, a: np.ndarray) -> np.ndarray:
        if a.shape[0] == self.res and a.shape[1] == self.res:
            return a
        return N.resize_bilinear(a, self.res, self.res)

    # ------------------------------------------------------------------ noise wrappers (metres in)
    def fbm(self, cell_m: float, octaves: int, si: int, div: int = 1, **kw) -> np.ndarray:
        r = self.sub_res(div)
        return self.up(N.fbm(r, r, self.period(cell_m, r), self.s(si), octaves=octaves, **kw))

    def value(self, cell_m: float, si: int, div: int = 1) -> np.ndarray:
        r = self.sub_res(div)
        return self.up(N.value_noise(r, r, self.period(cell_m, r), self.s(si)))

    def worley(self, cell_m: float, si: int, div: int = 1, feature: str = "f1", jitter: float = 1.0) -> np.ndarray:
        r = self.sub_res(div)
        return self.up(N.worley(r, r, self.period(cell_m, r), self.s(si), jitter=jitter, feature=feature))

    def aniso(self, cell_x_m: float, cell_y_m: float, octaves: int, si: int, div: int = 1, **kw) -> np.ndarray:
        r = self.sub_res(div)
        return self.up(N.anisotropic_fbm(r, r, self.period(cell_x_m, r), self.period(cell_y_m, r), self.s(si),
                                         octaves=octaves, **kw))

    def cracks(self, cell_m: float, si: int, div: int = 2, max_res: int = 2048, **kw) -> np.ndarray:
        r = min(self.sub_res(div), max_res)
        return self.up(N.cracks(r, r, self.period(cell_m, r), self.s(si), **kw))

    def streaks(self, count: int, length_m: float, width_m: float, si: int, angle: float = 0.0,
                jitter: float = 0.0, softness: float = 0.5, intensity=(0.4, 1.0)) -> np.ndarray:
        if count <= 0:
            return np.zeros((self.res, self.res), F32)
        return N.streaks(self.res, self.res, self.s(si), int(count), self.px(length_m), max(1.0, self.px(width_m)),
                         angle_deg=angle, softness=softness, angle_jitter_deg=jitter, intensity=intensity)

    def splatter(self, count: int, min_r_m: float, max_r_m: float, si: int, softness: float = 0.5,
                 intensity=(0.3, 1.0), irregular: float = 0.25) -> np.ndarray:
        if count <= 0:
            return np.zeros((self.res, self.res), F32)
        return N.splatter(self.res, self.res, self.s(si), int(count), max(1.0, self.px(min_r_m)),
                          max(1.5, self.px(max_r_m)), softness=softness, intensity=intensity, irregular=irregular)

    def rect_mask(self, count: int, size_m: tuple[float, float], si: int, soft_m: float = 0.01
                  ) -> list[tuple[np.ndarray, float]]:
        """Random wrapped rectangles: list of ((H,W) mask, random 0..1) for patches."""
        g = N.rng(self.s(si))
        out = []
        for _ in range(max(0, int(count))):
            cx, cy = g.random() * self.tile, g.random() * self.tile
            lx = g.uniform(size_m[0], size_m[1])
            ly = g.uniform(size_m[0], size_m[1])
            m = _box1d(self.res, self.tile, cy, ly, soft_m)[:, None] * _box1d(self.res, self.tile, cx, lx, soft_m)[None, :]
            out.append((m.astype(F32), float(g.random())))
        return out


# =========================================================================== generators
# Each returns a dict with:  albedo (H,W,3) linear, height_mm (H,W), rough (H,W), height_range (lo, hi) mm,
# and optionally: metallic (float | (H,W)), opacity (H,W), mask (H,W,4), ao_radius_m, ao_strength,
# normal_scale, puddles (bool), ao_mul (H,W), tint ('concrete' | 'asphalt' | None).

def _gen_concrete(g: Gen, kind: str) -> dict:
    """PCC pavement: RunwayConcrete ('runway', grooved) and ApronConcrete ('apron', calmer, oil stains).

    UFC 3-260-02 §12-6.1.3 straight continuous joints; P-605 sealant 3/8-1/2 in reservoir recessed 1/4 in;
    UFC 3-250-04 §8-8.1 burlap-drag finish (longitudinal, ~1.5 mm); AC 5320-12C §2-21 grooves 1/4 x 1/4 in at
    1-1/2 in, none within 6 in of transverse joints (UFC 3-250-04 §8-9.1); PASER-C map cracking / spalls."""
    p, res, tile, ppm = g.p, g.res, g.tile, g.ppm
    a = float(np.clip(p.age, 0.0, 1.0))
    runway = kind == "runway"
    slabs = max(1, int(round(tile / max(p.slab_size, 0.5))))     # keeps the library tile physically correct
    joint_w = 0.012                                                 # 12 mm sealant reservoir
    joint, tone, slab_h = N.slab_pattern(res, res, slabs, slabs, joint_w * ppm, g.s(0),
                                         tone_variation=0.06 if runway else 0.05,
                                         chamfer_px=max(1.0, g.px(0.014)))
    d_trans = _grid_dist_1d(res, tile, slabs)[None, :]              # distance to transverse joints (const x)
    d_long = _grid_dist_1d(res, tile, slabs)[:, None]               # longitudinal joints (const y)
    d_any = np.minimum(d_trans, d_long)

    # ---- surface fields
    burlap = g.aniso(0.30, 0.006, 3, 1, kind="value")               # longitudinal drag striations
    agg = g.worley(0.012, 2)                                        # fine aggregate / sand speckle
    speck = (F32(1.0) - agg) ** 3
    speck_sign = _signed(g.value(0.012, 3))
    macro = g.fbm(3.0, 4, 4, div=4)                                 # slab-scale drift
    mottle = g.fbm(0.35, 4, 5, div=2)                               # cement mottling
    del agg

    # ---- distress
    crack_amt = _ss(0.15, 0.85, a)
    crack = g.cracks(1.0, 6, div=2, threshold=0.035, width=1.0, octaves=4, cell_edges=0.35,
                     coverage=0.1 + 0.35 * a) * F32(crack_amt) if a > 0.1 else np.zeros((res, res), F32)
    halo = np.exp(-((d_any - 0.006) / 0.03) ** 2).astype(F32)       # 20-30 mm joint halo (stain / spall zone)
    breakup = F32(0.35) + F32(0.65) * mottle
    bleed = np.exp(-((d_any - 0.006) / 0.04) ** 2).astype(F32) * F32(_ss(0.15, 1.0, a) * (1.0 if runway else 0.6)) * breakup
    chips_f1 = g.worley(0.06, 7, div=2)
    chip_gate = _ss(0.55, 0.75, g.value(0.25, 8, div=4)) * F32(_ss(0.2, 0.9, a))
    spall = _ss(0.4, 0.2, chips_f1) * chip_gate * _ss(0.05, 0.015, d_any)
    del chips_f1, chip_gate
    if runway:
        scuff = g.streaks(int(50 * a * (0.5 + 0.5 * p.rubber)), 3.0, 0.32, 9, angle=0.0, jitter=2.5,
                          softness=0.7, intensity=(0.2, 0.6))
    else:
        scuff = g.streaks(int(24 * a), 1.6, 0.3, 9, angle=0.0, jitter=35.0, softness=0.7, intensity=(0.15, 0.45))
    stain = None
    if not runway:
        stain = np.maximum(
            g.splatter(int(5 + 12 * a), 0.12, 0.55, 10, softness=0.6, intensity=(0.45, 1.0), irregular=0.3),
            g.streaks(int(8 * a), 0.5, 0.05, 11, angle=90.0, jitter=25.0, softness=0.6, intensity=(0.3, 0.7)))
        stain *= F32(0.35) + F32(0.65) * g.fbm(0.15, 3, 12, div=2)

    # ---- grooves (runway only)
    groove = None
    if runway and p.grooved:
        cov, depth_f, count = _groove_profile(res, tile, 0.0381, 0.00635, g.s(13))
        fade = _ss(2.0, 5.0, res / count)                           # unresolvable pitch -> fades out (AC A9)
        excl = _ss(0.12, 0.18, d_trans)                             # UFC 3-250-04: none within 6 in of transverse joints
        groove = (cov[None, :] * excl).astype(F32) * F32(fade)
        groove_depth = groove * (F32(6.35) * depth_f[None, :])

    # ---- height (mm): 0 = slab surface
    h = _signed(burlap) * F32(0.75)                                 # +-0.75 mm burlap drag
    h += speck * F32(0.5)
    h -= (F32(1.0) - slab_h) * F32(6.0)                             # sealant recess 1/4 in
    h -= spall * F32(2.5)
    h -= crack * F32(0.6)
    if groove is not None:
        h -= groove_depth
    h_range = (-8.0, 2.0)

    # ---- albedo (linear)
    new_c = _lin("#AEADA7")
    old_c = _lin("#908E88")
    base = new_c + (old_c - new_c) * F32(a)
    lum = tone * (F32(1.0) + F32(0.04) * _signed(macro)) * (F32(1.0) + F32(0.03) * _signed(mottle))
    lum *= F32(1.0) + F32(0.07) * speck * speck_sign
    lum *= F32(1.0) + F32(0.025) * _signed(burlap)
    lum *= F32(1.0) - F32(0.10) * halo * breakup * F32(0.3 + 0.7 * a)
    lum *= F32(1.0) - F32(0.35) * bleed
    lum *= F32(1.0) - F32(0.32) * crack
    lum *= F32(1.0) - F32(0.22) * spall
    lum *= F32(1.0) - F32(0.35) * scuff
    if groove is not None:
        lum *= F32(1.0) - F32(0.14) * groove
    albedo = _grey(lum, base)
    sealant = _lin("#202020") + (_lin("#56524C") - _lin("#202020")) * F32(0.6 * a)   # dust greys the joint
    albedo = _mix(albedo, sealant, joint)
    if stain is not None:
        albedo = _mix(albedo, _lin("#2A2622"), F32(0.65) * stain)

    # ---- roughness
    rough = F32(0.78 + 0.10 * a) + F32(0.05) * (speck - F32(0.25)) + F32(0.02) * _signed(mottle)
    rough += F32(0.06) * crack + F32(0.05) * spall + F32(0.03) * halo * F32(a)
    rough -= F32(0.15) * scuff
    if groove is not None:
        rough += F32(0.04) * groove
    rough = rough + (F32(0.60 + 0.32 * a) - rough) * joint                  # new sealant is glossier
    if stain is not None:
        rough = rough + (F32(0.5) - rough) * stain
    rough = np.clip(rough, 0.35, 0.97)

    return {"albedo": albedo, "height_mm": h, "rough": rough, "height_range": h_range, "metallic": 0.0,
            "ao_radius_m": 0.03, "ao_strength": 1.0, "puddles": True, "tint": "concrete"}


def _gen_asphalt(g: Gen, kind: str) -> dict:
    """Dense-graded HMA: 'runway', 'taxiway' (patches, ravelling) and 'shoulder' (coarser, older, dusty).

    UFGS 32 12 15.13 Gradation 2 (12.5 mm NMAS surface), paving lanes ~3.8 m with cold-joint seams;
    ASTM D5340 cracks / crack seal (P-605 hot-pour, 50-100 mm bands); albedo new 0.04 -> aged 0.15-0.25."""
    p, res, tile = g.p, g.res, g.tile
    a = float(np.clip(p.age + (0.25 if kind == "shoulder" else 0.0), 0.0, 1.0))
    stone_m = 0.014 if kind == "shoulder" else 0.010
    f1 = g.worley(stone_m, 0)
    f1b = g.worley(stone_m * 0.5, 1)
    stones = np.clip(F32(1.0) - f1 * F32(1.6), 0.0, 1.0) ** 1.5
    small = np.clip(F32(1.0) - f1b * F32(1.7), 0.0, 1.0) ** 1.5
    stone_tone = g.value(stone_m, 2)
    del f1, f1b
    macro = g.fbm(2.0, 4, 3, div=4)
    mottle = g.fbm(0.3, 3, 4, div=2)

    # ---- paving lanes (cold joints) along X
    lanes = max(1, int(round(tile / 3.8)))
    d_seam = _grid_dist_1d(res, tile, lanes, phase=0.5 * tile / lanes)[:, None]
    seam = np.exp(-(d_seam / 0.05) ** 2).astype(F32)
    lane_id = (np.floor((_axis_m(res, tile) - 0.5 * tile / lanes) / (tile / lanes)).astype(np.int64) % lanes)   # joints at (k + 0.5) pitch
    lane_tone = (F32(1.0) + F32(0.025) * _signed(N.rng(g.s(5)).random(lanes).astype(F32)))[lane_id][:, None]
    # longitudinal joint cracking: only parts of the cold joints open up (ASTM D5340 "joint reflection")
    seam_crack = np.exp(-(d_seam / 0.006) ** 2).astype(F32) * F32(_ss(0.3, 1.0, a))
    seam_crack = seam_crack * _ss(0.42, 0.58, g.fbm(2.0, 2, 14, div=4))
    if kind == "shoulder":
        seam_crack = seam_crack * F32(0.0)
        seam = seam * F32(0.0)

    # ---- cracks + crack seal
    # Real airfield asphalt shows (1) transverse thermal cracks every 5-12 m that wander across the lanes,
    # (2) opened longitudinal joints and (3) a few meandering random cracks; block / alligator cracking
    # only on old shoulders.  Everything is routed and sealed (P-605 hot-pour, 60-100 mm band).
    crack_amt = _ss(0.1, 0.8, a)
    if a > 0.05:
        cr_res = min(g.sub_res(2), 2048)
        if kind == "shoulder":
            # old narrow shoulder strips: aggregate-rich and nearly uniform. No line cracks: the world-planar texture
            # has no relation to the strip direction, so a straight crack reads as a dashed line along a N-S shoulder
            cr = np.zeros((cr_res, cr_res), F32)
        else:
            # sealed cracks are long thin lines: a transverse thermal crack and a wandering longitudinal one per tile
            # (blobby contour cracks repeat as an obvious motif on an 6 m tile)
            trans = _wavy_lines(g, 1, 15, 'y', 0.0035, 0.09, 0.3, res=cr_res)
            longi = _wavy_lines(g, 1, 16, 'x', 0.003, 0.07, 0.5, res=cr_res)
            cr = np.maximum(trans, longi) * F32(_ss(0.25, 0.65, a))
        blur_m = 0.012
        seal_lo = N.blur(cr, max(1, int(round(blur_m * cr_res / tile))))
        seal_lo = _ss(0.06, 0.25, N.normalize01(seal_lo) if seal_lo.max() > 0 else seal_lo)
        crack = g.up(cr) * F32(crack_amt)
        seal = g.up(seal_lo) * _ss(0.35, 0.6, g.fbm(1.2, 3, 7, div=4)) * F32(crack_amt)
        del cr, seal_lo
    else:
        crack = np.zeros((res, res), F32)
        seal = crack
    seal = np.maximum(seal, seam_crack * F32(0.85))

    # ---- rubber streaks along X (low intensity; the TDZ build-up comes from vertex colour)
    rub_amt = 0.28 * p.rubber if kind == "runway" else 0.10
    rub = _ss(0.6, 0.85, g.aniso(2.0, 0.05, 4, 8, div=2, kind="value")) * F32(rub_amt)

    # ---- ravelling / patches / dust
    ravel = np.zeros((res, res), F32)
    if kind in ("taxiway", "shoulder"):
        ravel = _ss(0.66, 0.84, g.fbm(1.0, 3, 9, div=4)) * F32(0.5 * a)
    # (no rectangular utility-cut patches: anything that distinct repeats visibly with the 6 m tile)
    patch = np.zeros((res, res), F32)
    patch_tone = np.ones((res, res), F32)
    patch_edge = np.zeros((res, res), F32)
    dust = np.zeros((res, res), F32)
    if kind == "shoulder":
        dust = _ss(0.5, 0.9, g.fbm(0.7, 3, 11, div=2)) * F32(0.2 + 0.5 * p.dirt)

    # ---- height (mm)
    h = stones * F32(1.6) + small * F32(0.6) - F32(0.4)
    h -= seam * F32(0.6)
    h -= crack * F32(1.0)
    h += seal * F32(0.5)
    h += patch * F32(1.0)
    h_range = (-3.0, 2.0)

    # ---- albedo: binder + exposed aggregate (exposure grows with age)
    binder = _lin("#2E2E2D") * F32(0.8)
    binder_old = _lin("#4E4D4A") * F32(0.75)
    binder = binder + (binder_old - binder) * F32(a)
    stone_c = _lin("#8C8780")
    expo = stones * F32(0.35 + 0.55 * a) * (F32(0.6) + F32(0.8) * stone_tone) + small * F32(0.1 + 0.25 * a)
    expo = np.clip(expo + ravel * F32(0.15), 0.0, 1.0)
    albedo = _grey(np.ones((res, res), F32), binder)
    albedo = _mix(albedo, stone_c, expo)
    albedo = _mul(albedo, (F32(1.0) + F32(0.04) * _signed(macro)) * (F32(1.0) + F32(0.035) * _signed(mottle)) * lane_tone * patch_tone)
    albedo = _mul(albedo, F32(1.0) - F32(0.08) * seam)
    albedo = _mul(albedo, F32(1.0) - F32(0.4) * crack)
    albedo = _mix(albedo, _lin("#1C1C1B"), np.clip(seal + patch_edge, 0, 1) * F32(0.5))
    albedo = _mul(albedo, F32(1.0) - F32(0.55) * rub)
    if kind == "shoulder":
        albedo = _mix(albedo, _lin("#A99E8C"), dust * F32(0.18))

    # ---- roughness
    rough = F32(0.62 + 0.30 * a) + F32(0.08) * (stones - F32(0.3)) + F32(0.03) * _signed(mottle)
    rough += F32(0.06) * ravel + F32(0.04) * crack
    rough -= F32(0.25) * rub
    rough = rough + (F32(0.58) - rough) * np.clip(seal + patch_edge, 0, 1)
    rough = rough + (F32(1.0) - rough) * dust
    rough = np.clip(rough, 0.3, 0.98)
    return {"albedo": albedo, "height_mm": h, "rough": rough, "height_range": h_range, "metallic": 0.0,
            "ao_radius_m": 0.02, "ao_strength": 0.8, "puddles": True, "tint": "asphalt"}


def _gen_grass(g: Gen) -> dict:
    """Mowed airfield turf (FAA AC 150/5200-33 6-12 in; mowing stripes = mower deck width, parallel to the runway)."""
    p, res, tile = g.p, g.res, g.tile
    fine = g.fbm(0.06, 4, 0)
    clumps = F32(1.0) - g.worley(0.05, 1)
    # fine-scale only (<= 0.6 m): anything bigger than ~1 m repeats visibly on an 8 m tile; patchiness comes from the
    # non-repeating macro noise in the terrain shader
    dry = _ss(0.55, 0.85, g.fbm(0.6, 3, 2, div=2))
    dark = _ss(0.55, 0.85, g.fbm(0.45, 3, 3, div=2))
    gate = g.value(0.08, 4, div=2)
    dots = _ss(0.35, 0.1, g.worley(0.08, 5, div=2))
    clover = dots * _ss(0.8, 0.9, gate)
    dirt = dots * _ss(0.16, 0.06, gate) * F32(0.4 + 0.6 * p.dirt)
    base = _lin("#5E6E38")
    albedo = _grey(F32(0.8) + F32(0.4) * fine, base)
    albedo = _mul(albedo, F32(0.94) + F32(0.12) * clumps)
    albedo = _mix(albedo, _lin("#4A5A2E"), dark * F32(0.16))
    albedo = _mix(albedo, _lin("#8C8A55"), dry * F32(0.16))
    albedo = _mix(albedo, _lin("#7E9448"), clover * F32(0.22))
    albedo = _mix(albedo, _lin("#6B5A45"), dirt * F32(0.8))
    # Mowing stripes are NOT baked into the tile: one 8 m tile cannot hold a 7 m mower period without a
    # visible lattice once the terrain material blends a rotated second sample (anti-tiling). The shader adds them in
    # world space instead (materials.nodes, terrain.mowing_stripes).
    h = fine * F32(20.0) + clumps * F32(15.0) - dirt * F32(10.0)
    rough = F32(0.88) + F32(0.06) * _signed(fine) - F32(0.05) * dry + F32(0.05) * dirt
    return {"albedo": albedo, "height_mm": h, "rough": np.clip(rough, 0.7, 1.0), "height_range": (-10.0, 40.0),
            "metallic": 0.0, "ao_radius_m": 0.03, "ao_strength": 0.6, "normal_scale": 0.35, "puddles": False}


def _gen_dirt(g: Gen) -> dict:
    """Compacted dirt / fine gravel strip with vehicle ruts and dried-mud cracking."""
    p, res, tile = g.p, g.res, g.tile
    mottle = g.fbm(0.8, 5, 0, div=2)
    fine = g.fbm(0.05, 3, 1)
    f1 = g.worley(0.03, 2)
    stone_gate = _ss(0.5, 0.65, g.value(0.12, 3, div=2))
    stones = np.clip(F32(1.0) - f1 * F32(1.8), 0.0, 1.0) ** 1.5 * stone_gate
    del f1
    # ruts: two wavy tracks 2.0 m apart, along X
    wob = (N.fbm(1, res, 2, g.s(4), octaves=3, normalize=False)[0] - F32(0.5)) * F32(0.5)
    yy = _axis_m(res, tile).astype(F32)[:, None]
    rut = np.zeros((res, res), F32)
    for off in (-1.0, 1.0):
        d = _wrapped_delta(yy, 0.5 * tile + off, tile) - wob[None, :]
        rut = np.maximum(rut, np.exp(-(d / 0.16) ** 2).astype(F32))
    rut *= F32(0.6) + F32(0.4) * g.fbm(0.5, 3, 5, div=4)
    crack = g.cracks(0.35, 6, div=2, threshold=0.02, width=1.2, octaves=3, cell_edges=1.0, coverage=0.5)
    crack *= _ss(0.5, 0.7, g.fbm(1.5, 3, 7, div=4)) * F32(1.0 - 0.6 * p.wet)
    base = _lin("#74695A")
    albedo = _grey((F32(1.0) + F32(0.2) * _signed(mottle)) * (F32(1.0) + F32(0.08) * _signed(fine)), base)
    albedo = _mix(albedo, _lin("#A39A88"), stones * F32(0.7))
    albedo = _mul(albedo, F32(1.0) - F32(0.14) * rut)
    albedo = _mul(albedo, F32(1.0) - F32(0.3) * crack)
    h = _signed(mottle) * F32(4.0) + fine * F32(3.0) + stones * F32(7.0) * (F32(1.0) - rut) - rut * F32(35.0) - crack * F32(2.0)
    rough = F32(0.94) - F32(0.06) * rut + F32(0.04) * _signed(fine) - F32(0.08) * stones
    return {"albedo": albedo, "height_mm": h, "rough": np.clip(rough, 0.7, 1.0), "height_range": (-40.0, 12.0),
            "metallic": 0.0, "ao_radius_m": 0.04, "ao_strength": 0.7, "normal_scale": 0.5, "puddles": True}


def _gen_gravel(g: Gen) -> dict:
    """20-40 mm crushed stone (edge strips, fuel-farm and munitions yards)."""
    res, tile = g.res, g.tile
    f1 = g.worley(0.03, 0)
    ida = g.worley(0.03, 0, feature="cell_id")
    f1b = g.worley(0.014, 1)
    big = np.clip(F32(1.0) - f1 * F32(1.25), 0.0, 1.0) ** 0.6
    smallst = np.clip(F32(1.0) - f1b * F32(1.5), 0.0, 1.0) ** 0.8
    del f1, f1b
    fine = g.fbm(0.03, 3, 2)
    macro = g.fbm(1.0, 3, 3, div=4)
    stone_c = _lin("#6E695F") + (_lin("#ADA89C") - _lin("#6E695F")) * ida[..., None]
    stone_c = _mix(stone_c, _lin("#9A8E78"), _ss(0.6, 0.9, g.value(0.03, 4)) * F32(0.4))   # warm stones
    gap = _grey(F32(0.85) + F32(0.3) * macro, _lin("#5A5348"))
    cover = _ss(0.1, 0.45, big + F32(0.6) * smallst)
    albedo = gap + (stone_c - gap) * cover[..., None]
    albedo = _mul(albedo, F32(1.0) + F32(0.1) * _signed(fine))
    h = big * F32(25.0) + smallst * F32(8.0) * (F32(1.0) - big) + fine * F32(1.0)
    rough = F32(0.95) - F32(0.12) * cover + F32(0.03) * _signed(fine)
    return {"albedo": albedo, "height_mm": h, "rough": np.clip(rough, 0.6, 1.0), "height_range": (0.0, 30.0),
            "metallic": 0.0, "ao_radius_m": 0.02, "ao_strength": 0.9, "normal_scale": 0.6, "puddles": False}


def _gen_paint_wear(g: Gen) -> dict:
    """RGBA wear mask for all paint materials (AC 5340-1M: waterborne 15-20 mil film, glass beads).

    Mask R = intact paint (1) / chipped (0), G = dirt on paint, B = glass-bead sparkle.  Intact fraction is
    calibrated to 1 - 0.3 * age (0.85 at age 0.5).  BaseColor is a neutral multiplier for the spec colour."""
    p, res, tile = g.p, g.res, g.tile
    a = float(np.clip(p.age, 0.0, 1.0))
    zones = g.fbm(0.5, 4, 0, div=2)
    edges = g.worley(0.04, 1, feature="f2-f1")
    crack = g.cracks(0.3, 2, div=2, threshold=0.04, width=1.5, octaves=4, cell_edges=0.6, coverage=0.8)
    pits = _ss(0.22, 0.08, g.worley(0.006, 3))
    # worn zones + isolated pin-prick chips; the Voronoi cell borders / cracks only as faint hints (a strong
    # crackle reads as dried mud on a wide white stripe)
    field = zones * F32(0.62) + _ss(0.5, 0.05, edges) * F32(0.12) + pits * F32(0.4) + crack * F32(0.06)
    field += g.fbm(0.04, 3, 4) * F32(0.15)
    worn_frac = 0.3 * a
    thr = _quantile_threshold(field, worn_frac)
    worn = _ss(thr - 0.03, thr + 0.03, field)
    intact = F32(1.0) - worn
    dirt = _ss(0.45, 0.8, g.fbm(0.25, 4, 5, div=2)) * F32(0.3 + 0.7 * p.dirt) * F32(0.4 + 0.6 * a)
    dirt = np.clip(dirt + F32(0.35) * crack * F32(a), 0.0, 1.0)
    beads = _ss(0.16, 0.04, g.worley(0.004, 6)) * intact * F32(1.0 - 0.6 * a)
    chalk = g.fbm(0.3, 3, 7, div=4)
    lum = (F32(0.90) + F32(0.10) * chalk) * (F32(1.0) - F32(0.35) * dirt)
    albedo = _grey(lum, np.ones(3, F32))
    h = intact * F32(0.4) + beads * F32(0.12) + g.fbm(0.02, 2, 8) * F32(0.05)
    rough = F32(0.72) * intact + F32(0.86) * worn + F32(0.1) * dirt - F32(0.14) * beads + F32(0.04) * _signed(chalk)   # airfield paint is matte
    mask = np.stack([intact, dirt, beads, np.ones((res, res), F32)], axis=2)
    return {"albedo": albedo, "height_mm": h, "rough": np.clip(rough, 0.3, 0.95), "height_range": (0.0, 0.6),
            "metallic": 0.0, "opacity": intact, "mask": mask, "ao_radius_m": 0.004, "ao_strength": 0.3,
            "puddles": False}


def _gen_chain_link(g: Gen) -> dict:
    """Galvanised chain-link fabric: 2 in (50 mm) diamond mesh, 9-gauge (3 mm) wire, tile 1 m = 20 diamonds."""
    p, res, tile = g.p, g.res, g.tile
    n = max(1, int(round(tile / 0.05)))
    period = tile / (n * math.sqrt(2.0))                             # line spacing along the diagonals
    x = _axis_m(res, tile)
    y = _axis_m(res, tile)
    u = (x[None, :] + y[:, None]) / math.sqrt(2.0)
    v = (x[None, :] - y[:, None]) / math.sqrt(2.0)
    d1 = np.abs(np.mod(u, period) - 0.5 * period).astype(F32)
    d2 = np.abs(np.mod(v, period) - 0.5 * period).astype(F32)
    r = 0.0015                                                       # wire radius 1.5 mm
    t = float(g.texel)
    cov1 = np.clip((F32(r) - d1) / F32(t) + F32(0.5), 0.0, 1.0)
    cov2 = np.clip((F32(r) - d2) / F32(t) + F32(0.5), 0.0, 1.0)
    opacity = np.maximum(cov1, cov2)
    h1 = np.sqrt(np.clip(F32(r * r) - d1 * d1, 0.0, None))
    h2 = np.sqrt(np.clip(F32(r * r) - d2 * d2, 0.0, None))
    # woven look: alternate which family is on top per diamond row
    row = np.floor((u + v) / (2.0 * period)).astype(np.int64)
    top1 = (row % 2 == 0)
    h = np.where(top1, np.maximum(h1 + F32(r) * (cov1 > 0.5) * F32(0.5), h2), np.maximum(h2 + F32(r) * (cov2 > 0.5) * F32(0.5), h1))
    h = (h * F32(1000.0)).astype(F32)
    del d1, d2, u, v, h1, h2
    spangle = g.fbm(0.04, 3, 0)
    rust = _ss(0.72, 0.9, g.fbm(0.2, 3, 1, div=2)) * F32(np.clip(p.age, 0, 1))
    albedo = _grey(F32(0.85) + F32(0.3) * spangle, _lin("#8A8E92"))
    albedo = _mix(albedo, _lin("#7A4020"), rust * F32(0.8))
    rough = F32(0.42) + F32(0.2) * spangle + F32(0.45) * rust
    metallic = F32(0.9) - F32(0.8) * rust
    return {"albedo": albedo, "height_mm": h, "rough": np.clip(rough, 0.2, 0.95), "height_range": (0.0, 3.0),
            "metallic": metallic, "opacity": opacity, "ao_const": 1.0, "puddles": False}


def _gen_concrete_wall(g: Gen) -> dict:
    """Cast-in-place concrete: 1.5 m form panels, form-tie holes on a 0.6 m grid, rust and water streaks."""
    p, res, tile = g.p, g.res, g.tile
    a = float(np.clip(p.age, 0.0, 1.0))
    macro = g.fbm(1.2, 4, 0, div=4)
    mottle = g.fbm(0.25, 4, 1, div=2)
    agg = (F32(1.0) - g.worley(0.008, 2)) ** 3
    agg_sign = _signed(g.value(0.008, 3))
    panels = max(1, int(round(tile / 1.5)))
    seam = np.minimum(_grid_dist_1d(res, tile, panels)[None, :], _grid_dist_1d(res, tile, panels)[:, None])
    seam = (F32(1.0) - _ss(0.002, 0.005, seam)).astype(F32)
    ties = max(1, int(round(tile / 0.6)))
    pitch = tile / ties
    dxh = np.abs(np.mod(_axis_m(res, tile) - 0.5 * pitch, pitch) - 0.5 * pitch).astype(F32)[None, :]
    dyh_s = np.mod(_axis_m(res, tile) - 0.5 * pitch, pitch).astype(F32)[:, None]     # distance below the hole row
    dyh = np.minimum(dyh_s, F32(pitch) - dyh_s)
    dist = np.sqrt(dxh * dxh + dyh * dyh)
    hole = F32(1.0) - _ss(0.009, 0.016, dist)
    ix = (np.floor(_axis_m(res, tile) / pitch).astype(np.int64) % ties)
    iy = (np.floor((_axis_m(res, tile) - 0.5 * pitch) / pitch).astype(np.int64) % ties)
    lens = (0.08 + 0.35 * N.rng(g.s(4)).random((ties, ties))).astype(F32) * F32(a)
    L = lens[iy[:, None], ix[None, :]]                                    # per-hole streak length
    streak = (F32(1.0) - N.smoothstep_arr(L * F32(0.5), np.maximum(L, F32(1e-3)), dyh_s)) \
        * np.exp(-(dxh / F32(0.018)) ** 2) * _ss(0.0, 0.02, dyh_s)
    streak *= F32(0.5) + F32(0.5) * mottle
    water = g.streaks(int(10 + 10 * a), 1.2, 0.18, 5, angle=90.0, jitter=6.0, softness=0.8, intensity=(0.15, 0.45)) * F32(a)
    lum = (F32(1.0) + F32(0.06) * _signed(macro)) * (F32(1.0) + F32(0.05) * _signed(mottle)) * (F32(1.0) + F32(0.06) * agg * agg_sign)
    lum *= F32(1.0) - F32(0.45) * hole - F32(0.35) * seam - F32(0.25) * water
    albedo = _grey(lum, _lin("#A8A6A0"))
    albedo = _mix(albedo, _lin("#8A4A22"), np.clip(streak * F32(0.75), 0.0, 1.0))
    h = _signed(mottle) * F32(0.6) + agg * F32(0.4) - hole * F32(15.0) - seam * F32(2.0)
    rough = F32(0.86) + F32(0.04) * _signed(mottle) + F32(0.05) * agg + F32(0.06) * streak + F32(0.03) * water
    return {"albedo": albedo, "height_mm": h, "rough": np.clip(rough, 0.6, 0.98), "height_range": (-15.0, 2.0),
            "metallic": 0.0, "ao_radius_m": 0.02, "ao_strength": 0.8, "puddles": False, "tint": "concrete"}


def _gen_corrugated(g: Gen) -> dict:
    """Corrugated steel sheet: 76 mm pitch sinusoid (vertical ridges), fasteners, paint chips and rust."""
    p, res, tile = g.p, g.res, g.tile
    a = float(np.clip(p.age, 0.0, 1.0))
    n = max(1, int(round(tile / 0.076)))
    x = _axis_m(res, tile)
    prof = (F32(9.0) * (F32(1.0) + np.cos(2.0 * math.pi * x * n / tile))).astype(F32)   # 0..18 mm, crest at x=0
    h = np.broadcast_to(prof[None, :], (res, res)).astype(F32)
    crest = np.broadcast_to((F32(0.5) + F32(0.5) * np.cos(2.0 * math.pi * x * n / tile)).astype(F32)[None, :], (res, res))
    # fasteners: every 4th crest, rows every tile/3
    rows = 3
    fast_cols = _grid_dist_1d(res, tile, max(1, n // 4))[None, :]
    fast_rows = _grid_dist_1d(res, tile, rows, phase=0.5 * tile / rows)[:, None]
    fdist = np.sqrt(fast_cols ** 2 + fast_rows ** 2)
    fastener = F32(1.0) - _ss(0.005, 0.008, fdist)
    h = h + fastener * F32(2.5)
    zones = g.fbm(0.3, 4, 0, div=2)
    edges = g.worley(0.05, 1, feature="f2-f1", div=2)
    field = (F32(1.0) - zones) * F32(0.6) + _ss(0.5, 0.05, edges) * F32(0.4) + g.fbm(0.03, 3, 2) * F32(0.15)
    field += (F32(1.0) - crest) * F32(0.15)                                # troughs hold water -> chip more
    thr = _quantile_threshold(field, 0.14 * a)
    chip = _ss(thr - 0.02, thr + 0.02, field)
    rim = np.clip(N.blur(chip, max(1, int(round(g.px(0.006))))) - chip, 0.0, 1.0) * F32(3.0)
    rim = np.clip(rim, 0.0, 1.0)
    rust_streak = _ss(0.62, 0.85, g.aniso(0.02, 0.6, 4, 3, div=2, kind="value")) * (F32(1.0) - crest) * F32(a)
    fast_rust = np.clip(N.blur(fastener, max(1, int(round(g.px(0.01))))) * F32(4.0), 0.0, 1.0) * F32(_ss(0.3, 1.0, a))
    rust = np.clip(rim + rust_streak + fast_rust * F32(0.6), 0.0, 1.0) * (F32(1.0) - chip * F32(0.5))
    paint = _lin("#8C9094")
    albedo = _grey((F32(1.0) + F32(0.05) * _signed(zones)) * (F32(1.0) + F32(0.06) * crest * F32(a)), paint)
    albedo = _mix(albedo, np.array([0.5, 0.5, 0.52], F32), chip)
    albedo = _mix(albedo, _lin("#7A3E1C"), rust)
    albedo = _mix(albedo, np.array([0.55, 0.55, 0.56], F32), fastener * (F32(1.0) - rust))
    rough = F32(0.5) + F32(0.08) * _signed(zones) + (F32(0.35) - F32(0.5)) * chip + F32(0.45) * rust
    metallic = F32(0.1) + F32(0.85) * chip + F32(0.7) * fastener
    metallic = metallic * (F32(1.0) - rust)
    return {"albedo": albedo, "height_mm": h, "rough": np.clip(rough, 0.25, 0.95), "height_range": (0.0, 21.0),
            "metallic": np.clip(metallic, 0.0, 1.0), "ao_radius_m": 0.02, "ao_strength": 0.5, "puddles": False}


def _gen_metal_painted(g: Gen) -> dict:
    """Painted steel (NATO grey): orange-peel, light scratches, shallow dents, dust, a few rust spots."""
    p, res, tile = g.p, g.res, g.tile
    a = float(np.clip(p.age, 0.0, 1.0))
    peel = g.fbm(0.004, 2, 0)
    dust = _ss(0.45, 0.8, g.fbm(0.4, 4, 1, div=2)) * F32(0.3 + 0.7 * p.dirt)
    scratch = g.streaks(int(20 + 40 * a), 0.15, 0.0015, 2, angle=0.0, jitter=180.0, softness=0.3, intensity=(0.4, 1.0))
    dents = g.splatter(int(4 + 6 * a), 0.02, 0.06, 3, softness=0.9, intensity=(0.5, 1.0), irregular=0.2)
    rust = g.splatter(int(12 * a), 0.003, 0.008, 4, softness=0.5, intensity=(0.6, 1.0), irregular=0.3)
    chips = _ss(0.8, 0.9, g.fbm(0.05, 3, 5)) * _ss(0.6, 0.8, g.fbm(0.4, 3, 6, div=4)) * F32(a)
    base = _lin(C.NATO_GREY)
    albedo = _grey(F32(1.0) + F32(0.03) * _signed(peel), base)
    albedo = _mix(albedo, _lin("#A8A9A6"), dust * F32(0.3))
    albedo = _mix(albedo, np.array([0.5, 0.5, 0.52], F32), np.clip(scratch + chips, 0, 1))
    albedo = _mix(albedo, _lin("#7A3E1C"), rust)
    h = _signed(peel) * F32(0.05) - dents * F32(1.5) - scratch * F32(0.1) - chips * F32(0.3)
    rough = F32(0.5) + F32(0.06) * _signed(peel) + F32(0.2) * dust + (F32(0.35) - F32(0.5)) * scratch + F32(0.45) * rust
    metallic = F32(0.05) + F32(0.9) * np.clip(scratch + chips, 0, 1) * (F32(1.0) - rust)
    return {"albedo": albedo, "height_mm": h, "rough": np.clip(rough, 0.25, 0.95), "height_range": (-2.0, 0.3),
            "metallic": np.clip(metallic, 0, 1), "ao_radius_m": 0.01, "ao_strength": 0.4, "puddles": False}


def _gen_roof(g: Gen) -> dict:
    """Built-up bitumen membrane roof: 1 m sheet laps along X, partial gravel ballast, ponding stains."""
    p, res, tile = g.p, g.res, g.tile
    a = float(np.clip(p.age, 0.0, 1.0))
    mottle = g.fbm(0.4, 4, 0, div=2)
    laps = max(1, int(round(tile / 1.0)))
    d_lap = _grid_dist_1d(res, tile, laps)[:, None]
    lap_pos = np.mod(_axis_m(res, tile), tile / laps).astype(F32)[:, None]   # 0 at the lap edge, increasing
    lap = (F32(1.0) - _ss(0.07, 0.085, lap_pos)).astype(F32)                 # raised 80 mm overlap band
    bleed = (F32(1.0) - _ss(0.006, 0.014, d_lap)).astype(F32)                # bitumen bleed line
    cover = _ss(0.35, 0.65, g.fbm(0.6, 4, 1, div=2))
    f1 = g.worley(0.012, 2)
    stone = np.clip(F32(1.0) - f1 * F32(1.5), 0.0, 1.0) * cover
    del f1
    stone_tone = g.value(0.012, 3)
    pond = _ss(0.55, 0.75, g.fbm(1.0, 3, 4, div=4)) * F32(a)
    membrane = _lin("#55524E")
    albedo = _grey(F32(1.0) + F32(0.08) * _signed(mottle), membrane)
    albedo = _mul(albedo, F32(1.0) - F32(0.15) * pond)
    albedo = _mix(albedo, _lin("#3A4A2A"), pond * F32(0.2))
    stone_c = _lin("#8F8A80")
    stone_rgb = _grey(F32(0.8) + F32(0.4) * stone_tone, stone_c)
    albedo = albedo + (stone_rgb - albedo) * (stone * F32(0.9))[..., None]
    del stone_rgb
    albedo = _mix(albedo, _lin("#1E1D1B"), bleed * F32(0.7))
    albedo = _mul(albedo, F32(1.0) - F32(0.05) * lap)
    h = lap * F32(3.0) + stone * F32(8.0) + _signed(mottle) * F32(0.5)
    rough = F32(0.8) + F32(0.05) * _signed(mottle) + F32(0.1) * stone - F32(0.3) * bleed - F32(0.15) * pond * F32(p.wet)
    return {"albedo": albedo, "height_mm": h, "rough": np.clip(rough, 0.3, 0.98), "height_range": (0.0, 12.0),
            "metallic": 0.0, "ao_radius_m": 0.015, "ao_strength": 0.8, "puddles": True}


# =========================================================================== texture set table
# =========================================================================== decals atlas
def _cell_uv(cr: int) -> tuple[np.ndarray, np.ndarray]:
    """(u row-vector, v column-vector) pixel-centre coordinates of a cr x cr cell in [0, 1]."""
    a = ((np.arange(cr, dtype=np.float64) + 0.5) / cr).astype(F32)
    return a[None, :], a[:, None]


def _decal_skid(cr: int, seed: int, twin: bool = False, start_blob: bool = False, fade_both: bool = False,
                thin: bool = False) -> np.ndarray:
    """Tyre rubber streak along u (u = 0 is the touchdown point)."""
    u, v = _cell_uv(cr)
    wob = (N.fbm(1, cr, 3, seed + 1, octaves=3)[0] - F32(0.5)) * F32(0.05)
    hw = F32(0.16 if thin else (0.17 if twin else 0.27)) * (F32(1.0) + F32(0.3) * (N.fbm(1, cr, 4, seed + 2, octaves=2)[0] - F32(0.5)))

    def lane(vc: float) -> np.ndarray:
        d = np.abs(v - F32(vc) - wob[None, :]) / hw[None, :]
        return np.exp(-(d * d) * F32(1.4)).astype(F32)

    prof = np.maximum(lane(0.31), lane(0.69)) if twin else lane(0.5)
    if start_blob:
        along = _ss(0.0, 0.04, u) * (F32(0.25) + F32(0.75) * np.exp(-np.clip(u - F32(0.08), 0, None) / F32(0.22)))
        along = along * (F32(1.0) - _ss(0.8, 1.0, u))
    elif fade_both:
        along = np.sin(np.pi * u).astype(F32) ** F32(0.7)
    else:
        along = _ss(0.0, 0.05, u) * (F32(1.0) - F32(0.5) * u) * (F32(1.0) - _ss(0.82, 1.0, u))
    tread = F32(0.65) + F32(0.35) * N.anisotropic_fbm(cr, cr, 2, 28, seed + 3, octaves=3)    # streaky along u
    breakup = F32(0.8) + F32(0.2) * N.fbm(cr, cr, 6, seed + 4, octaves=3)
    a = prof * along * tread * breakup
    return np.clip(a / max(float(a.max()), 1e-3), 0.0, 1.0).astype(F32)


def _decal_stain(cr: int, seed: int, radius: float = 0.3, irregular: float = 0.3, soft: float = 0.12,
                 drips: int = 0, strength: float = 1.0) -> np.ndarray:
    u, v = _cell_uv(cr)
    r = np.sqrt((u - F32(0.5)) ** 2 + (v - F32(0.5)) ** 2)
    noise = N.fbm(cr, cr, 3, seed, octaves=4) - F32(0.5)
    edge_r = F32(radius) * (F32(1.0) + F32(2.0 * irregular) * noise)
    alpha = F32(1.0) - N.smoothstep_arr(edge_r * F32(1.0 - soft), edge_r * F32(1.0 + soft), r)
    mottle = F32(0.75) + F32(0.25) * N.fbm(cr, cr, 10, seed + 1, octaves=3)
    centre = F32(0.7) + F32(0.3) * np.clip(F32(1.0) - r / np.maximum(edge_r, F32(1e-3)), 0.0, 1.0)
    alpha = alpha * mottle * centre
    rng = N.rng(seed + 7)
    for _ in range(max(0, int(drips))):
        ang = rng.uniform(0.0, 2.0 * np.pi)
        x0 = 0.5 + np.cos(ang) * radius * 0.8
        y0 = 0.5 + np.sin(ang) * radius * 0.8
        length = rng.uniform(0.08, 0.25)
        w = rng.uniform(0.012, 0.03)
        dy = v - F32(y0)
        drip = np.exp(-((u - F32(x0)) / F32(w)) ** 2) * ((dy >= 0) & (dy <= length)).astype(F32) * (F32(1.0) - dy / F32(length))
        alpha = np.maximum(alpha, np.clip(drip, 0.0, 1.0) * F32(0.8))
    return np.clip(alpha * F32(strength), 0.0, 1.0).astype(F32)


def _decal_drips(cr: int, seed: int, count: int = 8) -> np.ndarray:
    u, v = _cell_uv(cr)
    rng = N.rng(seed)
    alpha = np.zeros((cr, cr), F32)
    for _ in range(count):
        cx, cy = rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8)
        rad = rng.uniform(0.025, 0.07)
        r = np.sqrt((u - F32(cx)) ** 2 + (v - F32(cy)) ** 2)
        alpha = np.maximum(alpha, (F32(1.0) - N.smoothstep_arr(F32(rad * 0.7), F32(rad), r)) * F32(rng.uniform(0.5, 1.0)))
    return alpha


def _decal_exhaust(cr: int, seed: int) -> np.ndarray:
    u, v = _cell_uv(cr)
    half = F32(0.07) + F32(0.3) * u
    core = np.exp(-((v - F32(0.5)) / half) ** 2 * F32(1.6))
    along = _ss(0.0, 0.05, u) * (F32(1.0) - F32(0.85) * _ss(0.3, 1.0, u))
    wisps = F32(0.6) + F32(0.4) * N.anisotropic_fbm(cr, cr, 3, 12, seed, octaves=4)
    a = core * along * wisps
    return np.clip(a / max(float(a.max()), 1e-3), 0.0, 1.0).astype(F32)


def _decal_rust(cr: int, seed: int) -> np.ndarray:
    u, v = _cell_uv(cr)
    wob = (N.fbm(1, cr, 3, seed, octaves=3)[0] - F32(0.5)) * F32(0.04)
    w = F32(0.035) * (F32(1.0) + F32(0.5) * (N.fbm(1, cr, 5, seed + 1, octaves=2)[0] - F32(0.5)))
    prof = np.exp(-((u - F32(0.5) - wob[:, None]) / w[:, None]) ** 2)
    along = _ss(0.05, 0.15, v) * (F32(1.0) - _ss(0.6, 0.95, v))
    return np.clip(prof * along * (F32(0.6) + F32(0.4) * N.fbm(cr, cr, 8, seed + 2, octaves=3)), 0.0, 1.0).astype(F32)


def _gen_decals(g: Gen) -> dict:
    """Decal atlas (4 x 4 cells): tyre rubber, oil / hydraulic stains, exhaust soot, grime.

    Cell table: ``core.constants.DECAL_ATLAS``.  Opacity carries the shape; BaseColor / Roughness are the
    deposit itself (rubber albedo ~0.02, slightly glossy; oil darker and glossier; soot matte)."""
    p, res_out = g.p, g.res
    n = C.DECAL_GRID
    cr = max(16, res_out // n)
    res = n * cr                       # the atlas is generated on a whole number of cells, resampled at the end
    base_seed = g.s(0)
    albedo = np.zeros((res, res, 3), F32)
    rough = np.full((res, res), 0.8, F32)
    opacity = np.zeros((res, res), F32)
    rubber = _lin("#171717")
    oil = _lin("#100D0A")
    soot = _lin("#1B1917")
    grime = _lin("#3A3630")
    rust = _lin("#5A3A20")
    cells = {
        "skid": (lambda s: _decal_skid(cr, s), rubber, 0.55),
        "skid_fade": (lambda s: _decal_skid(cr, s, fade_both=True), rubber, 0.58),
        "skid_twin": (lambda s: _decal_skid(cr, s, twin=True), rubber, 0.55),
        "touch_blob": (lambda s: _decal_skid(cr, s, start_blob=True), rubber, 0.5),
        "oil_round": (lambda s: _decal_stain(cr, s, radius=0.3, irregular=0.2, soft=0.1), oil, 0.3),
        "oil_spread": (lambda s: _decal_stain(cr, s, radius=0.26, irregular=0.45, soft=0.15, drips=3), oil, 0.32),
        "drip_cluster": (lambda s: _decal_drips(cr, s, 9), oil, 0.35),
        "fuel_spill": (lambda s: _decal_stain(cr, s, radius=0.38, irregular=0.35, soft=0.45, strength=0.55), _lin("#2A2622"), 0.5),
        "exhaust": (lambda s: _decal_exhaust(cr, s), soot, 0.75),
        "scuff_arc": (lambda s: _decal_skid(cr, s, fade_both=True, thin=True) * F32(0.8), rubber, 0.6),
        "grime": (lambda s: _decal_stain(cr, s, radius=0.36, irregular=0.4, soft=0.5, strength=0.45), grime, 0.85),
        "rust_streak": (lambda s: _decal_rust(cr, s), rust, 0.8),
    }
    for i, (name, (col, row)) in enumerate(C.DECAL_ATLAS.items()):
        fn, colour, rgh = cells[name]
        a = fn(base_seed + 31 * (i + 1))
        var = F32(0.85) + F32(0.3) * N.fbm(cr, cr, 5, base_seed + 97 * (i + 1), octaves=3)
        y0, x0 = row * cr, col * cr
        opacity[y0:y0 + cr, x0:x0 + cr] = a
        albedo[y0:y0 + cr, x0:x0 + cr] = _grey(var, colour)
        rough[y0:y0 + cr, x0:x0 + cr] = F32(rgh) + F32(0.08) * (var - F32(1.0))
    albedo, rough, opacity = np.clip(albedo, 0.0, 1.0), np.clip(rough, 0.2, 0.95), np.clip(opacity, 0.0, 1.0)
    if res != res_out:                                               # e.g. 100 px: resample the whole atlas
        albedo = np.stack([N.resize_bilinear(albedo[..., k], res_out, res_out) for k in range(3)], axis=2)
        rough = N.resize_bilinear(rough, res_out, res_out)
        opacity = N.resize_bilinear(opacity, res_out, res_out)
    zeros = np.zeros((res_out, res_out), F32)
    return {"albedo": albedo, "height_mm": zeros, "rough": rough,
            "height_range": (0.0, 1.0), "metallic": 0.0, "opacity": opacity, "ao_const": 1.0, "puddles": False,
            "normal_scale": 0.0}


@dataclass(frozen=True)
class TextureSetSpec:
    name: str
    tile: float                                   # metres per repeat (== materials.library SPECS tile)
    generator: Callable[[Gen], dict]
    maps: tuple[str, ...] = ("BaseColor", "Normal", "Roughness", "AO", "Height")
    has_opacity: bool = False
    family: str = "pavement"                      # pavement | terrain | paint | structure
    relevant: tuple[str, ...] = ()                # extra Params fields that affect the look (for params_key)


_BASE_MAPS = ("BaseColor", "Normal", "Roughness", "AO", "Height")
_PAVE = ("age", "rubber", "dirt", "wet", "concrete_tint", "asphalt_tint")

TEXTURE_SETS: dict[str, TextureSetSpec] = {s.name: s for s in [
    TextureSetSpec("RunwayConcrete", 12.19, lambda g: _gen_concrete(g, "runway"), _BASE_MAPS, False, "pavement",
                   _PAVE + ("slabs_per_tile", "slab_size", "grooved")),
    TextureSetSpec("RunwayAsphalt", 6.0, lambda g: _gen_asphalt(g, "runway"), _BASE_MAPS, False, "pavement", _PAVE),
    TextureSetSpec("TaxiwayAsphalt", 6.0, lambda g: _gen_asphalt(g, "taxiway"), _BASE_MAPS, False, "pavement", _PAVE),
    TextureSetSpec("ApronConcrete", 12.19, lambda g: _gen_concrete(g, "apron"), _BASE_MAPS, False, "pavement",
                   _PAVE + ("slabs_per_tile", "slab_size")),
    TextureSetSpec("ShoulderAsphalt", 5.0, lambda g: _gen_asphalt(g, "shoulder"), _BASE_MAPS, False, "pavement", _PAVE),
    TextureSetSpec("Grass", 8.0, _gen_grass, _BASE_MAPS, False, "terrain", ("dirt",)),
    TextureSetSpec("Dirt", 4.0, _gen_dirt, _BASE_MAPS, False, "terrain", ("dirt", "wet")),
    TextureSetSpec("Gravel", 3.0, _gen_gravel, _BASE_MAPS, False, "terrain", ("wet",)),
    TextureSetSpec("PaintWear", 2.0, _gen_paint_wear, _BASE_MAPS + ("Opacity", "Mask"), True, "paint", ("age", "dirt")),
    TextureSetSpec("Decals", 1.0, _gen_decals, _BASE_MAPS + ("Opacity",), True, "decal", ()),
    TextureSetSpec("ChainLink", 1.0, _gen_chain_link, _BASE_MAPS + ("Metallic", "Opacity"), True, "structure", ("age",)),
    TextureSetSpec("ConcreteWall", 3.0, _gen_concrete_wall, _BASE_MAPS, False, "structure", ("age", "concrete_tint")),
    TextureSetSpec("CorrugatedSteel", 2.0, _gen_corrugated, _BASE_MAPS + ("Metallic",), False, "structure", ("age",)),
    TextureSetSpec("MetalPainted", 2.0, _gen_metal_painted, _BASE_MAPS + ("Metallic",), False, "structure", ("age", "dirt")),
    TextureSetSpec("Roof", 3.0, _gen_roof, _BASE_MAPS, False, "structure", ("age", "wet")),
]}

_PACKED_NAME = {'UNREAL': "ORM", 'UNITY_URP': "MetallicSmoothness", 'UNITY_HDRP': "MaskMap", 'GLTF': "MetallicRoughness"}


def tile_metres(name: str, settings=None) -> float:
    """Physical size (m) of one repeat of the set (matches ``materials.library`` SPECS)."""
    return TEXTURE_SETS[name].tile


def expected_maps(name: str, settings=None, params: Params | None = None) -> list[str]:
    """Every map file ``generate_set`` writes for this set with these settings (incl. the packed map)."""
    spec = TEXTURE_SETS[name]
    p = params or read_params(settings)
    maps = list(spec.maps)
    packed = _PACKED_NAME.get(p.engine)
    if packed:
        maps.append(packed)
    return maps


def params_key(name: str, settings=None, resolution: int | None = None, params: Params | None = None) -> str:
    """Stable string of everything that changes the set's pixels (compared through the manifest)."""
    spec = TEXTURE_SETS[name]
    p = params or read_params(settings, resolution)
    d = {"name": name, "version": VERSION, "resolution": int(p.resolution), "seed": int(p.seed),
         "engine": p.engine, "normal_directx": bool(p.normal_directx), "tile": spec.tile, "wet": round(float(p.wet), 4)}
    for k in spec.relevant:
        v = getattr(p, k)
        d[k] = round(float(v), 4) if isinstance(v, float) else (list(v) if isinstance(v, tuple) else v)
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def outdated_sets(settings=None, directory: str | None = None, names=None) -> list[str]:
    """Sets whose files are missing or whose manifest entry was written with other parameters or an
    older synthesiser version.  The resolution is *not* compared: a higher-resolution set written by
    "Generate Textures" stays valid for a build at preview resolution."""
    directory = directory or texio.texture_dir(settings)
    p = read_params(settings)
    manifest = texio.read_manifest(directory)
    out: list[str] = []
    for name in (list(names) if names else list(TEXTURE_SETS.keys())):
        entry = manifest.get(name)
        try:
            stored = json.loads(entry.get("key", "{}")) if entry else {}
        except (ValueError, AttributeError):
            stored = {}
        wanted = json.loads(params_key(name, params=p))
        stored_res = stored.pop("resolution", 0)
        wanted.pop("resolution", None)
        if not stored or stored != wanted or int(stored_res or 0) < 1:
            out.append(name)
            continue
        if not all(os.path.exists(texio.map_path(directory, name, m)) for m in expected_maps(name, params=p)):
            out.append(name)
    return out


def below_resolution(directory: str, resolution: int, names=None) -> bool:
    """True when any (present) set in ``directory`` was written below ``resolution`` px."""
    manifest = texio.read_manifest(directory)
    for name in (list(names) if names else list(TEXTURE_SETS.keys())):
        entry = manifest.get(name)
        if not entry:
            continue
        try:
            stored = int(json.loads(entry.get("key", "{}")).get("resolution", 0))
        except (ValueError, AttributeError, TypeError):
            continue
        if 0 < stored < int(resolution):
            return True
    return False


def sidecar_path(directory: str, name: str) -> str:
    return os.path.join(directory, f"{C.TEX_PREFIX}{name}.json")


# =========================================================================== finishing (common post-process)
def _finish(g: Gen, out: dict) -> dict[str, np.ndarray]:
    """Height -> Normal/AO, wetness, tints, sRGB encode, packing. Returns {map: array} ready for io."""
    p = g.p
    res = g.res
    albedo = np.asarray(out["albedo"], F32)
    h_mm = np.asarray(out["height_mm"], F32)
    rough = np.asarray(out["rough"], F32)
    lo, hi = out["height_range"]
    h01 = np.clip((h_mm - F32(lo)) / F32(max(hi - lo, 1e-6)), 0.0, 1.0).astype(F32)
    del h_mm

    # ---- wetness: darker, glossier, puddles in blurred-low areas with a flattened normal
    wet = float(np.clip(p.wet, 0.0, 1.0))
    if wet > 0.0:
        albedo = albedo * F32(1.0 - 0.3 * wet)
        rough = rough + (F32(0.3) - rough) * F32(0.7 * wet)
        if out.get("puddles"):
            low = N.blur(h01, max(2, int(round(g.px(0.15) / 2))))
            depth = low - h01                                         # positive where the pixel sits below its surroundings
            level = float(np.quantile(depth[::4, ::4], 1.0 - 0.45 * wet))
            puddle = _ss(level, level + 0.06, depth).astype(F32)
            h01 = h01 + (low - h01) * puddle                         # flat water surface
            rough = rough + (F32(0.06) - rough) * puddle
            albedo = _mul(albedo, F32(1.0) - F32(0.35) * puddle)
            del low, depth, puddle

    # ---- tints (Blender colour props are scene-linear)
    tint = out.get("tint")
    if tint == "concrete":
        albedo = albedo * np.asarray(p.concrete_tint, F32)[None, None, :]
    elif tint == "asphalt":
        albedo = albedo * np.asarray(p.asphalt_tint, F32)[None, None, :]

    # ---- normal: physically scaled slope (height range in metres over one texel)
    strength = (hi - lo) * 1e-3 / g.texel * float(out.get("normal_scale", 1.0))
    normal = N.height_to_normal(h01, strength=strength, tileable=True, flip_y=bool(p.normal_directx))

    # ---- AO
    if "ao_const" in out:
        ao = np.full((res, res), float(out["ao_const"]), F32)
    else:
        radius = max(2, int(round(g.px(float(out.get("ao_radius_m", 0.02))))))
        ao = N.height_to_ao(h01, radius_px=radius, strength=float(out.get("ao_strength", 1.0)), samples=8)
        ao = np.clip(ao, 0.3, 1.0)                                    # sky-lit exterior never fully occluded
        if "ao_mul" in out:
            ao = np.clip(ao * np.asarray(out["ao_mul"], F32), 0.0, 1.0)

    rough = np.clip(rough, 0.02, 1.0).astype(F32)
    metallic = out.get("metallic", 0.0)
    maps: dict[str, np.ndarray] = {
        "BaseColor": np.clip(N.linear_to_srgb(np.clip(albedo, 0.0, 1.0)), 0.0, 1.0).astype(F32),
        "Normal": normal,
        "Roughness": rough,
        "AO": ao,
        "Height": h01,
    }
    if isinstance(metallic, np.ndarray):
        maps["Metallic"] = np.clip(metallic, 0.0, 1.0).astype(F32)
    if "opacity" in out:
        maps["Opacity"] = np.clip(out["opacity"], 0.0, 1.0).astype(F32)
    if "mask" in out:
        maps["Mask"] = np.clip(out["mask"], 0.0, 1.0).astype(F32)
    maps.update(texio.pack_maps(p.engine, ao, rough, metallic if isinstance(metallic, np.ndarray) else float(metallic)))
    return maps


# =========================================================================== public entry points
def generate_set(name: str, settings=None, directory: str | None = None, resolution: int | None = None,
                 force: bool = False, params: Params | None = None) -> dict[str, str]:
    """Generate (or reuse) one texture set; returns ``{map_name: absolute png path}``.

    Work is skipped when the manifest key matches and every expected file exists (``io.set_is_current``)
    unless ``force``.  ``resolution`` overrides ``settings.materials.resolution`` (e.g. ``preview_resolution``)."""
    spec = TEXTURE_SETS[name]
    p = params or read_params(settings, resolution)
    if resolution is not None and p.resolution != int(resolution):
        p = Params(**{**p.__dict__, "resolution": _resolution_of(None, resolution)})
    directory = directory or texio.texture_dir(settings)
    os.makedirs(directory, exist_ok=True)
    key = params_key(name, params=p)
    maps = expected_maps(name, params=p)
    if not force and texio.set_is_current(directory, name, key, maps):
        return {m: texio.map_path(directory, name, m) for m in maps}

    t0 = time.time()
    g = Gen(spec, p, p.resolution)
    raw = spec.generator(g)
    arrays = _finish(g, raw)
    lo, hi = raw["height_range"]
    del raw
    ordered = {m: arrays[m] for m in MAP_ORDER if m in arrays}
    ordered.update({m: a for m, a in arrays.items() if m not in ordered})
    paths = texio.write_texture_set(directory, name, ordered, key)
    try:
        with open(sidecar_path(directory, name), "w", encoding="utf-8") as f:
            json.dump({
                "set": name, "tile_m": spec.tile, "resolution": p.resolution, "texel_mm": round(g.texel * 1000.0, 4),
                "height_range_mm": [lo, hi], "height_map_scale": "0..1 spans height_range_mm",
                "normal": "DirectX (-Y)" if p.normal_directx else "OpenGL (+Y)",
                "packed": _PACKED_NAME.get(p.engine), "engine": p.engine, "seed": p.seed,
                "srgb": {m: (m == "BaseColor") for m in ordered}, "maps": {m: os.path.basename(v) for m, v in paths.items()},
                "opacity": spec.has_opacity, "family": spec.family, "params": json.loads(key),
                "generation_seconds": round(time.time() - t0, 2), "version": VERSION,
            }, f, indent=2)
    except OSError:
        pass
    return paths


def generate_all(settings=None, directory: str | None = None, resolution: int | None = None, force: bool = False,
                 names=None, progress: Callable[[int, int, str], None] | None = None) -> dict[str, dict[str, str]]:
    """Generate every set (or ``names``) into ``directory``; ``progress(i, n, name)`` is called before each set."""
    names = list(names) if names else list(TEXTURE_SETS.keys())
    directory = directory or texio.texture_dir(settings)
    p = read_params(settings, resolution)
    out: dict[str, dict[str, str]] = {}
    for i, name in enumerate(names):
        if progress is not None:
            try:
                progress(i, len(names), name)
            except Exception:       # a UI progress callback must never abort generation
                pass
        out[name] = generate_set(name, settings, directory, resolution=p.resolution, force=force, params=p)
    return out


def register():
    pass


def unregister():
    pass
