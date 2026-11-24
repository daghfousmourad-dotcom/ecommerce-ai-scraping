"""Save.co repair scraper: brand/model discovery via public HTML pages."""

from __future__ import annotations

import sys
from pathlib import Path
_SCRAPERS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRAPERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT))
_CORE_DIR = _SCRAPERS_ROOT / 'core'
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import csv
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import os

import requests
from tqdm import tqdm

from scraper.core.filtering import clean_dataset
from scraper.core.http_client import create_session, get_html, polite_sleep, request_with_retry
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.run_report import (
    REASON_FETCH_ERROR,
    REASON_NO_REPAIR_CATEGORIES,
    fetch_issue,
    finalize_run_artifacts,
    summarize_run_line,
)
from scraper.core.normalization import normalize_device_name, normalize_repair_type
from scraper.core.parser_utils import clean_text, dedupe_dict_rows, now_iso_utc, parse_html

BASE_URL = "https://save.co"
REPARATION_INDEX_URL = f"{BASE_URL}/reparation"
APIDAI_SPAREPART_PRICES_URL = "https://apidai.save.co/website/v1/prices/SPAREPART"
# APIDAI returns the same spare-part prices regardless of shop; one code is enough to read prices.
# Can be overridden with SAVE_PRICE_SHOP_CODE_FALLBACK (used only if the site shop list is missing).
_DEFAULT_PRICE_SHOP_CODE = "FR005"

# API path uses PCONNECTOR; site icons may expose CON.
_API_REPAIR_CODE_ALIASES: dict[str, str] = {
    "CON": "PCONNECTOR",
}

_GRID_UL_SELECTOR = 'ul[class*="RepairGrid__GridList"]'
_BRAND_EVENT = "select-brand"
_MODEL_EVENT = "select-model"

_FIELDSET_SELECTOR = 'fieldset[class*="RepairSelectOptions__FieldSet"]'
_OPTION_LINK_SELECTOR = 'a[class*="RepairSelectOptions__OptionLink"]'
_REPAIR_ICON_ALT_RE = re.compile(r"repair\s+icon:\s*([A-Za-z0-9_]+)", re.I)
_ICON_FILENAME_RE = re.compile(r"Icon_([A-Za-z0-9_]+)\.svg", re.I)

# Repair icon / API code -> last path segment before shop code (see Save booking URLs).
_REPAIR_ICON_TO_CATEGORY_SLUG: dict[str, str] = {
    "DOX": "desoxydation",
    "LCD": "reparation-ecran",
    "SWAP": "echange-standard",
    "BAT": "remplacement-batterie",
    "BTN_VOL": "remplacement-bouton-volume",
    "BTN_POW": "remplacement-bouton-power",
    "BTN_HOME": "remplacement-bouton-home",
    "BTN_VIB": "remplacement-vibreur",
    "VIB": "remplacement-vibreur",
    "PCONNECTOR": "remplacement-connecteur-charge",
    "CON": "remplacement-connecteur-charge",
    "CAM_REAR": "remplacement-camera-arriere",
    "CAM_FRONT": "remplacement-camera-avant",
    "RNL": "mise-a-jour-logiciel",
    "BACK_COVER": "reparation-face-arriere",
    "TCT": "remplacement-tactile",
    "HP": "haut-parleur",
    "HAUT PARLEUR": "haut-parleur",
    "DIAG": "diagnostic",
    "ANT": "remplacement-antenne-reseau",
    "ECO": "remplacement-ecouteur-interne",
    "MIC": "remplacement-micro",
    "MULTI": "multiples-reparations",
}

_SHOP_LINK_SELECTOR = 'a[class*="ShopSelect__OptionLink"]'
_REPARATION_SHOP_PATH_RE = re.compile(
    r"/reparation/([^/]+)/([^/]+)/([^/]+)/([^/]+)/?$",
    re.I,
)


def _reparation_href_segments(href: str) -> tuple[str, ...] | None:
    """Return path segments after /reparation/ (brand only or brand + model slugs)."""
    if not href:
        return None
    path = href.split("?", 1)[0].strip()
    if path.startswith("http"):
        path = urlparse(path).path
    path = path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2 or parts[0] != "reparation":
        return None
    tail = tuple(parts[1:])
    if not tail or any(not p for p in tail):
        return None
    return tail


def _link_display_name(anchor) -> str:
    ga = anchor.get("data-ga4-param-value")
    if ga and clean_text(ga):
        return clean_text(ga)
    h2 = anchor.find("h2")
    if h2 and clean_text(h2.get_text()):
        return clean_text(h2.get_text())
    img = anchor.find("img")
    if img and img.get("alt"):
        return clean_text(img["alt"])
    return ""


def _parse_brands_from_soup(soup) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for a in soup.select(f"{_GRID_UL_SELECTOR} a[href^='/reparation/']"):
        href = (a.get("href") or "").strip()
        if not href.startswith("/reparation/"):
            continue
        if a.get("data-ga4-event") != _BRAND_EVENT:
            continue
        segs = _reparation_href_segments(href)
        if not segs or len(segs) != 1:
            continue
        brand_slug = segs[0]
        if not re.match(r"^[a-z0-9-]+$", brand_slug):
            continue
        label = _link_display_name(a)
        if not label:
            label = brand_slug.replace("-", " ").title()
        rows.append({"brand_slug": brand_slug, "brand_label": label})
    return dedupe_dict_rows(rows, ("brand_slug",))


def _parse_models_from_soup(soup, brand_slug: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for a in soup.select(f"{_GRID_UL_SELECTOR} a[href^='/reparation/']"):
        href = (a.get("href") or "").strip()
        if a.get("data-ga4-event") != _MODEL_EVENT:
            continue
        segs = _reparation_href_segments(href)
        if not segs or len(segs) != 2:
            continue
        bslug, model_slug = segs[0], segs[1]
        if bslug != brand_slug:
            continue
        if not re.match(r"^[a-z0-9-]+$", model_slug):
            continue
        name = _link_display_name(a)
        if not name:
            name = model_slug.replace("-", " ").title()
        rows.append(
            {
                "brand_slug": brand_slug,
                "model_slug": model_slug,
                "model_name": name,
            }
        )
    return dedupe_dict_rows(rows, ("brand_slug", "model_slug"))


def _repair_icon_code_from_img(img) -> str | None:
    """Icon id from alt='repair icon: LCD' or src='.../Icon_LCD.svg'."""
    if not img:
        return None
    alt = img.get("alt") or ""
    m = _REPAIR_ICON_ALT_RE.search(alt)
    if m:
        return m.group(1).strip().upper()
    src = img.get("src") or ""
    m2 = _ICON_FILENAME_RE.search(src)
    if m2:
        return m2.group(1).strip().upper()
    return None


def _parse_repair_categories_from_soup(soup) -> list[dict[str, str]]:
    """
    Parse repair option rows from a model page (e.g. /reparation/apple/iphone-17-pro-max).
    Category = repair icon code (LCD, BAT, …); label = French h2; section = fieldset legend.
    """
    rows: list[dict[str, str]] = []
    for fs in soup.select(_FIELDSET_SELECTOR):
        leg = fs.find("legend")
        section = clean_text(leg.get_text()) if leg else ""
        for a in fs.select(_OPTION_LINK_SELECTOR):
            img = a.find("img")
            code = _repair_icon_code_from_img(img)
            if not code:
                continue
            h2 = a.find("h2")
            label = clean_text(h2.get_text()) if h2 else ""
            rows.append(
                {
                    "repair_icon": code,
                    "category_label": label,
                    "section_label": section,
                }
            )
    return dedupe_dict_rows(rows, ("repair_icon", "section_label"))


def repair_icon_to_category_slug(repair_icon: str) -> str:
    """
    Map Save repair icon / pannes code (e.g. LCD, BAT) to the URL segment before the shop code.
    Raises ValueError if unknown.
    """
    raw = (repair_icon or "").strip()
    if not raw:
        raise ValueError("repair_icon is empty")
    key = raw.upper()
    if key in _REPAIR_ICON_TO_CATEGORY_SLUG:
        return _REPAIR_ICON_TO_CATEGORY_SLUG[key]
    key_spaced = " ".join(raw.split()).upper()
    if key_spaced in _REPAIR_ICON_TO_CATEGORY_SLUG:
        return _REPAIR_ICON_TO_CATEGORY_SLUG[key_spaced]
    raise ValueError(f"Unknown repair_icon for Save category URL: {repair_icon!r}")


def model_repair_shops_url(
    brand_slug: str,
    model_slug: str,
    *,
    repair_icon: str | None = None,
    category_slug: str | None = None,
) -> str:
    """
    Absolute URL for the shop list step:
    /reparation/{brand}/{model}/{category_slug} (e.g. .../reparation-ecran).
    Pass either repair_icon (mapped internally) or category_slug directly.
    """
    b = brand_slug.strip().strip("/").lower()
    m = model_slug.strip().strip("/").lower()
    if category_slug:
        cat = category_slug.strip().strip("/").lower()
    elif repair_icon is not None:
        cat = repair_icon_to_category_slug(repair_icon)
    else:
        raise ValueError("Provide category_slug or repair_icon")
    path = f"/reparation/{b}/{m}/{cat}"
    return urljoin(BASE_URL, path)


def _parse_shops_from_soup(soup) -> list[dict[str, str]]:
    """Parse shop codes from /reparation/{brand}/{model}/{category}/{shop_code} links."""
    rows: list[dict[str, str]] = []
    for a in soup.select(_SHOP_LINK_SELECTOR):
        href = (a.get("href") or "").strip()
        if not href.startswith("/reparation/"):
            continue
        m = _REPARATION_SHOP_PATH_RE.match(href.split("?", 1)[0].rstrip("/"))
        if not m:
            continue
        brand, model, _cat, shop_code = m.groups()
        label = clean_text(a.get_text())
        rows.append(
            {
                "shop_code": shop_code.upper(),
                "shop_label": label,
                "shop_href": href.split("?", 1)[0],
                "shop_url": urljoin(BASE_URL, href.split("?", 1)[0]),
            }
        )
    return dedupe_dict_rows(rows, ("shop_code",))


def discover_shops_for_model_repair(
    session: requests.Session,
    brand_slug: str,
    model_slug: str,
    repair_icon: str,
) -> list[dict[str, str]]:
    """
    Resolve category from repair_icon, GET the shop-selection page, return shop_code rows.
    Example flow: https://save.co/reparation/apple/iphone-17-pro-max/reparation-ecran
    """
    url = model_repair_shops_url(brand_slug, model_slug, repair_icon=repair_icon)
    html = get_html(session, url)
    return _parse_shops_from_soup(parse_html(html))


def _api_repair_code(code: str) -> str:
    u = code.strip().upper()
    return _API_REPAIR_CODE_ALIASES.get(u, u)


def _sparepart_repair_codes_segment(repair_codes: str | list[str]) -> str:
    if isinstance(repair_codes, list):
        parts = [_api_repair_code(c) for c in repair_codes if str(c).strip()]
        if not parts:
            raise ValueError("repair_codes list is empty")
        return "+".join(parts)
    raw = str(repair_codes).strip().upper()
    if not raw:
        raise ValueError("repair_codes is empty")
    if "+" in raw:
        return "+".join(_api_repair_code(p) for p in raw.split("+") if p.strip())
    return _api_repair_code(raw)


def sparepart_prices_api_url(
    brand_name: str,
    model_name: str,
    repair_codes: str | list[str],
    shop_code: str,
) -> str:
    """
    Build APIDAI URL, e.g.
    .../prices/SPAREPART/Xiaomi/Mi%2010T/PCONNECTOR+CAM_REAR/FR005
    """
    # Échappement des '/' dans les noms de modèles pour corriger les erreurs HTTP 400 (Point 2 de la réunion)
    # Ex: 'P40 Lite/nova 7i' -> 'P40 Lite', 'Tommy 3 Plus/Harry 2' -> 'Tommy 3 Plus'
    clean_model = model_name.strip()
    if "/" in clean_model:
        clean_model = clean_model.split("/")[0].strip()

    b = quote(brand_name.strip().replace("/", "-"), safe="")
    m = quote(clean_model, safe="")
    codes = _sparepart_repair_codes_segment(repair_codes)
    if not re.fullmatch(r"[A-Z0-9_+]+", codes):
        raise ValueError(f"Invalid repair_codes segment for API path: {codes!r}")
    shop = quote(shop_code.strip().upper(), safe="")
    return f"{APIDAI_SPAREPART_PRICES_URL}/{b}/{m}/{codes}/{shop}"


def _effective_price_ttc(price_ttc: Any, discount_ttc: Any) -> float | None:
    if price_ttc is None:
        return None
    try:
        base = float(price_ttc)
    except (TypeError, ValueError):
        return None
    if discount_ttc is None:
        return base
    try:
        return base - float(discount_ttc)
    except (TypeError, ValueError):
        return base


def parse_sparepart_prices_payload(
    data: dict[str, Any],
    *,
    shop_code: str,
) -> list[dict[str, Any]]:
    """
    Flatten categories[].pricings[]. Real TTC to charge: priceTTC - discountTTC (0 if discount null).
    """
    brand = data.get("brandName")
    model = data.get("modeleName")
    shop = shop_code.strip().upper()
    rows: list[dict[str, Any]] = []
    for cat in data.get("categories") or []:
        cat_key = cat.get("category")
        for p in cat.get("pricings") or []:
            pttc = p.get("priceTTC")
            disc = p.get("discountTTC")
            rows.append(
                {
                    "shop_code": shop,
                    "brand_name": brand,
                    "model_name": model,
                    "category": p.get("category") or cat_key,
                    "part_label": p.get("label"),
                    "reference": p.get("reference"),
                    "category_type": p.get("categoryType"),
                    "price_ht": p.get("priceHT"),
                    "price_ttc": pttc,
                    "discount_ttc": disc,
                    "discount_type": p.get("discountType"),
                    "price_effective_ttc": _effective_price_ttc(pttc, disc),
                }
            )
    return rows


def fetch_sparepart_prices(
    session: requests.Session,
    brand_name: str,
    model_name: str,
    repair_codes: str | list[str],
    shop_code: str,
    *,
    timeout_seconds: int = 30,
    retries: int = 3,
) -> list[dict[str, Any]]:
    """
    GET apidai.save.co spare-part pricing; one row per pricing line with price_effective_ttc.
    Use brand_label + model_name from discovery (API expects display names, not URL slugs).
    """
    url = sparepart_prices_api_url(brand_name, model_name, repair_codes, shop_code)
    response = request_with_retry(
        session,
        "GET",
        url,
        timeout_seconds=timeout_seconds,
        retries=retries,
        headers={"Accept": "application/json"},
    )
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("APIDAI prices response is not a JSON object")
    return parse_sparepart_prices_payload(data, shop_code=shop_code)


def discover_brands(session: requests.Session) -> list[dict[str, str]]:
    """Fetch /reparation and return brand_slug + brand_label for each brand tile."""
    html = get_html(session, REPARATION_INDEX_URL)
    return _parse_brands_from_soup(parse_html(html))


def discover_models_for_brand(
    session: requests.Session,
    brand_slug: str,
) -> list[dict[str, str]]:
    """Fetch /reparation/{brand_slug} and return model rows for that brand."""
    url = urljoin(BASE_URL, f"/reparation/{brand_slug.strip('/')}")
    html = get_html(session, url)
    return _parse_models_from_soup(parse_html(html), brand_slug.strip().lower())


def discover_brand_model_pairs(
    session: requests.Session,
    *,
    polite_between_brands: bool = True,
    log: bool = True,
) -> list[dict[str, Any]]:
    """
    Walk every brand page and return one row per (brand, model) with slugs and display names.
    """
    def _log(msg: str) -> None:
        if log:
            print(msg, flush=True)

    _log("[Save] Discovering brands from save.co/reparation …")
    brands = discover_brands(session)
    _log(f"[Save] Brands discovered: {len(brands)}.")
    _log("[Save] Discovering models for each brand …")
    pairs: list[dict[str, Any]] = []
    for i, b in enumerate(brands):
        slug = b["brand_slug"]
        label = b["brand_label"]
        if i and polite_between_brands:
            polite_sleep()
        _log(f"[Save]   → models for {label} ({slug}) …")
        models = discover_models_for_brand(session, slug)
        _log(f"[Save]   ← {len(models)} model(s) for {label}.")
        for m in models:
            pairs.append(
                {
                    "brand_slug": slug,
                    "brand_label": label,
                    "model_slug": m["model_slug"],
                    "model_name": m["model_name"],
                    "model_url": urljoin(BASE_URL, f"/reparation/{slug}/{m['model_slug']}"),
                }
            )
    out = dedupe_dict_rows(pairs, ("brand_slug", "model_slug"))
    _log(
        f"[Save] Discovery finished: {len(brands)} brand(s), "
        f"{len(out)} brand/model pair(s) (after dedupe)."
    )
    return out


def discover_repair_categories_for_model(
    session: requests.Session,
    brand_slug: str,
    model_slug: str,
) -> list[dict[str, str]]:
    """
    Fetch /reparation/{brand}/{model} and return repair categories (icon codes + labels).
    """
    b = brand_slug.strip().strip("/").lower()
    m = model_slug.strip().strip("/").lower()
    url = urljoin(BASE_URL, f"/reparation/{b}/{m}")
    html = get_html(session, url)
    return _parse_repair_categories_from_soup(parse_html(html))


def discover_brand_model_with_categories(
    session: requests.Session,
    *,
    polite_between_requests: bool = True,
) -> list[dict[str, Any]]:
    """
    For each brand/model pair, attach repair categories (one row per brand/model/category).
    """
    pairs = discover_brand_model_pairs(
        session, polite_between_brands=polite_between_requests
    )
    out: list[dict[str, Any]] = []
    for i, row in enumerate(pairs):
        if i and polite_between_requests:
            polite_sleep()
        cats = discover_repair_categories_for_model(
            session, row["brand_slug"], row["model_slug"]
        )
        if not cats:
            out.append(
                {
                    **row,
                    "repair_icon": "",
                    "category_label": "",
                    "section_label": "",
                }
            )
            continue
        for c in cats:
            out.append({**row, **c})
    return dedupe_dict_rows(
        out,
        ("brand_slug", "model_slug", "repair_icon", "section_label"),
    )


def _resolve_shops_for_model(
    session: requests.Session,
    pair: dict[str, Any],
    categories: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Load shop list from the first repair type on the model that has a known booking slug."""
    for c in categories:
        icon = (c.get("repair_icon") or "").strip()
        if not icon:
            continue
        try:
            repair_icon_to_category_slug(icon)
        except ValueError:
            continue
        shops = discover_shops_for_model_repair(
            session, pair["brand_slug"], pair["model_slug"], icon
        )
        if shops:
            return shops
    return []


def _reference_shop_for_apidai(
    session: requests.Session,
    pairs: list[dict[str, Any]],
) -> tuple[dict[str, str], bool]:
    """
    Pick one shop code for all pricing API calls (prices do not require iterating every shop).
    Uses the first shop returned from the site when possible; otherwise a configurable fallback code.
    Returns ``(shop_row, used_fallback_code)``.
    """
    for pair in pairs:
        try:
            cats = discover_repair_categories_for_model(
                session, pair["brand_slug"], pair["model_slug"]
            )
        except Exception:
            continue
        if not cats:
            continue
        shops = _resolve_shops_for_model(session, pair, cats)
        if shops:
            return dict(shops[0]), False
    fallback_code = (
        os.environ.get("SAVE_PRICE_SHOP_CODE_FALLBACK", "").strip()
        or _DEFAULT_PRICE_SHOP_CODE
    )
    return (
        {
            "shop_code": fallback_code,
            "shop_label": "",
            "shop_href": "",
            "shop_url": "",
        },
        True,
    )


def _save_repair_price_row(
    pair: dict[str, Any],
    cat: dict[str, str],
    shop: dict[str, str],
    pr: dict[str, Any],
    *,
    scrape_started_at: str,
    timestamp: str,
) -> dict[str, Any]:
    """
    One exported row for Save: shared column names with other sources (repair_price schema).
    ``source_url`` includes ``?shop=CODE`` (reference shop used for APIDAI pricing).
    """
    device_type = "smartphone"
    brand_title = pair["brand_slug"].replace("-", " ").title()
    model_upper = pair["model_slug"].replace("-", " ").upper()
    dev = normalize_device_name(device_type, brand=brand_title, model=model_upper)
    repair_name = clean_text(
        str(pr.get("part_label") or cat.get("category_label") or "")
    )
    if not repair_name and cat.get("repair_icon"):
        repair_name = str(cat["repair_icon"])
    repair_norm, repair_matched = normalize_repair_type(repair_name)
    eff = pr.get("price_effective_ttc")
    price: float | None
    try:
        price = float(eff) if eff is not None else None
    except (TypeError, ValueError):
        price = None
    price_raw = "" if price is None else str(price)
    source_url = f"{pair['model_url']}?shop={shop['shop_code']}"
    model_val = dev.get("model") or model_upper
    model_norm = (model_val or "").lower().strip()
    return {
        "source_site": "save",
        "source_url": source_url,
        "timestamp": timestamp,
        "scrape_started_at": scrape_started_at,
        "data_type": "repair_price",
        "device_type": device_type,
        "device_family": dev["device_family"],
        "brand": dev["brand"],
        "model": model_val,
        "model_normalized": model_norm,
        "product_or_part_name": repair_name,
        "repair_type_raw": repair_name,
        "repair_type": repair_norm,
        "repair_type_matched": repair_matched,
        "price": price,
        "currency": "EUR",
        "price_raw": price_raw,
        "warranty": "1 an",
        "delay": "30 min",
        "shop_name": "Save.co Atelier",
        "city": None,
        "postal_code": None,
    }


def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
) -> list[dict[str, Any]]:
    """
    End-to-end Save.co repair prices: discover models & categories, query APIDAI with one reference shop.
    Rows are built with ``_save_repair_price_row`` for a common repair_price export schema.

    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    scrape_started_at = now_iso_utc()
    ts_suffix = (
        scrape_started_at.replace(":", "")
        .replace("-", "")
        .replace("+", "")
        .replace("T", "_")
    )

    session = create_session()
    pairs = discover_brand_model_pairs(session, polite_between_brands=True)
    if not pairs:
        raise RuntimeError("No Save brand/model pairs discovered.")

    price_shop, price_shop_fallback = _reference_shop_for_apidai(session, pairs)
    if price_shop_fallback:
        print(
            f"[Save] Using fallback shop code {_DEFAULT_PRICE_SHOP_CODE!r} for all price API calls "
            "(no shop list found on site).",
            flush=True,
        )
    else:
        label = clean_text(price_shop.get("shop_label") or "")
        extra = f" — {label}" if label else ""
        print(
            f"[Save] Using shop code {price_shop['shop_code']!r} for all price API calls{extra}.",
            flush=True,
        )

    jsonl_path = out_dir / f"save_repair_prices_{ts_suffix}.jsonl"
    json_path = out_dir / f"save_repair_prices_{ts_suffix}.json"
    csv_path = out_dir / f"save_repair_prices_{ts_suffix}.csv"

    jsonl_file = jsonl_path.open("w", encoding="utf-8")
    csv_file = csv_path.open("w", encoding="utf-8", newline="")
    csv_writer: csv.DictWriter | None = None
    all_rows: list[dict[str, Any]] = []

    def _stream_row(row: dict[str, Any]) -> None:
        nonlocal csv_writer
        r = dict(row)
        line = json.dumps(r, ensure_ascii=False)
        jsonl_file.write(line + "\n")
        jsonl_file.flush()
        all_rows.append(r)
        if csv_writer is None:
            csv_writer = csv.DictWriter(csv_file, fieldnames=list(r.keys()))
            csv_writer.writeheader()
        csv_writer.writerow(r)
        csv_file.flush()

    def _step_sleep() -> None:
        polite_sleep(0.45, 1.15)

    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    with tqdm(
        total=len(pairs),
        desc="[Save] Price scrape",
        unit="model",
        dynamic_ncols=True,
    ) as mbar:
        for mi, pair in enumerate(pairs):
            if mi:
                polite_sleep(0.45, 1.15)
            page_timestamp = now_iso_utc()
            rows_this_pair = 0
            try:
                cats = discover_repair_categories_for_model(
                    session, pair["brand_slug"], pair["model_slug"]
                )
            except Exception as exc:
                tqdm.write(
                    f"[Save] Categories failed {pair['brand_slug']}/{pair['model_slug']}: {exc}"
                )
                fetch_errors += 1
                if emit_run_report:
                    issues.append(
                        fetch_issue(
                            reason=REASON_FETCH_ERROR,
                            source_site="save",
                            detail=str(exc),
                            brand_slug=pair.get("brand_slug"),
                            model_slug=pair.get("model_slug"),
                            brand_label=pair.get("brand_label"),
                            model_name=pair.get("model_name"),
                            step="discover_repair_categories",
                        )
                    )
                mbar.update(1)
                continue
            if not cats:
                fetch_errors += 1
                if emit_run_report:
                    issues.append(
                        fetch_issue(
                            reason=REASON_NO_REPAIR_CATEGORIES,
                            source_site="save",
                            brand_slug=pair.get("brand_slug"),
                            model_slug=pair.get("model_slug"),
                            brand_label=pair.get("brand_label"),
                            model_name=pair.get("model_name"),
                        )
                    )
                mbar.update(1)
                continue

            _step_sleep()

            for cat in cats:
                icon = (cat.get("repair_icon") or "").strip()
                if not icon:
                    continue
                try:
                    price_rows = fetch_sparepart_prices(
                        session,
                        pair["brand_label"],
                        pair["model_name"],
                        icon,
                        price_shop["shop_code"],
                    )
                except Exception as exc:
                    tqdm.write(
                        f"[Save] API {pair['model_slug']} {icon} {price_shop['shop_code']}: {exc}"
                    )
                    fetch_errors += 1
                    if emit_run_report:
                        issues.append(
                            fetch_issue(
                                reason=REASON_FETCH_ERROR,
                                source_site="save",
                                detail=str(exc),
                                brand_slug=pair.get("brand_slug"),
                                model_slug=pair.get("model_slug"),
                                repair_icon=icon,
                                shop_code=price_shop.get("shop_code"),
                                step="apidai_sparepart_prices",
                            )
                        )
                    _step_sleep()
                    continue
                for pr in price_rows:
                    row = _save_repair_price_row(
                        pair,
                        cat,
                        price_shop,
                        pr,
                        scrape_started_at=scrape_started_at,
                        timestamp=page_timestamp,
                    )
                    _stream_row(row)
                    rows_this_pair += 1
                _step_sleep()

            hint = f"{pair['brand_slug']}/{pair['model_slug']}"[:36]
            mbar.set_postfix(rows=rows_this_pair, last=hint, refresh=False)
            mbar.update(1)

    jsonl_file.close()
    csv_file.close()

    all_rows = dedupe_dict_rows(all_rows, ("source_url", "repair_type_raw", "price_raw"))
    clean_report = None
    if apply_cleaning:
        all_rows, clean_report = clean_dataset(
            all_rows,
            data_type="repair_price",
            cleaning_issues=issues if emit_run_report else None,
        )
        print(clean_report.summary_line(), flush=True)

    write_jsonl_and_csv(all_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    json_path.write_text(
        json.dumps(
            {
                "source_site": "save",
                "scrape_started_at": scrape_started_at,
                "row_count": len(all_rows),
                "rows": all_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"[Save] Done. Exported {len(all_rows)} rows", flush=True)
    print(f"[Save] JSON : {json_path}", flush=True)
    print(f"[Save] JSONL: {jsonl_path}", flush=True)
    print(f"[Save] CSV  : {csv_path}", flush=True)
    if emit_run_report:
        payload = finalize_run_artifacts(
            source_site="save",
            scrape_started_at=scrape_started_at,
            json_path=json_path,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(all_rows),
            apply_cleaning=apply_cleaning,
            clean_report=clean_report,
            fetch_errors=fetch_errors,
            issues=issues,
        )
        print(summarize_run_line(payload), flush=True)
        if payload.paths:
            print(f"[Save] Run   : {payload.paths.run_json}", flush=True)
            print(f"[Save] Issues: {payload.paths.issues_jsonl}", flush=True)
    return all_rows


if __name__ == "__main__":
    scrape()
