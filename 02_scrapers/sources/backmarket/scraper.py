"""Back Market scraper — refurbished smartphones, all brands.

Two scraping modes:

  1. API mode (--bm-api-token TOKEN)
     Uses the official Back Market Merchant REST API.

  2. HTTP mode (default)
     Fetches listing pages with requests and parses __NEXT_DATA__,
     falling back to plain HTML card parsing.

Diagnostic version:
  - Detailed HTTP 403 diagnostics
  - Saves blocked response body to disk
  - Records HTTP errors in the run issues
"""

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
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from scraper.core.normalization import normalize_device_name
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.run_report import (
    REASON_FETCH_ERROR,
    fetch_issue,
    finalize_run_artifacts,
    summarize_run_line,
)
from scraper.core.parser_utils import (
    clean_text,
    dedupe_dict_rows,
    now_iso_utc,
)


# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_URL = "https://www.backmarket.fr"

DEFAULT_LISTING_URL = (
    "https://www.backmarket.fr/fr-fr/l/smartphones/"
    "6c290010-c0c2-47a4-b68a-ac2ec2b64dca"
)

_STORAGE_RE = re.compile(
    r"\b(\d+\s*(?:Go|GB|To|TB))\b",
    re.I,
)

_PRICE_RE = re.compile(
    r"([\d][\d\s .,]*)[\s ]*€"
)


_BRAND_ALIASES: dict[str, str] = {
    "iphone": "Apple",
    "apple": "Apple",

    "samsung": "Samsung",
    "galaxy": "Samsung",

    "google": "Google",
    "pixel": "Google",

    "huawei": "Huawei",

    "xiaomi": "Xiaomi",
    "redmi": "Xiaomi",
    "poco": "Xiaomi",

    "oppo": "Oppo",

    "oneplus": "OnePlus",

    "sony": "Sony",
    "xperia": "Sony",

    "nokia": "Nokia",

    "motorola": "Motorola",
    "moto": "Motorola",

    "fairphone": "Fairphone",

    "realme": "Realme",

    "nothing": "Nothing",

    "asus": "Asus",
    "zenfone": "Asus",

    "lg": "LG",

    "honor": "Honor",

    "vivo": "Vivo",

    "wiko": "Wiko",

    "alcatel": "Alcatel",

    "tcl": "TCL",

    "blackberry": "BlackBerry",

    "htc": "HTC",

    "doro": "Doro",
}


_BRAND_RE = re.compile(
    r"\b("
    + "|".join(
        re.escape(k)
        for k in sorted(
            _BRAND_ALIASES,
            key=len,
            reverse=True,
        )
    )
    + r")\b",
    re.I,
)


# =============================================================================
# HTTP HEADERS
# =============================================================================
#
# IMPORTANT:
# The original code declared Windows/Chrome headers even when running
# on a Mac. For this diagnostic version we use a simpler, coherent
# browser-like request.
#
# We deliberately do NOT try to bypass Cloudflare here.
# The goal is to identify what the server returns.
#

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) "
        "Version/17.6 Safari/605.1.15"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": (
        "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7"
    ),
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


# =============================================================================
# HELPERS
# =============================================================================

def _log(msg: str) -> None:
    print(f"[BackMarket] {msg}", flush=True)


def _detect_brand(text: str) -> str:
    m = _BRAND_RE.search(text.lower())

    if m:
        return _BRAND_ALIASES.get(
            m.group(1).lower(),
            m.group(1).title(),
        )

    return "Autre"


def _coerce_price(value: Any) -> float | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None

    s = str(value)

    s = re.sub(
        r"\bEUR\b|€",
        "",
        s,
        flags=re.I,
    )

    s = (
        s.replace(",", ".")
        .replace("\xa0", "")
        .replace(" ", "")
        .strip()
    )

    m = re.search(r"\d[\d.]*", s)

    if m:
        try:
            v = float(m.group())

            return v if v > 0 else None

        except ValueError:
            pass

    return None


def _page_url(
    base: str,
    page_num: int,
) -> str:

    if page_num <= 1:
        return base

    parsed = urlparse(base)

    qs = parse_qs(
        parsed.query,
        keep_blank_values=True,
    )

    qs["page"] = [str(page_num)]

    new_query = urlencode(
        {
            k: v[0]
            for k, v in qs.items()
        }
    )

    return urlunparse(
        parsed._replace(
            query=new_query
        )
    )


# =============================================================================
# __NEXT_DATA__
# =============================================================================

def _extract_next_data(
    html: str,
) -> dict | None:

    """Pull the __NEXT_DATA__ JSON blob from page HTML."""

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    tag = soup.find(
        "script",
        id="__NEXT_DATA__",
    )

    if not tag or not tag.string:
        return None

    try:
        return json.loads(tag.string)

    except json.JSONDecodeError:
        return None


_TITLE_KEYS = (
    "title",
    "name",
    "productTitle",
    "label",
    "product_title",
    "offerTitle",
)


_PRICE_KEYS = (
    "price",
    "salesPrice",
    "minPrice",
    "startingPrice",
    "priceNew",
    "priceFrom",
    "offerPrice",
    "amount",
)


def _looks_like_product(
    obj: dict,
) -> bool:

    has_title = any(
        obj.get(k)
        and isinstance(
            obj[k],
            (str, dict),
        )
        for k in _TITLE_KEYS
    )

    has_price = any(
        obj.get(k) is not None
        for k in _PRICE_KEYS
    )

    has_url = any(
        obj.get(k)
        and isinstance(
            obj[k],
            str,
        )
        and "/p/" in str(obj[k])
        for k in (
            "url",
            "href",
            "link",
            "productUrl",
            "path",
            "slug",
        )
    )

    return (
        has_title
        and (
            has_price
            or has_url
        )
    )


def _walk_all_products(
    obj: Any,
    depth: int = 0,
) -> list[dict]:

    """Recursively find every product-like dict."""

    if depth > 15 or obj is None:
        return []

    results: list[dict] = []

    if isinstance(obj, dict):

        if _looks_like_product(obj):
            results.append(obj)

        for v in obj.values():
            results.extend(
                _walk_all_products(
                    v,
                    depth + 1,
                )
            )

    elif isinstance(obj, list):

        for item in obj:
            results.extend(
                _walk_all_products(
                    item,
                    depth + 1,
                )
            )

    return results


def _normalise_product(
    raw: dict,
) -> dict | None:

    # -------------------------------------------------------------------------
    # TITLE
    # -------------------------------------------------------------------------

    title = ""

    for k in _TITLE_KEYS:

        v = raw.get(k)

        if isinstance(v, str) and v.strip():

            title = v.strip()
            break

        if isinstance(v, dict):

            title = clean_text(
                str(
                    v.get("label")
                    or v.get("name")
                    or ""
                )
            )

            if title:
                break

    if not title:
        return None

    # -------------------------------------------------------------------------
    # PRICE
    # -------------------------------------------------------------------------

    price: float | None = None
    price_raw = ""

    for k in _PRICE_KEYS:

        v = raw.get(k)

        if v is None:
            continue

        if isinstance(v, dict):

            amount = (
                v.get("amount")
                or v.get("value")
                or v.get("cents")
            )

            if amount is not None:

                if isinstance(
                    amount,
                    (int, float),
                ):

                    price = float(amount) / (
                        100
                        if amount > 10000
                        else 1
                    )

                else:

                    price = _coerce_price(
                        amount
                    )

                price_raw = str(amount)

                break

        else:

            price = _coerce_price(v)
            price_raw = str(v)

            if price:
                break

    # -------------------------------------------------------------------------
    # URL
    # -------------------------------------------------------------------------

    href = ""

    for k in (
        "url",
        "href",
        "link",
        "productUrl",
        "path",
        "slug",
    ):

        v = raw.get(k)

        if isinstance(
            v,
            str,
        ) and v.strip():

            href = v.strip()
            break

    if href and not href.startswith("http"):

        href = urljoin(
            BASE_URL,
            href,
        )

    # -------------------------------------------------------------------------
    # GRADE
    # -------------------------------------------------------------------------

    grade = ""

    for k in (
        "bestGrade",
        "grade",
        "condition",
        "qualityLabel",
    ):

        v = raw.get(k)

        if isinstance(
            v,
            str,
        ) and v.strip():

            grade = v.strip()
            break

        if isinstance(
            v,
            dict,
        ):

            grade = clean_text(
                str(
                    v.get("label")
                    or v.get("name")
                    or ""
                )
            )

            if grade:
                break

    # -------------------------------------------------------------------------
    # BRAND
    # -------------------------------------------------------------------------

    brand_raw = ""

    for k in (
        "brand",
        "brandName",
        "brand_name",
        "manufacturer",
    ):

        v = raw.get(k)

        if isinstance(
            v,
            str,
        ) and v.strip():

            brand_raw = v.strip()
            break

        if isinstance(
            v,
            dict,
        ):

            brand_raw = clean_text(
                str(
                    v.get("name")
                    or v.get("label")
                    or ""
                )
            )

            if brand_raw:
                break

    brand = (
        brand_raw
        or _detect_brand(
            f"{title} {href}"
        )
    )

    # -------------------------------------------------------------------------
    # STORAGE
    # -------------------------------------------------------------------------

    storage_m = _STORAGE_RE.search(
        title
    )

    storage = (
        clean_text(
            storage_m.group(0)
        )
        if storage_m
        else ""
    )

    return {
        "title": clean_text(title),
        "price": price,
        "price_raw": price_raw,
        "href": href,
        "grade": grade,
        "storage": storage,
        "brand": brand,
    }


def _products_from_next_data(
    nd: dict,
) -> list[dict]:

    raw_items = _walk_all_products(nd)

    seen: set[str] = set()

    products: list[dict] = []

    for raw in raw_items:

        p = _normalise_product(raw)

        if not p:
            continue

        key = (
            f"{p['title']}|"
            f"{p['href']}"
        )

        if key in seen:
            continue

        seen.add(key)

        products.append(p)

    return products


def _total_from_next_data(
    nd: dict,
) -> int | None:

    """Try to extract total product count."""

    def _walk(
        obj: Any,
        depth: int = 0,
    ) -> int | None:

        if depth > 10:
            return None

        if not isinstance(
            obj,
            (dict, list),
        ):
            return None

        if isinstance(obj, dict):

            for k in (
                "total",
                "count",
                "nbHits",
                "totalCount",
                "totalResults",
                "nbResults",
            ):

                v = obj.get(k)

                if (
                    isinstance(v, int)
                    and v > 10
                ):
                    return v

            for v in obj.values():

                r = _walk(
                    v,
                    depth + 1,
                )

                if r:
                    return r

        elif isinstance(obj, list):

            for item in obj:

                r = _walk(
                    item,
                    depth + 1,
                )

                if r:
                    return r

        return None

    return _walk(nd)


# =============================================================================
# HTML FALLBACK
# =============================================================================

def _products_from_html_cards(
    html: str,
) -> list[dict]:

    """Parse product cards directly from HTML."""

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    products: list[dict] = []

    seen: set[str] = set()

    for link in soup.find_all(
        "a",
        href=re.compile(r"/p/"),
    ):

        href = link.get(
            "href",
            "",
        )

        if href in seen:
            continue

        seen.add(href)

        if href and not href.startswith(
            "http"
        ):

            href = urljoin(
                BASE_URL,
                href,
            )

        text = link.get_text(
            " ",
            strip=True,
        )

        # ---------------------------------------------------------------------
        # PRICE
        # ---------------------------------------------------------------------

        pm = _PRICE_RE.search(text)

        price_raw = (
            pm.group(0).strip()
            if pm
            else ""
        )

        price: float | None = None

        if pm and price_raw:

            digits = re.sub(
                r"[\s\xa0]",
                "",
                pm.group(1),
            ).replace(
                ",",
                ".",
            )

            try:
                price = float(digits)

            except ValueError:
                pass

        # ---------------------------------------------------------------------
        # TITLE
        # ---------------------------------------------------------------------

        chunks = [
            c.strip()
            for c in re.split(
                r"[•·|]|€|\d+,\d+",
                text,
            )
            if c.strip()
        ]

        title = (
            chunks[0]
            if chunks
            else text[:80]
        )

        if not title:
            continue

        # ---------------------------------------------------------------------
        # STORAGE
        # ---------------------------------------------------------------------

        storage_m = _STORAGE_RE.search(
            text
        )

        storage = (
            clean_text(
                storage_m.group(0)
            )
            if storage_m
            else ""
        )

        products.append(
            {
                "title": clean_text(title),
                "price": price,
                "price_raw": price_raw,
                "href": href,
                "grade": "",
                "storage": storage,
                "brand": _detect_brand(
                    f"{title} {href}"
                ),
            }
        )

    return products


# =============================================================================
# ROW BUILDER
# =============================================================================

def _build_row(
    product: dict,
    page_url: str,
    listing_url: str,
    timestamp: str,
    scrape_started_at: str,
) -> dict[str, Any]:

    title = product["title"]
    brand = product["brand"]

    dev = normalize_device_name(
        "smartphone",
        brand=brand,
        model=title,
    )

    href = (
        product["href"]
        or page_url
    )

    price = product["price"]

    return {
        "source_site": "backmarket",
        "source_url": href,
        "listing_url": listing_url,
        "timestamp": timestamp,
        "scrape_started_at": scrape_started_at,
        "data_type": "refurb_phone_price",
        "device_type": "smartphone",
        "brand": brand,
        "model": dev["model"] or title,
        "product_name": title,
        "storage": product.get("storage") or "",
        "grade": product.get("grade") or "",
        "price": price,
        "currency": "EUR",
        "price_raw": (
            product.get("price_raw")
            or (
                ""
                if price is None
                else str(price)
            )
        ),
    }


# =============================================================================
# LOGGING HELPERS
# =============================================================================

def _brand_summary(
    counter: Counter,
    top: int = 6,
) -> str:

    parts = [
        f"{b}={n}"
        for b, n in counter.most_common(top)
    ]

    extra = len(counter) - top

    if extra > 0:
        parts.append(
            f"+{extra} autres"
        )

    return ", ".join(parts)


# =============================================================================
# 403 DIAGNOSTIC
# =============================================================================

def _diagnose_http_error(
    resp: requests.Response,
    url: str,
    output_dir: Path,
    page_num: int,
) -> Path | None:

    """
    Save diagnostic information for non-200 HTTP responses.

    In particular, HTTP 403 responses are saved so we can inspect
    exactly what Back Market returned.
    """

    _log("")
    _log("=" * 64)
    _log("DIAGNOSTIC HTTP")
    _log("=" * 64)

    _log(
        f"Status code       : {resp.status_code}"
    )

    _log(
        f"Requested URL     : {url}"
    )

    _log(
        f"Final URL         : {resp.url}"
    )

    _log(
        f"Redirect history : {len(resp.history)}"
    )

    if resp.history:

        for i, previous in enumerate(
            resp.history,
            start=1,
        ):

            _log(
                f"  Redirect {i}: "
                f"{previous.status_code} "
                f"{previous.url}"
            )

    _log(
        "Content-Type      : "
        f"{resp.headers.get('Content-Type', '')}"
    )

    _log(
        "Server            : "
        f"{resp.headers.get('Server', '')}"
    )

    _log(
        "Content-Length    : "
        f"{resp.headers.get('Content-Length', '')}"
    )

    _log(
        "CF-Ray            : "
        f"{resp.headers.get('CF-Ray', '')}"
    )

    _log(
        "CF-Cache-Status   : "
        f"{resp.headers.get('CF-Cache-Status', '')}"
    )

    _log(
        "Location          : "
        f"{resp.headers.get('Location', '')}"
    )

    _log(
        "Retry-After       : "
        f"{resp.headers.get('Retry-After', '')}"
    )

    # -------------------------------------------------------------------------
    # Response body
    # -------------------------------------------------------------------------

    body = resp.text or ""

    _log(
        f"Body length       : {len(body)} caractères"
    )

    preview = body[:2000]

    _log("")
    _log("BODY PREVIEW (2000 caractères)")
    _log("-" * 64)
    _log(repr(preview))
    _log("-" * 64)

    # -------------------------------------------------------------------------
    # Detection hints
    # -------------------------------------------------------------------------

    body_lower = body.lower()

    indicators = {
        "cloudflare": (
            "cloudflare" in body_lower
        ),
        "just_a_moment": (
            "just a moment" in body_lower
        ),
        "cf-browser-verification": (
            "cf-browser-verification"
            in body_lower
        ),
        "challenge-platform": (
            "challenge-platform"
            in body_lower
        ),
        "captcha": (
            "captcha" in body_lower
        ),
        "access_denied": (
            "access denied" in body_lower
        ),
        "forbidden": (
            "forbidden" in body_lower
        ),
        "back_market": (
            "back market" in body_lower
        ),
        "next_data": (
            "__next_data__" in body_lower
        ),
    }

    _log("")
    _log("INDICATEURS")
    _log("-" * 64)

    for name, found in indicators.items():

        _log(
            f"  {name:<25} : "
            f"{'OUI' if found else 'non'}"
        )

    # -------------------------------------------------------------------------
    # Save response body
    # -------------------------------------------------------------------------

    try:

        diagnostic_path = (
            output_dir
            / (
                f"backmarket_http_error_"
                f"page_{page_num}_"
                f"{resp.status_code}.html"
            )
        )

        diagnostic_path.write_text(
            body,
            encoding="utf-8",
        )

        _log("")
        _log(
            "Body sauvegardé : "
            f"{diagnostic_path}"
        )

        return diagnostic_path

    except Exception as exc:

        _log(
            f"Impossible de sauvegarder "
            f"le body : {exc}"
        )

        return None


# =============================================================================
# BACK MARKET MERCHANT API
# =============================================================================

_BM_API_BASE = (
    "https://www.backmarket.fr"
)

_BM_API_LISTINGS = (
    f"{_BM_API_BASE}/ws/listings/"
)

_BM_COMPANY = "T-SCOP"

_BM_EMAIL = "info@t-scop.com"


def _api_headers(
    token: str,
) -> dict[str, str]:

    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Basic {token}",
        "User-Agent": (
            f"BM-{_BM_COMPANY}-PriceFetcher;"
            f"{_BM_EMAIL}"
        ),
        "Accept-Language": "fr-fr",
    }


def _api_listing_to_product(
    item: dict,
) -> dict | None:

    title = (
        item.get("title")
        or item.get("product_title")
        or item.get("name")
        or item.get("label")
        or ""
    )

    if isinstance(
        title,
        dict,
    ):

        title = str(
            title.get("label")
            or title.get("name")
            or ""
        )

    title = clean_text(
        str(title)
    )

    if not title:
        return None

    # -------------------------------------------------------------------------
    # PRICE
    # -------------------------------------------------------------------------

    price_val = (
        item.get("price")
        or item.get("salesPrice")
        or item.get("minPrice")
    )

    price = _coerce_price(
        price_val
    )

    price_raw = (
        str(price_val)
        if price_val is not None
        else ""
    )

    # -------------------------------------------------------------------------
    # URL
    # -------------------------------------------------------------------------

    href = (
        item.get("url")
        or item.get("link")
        or item.get("href")
        or item.get("path")
        or ""
    )

    if href and not href.startswith(
        "http"
    ):

        href = urljoin(
            _BM_API_BASE,
            href,
        )

    # -------------------------------------------------------------------------
    # GRADE
    # -------------------------------------------------------------------------

    grade = ""

    for k in (
        "grade",
        "bestGrade",
        "condition",
        "qualityLabel",
    ):

        v = item.get(k)

        if isinstance(
            v,
            str,
        ) and v.strip():

            grade = v.strip()
            break

        if isinstance(
            v,
            dict,
        ):

            grade = clean_text(
                str(
                    v.get("label")
                    or v.get("name")
                    or ""
                )
            )

            if grade:
                break

    # -------------------------------------------------------------------------
    # BRAND
    # -------------------------------------------------------------------------

    brand_raw = ""

    for k in (
        "brand",
        "brandName",
        "manufacturer",
    ):

        v = item.get(k)

        if isinstance(
            v,
            str,
        ) and v.strip():

            brand_raw = v.strip()
            break

        if isinstance(
            v,
            dict,
        ):

            brand_raw = clean_text(
                str(
                    v.get("name")
                    or v.get("label")
                    or ""
                )
            )

            if brand_raw:
                break

    brand = (
        brand_raw
        or _detect_brand(
            f"{title} {href}"
        )
    )

    # -------------------------------------------------------------------------
    # STORAGE
    # -------------------------------------------------------------------------

    storage_m = _STORAGE_RE.search(
        title
    )

    storage = (
        clean_text(
            storage_m.group(0)
        )
        if storage_m
        else ""
    )

    return {
        "title": title,
        "price": price,
        "price_raw": price_raw,
        "href": href,
        "grade": grade,
        "storage": storage,
        "brand": brand,
    }


def _scrape_via_api(
    token: str,
    listing_url: str,
    max_pages: int,
    stream_fn,
) -> int:

    """
    Fetch all Back Market listings via Merchant REST API.
    """

    _log(
        "Mode API Back Market "
        "(token fourni)"
    )

    _log(
        f"Endpoint : {_BM_API_LISTINGS}"
    )

    headers = _api_headers(
        token
    )

    session = requests.Session()

    session.headers.update(
        headers
    )

    page_size = 50
    page_num = 1
    total_seen = 0

    pbar = tqdm(
        total=None,
        desc="[BackMarket-API]",
        unit="page",
        dynamic_ncols=True,
    )

    brand_counter: Counter = Counter()

    try:

        while page_num <= max_pages:

            params = {
                "page": page_num,
                "page_size": page_size,
            }

            try:

                resp = session.get(
                    _BM_API_LISTINGS,
                    params=params,
                    timeout=30,
                )

            except Exception as exc:

                _log(
                    f"API page {page_num} : "
                    f"erreur réseau : {exc}"
                )

                break

            if resp.status_code == 401:

                _log(
                    "API : token invalide "
                    "ou expiré (HTTP 401). "
                    "Vérifiez --bm-api-token."
                )

                break

            if resp.status_code == 403:

                _log(
                    "API : accès interdit "
                    "(HTTP 403). Token valide "
                    "mais droits insuffisants."
                )

                break

            if resp.status_code != 200:

                _log(
                    f"API page {page_num} : "
                    f"HTTP {resp.status_code}"
                )

                break

            try:

                data = resp.json()

            except Exception:

                _log(
                    f"API page {page_num} : "
                    "réponse non-JSON"
                )

                break

            results = (
                data.get("results", [])
                if isinstance(data, dict)
                else data
            )

            if not results:

                _log(
                    "API : aucun résultat — "
                    "fin de pagination."
                )

                break

            for item in results:

                prod = (
                    _api_listing_to_product(
                        item
                    )
                )

                if prod:

                    stream_fn(prod)

                    brand_counter[
                        prod["brand"]
                    ] += 1

                    total_seen += 1

            _log(
                f"API page {page_num:>3}  "
                f"+{len(results):>3} items  |  "
                f"total={total_seen:>5}  |  "
                f"marques: "
                f"{_brand_summary(brand_counter)}"
            )

            pbar.update(1)

            pbar.set_postfix(
                total=total_seen
            )

            next_url = (
                data.get("next")
                if isinstance(
                    data,
                    dict,
                )
                else None
            )

            if (
                not next_url
                or len(results) < page_size
            ):

                _log(
                    "API : dernière page atteinte."
                )

                break

            page_num += 1

            time.sleep(0.3)

    finally:

        pbar.close()

    return total_seen


# =============================================================================
# MAIN SCRAPER
# =============================================================================

def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    listing_url: str = DEFAULT_LISTING_URL,
    headless: bool = True,
    max_pages: int = 200,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    email: str = "",
    password: str = "",
    api_token: str = "",
) -> list[dict[str, Any]]:

    """
    Scrape Back Market refurbished smartphones.

    Without token:
        HTTP mode.

    With token:
        Merchant API mode.
    """

    out_dir = Path(
        output_dir
    )

    out_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    scrape_started_at = now_iso_utc()

    ts = (
        scrape_started_at
        .replace(":", "")
        .replace("-", "")
        .replace("+", "")
        .replace("T", "_")
    )

    all_rows: list[
        dict[str, Any]
    ] = []

    issues: list[
        dict[str, Any]
    ] = []

    fetch_errors = 0

    brand_counter: Counter = Counter()

    jsonl_path = (
        out_dir
        / f"backmarket_smartphones_{ts}.jsonl"
    )

    json_path = (
        out_dir
        / f"backmarket_smartphones_{ts}.json"
    )

    csv_path = (
        out_dir
        / f"backmarket_smartphones_{ts}.csv"
    )

    jsonl_file = jsonl_path.open(
        "w",
        encoding="utf-8",
    )

    csv_file = csv_path.open(
        "w",
        encoding="utf-8",
        newline="",
    )

    csv_writer: csv.DictWriter | None = None

    # =========================================================================
    # STREAM ROW
    # =========================================================================

    def _stream(
        row: dict[str, Any],
    ) -> None:

        nonlocal csv_writer

        all_rows.append(row)

        brand_counter[
            row.get("brand", "?")
        ] += 1

        jsonl_file.write(
            json.dumps(
                row,
                ensure_ascii=False,
            )
            + "\n"
        )

        jsonl_file.flush()

        if csv_writer is None:

            csv_writer = csv.DictWriter(
                csv_file,
                fieldnames=list(
                    row.keys()
                ),
            )

            csv_writer.writeheader()

        csv_writer.writerow(row)

        csv_file.flush()

    # =========================================================================
    # STREAM PRODUCT
    # =========================================================================

    def _stream_product(
        prod: dict,
    ) -> None:

        _stream(
            _build_row(
                prod,
                listing_url,
                listing_url,
                now_iso_utc(),
                scrape_started_at,
            )
        )

    # =========================================================================
    # START LOG
    # =========================================================================

    _log("=" * 64)

    _log(
        "Mode      : "
        + (
            "API (token)"
            if api_token
            else "HTTP (__NEXT_DATA__)"
        )
    )

    _log(
        f"URL       : {listing_url}"
    )

    _log(
        f"Max pages : {max_pages}"
    )

    _log("=" * 64)

    try:

        # =====================================================================
        # API MODE
        # =====================================================================

        if api_token:

            _scrape_via_api(
                api_token,
                listing_url,
                max_pages,
                _stream_product,
            )

        # =====================================================================
        # HTTP MODE
        # =====================================================================

        else:

            session = requests.Session()

            session.headers.update(
                _HEADERS
            )

            total_pages = 0

            pbar = tqdm(
                total=None,
                desc="[BackMarket]",
                unit="page",
                dynamic_ncols=True,
            )

            try:

                for page_num in range(
                    1,
                    max_pages + 1,
                ):

                    url = _page_url(
                        listing_url,
                        page_num,
                    )

                    timestamp = (
                        now_iso_utc()
                    )

                    _log("")
                    _log(
                        f"FETCH page {page_num}: "
                        f"{url}"
                    )

                    # =========================================================
                    # HTTP REQUEST
                    # =========================================================

                    try:

                        resp = session.get(
                            url,
                            timeout=30,
                            allow_redirects=True,
                        )

                    except Exception as exc:

                        _log(
                            f"Page {page_num} : "
                            f"erreur réseau : {exc}"
                        )

                        fetch_errors += 1

                        if emit_run_report:

                            issues.append(
                                fetch_issue(
                                    reason=REASON_FETCH_ERROR,
                                    source_site="backmarket",
                                    source_url=url,
                                    page_index=page_num,
                                    detail=str(exc),
                                )
                            )

                        if page_num == 1:
                            break

                        continue

                    # =========================================================
                    # RESPONSE SUMMARY
                    # =========================================================

                    _log(
                        f"HTTP {resp.status_code} "
                        f"| final_url={resp.url}"
                    )

                    # =========================================================
                    # NON-200
                    # =========================================================

                    if resp.status_code != 200:

                        fetch_errors += 1

                        diagnostic_path = (
                            _diagnose_http_error(
                                resp,
                                url,
                                out_dir,
                                page_num,
                            )
                        )

                        detail = (
                            f"HTTP {resp.status_code}"
                        )

                        if diagnostic_path:

                            detail += (
                                f" | body="
                                f"{diagnostic_path}"
                            )

                        if emit_run_report:

                            issues.append(
                                fetch_issue(
                                    reason=REASON_FETCH_ERROR,
                                    source_site="backmarket",
                                    source_url=url,
                                    page_index=page_num,
                                    detail=detail,
                                )
                            )

                        if page_num == 1:

                            _log(
                                "Page 1 bloquée : "
                                "arrêt du scraping HTTP."
                            )

                            break

                        _log(
                            "Page bloquée : "
                            "fin de pagination."
                        )

                        break

                    # =========================================================
                    # HTML
                    # =========================================================

                    html = resp.text

                    _log(
                        f"HTML reçu : "
                        f"{len(html)} caractères"
                    )

                    # =========================================================
                    # CLOUDFLARE CHECK
                    # =========================================================

                    if (
                        "cf-browser-verification"
                        in html
                        or (
                            "Just a moment"
                            in html
                            and "cloudflare"
                            in html.lower()
                        )
                    ):

                        _log(
                            f"Page {page_num} : "
                            "Cloudflare bloque "
                            "la requête HTTP."
                        )

                        fetch_errors += 1

                        if emit_run_report:

                            issues.append(
                                fetch_issue(
                                    reason=REASON_FETCH_ERROR,
                                    source_site="backmarket",
                                    source_url=url,
                                    page_index=page_num,
                                    detail=(
                                        "Cloudflare "
                                        "browser verification"
                                    ),
                                )
                            )

                        if page_num == 1:
                            break

                        break

                    # =========================================================
                    # NEXT DATA
                    # =========================================================

                    nd = _extract_next_data(
                        html
                    )

                    products: list[
                        dict
                    ] = []

                    source = "?"

                    if nd:

                        products = (
                            _products_from_next_data(
                                nd
                            )
                        )

                        source = "next_data"

                        _log(
                            "__NEXT_DATA__ : trouvé"
                        )

                        _log(
                            f"Produits Next.js : "
                            f"{len(products)}"
                        )

                        if (
                            page_num == 1
                            and not total_pages
                        ):

                            total_count = (
                                _total_from_next_data(
                                    nd
                                )
                            )

                            if total_count:

                                total_pages = min(
                                    (
                                        total_count
                                        + 29
                                    )
                                    // 30,
                                    max_pages,
                                )

                                _log(
                                    "Total produits : "
                                    f"~{total_count} "
                                    f"→ ~{total_pages} pages"
                                )

                                pbar.total = (
                                    total_pages
                                )

                                pbar.refresh()

                    else:

                        _log(
                            "__NEXT_DATA__ : "
                            "non trouvé"
                        )

                    # =========================================================
                    # HTML FALLBACK
                    # =========================================================

                    if not products:

                        _log(
                            "Fallback HTML cards..."
                        )

                        products = (
                            _products_from_html_cards(
                                html
                            )
                        )

                        source = "html_cards"

                        _log(
                            f"Produits HTML : "
                            f"{len(products)}"
                        )

                    # =========================================================
                    # NO PRODUCTS
                    # =========================================================

                    if not products:

                        _log(
                            f"Page {page_num} : "
                            "aucun produit trouvé."
                        )

                        _log(
                            f"Status={resp.status_code}"
                        )

                        _log(
                            "__NEXT_DATA__: "
                            + (
                                "oui"
                                if nd
                                else "non"
                            )
                        )

                        _log(
                            "Body (400 car.) : "
                            f"{html[:400]!r}"
                        )

                        fetch_errors += 1

                        if emit_run_report:

                            issues.append(
                                fetch_issue(
                                    reason=REASON_FETCH_ERROR,
                                    source_site="backmarket",
                                    source_url=url,
                                    page_index=page_num,
                                    detail=(
                                        "HTTP 200 "
                                        "mais aucun "
                                        "produit détecté"
                                    ),
                                )
                            )

                        if page_num == 1:
                            break

                        _log(
                            "Fin de pagination."
                        )

                        break

                    # =========================================================
                    # BUILD ROWS
                    # =========================================================

                    for prod in products:

                        row = _build_row(
                            prod,
                            url,
                            listing_url,
                            timestamp,
                            scrape_started_at,
                        )

                        _stream(row)

                    # =========================================================
                    # PAGE LOG
                    # =========================================================

                    pct_str = (
                        f"{page_num / total_pages * 100:5.1f}%"
                        if total_pages
                        else "    ?%"
                    )

                    tot_str = (
                        f"/ {total_pages}"
                        if total_pages
                        else "      "
                    )

                    _log(
                        f"Page {page_num:>3} "
                        f"{tot_str:<6} "
                        f"[{pct_str}]  "
                        f"+{len(products):>3} "
                        f"produits  |  "
                        f"total={len(all_rows):>5}  |  "
                        f"source={source:<10}  |  "
                        f"marques: "
                        f"{_brand_summary(brand_counter)}"
                    )

                    pbar.update(1)

                    pbar.set_postfix(
                        total=len(all_rows),
                        pct=pct_str.strip(),
                    )

                    # =========================================================
                    # PAGINATION STOP
                    # =========================================================

                    if len(products) < 4:

                        _log(
                            "Moins de 4 produits — "
                            "probablement dernière page."
                        )

                        break

                    time.sleep(0.8)

            finally:

                pbar.close()

    finally:

        jsonl_file.close()
        csv_file.close()

    # =========================================================================
    # DEDUPLICATION
    # =========================================================================

    all_rows = dedupe_dict_rows(
        all_rows,
        (
            "source_url",
            "product_name",
            "price_raw",
        ),
    )

    # =========================================================================
    # REWRITE JSONL / CSV
    # =========================================================================

    write_jsonl_and_csv(
        all_rows,
        jsonl_path=jsonl_path,
        csv_path=csv_path,
    )

    # =========================================================================
    # JSON
    # =========================================================================

    json_path.write_text(
        json.dumps(
            {
                "source_site": "backmarket",
                "scrape_started_at": (
                    scrape_started_at
                ),
                "row_count": len(all_rows),
                "brand_counts": dict(
                    brand_counter.most_common()
                ),
                "rows": all_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # =========================================================================
    # FINAL LOG
    # =========================================================================

    _log("")
    _log("=" * 64)
    _log("SCRAPE TERMINÉ")
    _log(
        f"  Produits exportés  : "
        f"{len(all_rows)}"
    )

    _log(
        f"  Marques collectées : "
        f"{len(brand_counter)}"
    )

    for brand, count in (
        brand_counter.most_common()
    ):

        _log(
            f"    {brand:<22} "
            f"{count:>5} produits"
        )

    _log(
        f"  Erreurs fetch      : "
        f"{fetch_errors}"
    )

    _log("=" * 64)

    _log(
        f"JSON : {json_path}"
    )

    _log(
        f"JSONL: {jsonl_path}"
    )

    _log(
        f"CSV  : {csv_path}"
    )

    # =========================================================================
    # RUN REPORT
    # =========================================================================

    if emit_run_report:

        payload = finalize_run_artifacts(
            source_site="backmarket",
            scrape_started_at=(
                scrape_started_at
            ),
            json_path=json_path,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(all_rows),
            apply_cleaning=False,
            clean_report=None,
            fetch_errors=fetch_errors,
            issues=issues,
        )

        _log(
            summarize_run_line(
                payload
            ).replace(
                "[Run] ",
                "Run  : ",
            )
        )

        if payload.paths:

            _log(
                f"Run  : "
                f"{payload.paths.run_json}"
            )

            _log(
                f"Issues: "
                f"{payload.paths.issues_jsonl}"
            )

    return all_rows


# =============================================================================
# DIRECT EXECUTION
# =============================================================================

if __name__ == "__main__":
    scrape()