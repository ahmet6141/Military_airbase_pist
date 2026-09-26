"""numpy <-> Blender image I/O and engine channel packing.

Verified on Blender 4.2 and 5.0 (pip bpy):
* 8-bit PNG: ``bpy.data.images.new`` (byte buffer) + ``pixels.foreach_set`` + ``filepath_raw``/``file_format``/``save()``.
* 16-bit PNG: float-buffer image + ``save_render`` with the scene's image settings temporarily set to PNG/16.
"""
from __future__ import annotations

import json
import os
import tempfile

import bpy
import numpy as np

from ..core import constants as C

_IMAGE_CACHE: dict[str, str] = {}   # abs path -> image datablock name


# --------------------------------------------------------------------------- paths
def texture_dir(settings=None, create: bool = True) -> str:
    """Absolute texture folder: settings.materials.texture_dir ('//textures/' relative to the .blend).

    When the .blend is unsaved, '//' cannot resolve, so a per-session temp folder is used.
    """
    raw = "//textures/"
    if settings is not None:
        try:
            raw = settings.materials.texture_dir or raw
        except AttributeError:
            pass
    if raw.startswith("//") and not bpy.data.filepath:
        path = os.path.join(tempfile.gettempdir(), "mad_textures")
    else:
        path = bpy.path.abspath(raw)
    path = os.path.normpath(path)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def map_path(directory: str, texture_set: str, map_name: str) -> str:
    return os.path.join(directory, f"{C.TEX_PREFIX}{texture_set}_{map_name}.png")


# --------------------------------------------------------------------------- numpy helpers
def to_rgba(arr: np.ndarray) -> np.ndarray:
    """(H,W) / (H,W,1) / (H,W,3) / (H,W,4) float -> (H,W,4) float32 in [0,1]."""
    a = np.asarray(arr, dtype=np.float32)
    if a.ndim == 2:
        a = a[..., None]
    if a.shape[2] == 1:
        a = np.repeat(a, 3, axis=2)
    if a.shape[2] == 3:
        a = np.concatenate([a, np.ones_like(a[..., :1])], axis=2)
    return np.clip(a, 0.0, 1.0)


def _flat_pixels(rgba: np.ndarray) -> np.ndarray:
    # Blender stores rows bottom-up; numpy images are top-down
    return np.ascontiguousarray(rgba[::-1]).ravel()


# --------------------------------------------------------------------------- save
def save_png(arr: np.ndarray, path: str, colorspace: str = 'sRGB', depth: int = 8, keep_datablock: bool = False,
             name: str | None = None) -> bpy.types.Image | None:
    """Write a numpy image (values 0..1, sRGB-encoded colour or linear data) as PNG.

    ``colorspace`` 'sRGB' for base colour, 'Non-Color' for normal/roughness/AO/height/packed maps.
    ``depth`` 8 or 16 (16 uses a float buffer + save_render).
    """
    rgba = to_rgba(arr)
    h, w = rgba.shape[:2]
    is_data = colorspace != 'sRGB'
    name = name or os.path.splitext(os.path.basename(path))[0]
    old = bpy.data.images.get(name)
    if old is not None:
        bpy.data.images.remove(old)
    img = bpy.data.images.new(name, w, h, alpha=True, float_buffer=(depth == 16), is_data=is_data)
    try:
        img.colorspace_settings.name = 'Non-Color' if is_data else 'sRGB'
    except TypeError:
        pass
    img.pixels.foreach_set(_flat_pixels(rgba))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if depth == 16:
        scene = bpy.context.scene
        ims = scene.render.image_settings
        saved = (ims.file_format, ims.color_depth, ims.color_mode, ims.compression)
        try:
            if hasattr(ims, "media_type"):      # Blender 5.0+: must be set before file_format
                ims.media_type = 'IMAGE'
            ims.file_format = 'PNG'
            ims.color_depth = '16'
            ims.color_mode = 'RGBA' if rgba.shape[2] == 4 else 'RGB'
            ims.compression = 50
            img.save_render(path, scene=scene)
        finally:
            ims.file_format, ims.color_depth, ims.color_mode, ims.compression = saved
    else:
        img.filepath_raw = path
        img.file_format = 'PNG'
        img.save()
    if keep_datablock:
        img.filepath = path
        img.source = 'FILE'
        return img
    bpy.data.images.remove(img)
    return None


def load_image(path: str, is_data: bool) -> bpy.types.Image | None:
    """Load (or reuse) an image datablock for a texture file."""
    path = os.path.normpath(path)
    if not os.path.exists(path):
        return None
    name = _IMAGE_CACHE.get(path)
    img = bpy.data.images.get(name) if name else None
    if img is None:
        img = bpy.data.images.load(path, check_existing=True)
        _IMAGE_CACHE[path] = img.name
    try:
        img.colorspace_settings.name = 'Non-Color' if is_data else 'sRGB'
    except TypeError:
        pass
    return img


def reload_all(directory: str) -> None:
    for img in bpy.data.images:
        if img.filepath and os.path.normpath(bpy.path.abspath(img.filepath)).startswith(os.path.normpath(directory)):
            try:
                img.reload()
            except RuntimeError:
                pass


# --------------------------------------------------------------------------- packing
def pack_maps(engine: str, ao: np.ndarray, roughness: np.ndarray, metallic: np.ndarray | float = 0.0) -> dict[str, np.ndarray]:
    """Engine-specific packed maps from linear AO / roughness / metallic (all (H,W) in 0..1).

    UNREAL      -> ORM: R=AO, G=Roughness, B=Metallic
    UNITY_URP   -> MetallicSmoothness: RGB=Metallic, A=Smoothness (1-roughness)
    UNITY_HDRP  -> MaskMap: R=Metallic, G=AO, B=Detail(0.5), A=Smoothness
    GLTF        -> MetallicRoughness: R=1 (unused), G=Roughness, B=Metallic (+ AO as R per glTF occlusion convention)
    SEPARATE    -> {} (no packed map)
    """
    ao = np.asarray(ao, dtype=np.float32)
    rough = np.asarray(roughness, dtype=np.float32)
    metal = np.full_like(ao, float(metallic)) if np.isscalar(metallic) else np.asarray(metallic, dtype=np.float32)
    one = np.ones_like(ao)
    if engine == 'UNREAL':
        return {"ORM": np.stack([ao, rough, metal, one], axis=2)}
    if engine == 'UNITY_URP':
        return {"MetallicSmoothness": np.stack([metal, metal, metal, 1.0 - rough], axis=2)}
    if engine == 'UNITY_HDRP':
        return {"MaskMap": np.stack([metal, ao, np.full_like(ao, 0.5), 1.0 - rough], axis=2)}
    if engine == 'GLTF':
        return {"MetallicRoughness": np.stack([ao, rough, metal, one], axis=2)}
    return {}


# --------------------------------------------------------------------------- manifest
def manifest_path(directory: str) -> str:
    return os.path.join(directory, "mad_textures.json")


def read_manifest(directory: str) -> dict:
    p = manifest_path(directory)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}
    return {}


def write_manifest(directory: str, data: dict) -> None:
    with open(manifest_path(directory), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def set_is_current(directory: str, texture_set: str, params_key: str, maps: list[str]) -> bool:
    """True when the manifest records the same params for the set and every map file exists."""
    m = read_manifest(directory).get(texture_set)
    if not m or m.get("key") != params_key:
        return False
    return all(os.path.exists(map_path(directory, texture_set, mp)) for mp in maps)


def write_texture_set(directory: str, texture_set: str, maps: dict[str, np.ndarray], params_key: str,
                      depth16: tuple[str, ...] = ("Height",)) -> dict[str, str]:
    """Save every map of a set; colour maps sRGB 8-bit, data maps Non-Color (Height 16-bit). Returns {map: path}."""
    out = {}
    for name, arr in maps.items():
        is_color = name in ("BaseColor", "Emissive")
        p = map_path(directory, texture_set, name)
        save_png(arr, p, colorspace='sRGB' if is_color else 'Non-Color', depth=16 if name in depth16 else 8)
        out[name] = p
    m = read_manifest(directory)
    m[texture_set] = {"key": params_key, "maps": sorted(maps.keys())}
    write_manifest(directory, m)
    return out


def register():
    pass


def unregister():
    pass
