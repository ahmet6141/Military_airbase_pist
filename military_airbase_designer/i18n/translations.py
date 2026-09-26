"""Turkish (tr_TR) UI translation via ``bpy.app.translations``.

What gets translated
    Property names, descriptions (tooltips), enum item names/descriptions, panel labels,
    operator labels/descriptions and report messages of this add-on. Blender looks every
    label up at draw time, so nothing in the other modules has to change: they keep plain
    English strings and Blender substitutes the Turkish text from :mod:`tr_TR`.

Modes (add-on preference ``language`` in ``prefs.py``)
    ``AUTO``  Follow Blender. The table is registered under the ``tr_TR`` locale only, so it is
              used when Blender's interface language is Türkçe (Preferences > Interface >
              Language, with the Tooltips / Interface translation options enabled).
    ``TR``    Turkish even when Blender runs in another language: the same table is registered a
              second time under the locale Blender is currently running
              (``bpy.app.translations.locale``, e.g. ``en_US``), and Blender's Interface /
              Tooltips / Reports translation switches are turned on (they are greyed out in the
              preferences UI for ``en_US``, so the add-on has to do it).
    ``EN``    English always: nothing is registered, even on a Turkish Blender.

Registration order
    ``__init__`` registers this module *before* ``prefs``, so the preference cannot be read
    during :func:`register`. The table is registered for ``tr_TR`` right away and a one-shot
    ``bpy.app.timers`` callback re-evaluates the preference as soon as Blender is idle; it also
    subscribes (``bpy.msgbus``) to the ``language`` preference so a change in the Preferences
    window re-registers the dictionary live. Under the pip ``bpy`` module (no window loop,
    Blender built without internationalization) all of this degrades to no-ops.

Runtime strings
    :func:`tr` translates strings that are not looked up by Blender itself, or that are built
    with f-strings (plan warnings shown in the panel, report messages). It tries
    ``pgettext_iface`` first, then the table directly (``TR`` mode / pip module), then the
    ``{placeholder}`` templates of :mod:`tr_TR` by regular expression. Use
    ``tr("Removed {n} objects").format(n=n)`` for new messages, or pass the finished English
    message through :func:`tr` / :func:`tr_lines`.
"""
from __future__ import annotations

import re

import bpy

from . import tr_TR

__all__ = (
    "LOCALE", "TRANSLATIONS", "MODES", "tr", "tr_lines", "is_turkish", "language_mode",
    "blender_locale", "build_translations_dict", "refresh", "set_mode_override",
    "register", "unregister",
)

LOCALE = "tr_TR"
TRANSLATIONS = tr_TR.TRANSLATIONS
MODES = ('AUTO', 'EN', 'TR')

# Unique name for bpy.app.translations (works for legacy add-on and bl_ext.* extension installs).
I18N_NAME = __name__
# Top-level package of the add-on: "<addon>.i18n" -> "<addon>" (also "bl_ext.repo.<addon>").
ADDON_PACKAGE = __package__.rsplit(".", 1)[0] if __package__ and "." in __package__ else (__package__ or "military_airbase_designer")

_FORCE_MODE: str | None = None          # programmatic / test override of the preference
_registered = False                     # table currently registered with Blender
_registered_mode: str | None = None     # mode the current registration was made for
_registered_locale: str | None = None   # extra locale key used ('' = none)
_msgbus_owner = object()
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_TEMPLATES: list[tuple[re.Pattern, str]] | None = None


# --------------------------------------------------------------------------- preferences / mode
def _addon_prefs():
    """AddonPreferences instance or None (pip bpy, prefs class not registered yet, ...)."""
    try:
        from ..core.scene_utils import addon_prefs
        p = addon_prefs()
        if p is not None:
            return p
    except Exception:
        pass
    try:
        addon = bpy.context.preferences.addons.get(ADDON_PACKAGE)
        return addon.preferences if addon is not None else None
    except Exception:
        return None


def language_mode() -> str:
    """'AUTO' | 'EN' | 'TR' from the add-on preference (AUTO when prefs are unavailable)."""
    if _FORCE_MODE in MODES:
        return _FORCE_MODE
    prefs = _addon_prefs()
    mode = getattr(prefs, "language", 'AUTO') if prefs is not None else 'AUTO'
    return mode if mode in MODES else 'AUTO'


def set_mode_override(mode: str | None) -> None:
    """Force a mode regardless of the preference (None = follow the preference) and re-register."""
    global _FORCE_MODE
    _FORCE_MODE = mode if mode in MODES else None
    refresh()


def blender_locale() -> str:
    """Locale Blender is running ('' under the pip module / builds without i18n)."""
    try:
        return bpy.app.translations.locale or ""
    except Exception:
        return ""


def _view_prefs():
    try:
        return bpy.context.preferences.view
    except Exception:
        return None


def is_turkish() -> bool:
    """True when this add-on's UI should be Turkish right now."""
    mode = language_mode()
    if mode == 'TR':
        return True
    if mode == 'EN':
        return False
    view = _view_prefs()
    translating = bool(view is not None and getattr(view, "use_translate_interface", False))
    return translating and blender_locale().lower().startswith("tr")


# --------------------------------------------------------------------------- dictionary
def build_translations_dict(force_locale: str | None = None) -> dict:
    """``{"tr_TR": {(ctx, msgid): tr, ...}}`` plus the same table under ``force_locale``."""
    table = dict(tr_TR.TRANSLATIONS)
    out = {LOCALE: table}
    if force_locale and force_locale != LOCALE:
        out[force_locale] = table
    return out


def _extra_locale_for(mode: str) -> str:
    """Locale key the table must additionally be registered under (TR mode on a non-Turkish Blender)."""
    if mode != 'TR':
        return ""
    loc = blender_locale()
    if not loc or loc.lower().startswith("tr"):
        return ""
    return loc


def _enable_blender_translation_flags() -> None:
    """Blender consults add-on dictionaries only when its own translation switches are on.

    For en_US those checkboxes are greyed out in the Preferences UI, so in 'TR' mode the add-on
    switches Interface / Tooltips / Reports translation on itself (Blender's own UI stays
    English because Blender ships no en_US catalogue)."""
    view = _view_prefs()
    if view is None:
        return
    for attr in ("use_translate_interface", "use_translate_tooltips", "use_translate_reports"):
        try:
            if hasattr(view, attr) and not getattr(view, attr):
                setattr(view, attr, True)
        except Exception:
            pass


def _do_unregister() -> None:
    global _registered
    try:
        bpy.app.translations.unregister(I18N_NAME)
    except Exception:
        pass
    _registered = False


def _do_register(mode: str) -> None:
    global _registered, _registered_mode, _registered_locale
    _do_unregister()
    _registered_mode = mode
    _registered_locale = ""
    if mode == 'EN':
        return                                  # stay English even on a Turkish Blender
    extra = _extra_locale_for(mode)
    d = build_translations_dict(extra or None)
    try:
        bpy.app.translations.register(I18N_NAME, d)
    except ValueError:                          # a previous instance leaked its registration
        try:
            bpy.app.translations.unregister(I18N_NAME)
            bpy.app.translations.register(I18N_NAME, d)
        except Exception:
            return
    except Exception:
        return
    _registered = True
    _registered_locale = extra
    if extra:
        _enable_blender_translation_flags()


def refresh() -> bool:
    """Re-register according to the current preference / Blender locale. Returns True if changed."""
    mode = language_mode()
    extra = _extra_locale_for(mode)
    if _registered_mode == mode and _registered_locale == extra and (_registered or mode == 'EN'):
        return False
    _do_register(mode)
    _redraw_all()
    return True


def _redraw_all() -> None:
    try:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                area.tag_redraw()
    except Exception:
        pass


# --------------------------------------------------------------------------- deferred preference hook
def _on_language_changed(*_args) -> None:
    refresh()


def _subscribe_prefs() -> None:
    prefs = _addon_prefs()
    if prefs is None or not hasattr(bpy, "msgbus"):
        return
    try:
        bpy.msgbus.clear_by_owner(_msgbus_owner)
        bpy.msgbus.subscribe_rna(key=(type(prefs), "language"), owner=_msgbus_owner, args=(),
                                 notify=_on_language_changed, options={'PERSISTENT'})
    except Exception:
        pass


def _deferred_refresh():
    """One-shot timer: prefs exist now (prefs module registered after this one)."""
    try:
        _subscribe_prefs()
        refresh()
    except Exception:
        pass
    return None


def _schedule_deferred_refresh() -> None:
    timers = getattr(bpy.app, "timers", None)
    if timers is None:
        return
    try:
        if not timers.is_registered(_deferred_refresh):
            timers.register(_deferred_refresh, first_interval=0.0, persistent=True)
    except Exception:
        pass


# --------------------------------------------------------------------------- runtime strings
def _templates() -> list[tuple[re.Pattern, str]]:
    global _TEMPLATES
    if _TEMPLATES is None:
        items = []
        for (ctx, msgid), turkish in tr_TR.TRANSLATIONS.items():
            if ctx != "*" or not _PLACEHOLDER.search(msgid):
                continue
            parts = _PLACEHOLDER.split(msgid)        # literal, name, literal, name, ...
            regex = "^" + "".join(re.escape(p) if i % 2 == 0 else "(?P<%s>.*?)" % p
                                  for i, p in enumerate(parts)) + "$"
            try:
                items.append((re.compile(regex, re.S), turkish))
            except re.error:
                continue
        _TEMPLATES = items
    return _TEMPLATES


def _translate_template(msg: str) -> str:
    for rx, turkish in _templates():
        m = rx.match(msg)
        if m:
            try:
                return turkish.format(**m.groupdict())
            except (KeyError, IndexError, ValueError):
                return msg
    return msg


def tr(msg: str) -> str:
    """Translate a runtime string (warnings, report messages, dynamic labels).

    Falls back to the English text when the add-on is not in Turkish mode or no entry exists.
    Messages built with f-strings are matched against the ``{placeholder}`` templates of tr_TR."""
    if not msg:
        return msg
    try:
        out = bpy.app.translations.pgettext_iface(msg)
    except Exception:
        out = msg
    if out != msg:
        return out
    if not is_turkish():
        return msg
    hit = tr_TR.TRANSLATIONS.get(("*", msg))
    if hit is not None:
        return hit
    return _translate_template(msg)


def tr_lines(text: str) -> str:
    """:func:`tr` applied to every line of a multi-line string (e.g. ``Object.mad.warnings``)."""
    if not text:
        return text
    return "\n".join(tr(line) for line in text.split("\n"))


# --------------------------------------------------------------------------- register
def register() -> None:
    _do_register(language_mode())       # prefs usually unavailable yet -> plain tr_TR registration
    _schedule_deferred_refresh()        # re-evaluate the preference once Blender is idle


def unregister() -> None:
    global _registered_mode, _registered_locale
    try:
        bpy.msgbus.clear_by_owner(_msgbus_owner)
    except Exception:
        pass
    try:
        if bpy.app.timers.is_registered(_deferred_refresh):
            bpy.app.timers.unregister(_deferred_refresh)
    except Exception:
        pass
    _do_unregister()
    _registered_mode = None
    _registered_locale = None
