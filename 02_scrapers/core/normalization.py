"""Repair-type and device normalization using ``pannes_mapping_final.json``."""

from __future__ import annotations

import json
import os
import unicodedata
from functools import lru_cache
from pathlib import Path

from scraper.core.parser_utils import clean_text

_DEFAULT_MAPPING = Path(__file__).resolve().parent.parent / "data" / "pannes_mapping_final.json"

# WeFix URL path segment (slug) -> human-readable device family for reporting.
_DEVICE_SLUG_TO_FAMILY: dict[str, str] = {
    "smartphone": "Smartphone",
    "tablette": "Tablette",
    "pc-portable": "Ordinateur portable",
    "console": "Console",
}


def default_mapping_path() -> Path:
    """Path to the bundled mapping file (overridable via ``PANNES_MAPPING_PATH``)."""
    env = os.environ.get("PANNES_MAPPING_PATH", "").strip()
    return Path(env) if env else _DEFAULT_MAPPING


@lru_cache(maxsize=8)
def _load_mapping_cached(path_str: str) -> dict[str, list[str]]:
    path = Path(path_str)
    if not path.is_file():
        raise FileNotFoundError(f"Pannes mapping not found: {path}")
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Pannes mapping must be a JSON object of label -> aliases")
    out: dict[str, list[str]] = {}
    for canon, aliases in data.items():
        if not isinstance(canon, str):
            continue
        if isinstance(aliases, list):
            out[canon] = [a for a in aliases if isinstance(a, str)]
        else:
            out[canon] = []
    return out


def load_pannes_mapping(path: Path | None = None) -> dict[str, list[str]]:
    """Load the canonical repair label -> keyword aliases mapping."""
    p = path or default_mapping_path()
    return _load_mapping_cached(str(p.resolve()))


def _fold_for_match(text: str) -> str:
    """Lowercase + NFKC for stable substring matching."""
    t = clean_text(text)
    if not t:
        return ""
    t = unicodedata.normalize("NFKC", t)
    return t.casefold()


def _build_alias_index(mapping: dict[str, list[str]]) -> list[tuple[str, str, int]]:
    """
    Flatten to (canonical_label, alias_folded, alias_len), longest aliases first.
    """
    rows: list[tuple[str, str, int]] = []
    for canon, aliases in mapping.items():
        cfold = _fold_for_match(canon)
        rows.append((canon, cfold, len(cfold)))
        for a in aliases:
            af = _fold_for_match(a)
            if af:
                rows.append((canon, af, len(af)))
    rows.sort(key=lambda x: x[2], reverse=True)
    return rows


@lru_cache(maxsize=8)
def _alias_index(path_str: str) -> tuple[tuple[str, str, int], ...]:
    mapping = _load_mapping_cached(path_str)
    return tuple(_build_alias_index(mapping))


def normalize_repair_type(
    raw_label: str,
    *,
    mapping_path: Path | None = None,
) -> tuple[str, bool]:
    """
    Map a free-text repair / part label to a canonical label from ``pannes_mapping_final.json``.

    Matching strategy:
    1. Exact case-insensitive match on a canonical key.
    2. Longest substring match on any canonical key or alias (prefers specific phrases).

    Returns ``(normalized_label, True)`` when a mapping rule matched, else
    ``(clean_text(raw_label), False)``.
    """
    raw = clean_text(raw_label)
    if not raw:
        return "", False

    path = (mapping_path or default_mapping_path()).resolve()
    path_str = str(path)
    try:
        index = _alias_index(path_str)
        mapping = _load_mapping_cached(path_str)
    except (OSError, ValueError, json.JSONDecodeError):
        return raw, False

    folded = _fold_for_match(raw)

    # Exact canonical key
    for canon in mapping.keys():
        if _fold_for_match(canon) == folded:
            return canon, True

    best_canon: str | None = None
    best_len = 0
    for canon, alias_folded, ln in index:
        if ln <= 0:
            continue
        if alias_folded and alias_folded in folded:
            if ln > best_len:
                best_len = ln
                best_canon = canon

    if best_canon is not None:
        return best_canon, True

    return raw, False


def normalize_device_name(
    device_type_slug: str | None = None,
    *,
    brand: str | None = None,
    model: str | None = None,
    series: str | None = None,
) -> dict[str, str]:
    """
    Normalize device fields for cross-source reporting.

    ``device_type_slug`` is the WeFix URL segment (e.g. ``smartphone``, ``pc-portable``).
    When absent (e.g. Utopya), ``series`` can stand in as the product line label.
    Brand and model are cleaned for whitespace.
    """
    slug = clean_text(device_type_slug or "").lower().replace(" ", "-")
    if slug in _DEVICE_SLUG_TO_FAMILY:
        family = _DEVICE_SLUG_TO_FAMILY[slug]
    elif slug:
        family = clean_text(device_type_slug or "")
    elif clean_text(series or ""):
        family = clean_text(series or "")
    else:
        family = "Non spécifié"

    return {
        "device_family": family,
        "brand": clean_text(brand or ""),
        "model": clean_text(model or ""),
        "series": clean_text(series or ""),
    }

