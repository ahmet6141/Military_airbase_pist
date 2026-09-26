"""Rebuild scheduler: debounced regeneration triggered by property updates."""
from __future__ import annotations

import time
import traceback

import bpy

_pending: dict[str, set[str]] = {}     # root object name -> kinds
_timer_registered = False
_DEBOUNCE = 0.25
_last_request = 0.0
_busy = False


def on_param_change(root: bpy.types.Object | None, kind: str = 'GEOMETRY') -> None:
    """Called from property update callbacks."""
    global _timer_registered, _last_request
    if root is None or not root.get("mad_base_root"):
        return
    scene = bpy.context.scene
    ui = getattr(scene, "mad_ui", None)
    if ui is None or not ui.live_update:
        return
    _pending.setdefault(root.name, set()).add(kind)
    _last_request = time.time()
    if not _timer_registered and hasattr(bpy.app, "timers"):
        bpy.app.timers.register(_flush, first_interval=_DEBOUNCE)
        _timer_registered = True


def _flush():
    global _timer_registered, _busy
    if time.time() - _last_request < _DEBOUNCE * 0.8:
        return _DEBOUNCE
    _timer_registered = False
    if _busy:
        return None
    _busy = True
    try:
        items = list(_pending.items())
        _pending.clear()
        for name, kinds in items:
            root = bpy.data.objects.get(name)
            if root is None:
                continue
            rebuild_root(root, kinds)
    except Exception:  # never let an exception kill the timer loop silently
        traceback.print_exc()
    finally:
        _busy = False
    return None


def rebuild_root(root: bpy.types.Object, kinds: set[str] | None = None) -> None:
    """Regenerate a base. ``kinds`` narrows the work (MATERIAL / LIGHTING / GEOMETRY)."""
    from ..layout import builder
    kinds = kinds or {'GEOMETRY'}
    if kinds == {'MATERIAL'}:
        builder.update_materials(root)
    else:
        builder.build_base(root)


def is_busy() -> bool:
    return _busy
