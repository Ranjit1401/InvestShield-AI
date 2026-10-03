"""Localized report presentation resources (Phase 15).

The investigation itself is language-independent. Claims, entities,
red flags, verification verdicts, evidence and the risk score are
canonical and stay in the language the pipeline produced them in.
Only the *presentation* layer — section titles, controlled-vocabulary
labels and the fixed report prose — is localized, and it is localized
deterministically from the dictionaries the language modules export.

Each language module (``en``, ``hi``, ``mr``) exports a ``RESOURCES``
mapping with three parts: ``sections``, ``labels`` and ``fixed_text``.
This package registers them by :class:`~app.schemas.api.Language` and
exposes the one rule that makes a partial translation safe: **any leaf
the requested language does not provide is taken from English**, so an
unfinished translation degrades to the original wording instead of
producing an empty title or a missing label.
"""

from __future__ import annotations

from app.locales.en import RESOURCES as _ENGLISH
from app.locales.hi import RESOURCES as _HINDI
from app.locales.mr import RESOURCES as _MARATHI
from app.schemas.api import Language

__all__ = ["RESOURCES_BY_LANGUAGE", "deep_merge", "resources_for"]

#: The complete resource set per language. English is the base every
#: other language is merged over, so it is always present.
RESOURCES_BY_LANGUAGE: dict[Language, dict[str, dict]] = {
    Language.EN: _ENGLISH,
    Language.HI: _HINDI,
    Language.MR: _MARATHI,
}


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` onto ``base`` without mutating either.

    A key present in both maps to a dictionary in both is merged
    recursively; anything else in ``override`` replaces ``base``. A key
    absent from ``override`` is inherited from ``base`` unchanged, which
    is the rule that makes a missing translation fall back to English.

    Args:
        base: The fallback mapping (English).
        override: The requested language's mapping.

    Returns:
        A new mapping with every leaf present in either input.
    """
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def resources_for(language: Language) -> dict[str, dict]:
    """Return the presentation resources for a language, English-filled.

    The requested language's resources are deep-merged over the English
    base, so the result always carries every section title, every label
    and every piece of fixed prose. A language that is registered but
    partially translated, or not registered at all, therefore reads as
    English wherever it has no translation of its own.

    Args:
        language: The language the report is rendered in.

    Returns:
        A merged ``sections`` / ``labels`` / ``fixed_text`` mapping.
    """
    requested = RESOURCES_BY_LANGUAGE.get(language, _ENGLISH)
    return deep_merge(_ENGLISH, requested)
