"""Tileable procedural noise and pattern primitives in pure numpy.

The texture synthesiser builds 1K-4K PBR sets (concrete slabs, asphalt, grass,
dirt, paint wear, fence masks) from these building blocks. Nothing here needs
scipy, Pillow or OpenCV: only numpy (which Blender ships).

Conventions
-----------
* Every generator returns a ``float32`` array of shape ``(H, W)`` with values in
  ``[0, 1]`` (``height_to_normal`` returns ``(H, W, 3)``).  Row 0 is the *top*
  of the image, column 0 the left.
* Everything is **seamlessly tileable**: pixel ``w-1`` is followed by pixel ``0``
  without a discontinuity.  Lattice noises achieve this by mapping the whole
  image onto exactly ``period`` lattice cells, so ``period`` never has to divide
  the pixel size (a non-divisible period only means cells are a non-integer
  number of pixels wide).  Pixel *centres* are sampled, i.e. pixel ``x`` reads
  lattice coordinate ``(x + 0.5) * period / w``.
* ``period`` counts lattice cells across the image *width*; for non-square
  images the cell count along the height is ``round(period * h / w)`` so cells
  stay roughly square.  ``anisotropic_fbm`` takes both counts explicitly.
* All randomness goes through ``np.random.default_rng(seed)``; identical
  arguments give identical output on every machine.
* Image operators (``blur``, ``height_to_normal``, ``height_to_ao``) wrap around
  the borders with ``np.roll`` / wrapped index arrays, so they preserve
  tileability.
* Normal maps follow the OpenGL convention (+X right, +Y up, +Z out):
  ``n = normalize(-dh/dx, -dh/dy_up, 1) * 0.5 + 0.5``.  Pass ``flip_y=True``
  for DirectX-style maps.
* ``tile_check`` returns the *seam excess*: how much the wrapped step between the
  first and last row/column exceeds the largest interior step.  It is 0.0 for
  seamless images and clearly positive for images with a hard seam.  Note it
  cannot detect a seam in an image that is full of hard edges anyway (a binary
  mask), so the module's own test-suite additionally verifies periodicity of
  the private ``_*_grid`` evaluators at shifted coordinates.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

__all__ = [
    "rng", "value_noise", "gradient_noise", "fbm", "worley", "anisotropic_fbm",
    "slab_pattern", "grooves", "stripes", "splatter", "streaks", "cracks",
    "height_to_normal", "height_to_ao", "blur", "remap", "smoothstep", "lerp",
    "srgb_to_linear", "linear_to_srgb", "hex_to_rgb", "normalize01",
    "resize_nearest", "resize_bilinear", "tile_check",
]

F32 = np.float32
TWO_PI = math.tau
_INV_SQRT_HALF = 1.0 / math.sqrt(0.5)      # gradient noise theoretical amplitude is sqrt(2)/2


# --------------------------------------------------------------------------- rng / small helpers
def rng(seed: int) -> np.random.Generator:
    """Seeded generator. Any Python int (also negative) maps to a valid seed."""
    return np.random.default_rng(int(seed) % (1 << 63))


def _sub_seeds(seed: int, n: int) -> np.ndarray:
    """``n`` independent child seeds derived deterministically from ``seed``."""
    return rng(seed).integers(0, (1 << 31) - 1, size=max(1, int(n)), dtype=np.int64)


def _lattice_dims(h: int, w: int, period: int) -> tuple[int, int]:
    """Cells across width and height for a nominal ``period`` (square cells)."""
    px = max(1, int(round(period)))
    py = max(1, int(round(px * h / w)))
    return px, py


def _axis_coords(n: int, period: float) -> np.ndarray:
    """Lattice-space coordinate of every pixel centre along one axis (float64)."""
    return (np.arange(n, dtype=np.float64) + 0.5) * (float(period) / n)


def _fade(t: np.ndarray, interp: str) -> np.ndarray:
    if interp == "quintic":
        return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
    if interp == "cubic":
        return t * t * (3.0 - 2.0 * t)
    if interp == "linear":
        return t
    raise ValueError(f"unknown interpolation {interp!r} (use 'quintic', 'cubic' or 'linear')")


def _split(coord: np.ndarray, period: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Lattice coordinate -> (wrapped cell index, next wrapped index, fraction as f32)."""
    i = np.floor(coord)
    frac = (coord - i).astype(F32)
    i0 = i.astype(np.int64) % period
    i1 = (i0 + 1) % period
    return i0, i1, frac


def _gather(arr: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """``arr[rows, cols]`` for 1-D (outer product, fast) or broadcastable 2-D index arrays."""
    if rows.ndim == 1 and cols.ndim == 1:
        return np.take(np.take(arr, rows, axis=0), cols, axis=1)
    return arr[rows, cols]


def _as_2d(img: np.ndarray) -> np.ndarray:
    a = np.asarray(img, dtype=F32)
    if a.ndim == 3:
        a = a.mean(axis=2, dtype=F32)
    if a.ndim != 2:
        raise ValueError("expected an (H, W) or (H, W, C) array")
    return a


# --------------------------------------------------------------------------- lattice noises
def _value_grid(u: np.ndarray, v: np.ndarray, px: int, py: int, seed: int, interp: str) -> np.ndarray:
    """Periodic value noise on the grid ``v x u`` (lattice coordinates). Range [0, 1]."""
    iu0, iu1, fu = _split(u, px)
    iv0, iv1, fv = _split(v, py)
    lattice = rng(seed).random((py, px), dtype=np.float64).astype(F32)
    wu = _fade(fu, interp)[None, :]
    wv = _fade(fv, interp)[:, None]
    n00 = _gather(lattice, iv0, iu0)
    n10 = _gather(lattice, iv0, iu1)
    n01 = _gather(lattice, iv1, iu0)
    n11 = _gather(lattice, iv1, iu1)
    top = n00 + (n10 - n00) * wu
    bot = n01 + (n11 - n01) * wu
    return top + (bot - top) * wv


def _gradient_grid(u: np.ndarray, v: np.ndarray, px: int, py: int, seed: int) -> np.ndarray:
    """Periodic Perlin gradient noise on the grid ``v x u``. Raw range about [-0.707, 0.707]."""
    iu0, iu1, fu = _split(u, px)
    iv0, iv1, fv = _split(v, py)
    ang = rng(seed).random((py, px), dtype=np.float64) * TWO_PI
    gx = np.cos(ang).astype(F32)
    gy = np.sin(ang).astype(F32)
    fu_ = fu[None, :]
    fv_ = fv[:, None]
    fu1 = fu_ - F32(1.0)
    fv1 = fv_ - F32(1.0)
    n00 = _gather(gx, iv0, iu0) * fu_ + _gather(gy, iv0, iu0) * fv_
    n10 = _gather(gx, iv0, iu1) * fu1 + _gather(gy, iv0, iu1) * fv_
    n01 = _gather(gx, iv1, iu0) * fu_ + _gather(gy, iv1, iu0) * fv1
    n11 = _gather(gx, iv1, iu1) * fu1 + _gather(gy, iv1, iu1) * fv1
    wu = _fade(fu, "quintic")[None, :]
    wv = _fade(fv, "quintic")[:, None]
    top = n00 + (n10 - n00) * wu
    bot = n01 + (n11 - n01) * wu
    return top + (bot - top) * wv


def _worley_grid(u: np.ndarray, v: np.ndarray, px: int, py: int, seed: int,
                 jitter: float, metric: str, feature: str) -> np.ndarray:
    """Periodic cellular noise. ``u``/``v`` may be 1-D (regular grid) or broadcastable 2-D
    arrays (e.g. domain-warped coordinates). Distances are in cell units (raw, unnormalised);
    ``feature='cell_id'`` returns a random value in [0, 1] per cell."""
    if u.ndim == 1 and v.ndim == 1:
        iu = np.floor(u)
        iv = np.floor(v)
        fu = (u - iu).astype(F32)[None, :]
        fv = (v - iv).astype(F32)[:, None]
    else:
        u, v = np.broadcast_arrays(u, v)
        iu = np.floor(u)
        iv = np.floor(v)
        fu = (u - iu).astype(F32)
        fv = (v - iv).astype(F32)
    iu = iu.astype(np.int64)
    iv = iv.astype(np.int64)

    g = rng(seed)
    jitter = float(np.clip(jitter, 0.0, 1.0))
    ox = (0.5 + (g.random((py, px)) - 0.5) * jitter).astype(F32)
    oy = (0.5 + (g.random((py, px)) - 0.5) * jitter).astype(F32)
    ids = g.random((py, px)).astype(F32)

    need_f2 = feature in ("f2", "f2-f1")
    need_id = feature == "cell_id"
    shape = np.broadcast_shapes(fu.shape, fv.shape)
    f1 = np.full(shape, np.inf, dtype=F32)
    f2 = np.full(shape, np.inf, dtype=F32) if need_f2 else None
    best = np.zeros(shape, dtype=F32) if need_id else None

    for dj in (-1, 0, 1):
        rows = (iv + dj) % py
        for di in (-1, 0, 1):
            cols = (iu + di) % px
            dx = (_gather(ox, rows, cols) + F32(di)) - fu
            dy = (_gather(oy, rows, cols) + F32(dj)) - fv
            if metric == "euclid":
                d = np.sqrt(dx * dx + dy * dy)
            elif metric == "manhattan":
                d = np.abs(dx) + np.abs(dy)
            elif metric == "chebyshev":
                d = np.maximum(np.abs(dx), np.abs(dy))
            else:
                raise ValueError(f"unknown metric {metric!r}")
            closer = d < f1
            if need_f2:
                f2 = np.where(closer, f1, np.minimum(f2, d))
            if need_id:
                best = np.where(closer, _gather(ids, rows, cols), best)
            f1 = np.minimum(f1, d)

    if feature == "f1":
        return f1
    if feature == "f2":
        return f2
    if feature == "f2-f1":
        return f2 - f1
    if feature == "cell_id":
        return best
    raise ValueError(f"unknown feature {feature!r} (use 'f1', 'f2', 'f2-f1' or 'cell_id')")


def value_noise(h: int, w: int, period: int, seed: int, interp: str = "quintic") -> np.ndarray:
    """Tileable lattice value noise, ``period`` cells across the width. Range [0, 1].

    ``interp``: 'quintic' (C2 smooth, default), 'cubic' (smoothstep) or 'linear'.
    """
    px, py = _lattice_dims(h, w, period)
    out = _value_grid(_axis_coords(w, px), _axis_coords(h, py), px, py, seed, interp)
    return np.clip(out, 0.0, 1.0).astype(F32, copy=False)


def gradient_noise(h: int, w: int, period: int, seed: int) -> np.ndarray:
    """Tileable Perlin-style gradient noise remapped to [0, 1] with a *fixed* scale
    (raw / 0.707 * 0.5 + 0.5, clamped), so different seeds/sizes share one scale.
    Typical values span roughly 0.2-0.8; use ``normalize01`` for full contrast."""
    px, py = _lattice_dims(h, w, period)
    raw = _gradient_grid(_axis_coords(w, px), _axis_coords(h, py), px, py, seed)
    return np.clip(raw * F32(_INV_SQRT_HALF * 0.5) + F32(0.5), 0.0, 1.0).astype(F32, copy=False)


def worley(h: int, w: int, period: int, seed: int, jitter: float = 1.0,
           metric: str = "euclid", feature: str = "f1") -> np.ndarray:
    """Tileable cellular (Worley/Voronoi) noise with one jittered point per lattice cell.

    ``feature``: 'f1' (distance to nearest point), 'f2', 'f2-f1' (cell edges are dark),
    'cell_id' (random flat value per cell). Distances are min-max normalised to [0, 1].
    ``metric``: 'euclid', 'manhattan' or 'chebyshev'.  The neighbour search covers the
    3x3 surrounding cells (standard Worley): F1 is exact for ``jitter <= 0.5`` and has only
    rare, tiny artefacts for larger jitter.
    """
    px, py = _lattice_dims(h, w, period)
    out = _worley_grid(_axis_coords(w, px), _axis_coords(h, py), px, py, seed, jitter, metric, feature)
    if feature == "cell_id":
        return np.clip(out, 0.0, 1.0).astype(F32, copy=False)
    return normalize01(out)


def _octave_signal(u: np.ndarray, v: np.ndarray, px: int, py: int, seed: int, kind: str) -> np.ndarray:
    """One octave as a signed signal in [-1, 1]."""
    if kind == "gradient":
        return np.clip(_gradient_grid(u, v, px, py, seed) * F32(_INV_SQRT_HALF), -1.0, 1.0)
    if kind == "value":
        return _value_grid(u, v, px, py, seed, "quintic") * F32(2.0) - F32(1.0)
    if kind == "worley":
        d = _worley_grid(u, v, px, py, seed, 1.0, "euclid", "f1")
        return np.clip(d, 0.0, 1.0) * F32(2.0) - F32(1.0)
    raise ValueError(f"unknown noise kind {kind!r} (use 'gradient', 'value' or 'worley')")


def _fbm_grid(h: int, w: int, px: int, py: int, seed: int, octaves: int = 6,
              lacunarity: float = 2.0, gain: float = 0.5, kind: str = "gradient",
              ridged: bool = False, turbulence: bool = False, normalize: bool = True) -> np.ndarray:
    seeds = _sub_seeds(seed, octaves)
    acc = np.zeros((h, w), dtype=F32)
    norm = 0.0
    amp = 1.0
    for i in range(max(1, int(octaves))):
        pxi = max(1, int(round(px * lacunarity ** i)))
        pyi = max(1, int(round(py * lacunarity ** i)))
        if pxi > w and pyi > h:          # cells smaller than a pixel on both axes: pure aliasing, stop
            break
        pxi, pyi = min(pxi, w), min(pyi, h)
        s = _octave_signal(_axis_coords(w, pxi), _axis_coords(h, pyi), pxi, pyi, int(seeds[i]), kind)
        if ridged:
            s = F32(1.0) - np.abs(s)
        elif turbulence:
            s = np.abs(s)
        acc += F32(amp) * s
        norm += amp
        amp *= gain
    out = acc / F32(norm)
    if not (ridged or turbulence):
        out = out * F32(0.5) + F32(0.5)
    if normalize:
        out = normalize01(out)
    return np.clip(out, 0.0, 1.0).astype(F32, copy=False)


def fbm(h: int, w: int, period: int, seed: int, octaves: int = 6, lacunarity: float = 2.0,
        gain: float = 0.5, kind: str = "gradient", ridged: bool = False,
        turbulence: bool = False, normalize: bool = True) -> np.ndarray:
    """Tileable fractal Brownian motion: ``octaves`` layers of ``kind`` noise
    ('gradient', 'value' or 'worley'), octave ``i`` having ``round(period * lacunarity**i)``
    cells and amplitude ``gain**i``.  Integer periods keep every octave tileable
    (lacunarity 2 doubles exactly).  Octaves finer than one pixel are skipped.

    ``ridged``: each octave becomes ``1 - |s|`` (sharp ridges / crack-like veins).
    ``turbulence``: each octave becomes ``|s|`` (billowy).
    ``normalize=True`` (default) stretches the result to the full [0, 1] range; with
    ``False`` a fixed scale is used (0.5 = zero signal), which keeps different seeds
    comparable.
    """
    px, py = _lattice_dims(h, w, period)
    return _fbm_grid(h, w, px, py, seed, octaves, lacunarity, gain, kind, ridged, turbulence, normalize)


def anisotropic_fbm(h: int, w: int, period_x: int, period_y: int, seed: int, octaves: int = 5,
                    **kw) -> np.ndarray:
    """``fbm`` with independent cell counts along X (``period_x`` across the width) and Y
    (``period_y`` across the height) - stretched streaks, tyre marks, mowing stripes.
    Extra keyword arguments are passed to ``fbm`` (lacunarity, gain, kind, ridged,
    turbulence, normalize).  Tileable for any pair of periods."""
    px = max(1, int(round(period_x)))
    py = max(1, int(round(period_y)))
    return _fbm_grid(h, w, px, py, seed, octaves, **kw)


# --------------------------------------------------------------------------- analytic patterns
def slab_pattern(h: int, w: int, cells_x: int, cells_y: int, joint_width_px: float, seed: int,
                 tone_variation: float = 0.15, chamfer_px: float = 0.0
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rectangular concrete slab grid, ``cells_x`` x ``cells_y`` slabs across the image.

    Returns ``(joint_mask, slab_tone, height)``:
    * ``joint_mask`` - 1.0 inside the joints (``joint_width_px`` wide, 1 px anti-aliased edge).
    * ``slab_tone``  - per-slab random multiplier in ``1 +- tone_variation``, flat per slab
      (the joint pixels take the tone of the slab whose centre is nearest).
    * ``height``     - 1.0 on the slab surface, 0.0 in the joints; with ``chamfer_px > 0``
      the slab edges are rounded off over that many pixels.
    """
    cells_x = max(1, int(cells_x))
    cells_y = max(1, int(cells_y))
    u = _axis_coords(w, cells_x)
    v = _axis_coords(h, cells_y)
    iu = np.floor(u).astype(np.int64) % cells_x
    iv = np.floor(v).astype(np.int64) % cells_y
    fu = u - np.floor(u)
    fv = v - np.floor(v)
    dx_px = (np.minimum(fu, 1.0 - fu) * (w / cells_x)).astype(F32)   # px to nearest vertical joint line
    dy_px = (np.minimum(fv, 1.0 - fv) * (h / cells_y)).astype(F32)   # px to nearest horizontal joint line
    d = np.minimum(dx_px[None, :], dy_px[:, None])

    half = 0.5 * float(joint_width_px)
    if joint_width_px > 0:
        joint = F32(1.0) - smoothstep(half - 0.5, half + 0.5, d)
    else:
        joint = np.zeros((h, w), dtype=F32)

    tones = (1.0 + float(tone_variation) * (2.0 * rng(seed).random((cells_y, cells_x)) - 1.0)).astype(F32)
    slab_tone = _gather(tones, iv, iu)

    if chamfer_px > 0:
        t = np.clip((d - F32(half)) / F32(chamfer_px), 0.0, 1.0)
        height = F32(1.0) - (F32(1.0) - t) ** 2            # quarter-ellipse style rounded edge
        if joint_width_px > 0:
            height = np.minimum(height, F32(1.0) - joint)
    else:
        height = F32(1.0) - joint
    return (np.clip(joint, 0, 1).astype(F32, copy=False),
            slab_tone.astype(F32, copy=False),
            np.clip(height, 0, 1).astype(F32, copy=False))


def _line_profile(n: int, spacing_px: float, width_px: float, depth: float, jitter: float,
                  seed: int) -> np.ndarray:
    """1-D periodic profile of parallel lines along an axis of ``n`` pixels."""
    count = max(1, int(round(n / max(float(spacing_px), 1e-6))))
    spacing = n / count                                   # exact spacing so the pattern tiles
    g = rng(seed)
    centres = (np.arange(count) + 0.5) * spacing
    if jitter > 0:
        room = max(spacing - float(width_px), 0.0)
        centres = centres + (g.random(count) - 0.5) * float(jitter) * room
    coord = np.arange(n, dtype=np.float64) + 0.5
    k = np.floor(coord / spacing).astype(np.int64)
    dist = np.full(n, np.inf)
    for dk in (-1, 0, 1):
        kk = k + dk
        wrapped = kk % count
        c = centres[wrapped] + (kk - wrapped) * spacing   # unwrap across the border
        dist = np.minimum(dist, np.abs(coord - c))
    half = 0.5 * float(width_px)
    line = 1.0 - smoothstep(half - 0.5, half + 0.5, dist.astype(F32))
    return (line * F32(depth)).astype(F32, copy=False)


def grooves(h: int, w: int, spacing_px: float, width_px: float, axis: str = "x", depth: float = 1.0,
            jitter: float = 0.0, seed: int = 0) -> np.ndarray:
    """Parallel groove lines (runway grooving). ``axis='x'``: lines run along X and the
    pattern varies along Y; ``axis='y'`` the other way round. Value is ``depth`` inside a
    groove (1 px anti-aliased edges), 0 elsewhere. ``spacing_px`` is rounded so a whole
    number of grooves fits the image (keeps it tileable); ``jitter`` (0-1) randomly
    offsets each groove within its slot."""
    if axis == "x":
        prof = _line_profile(h, spacing_px, width_px, depth, jitter, seed)
        return np.broadcast_to(prof[:, None], (h, w)).astype(F32)
    if axis == "y":
        prof = _line_profile(w, spacing_px, width_px, depth, jitter, seed)
        return np.broadcast_to(prof[None, :], (h, w)).astype(F32)
    raise ValueError("axis must be 'x' or 'y'")


def _stripe_profile(n: int, period_px: float, duty: float, soft: float) -> np.ndarray:
    count = max(1, int(round(n / max(float(period_px), 1e-6))))
    phase = np.mod((np.arange(n, dtype=np.float64) + 0.5) * count / n, 1.0)
    duty = float(np.clip(duty, 0.0, 1.0))
    if duty <= 0.0:
        return np.zeros(n, dtype=F32)
    if duty >= 1.0:
        return np.ones(n, dtype=F32)
    inside = phase < duty
    d_edge = np.minimum(np.minimum(phase, np.abs(phase - duty)), 1.0 - phase)
    sd = np.where(inside, d_edge, -d_edge)                # signed distance (phase units) to the edge
    px_phase = count / n                                  # one pixel in phase units
    s = max(0.5 * float(soft), 0.5 * px_phase)            # at least 1 px anti-aliasing
    return smoothstep(-s, s, sd.astype(F32)).astype(F32, copy=False)


def stripes(h: int, w: int, period_px: float, duty: float = 0.5, axis: str = "x",
            soft: float = 0.0) -> np.ndarray:
    """Periodic stripes (mowing stripes, paving lanes). ``axis='x'``: stripes run along X and
    alternate along Y. ``duty`` is the fraction of each period that is 1; ``soft`` (0-0.5,
    fraction of a period) widens the transitions. ``period_px`` is rounded so a whole
    number of periods fits the image."""
    if axis == "x":
        prof = _stripe_profile(h, period_px, duty, soft)
        return np.broadcast_to(prof[:, None], (h, w)).astype(F32)
    if axis == "y":
        prof = _stripe_profile(w, period_px, duty, soft)
        return np.broadcast_to(prof[None, :], (h, w)).astype(F32)
    raise ValueError("axis must be 'x' or 'y'")


def _wrapped_box(h: int, w: int, cx: float, cy: float, ex: float, ey: float
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Row/col index arrays of the (wrapped) box of half extents ``ex, ey`` around ``(cx, cy)``
    plus the wrapped pixel-centre offsets ``dx (1, n)``, ``dy (m, 1)`` from the centre."""
    rx = int(math.ceil(ex)) + 1
    ry = int(math.ceil(ey)) + 1
    if 2 * rx + 1 >= w:
        cols = np.arange(w)
    else:
        cols = np.arange(int(round(cx)) - rx, int(round(cx)) + rx + 1) % w
    if 2 * ry + 1 >= h:
        rows = np.arange(h)
    else:
        rows = np.arange(int(round(cy)) - ry, int(round(cy)) + ry + 1) % h
    dx = (np.mod(cols + 0.5 - cx + 0.5 * w, w) - 0.5 * w).astype(F32)[None, :]
    dy = (np.mod(rows + 0.5 - cy + 0.5 * h, h) - 0.5 * h).astype(F32)[:, None]
    return rows, cols, dx, dy


def _soft_disc(t: np.ndarray, softness: float, radius_px: float) -> np.ndarray:
    """Falloff for a normalised distance ``t = d / r``: 1 in the core, 0 outside.
    ``softness`` (0-1) is the fraction of the radius over which it fades; at least 1 px."""
    s = max(float(np.clip(softness, 0.0, 1.0)), 1.0 / max(float(radius_px), 1e-6))
    return F32(1.0) - smoothstep(1.0 - s, 1.0, t)


def _paint_blobs(h: int, w: int, cx: np.ndarray, cy: np.ndarray, radius: np.ndarray,
                 inten: np.ndarray, softness: float, irregular: float,
                 harm: np.ndarray | None, phase: np.ndarray | None) -> np.ndarray:
    out = np.zeros((h, w), dtype=F32)
    irregular = float(np.clip(irregular, 0.0, 0.9))
    for i in range(len(cx)):
        r = float(radius[i])
        if r <= 0:
            continue
        rmax = r * (1.0 + irregular)
        rows, cols, dx, dy = _wrapped_box(h, w, float(cx[i]), float(cy[i]), rmax, rmax)
        dist = np.sqrt(dx * dx + dy * dy)
        if irregular > 0 and harm is not None:
            theta = np.arctan2(dy, dx)
            dev = np.zeros_like(dist)
            for k in range(harm.shape[1]):
                dev += F32(harm[i, k]) * np.cos(F32(k + 1) * theta + F32(phase[i, k]))
            r_eff = F32(r) * (F32(1.0) + F32(irregular) * dev)
        else:
            r_eff = F32(r)
        blob = _soft_disc(dist / r_eff, softness, r) * F32(inten[i])
        sub = out[np.ix_(rows, cols)]
        out[np.ix_(rows, cols)] = np.maximum(sub, blob)
    return out


def splatter(h: int, w: int, seed: int, count: int, min_r: float, max_r: float,
             softness: float = 0.5, intensity: Sequence[float] = (0.3, 1.0),
             irregular: float = 0.0) -> np.ndarray:
    """``count`` random soft discs (stains, oil spots) with radii in ``[min_r, max_r]`` px and
    peak values drawn from ``intensity``; blobs are max-accumulated and wrap around the
    borders. ``softness`` (0-1) is the fraction of the radius that fades out.
    ``irregular`` (0-0.9) distorts each disc's outline with random low-order harmonics
    so stains are not perfect circles (0.15-0.3 looks natural; larger values pinch)."""
    g = rng(seed)
    count = max(0, int(count))
    cx = g.random(count) * w
    cy = g.random(count) * h
    radius = g.uniform(min(min_r, max_r), max(min_r, max_r), count)
    inten = g.uniform(min(intensity), max(intensity), count)
    nharm = 4
    harm = g.normal(0.0, 1.0, (count, nharm)) / (np.arange(nharm) + 1.0) ** 1.5
    harm /= np.maximum(np.abs(harm).sum(axis=1, keepdims=True), 1e-9)    # deviation within +-1
    phase = g.random((count, nharm)) * TWO_PI
    return _paint_blobs(h, w, cx, cy, radius, inten, softness, irregular, harm, phase)


def _paint_capsules(h: int, w: int, cx: np.ndarray, cy: np.ndarray, length: np.ndarray,
                    width: np.ndarray, angle: np.ndarray, inten: np.ndarray, softness: float
                    ) -> np.ndarray:
    out = np.zeros((h, w), dtype=F32)
    for i in range(len(cx)):
        hl = 0.5 * float(length[i])
        hw = 0.5 * float(width[i])
        if hw <= 0:
            continue
        ux, uy = math.cos(float(angle[i])), -math.sin(float(angle[i]))   # CCW as displayed (rows go down)
        rows, cols, dx, dy = _wrapped_box(h, w, float(cx[i]), float(cy[i]),
                                          hl * abs(ux) + hw + 1.0, hl * abs(uy) + hw + 1.0)
        t = dx * F32(ux) + dy * F32(uy)
        tc = np.clip(t, -hl, hl)
        px = dx - tc * F32(ux)
        py = dy - tc * F32(uy)
        dist = np.sqrt(px * px + py * py)
        mark = _soft_disc(dist / F32(hw), softness, hw)
        if hl > 0:
            taper = F32(1.0) - F32(0.6) * smoothstep(0.4, 1.0, np.abs(t) / F32(hl))
            mark = mark * taper
        sub = out[np.ix_(rows, cols)]
        out[np.ix_(rows, cols)] = np.maximum(sub, mark * F32(inten[i]))
    return out


def streaks(h: int, w: int, seed: int, count: int, length_px: float, width_px: float,
            angle_deg: float = 0.0, softness: float = 0.5, angle_jitter_deg: float = 0.0,
            intensity: Sequence[float] = (0.4, 1.0)) -> np.ndarray:
    """``count`` elongated soft marks (skid marks, drips) that wrap around the borders.
    Each mark is a capsule of length ``length_px * U(0.6, 1)`` and width ``width_px * U(0.7, 1)``
    oriented at ``angle_deg`` (counter-clockwise from +X as displayed, 0 = along X,
    90 = along Y) plus ``+- angle_jitter_deg``; intensities fade towards the ends and are
    drawn from ``intensity``. Max-accumulated."""
    g = rng(seed)
    count = max(0, int(count))
    cx = g.random(count) * w
    cy = g.random(count) * h
    length = float(length_px) * g.uniform(0.6, 1.0, count)
    width = float(width_px) * g.uniform(0.7, 1.0, count)
    ang = np.radians(float(angle_deg) + (2.0 * g.random(count) - 1.0) * float(angle_jitter_deg))
    inten = g.uniform(min(intensity), max(intensity), count)
    return _paint_capsules(h, w, cx, cy, length, width, ang, inten, softness)


def cracks(h: int, w: int, period: int, seed: int, threshold: float = 0.06, width: float = 1.0,
           octaves: int = 4, cell_edges: float = 0.0, coverage: float = 1.0) -> np.ndarray:
    """Thin crack network mask (1 = crack), tileable.

    Cracks follow the zero contour of a tileable ``fbm`` signal ``s`` (``period`` cells,
    ``octaves`` octaves).  The line is drawn ``width`` pixels wide (the distance to the
    contour is estimated as ``|s| / |grad s|``, so the pixel width is the same at 512 px or
    4K and for any period), with a natural +-40 % width variation where the field is
    flatter/steeper.  ``threshold`` is an additional band in signal units (``s`` normalised
    to unit RMS, calibrated at 512 px): where the field is steep the crack thins out and
    disappears once ``|s| >= threshold``.  ``cell_edges`` (0-1) adds a second crack family
    with that weight: domain-warped Worley cell borders (branching, mud-crack polygons).
    ``coverage`` (0-1) fades cracks out in random low-frequency regions (1 = everywhere).
    """
    seeds = _sub_seeds(seed, 6)
    px, py = _lattice_dims(h, w, period)
    scale = 512.0 / max(1, min(h, w))
    th = max(float(threshold) * scale, 1e-4)
    half_w = 0.5 * max(float(width), 0.25)

    s = _fbm_grid(h, w, px, py, int(seeds[0]), octaves, 2.0, 0.5, "gradient", False, False, False)
    s = s * F32(2.0) - F32(1.0)
    s = s / F32(max(float(s.std()), 1e-6))
    gx = (np.roll(s, -1, axis=1) - np.roll(s, 1, axis=1)) * F32(0.5)
    gy = (np.roll(s, -1, axis=0) - np.roll(s, 1, axis=0)) * F32(0.5)
    gmag = np.sqrt(gx * gx + gy * gy)
    gmean = float(gmag.mean()) + 1e-9
    gmag = gmag + F32(1e-4)
    d_px = np.abs(s) / gmag                                          # px distance to the contour
    local = np.clip(F32(gmean) / gmag, 0.6, 1.4)                     # wider where flatter
    hw = F32(half_w) * local
    line = F32(1.0) - smoothstep_arr(hw - F32(0.5), hw + F32(0.5), d_px)
    band_hw = np.maximum(F32(th) / gmag, F32(1.0))                   # |s| < th band, at least 1 px
    band = F32(1.0) - smoothstep_arr(band_hw - F32(0.5), band_hw + F32(0.5), d_px)
    mask = np.minimum(line, band)

    if cell_edges > 0:
        warp = 0.35
        wx = _fbm_grid(h, w, px, py, int(seeds[1]), 3, 2.0, 0.5, "gradient", False, False, False)
        wy = _fbm_grid(h, w, px, py, int(seeds[2]), 3, 2.0, 0.5, "gradient", False, False, False)
        u = _axis_coords(w, px)[None, :] + (wx.astype(np.float64) * 2.0 - 1.0) * warp
        v = _axis_coords(h, py)[:, None] + (wy.astype(np.float64) * 2.0 - 1.0) * warp
        edge = _worley_grid(u, v, px, py, int(seeds[3]), 1.0, "euclid", "f2-f1")
        ex = (np.roll(edge, -1, axis=1) - np.roll(edge, 1, axis=1)) * F32(0.5)
        ey = (np.roll(edge, -1, axis=0) - np.roll(edge, 1, axis=0)) * F32(0.5)
        emag = np.sqrt(ex * ex + ey * ey)
        emag = np.maximum(emag, F32(0.5 * float(emag.mean()) + 1e-6))   # keep vertices from bloating
        e_px = edge / emag                                             # px distance to the cell border
        e = F32(1.0) - smoothstep(half_w - 0.5, half_w + 0.5, e_px)
        mask = np.maximum(mask, e * F32(np.clip(cell_edges, 0.0, 1.0)))

    if coverage < 1.0:
        gate = _fbm_grid(h, w, max(1, px // 2), max(1, py // 2), int(seeds[4]), 2, 2.0, 0.5,
                         "gradient", False, False, True)
        level = 1.0 - float(np.clip(coverage, 0.0, 1.0))
        mask = mask * smoothstep(level - 0.15, level + 0.15, gate)
    return np.clip(mask, 0.0, 1.0).astype(F32, copy=False)


# --------------------------------------------------------------------------- height -> maps
def height_to_normal(height: np.ndarray, strength: float = 1.0, tileable: bool = True,
                     flip_y: bool = False) -> np.ndarray:
    """Tangent-space normal map ``(H, W, 3)`` float32 in [0, 1] from a height field.

    Central differences: ``dh/dx = (h[x+1] - h[x-1]) / 2`` (wrapping with ``np.roll`` when
    ``tileable``, one-sided at the borders otherwise).  ``strength`` scales the slope, so it
    acts like the height amplitude in pixels.

    Convention (OpenGL, +Y up): ``n = normalize(-dh/dx, -dh/dy_up, 1)``.  Because row
    indices grow downwards, ``dh/dy_up = -dh/drow`` and the green component is ``+dh/drow``.
    Check: on the *top* slope of a dome (height rises going down the image) green > 0.5 and
    on the *left* slope (height rises going right) red < 0.5, which is the OpenGL look
    ("lit from the top-right").  ``flip_y=True`` negates green for DirectX.
    """
    hgt = _as_2d(height)
    if tileable:
        dx = (np.roll(hgt, -1, axis=1) - np.roll(hgt, 1, axis=1)) * F32(0.5)
        drow = (np.roll(hgt, -1, axis=0) - np.roll(hgt, 1, axis=0)) * F32(0.5)
    else:
        drow, dx = np.gradient(hgt)
        drow = drow.astype(F32)
        dx = dx.astype(F32)
    nx = -dx * F32(strength)
    ny = drow * F32(strength)
    if flip_y:
        ny = -ny
    inv = F32(1.0) / np.sqrt(nx * nx + ny * ny + F32(1.0))
    out = np.empty(hgt.shape + (3,), dtype=F32)
    out[..., 0] = nx * inv * F32(0.5) + F32(0.5)
    out[..., 1] = ny * inv * F32(0.5) + F32(0.5)
    out[..., 2] = inv * F32(0.5) + F32(0.5)
    return np.clip(out, 0.0, 1.0, out=out)


def height_to_ao(height: np.ndarray, radius_px: int = 8, strength: float = 1.0,
                 samples: int = 8) -> np.ndarray:
    """Cheap tileable ambient occlusion (1 = unoccluded) from a [0, 1] height field.

    Horizon-style: for ``samples`` directions the height field is shifted (``np.roll``) by
    several distances up to ``radius_px``; the steepest rise ``(h_shift - h) * radius / dist``
    seen in a direction (a rise of 1.0 over ``radius_px`` pixels = 45 degrees) is turned into
    an occlusion term ``t / sqrt(1 + t^2)`` and averaged over directions. ``strength``
    scales the occlusion."""
    hgt = _as_2d(height)
    radius = max(1, int(radius_px))
    steps = min(4, radius)
    samples = max(1, int(samples))
    occ = np.zeros_like(hgt)
    for k in range(samples):
        ang = TWO_PI * (k + 0.5) / samples
        cx, cy = math.cos(ang), math.sin(ang)
        horizon = np.zeros_like(hgt)
        for j in range(steps):
            r = radius * (j + 1) / steps
            sx = int(round(r * cx))
            sy = int(round(r * cy))
            if sx == 0 and sy == 0:
                continue
            shifted = np.roll(np.roll(hgt, -sy, axis=0), -sx, axis=1)   # value at (x+sx, y+sy)
            dist = math.hypot(sx, sy)
            np.maximum(horizon, (shifted - hgt) * F32(radius / dist), out=horizon)
        np.maximum(horizon, 0.0, out=horizon)
        occ += horizon / np.sqrt(F32(1.0) + horizon * horizon)
    ao = F32(1.0) - F32(strength) * (occ / F32(samples))
    return np.clip(ao, 0.0, 1.0).astype(F32, copy=False)


# --------------------------------------------------------------------------- image utilities
def _box_blur_wrap(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    """Box blur of half width ``r`` along ``axis`` with wrap-around (float64 cumulative sums)."""
    a = np.moveaxis(a, axis, 0)
    n = a.shape[0]
    idx = np.arange(-r, n + r) % n
    padded = np.take(a, idx, axis=0)
    cs = np.cumsum(padded, axis=0, dtype=np.float64)
    zero = np.zeros((1,) + cs.shape[1:], dtype=np.float64)
    cs = np.concatenate([zero, cs], axis=0)
    win = (cs[2 * r + 1: 2 * r + 1 + n] - cs[0:n]) / float(2 * r + 1)
    return np.moveaxis(win, 0, axis)


def blur(img: np.ndarray, radius_px: int) -> np.ndarray:
    """Tileable Gaussian-like blur (three wrapped box passes per axis, ``sigma ~= radius_px``)
    for ``(H, W)`` or ``(H, W, C)`` arrays. ``radius_px <= 0`` returns a float32 copy."""
    a = np.asarray(img, dtype=np.float64)
    r = int(round(float(radius_px)))
    if r <= 0:
        return a.astype(F32)
    for _ in range(3):
        a = _box_blur_wrap(a, r, 0)
        a = _box_blur_wrap(a, r, 1)
    return a.astype(F32)


def remap(img, in_min: float, in_max: float, out_min: float = 0.0, out_max: float = 1.0,
          clamp: bool = True) -> np.ndarray:
    """Linear remap of ``[in_min, in_max]`` onto ``[out_min, out_max]`` (optionally clamped)."""
    a = np.asarray(img, dtype=F32)
    span = float(in_max) - float(in_min)
    if abs(span) < 1e-12:
        t = (a >= in_min).astype(F32)
    else:
        t = (a - F32(in_min)) / F32(span)
    if clamp:
        t = np.clip(t, 0.0, 1.0)
    return (F32(out_min) + t * F32(float(out_max) - float(out_min))).astype(F32, copy=False)


def smoothstep(e0: float, e1: float, x) -> np.ndarray:
    """Hermite smoothstep of ``x`` between ``e0`` and ``e1`` (a hard step when ``e0 == e1``)."""
    x = np.asarray(x, dtype=F32)
    if abs(float(e1) - float(e0)) < 1e-12:
        return (x >= e1).astype(F32)
    t = np.clip((x - F32(e0)) / F32(float(e1) - float(e0)), 0.0, 1.0)
    return t * t * (F32(3.0) - F32(2.0) * t)


def smoothstep_arr(e0: np.ndarray, e1: np.ndarray, x: np.ndarray) -> np.ndarray:
    """``smoothstep`` with per-pixel edges (arrays broadcastable against ``x``)."""
    x = np.asarray(x, dtype=F32)
    t = np.clip((x - e0) / np.maximum(e1 - e0, F32(1e-6)), 0.0, 1.0)
    return t * t * (F32(3.0) - F32(2.0) * t)


def lerp(a, b, t):
    """``a + (b - a) * t`` (works for scalars and arrays)."""
    return a + (b - a) * t


def srgb_to_linear(x):
    """Exact piecewise sRGB -> linear transfer (scalars or arrays)."""
    a = np.asarray(x, dtype=np.float64)
    out = np.where(a <= 0.04045, a / 12.92, ((np.maximum(a, 0.0) + 0.055) / 1.055) ** 2.4)
    return float(out) if out.ndim == 0 else out.astype(F32)


def linear_to_srgb(x):
    """Exact piecewise linear -> sRGB transfer (scalars or arrays)."""
    a = np.asarray(x, dtype=np.float64)
    out = np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(np.maximum(a, 0.0), 1.0 / 2.4) - 0.055)
    return float(out) if out.ndim == 0 else out.astype(F32)


def hex_to_rgb(hex_str: str) -> tuple[float, float, float]:
    """``'#RRGGBB'`` (also ``'RGB'`` and ``'RRGGBBAA'``, alpha ignored) -> sRGB floats 0..1."""
    s = hex_str.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) not in (6, 8):
        raise ValueError(f"bad hex colour {hex_str!r}")
    return tuple(int(s[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def normalize01(img) -> np.ndarray:
    """Min-max stretch to [0, 1]; a constant image becomes all zeros."""
    a = np.asarray(img, dtype=F32)
    lo = float(a.min())
    hi = float(a.max())
    if hi - lo < 1e-12:
        return np.zeros_like(a)
    return ((a - F32(lo)) / F32(hi - lo)).astype(F32, copy=False)


def resize_nearest(img: np.ndarray, h: int, w: int) -> np.ndarray:
    """Nearest-neighbour resample of an ``(H, W[, C])`` array to ``(h, w[, C])``."""
    a = np.asarray(img, dtype=F32)
    H, W = a.shape[:2]
    ys = np.minimum(np.floor((np.arange(h) + 0.5) * H / h).astype(np.int64), H - 1)
    xs = np.minimum(np.floor((np.arange(w) + 0.5) * W / w).astype(np.int64), W - 1)
    return np.take(np.take(a, ys, axis=0), xs, axis=1)


def resize_bilinear(img: np.ndarray, h: int, w: int) -> np.ndarray:
    """Bilinear resample of an ``(H, W[, C])`` array to ``(h, w[, C])`` with wrap-around
    sampling at the borders (a tileable input stays tileable)."""
    a = np.asarray(img, dtype=F32)
    H, W = a.shape[:2]
    sy = (np.arange(h) + 0.5) * H / h - 0.5
    sx = (np.arange(w) + 0.5) * W / w - 0.5
    y0 = np.floor(sy)
    x0 = np.floor(sx)
    fy = (sy - y0).astype(F32)
    fx = (sx - x0).astype(F32)
    y0 = y0.astype(np.int64) % H
    x0 = x0.astype(np.int64) % W
    y1 = (y0 + 1) % H
    x1 = (x0 + 1) % W
    extra = (1,) * (a.ndim - 2)
    fy = fy.reshape((h, 1) + extra)
    fx = fx.reshape((1, w) + extra)
    r0 = np.take(a, y0, axis=0)
    r1 = np.take(a, y1, axis=0)
    top = np.take(r0, x0, axis=1) * (F32(1.0) - fx) + np.take(r0, x1, axis=1) * fx
    bot = np.take(r1, x0, axis=1) * (F32(1.0) - fx) + np.take(r1, x1, axis=1) * fx
    return (top * (F32(1.0) - fy) + bot * fy).astype(F32, copy=False)


def tile_check(img: np.ndarray) -> float:
    """Seam excess of an image: for each axis, the largest wrapped step between the first and
    last row/column minus the largest step between interior neighbours; the maximum over
    both axes, clipped at 0.  Seamless images give 0.0 (the seam is just another pixel
    step); an image with a hard seam gives a clearly positive value (up to ~1)."""
    a = np.asarray(img, dtype=np.float64)
    worst = 0.0
    for axis in (0, 1):
        if a.shape[axis] < 2:
            continue
        interior = float(np.abs(np.diff(a, axis=axis)).max())
        seam = float(np.abs(np.take(a, 0, axis=axis) - np.take(a, -1, axis=axis)).max())
        worst = max(worst, seam - interior)
    return max(0.0, worst)
