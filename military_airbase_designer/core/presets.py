"""Preset system: JSON files in the package ``presets/`` folder plus user presets.

A preset is a nested dict mirroring the property groups (``runway``, ``markings``,
``taxiways`` (list), ``aprons`` (list), ``lighting`` ...). Missing keys keep the
defaults, so presets stay short and readable.
"""
from __future__ import annotations

import json
import os

import bpy

from .props import resume_updates, suspend_updates

PRESET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "presets")


def user_preset_dir() -> str:
    base = bpy.utils.user_resource('SCRIPTS', path=os.path.join("presets", "military_airbase_designer"), create=True)
    return base


def list_presets() -> list[tuple[str, str, str]]:
    """[(identifier, label, path)] bundled first, then user presets."""
    out = []
    for d in (PRESET_DIR, user_preset_dir()):
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.lower().endswith(".json"):
                path = os.path.join(d, fn)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    label = data.get("label", fn[:-5])
                except Exception:
                    label = fn[:-5]
                out.append((fn[:-5], label, path))
    return out


def load_preset(identifier: str) -> dict:
    for ident, _label, path in list_presets():
        if ident == identifier:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    raise KeyError(identifier)


def _apply_group(pg, data: dict) -> None:
    for k, v in data.items():
        if not hasattr(pg, k):
            continue
        try:
            setattr(pg, k, v)
        except (TypeError, ValueError):
            pass


def apply_preset(root: bpy.types.Object, identifier_or_data) -> None:
    data = identifier_or_data if isinstance(identifier_or_data, dict) else load_preset(identifier_or_data)
    s = root.mad
    suspend_updates()
    try:
        for group in ("runway", "markings", "lighting", "signage", "structures", "perimeter", "terrain", "materials", "export"):
            if group in data:
                _apply_group(getattr(s, group), data[group])
        for key in ("base_name", "icao_code", "quality"):
            if key in data:
                setattr(s, key, data[key])
        if "taxiways" in data:
            s.taxiways.clear()
            for item in data["taxiways"]:
                t = s.taxiways.add()
                _apply_group(t, item)
        if "aprons" in data:
            s.aprons.clear()
            for item in data["aprons"]:
                a = s.aprons.add()
                _apply_group(a, item)
        s.preset_name = data.get("label", str(identifier_or_data) if not isinstance(identifier_or_data, dict) else "")
    finally:
        resume_updates()


def _group_to_dict(pg) -> dict:
    out = {}
    for prop in pg.bl_rna.properties:
        if prop.identifier in ("rna_type", "name") and prop.identifier != "name":
            continue
        if prop.identifier == "rna_type":
            continue
        val = getattr(pg, prop.identifier)
        if prop.type in ('POINTER', 'COLLECTION'):
            continue
        if prop.type == 'FLOAT' and getattr(prop, "array_length", 0):
            val = list(val)
        out[prop.identifier] = val
    return out


def settings_to_dict(root: bpy.types.Object, label: str = "") -> dict:
    s = root.mad
    data = {"label": label or s.base_name, "base_name": s.base_name, "icao_code": s.icao_code, "quality": s.quality}
    for group in ("runway", "markings", "lighting", "signage", "structures", "perimeter", "terrain", "materials", "export"):
        data[group] = _group_to_dict(getattr(s, group))
    data["taxiways"] = [_group_to_dict(t) for t in s.taxiways]
    data["aprons"] = [_group_to_dict(a) for a in s.aprons]
    return data


def save_user_preset(root: bpy.types.Object, name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name.strip()) or "preset"
    path = os.path.join(user_preset_dir(), safe + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings_to_dict(root, name), f, indent=2)
    return path
