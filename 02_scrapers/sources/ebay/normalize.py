from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any


CONDITION_NAMES = {
    "1000": "NEW",
    "1500": "NEW_OTHER",
    "2000": "CERTIFIED_REFURBISHED",
    "2010": "EXCELLENT_REFURBISHED",
    "2020": "VERY_GOOD_REFURBISHED",
    "2030": "GOOD_REFURBISHED",
    "2500": "SELLER_REFURBISHED",
}

BRANDS = [
    "Apple", "Samsung", "Google", "Xiaomi", "Redmi", "OnePlus",
    "Huawei", "Honor", "Oppo", "Realme", "Motorola", "Sony",
    "Nokia", "Nothing", "Asus", "Vivo", "ZTE", "TCL", "Doro",
]

ASPECT_ALIASES = {
    "brand": {"brand", "marque"},
    "model": {"model", "modèle", "model number", "numéro de modèle"},
    "storage": {
        "storage capacity", "capacité de stockage", "stockage",
        "internal storage", "mémoire interne", "rom",
    },
    "ram": {"ram", "ram capacity", "mémoire vive"},
    "color": {"color", "colour", "couleur"},
    "variant": {"variant", "version", "model variant", "variante"},
}


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def norm_key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def aspect_map(item: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for aspect in item.get("localizedAspects", []) or []:
        name = clean_text(aspect.get("name"))
        value = clean_text(aspect.get("value"))
        if name and value:
            result.setdefault(norm_key(name), []).append(value)
    return result


def get_aspect(aspects: dict[str, list[str]], aliases: set[str]) -> str | None:
    aliases_norm = {norm_key(x) for x in aliases}
    for key, values in aspects.items():
        if key in aliases_norm and values:
            return clean_text(values[0])
    return None


def parse_gb(value: str | None) -> float | None:
    if not value:
        return None
    text = value.lower().replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(tb|to|gb|go)\b", text)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2)
    if unit in {"tb", "to"}:
        number *= 1024
    return number


def parse_ram(value: str | None) -> float | None:
    if not value:
        return None
    text = value.lower().replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)\s*(gb|go)\b", text)
    return float(match.group(1)) if match else None


def first_brand_from_title(title: str | None) -> str | None:
    if not title:
        return None
    low = title.casefold()
    for brand in BRANDS:
        if re.search(rf"\b{re.escape(brand.casefold())}\b", low):
            return brand
    return None


def fallback_model(title: str | None, brand: str | None) -> str | None:
    if not title:
        return None
    text = title

    # Heuristiques prudentes : elles complètent seulement les aspects absents.
    patterns = [
        r"\b(iPhone\s+(?:\d{1,2}(?:\s+(?:Pro|Pro Max|Plus|mini|SE))?(?:\s+\d(?:GB|Go))?))\b",
        r"\b(Galaxy\s+[A-Z]?\s*\d{1,3}(?:\s+(?:Ultra|Plus|\+|FE|Fold|Flip))?)\b",
        r"\b(Pixel\s+\d{1,2}(?:\s+(?:Pro|XL|a))?)\b",
        r"\b(Redmi\s+(?:Note\s+)?\d{1,2}(?:\s+(?:Pro|Pro\+|Ultra))?)\b",
        r"\b(Xiaomi\s+\d{1,3}(?:\s+(?:Pro|Pro\+|Ultra|T))?)\b",
        r"\b(OnePlus\s+\d{1,2}(?:\s+(?:Pro|T|R))?)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            return clean_text(m.group(1))

    if brand:
        brand_re = re.escape(brand)
        m = re.search(
            rf"\b{brand_re}\s+([A-Za-z0-9][A-Za-z0-9 .+\-]{1,40})",
            text,
            flags=re.I,
        )
        if m:
            candidate = re.split(
                r"\b(?:5G|4G|LTE|Dual SIM|SIM|GB|Go|RAM|ROM|Unlocked|Débloqué)\b",
                m.group(1),
                flags=re.I,
            )[0]
            return clean_text(candidate)

    return None


def classify_condition(condition_id: Any, condition_name: str | None) -> str | None:
    cid = str(condition_id) if condition_id is not None else ""
    if cid in {"1000", "1500"}:
        return "NEW"
    if cid in {"2000", "2010", "2020", "2030", "2500"}:
        return "REFURBISHED"

    text = (condition_name or "").casefold()
    if "refurb" in text or "recondition" in text:
        return "REFURBISHED"
    if "new" in text or "neuf" in text:
        return "NEW"
    return None


def normalize_item(item: dict[str, Any]) -> dict[str, Any] | None:
    title = clean_text(item.get("title"))

    price = item.get("price")
    if not isinstance(price, dict):
        price = {}

    condition = item.get("condition")

    if isinstance(condition, dict):
        condition_id = (
            condition.get("conditionId")
            or item.get("conditionId")
            or item.get("conditionIdPath")
        )

        condition_name = (
            clean_text(condition.get("conditionDisplayName"))
            or clean_text(condition.get("conditionDescription"))
            or CONDITION_NAMES.get(str(condition_id))
        )

    elif isinstance(condition, str):
        condition_id = (
            item.get("conditionId")
            or item.get("conditionIdPath")
        )

        condition_name = clean_text(condition) or CONDITION_NAMES.get(
            str(condition_id)
        )

    else:
        condition_id = (
            item.get("conditionId")
            or item.get("conditionIdPath")
        )

        condition_name = CONDITION_NAMES.get(str(condition_id))

    condition_class = classify_condition(condition_id, condition_name)
    if condition_class not in {"NEW", "REFURBISHED"}:
        return None

    aspects = aspect_map(item)

    brand = get_aspect(aspects, ASPECT_ALIASES["brand"]) or first_brand_from_title(title)
    model = get_aspect(aspects, ASPECT_ALIASES["model"]) or fallback_model(title, brand)
    variant = get_aspect(aspects, ASPECT_ALIASES["variant"])

    storage_raw = get_aspect(aspects, ASPECT_ALIASES["storage"])
    ram_raw = get_aspect(aspects, ASPECT_ALIASES["ram"])
    color = get_aspect(aspects, ASPECT_ALIASES["color"])

    storage_gb = parse_gb(storage_raw)
    ram_gb = parse_ram(ram_raw)

    # Fallbacks très prudents sur le titre.
    if storage_gb is None and title:
        m = re.search(r"\b(32|64|128|256|512|1024)\s*(GB|Go|TB|To)\b", title, re.I)
        if m:
            storage_gb = parse_gb(m.group(0))

    if ram_gb is None and title:
        m = re.search(r"\b([2-9]\d?|1[0-9]|32)\s*(GB|Go)\s*RAM\b", title, re.I)
        if m:
            ram_gb = parse_ram(m.group(0))

    if variant is None and model and title:
        m = re.search(
            r"\b(Pro Max|Pro\+|Ultra|Plus|mini|Max|FE|SE)\b",
            title,
            re.I,
        )
        if m:
            variant = m.group(1)

    extracted_at = datetime.now(timezone.utc).isoformat()

    return {
        "ebay_item_id": clean_text(item.get("itemId")),
        "item_group_id": clean_text(item.get("itemGroupId")),
        "title": title,
        "brand": brand,
        "model": model,
        "variant": variant,
        "price": price.get("value"),
        "currency": clean_text(price.get("currency")),
        "condition_id": str(condition_id) if condition_id is not None else None,
        "condition_name": condition_name,
        "condition_class": condition_class,
        "storage_gb": storage_gb,
        "ram_gb": ram_gb,
        "color": color,
        "category_id": clean_text(
            (item.get("categories") or [{}])[0].get("categoryId")
        ),
        "category_name": clean_text(
            (item.get("categories") or [{}])[0].get("categoryName")
        ),
        "item_url": clean_text(item.get("itemWebUrl")),
        "item_creation_date": clean_text(item.get("itemCreationDate")),
        "item_end_date": clean_text(item.get("itemEndDate")),
        "extracted_at": extracted_at,
    }
