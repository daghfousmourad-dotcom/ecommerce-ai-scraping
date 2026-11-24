"""
Amazon Marketplace scraper (sources2).
Intégré dans ecommerce_ai_scraping_scraper_V2-main basé sur amazon_scraper_unified.py.
Collecte multi-marques avec élimination IQR des valeurs aberrantes.
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

import asyncio
import json
import logging
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright, Page

from scraper.core.filtering import clean_dataset
from scraper.core.iqr_outliers import filter_by_iqr
from scraper.core.parser_utils import now_iso_utc
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.run_report import (
    REASON_FETCH_ERROR,
    fetch_issue,
    finalize_run_artifacts,
    summarize_run_line,
)

SOURCE_SITE = "amazon"
BASE_URL = "https://www.amazon.fr"

SMARTPHONE_BRANDS = [
    "Apple", "Samsung", "Xiaomi", "Google", "OnePlus", "Sony",
    "Motorola", "Nokia", "Oppo", "Realme", "Vivo", "Nothing",
    "Honor", "Huawei", "Asus",
]

BRAND_TARGET_MODELS: dict[str, list[str]] = {
    "Apple": [
        "iPhone 15", "iPhone 15 Pro", "iPhone 15 Pro Max", "iPhone 15 Plus",
        "iPhone 14", "iPhone 14 Pro", "iPhone 13", "iPhone 12", "iPhone 11", "iPhone SE",
    ],
    "Samsung": [
        "Galaxy S24", "Galaxy S24 Ultra", "Galaxy S24+", "Galaxy S23", "Galaxy S23 Ultra",
        "Galaxy A55", "Galaxy A54", "Galaxy A35", "Galaxy A15",
        "Galaxy Z Flip5", "Galaxy Z Fold5",
    ],
    "Xiaomi": [
        "Xiaomi 14", "Xiaomi 13T", "Redmi Note 13 Pro", "Redmi Note 13", "POCO X6 Pro", "POCO F6",
    ],
    "Google": [
        "Pixel 8", "Pixel 8 Pro", "Pixel 8a", "Pixel 7a", "Pixel 7",
    ],
    "OnePlus": ["OnePlus 12", "OnePlus 12R", "OnePlus Nord 3"],
    "Honor": ["Honor Magic6 Pro", "Honor 90", "Honor 200"],
    "Motorola": ["Motorola Edge 50 Pro", "Moto G84"],
    "Nothing": ["Nothing Phone 2", "Nothing Phone 2a"],
}

SMARTPHONE_KEYWORDS = [
    "smartphone", "iphone", "galaxy", "pixel", "xperia", "moto",
    "oneplus", "redmi", "poco", "zenfone", "rog phone", "fairphone",
    "nothing phone", "find x", "reno", "magic", "xiaomi 14", "xiaomi 15",
    "ipad", "tablette", "macbook", "switch", "ps5", "xbox", "ordinateur"
]

BLACKLIST_KEYWORDS = [
    "fixe", "sans fil", "dect", "filaire", "gigaset", "panasonic", "combine", "combiné",
    "residentiel", "résidentiel", "bureau", "interphone", "standard",
    "senior", "personnes âgées", "grosses touches", "clapet 2g", "touche sos", "doro",
    "talkie", "walkie", "babyphone",
    "chargeur", "câble", "cable", "coque", "étui", "etui", "housse",
    "verre trempé", "verre trempe", "film", "protection écran", "protection ecran",
    "vitre", "airpods", "ecouteurs", "écouteurs", "watch", "montre",
    "adaptateur", "accessoire", "support", "stand", "batterie externe", "power bank",
    "lanière", "dragonne", "carte sd", "micro sd", "stylet",
]

EXTRACT_PRODUCTS_JS = """() => {
    const items = [];
    document.querySelectorAll('div[data-component-type="s-search-result"]').forEach(card => {
        const titleEl = card.querySelector('h2 a span, h2 span');
        const linkEl  = card.querySelector('h2 a');
        const priceOffscreen = card.querySelector('.a-price .a-offscreen');
        const priceWhole = card.querySelector('.a-price .a-price-whole');
        const priceFract = card.querySelector('.a-price .a-price-fraction');

        let priceStr = null;
        if (priceOffscreen && priceOffscreen.innerText.trim()) {
            priceStr = priceOffscreen.innerText.trim();
        } else if (priceWhole) {
            const whole = priceWhole.innerText.replace(/[^0-9]/g, '');
            const fract = priceFract ? priceFract.innerText.replace(/[^0-9]/g, '') : '00';
            priceStr = `${whole},${fract} €`;
        }

        const asin = card.getAttribute('data-asin') || '';

        if (titleEl && priceStr) {
            items.push({
                title: titleEl.innerText.trim(),
                priceStr: priceStr,
                url: linkEl ? linkEl.href : '',
                asin: asin,
            });
        }
    });
    return items;
}"""


def normalize_name(name: str) -> str:
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name.strip().lower())
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def parse_price_fr(text: str | None) -> float | None:
    if not text:
        return None
    clean = text.replace("\xa0", " ").replace("\u202f", " ").strip()
    m = re.search(r"(\d[\d\s]*[,\.]\d{2})\s*€", clean)
    if m:
        raw_num = m.group(1).replace(" ", "").replace(",", ".")
        try:
            return float(raw_num)
        except ValueError:
            pass
    m2 = re.search(r"(\d[\d\s]+)\s*€", clean)
    if m2:
        try:
            return float(m2.group(1).replace(" ", ""))
        except ValueError:
            pass
    return None


def is_smartphone(title: str) -> bool:
    t_lower = title.lower()
    for kw in BLACKLIST_KEYWORDS:
        if kw in t_lower:
            return False
    has_brand = any(b.lower() in t_lower for b in SMARTPHONE_BRANDS)
    has_storage = bool(re.search(r"\b\d+\s*(?:go|gb|to|tb)\b", t_lower))
    if has_brand and has_storage:
        return True
    return any(kw in t_lower for kw in SMARTPHONE_KEYWORDS)


def extract_product_info(title: str, brand: str, search_model: str) -> dict[str, Any]:
    t = title.strip()
    condition = "new"
    t_low = t.lower()
    if any(kw in t_low for kw in ["reconditionné", "reconditionne", "renew", "refurb"]):
        condition = "refurbished"
    elif any(kw in t_low for kw in ["occasion", "used"]):
        condition = "used"

    storage_gb = None
    m_tb = re.search(r"(\d+)\s*(?:To|TB)\b", t, re.IGNORECASE)
    if m_tb:
        storage_gb = int(m_tb.group(1)) * 1024
    else:
        m_gb = re.search(r"(\d+)\s*(?:Go|GB)\b", t, re.IGNORECASE)
        if m_gb:
            storage_gb = int(m_gb.group(1))

    color = ""
    color_m = re.search(r"-\s*([A-ZÀ-ÿa-z][A-ZÀ-ÿa-z\s]+?)(?:\s*\(|$)", t)
    if color_m:
        cand = color_m.group(1).strip()
        if not any(excl in cand.lower() for excl in ["recondition", "version", "fr", "exclusivit"]):
            color = cand[:40]

    model_clean = re.sub(r"\s*\(.*?\)", " ", t)
    model_clean = re.sub(r"\s*-\s*.{0,50}$", "", model_clean)
    model_clean = re.sub(r",?\s*\d+\s*(?:go|gb|to|tb)\b.*$", "", model_clean, flags=re.IGNORECASE)
    model_clean = re.sub(r"\s+", " ", model_clean).strip()
    if brand.lower() not in model_clean.lower():
        model_clean = f"{brand} {model_clean}".strip()

    return {
        "model": model_clean or search_model,
        "model_normalized": normalize_name(model_clean or search_model),
        "storage_gb": storage_gb,
        "color": color,
        "condition": condition,
    }


async def _search_amazon(page: Page, query: str, issues: list[dict[str, Any]], max_results: int = 15) -> list[dict[str, Any]]:
    url = f"{BASE_URL}/s?k={query.replace(' ', '+')}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        try:
            cookie_btn = page.locator("#sp-cc-accept").first
            if await cookie_btn.count() and await cookie_btn.is_visible():
                await cookie_btn.click(timeout=2000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)
        await page.evaluate("window.scrollTo(0, 400)")
        await page.wait_for_timeout(1000)
        items = await page.evaluate(EXTRACT_PRODUCTS_JS)
        return items[:max_results]
    except Exception as e:
        issues.append(fetch_issue(
            source_site=SOURCE_SITE,
            source_url=url,
            reason=REASON_FETCH_ERROR,
            detail=f"Erreur recherche '{query}': {e}",
        ))
        return []


async def _async_scrape(
    output_dir: Path,
    headless: bool = True,
    max_models: int | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    iqr_factor: float = 1.5,
) -> list[dict[str, Any]]:
    started_at = now_iso_utc()
    time_suffix = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S0000")

    json_bundle = output_dir / f"amazon_market_prices_{time_suffix}.json"
    jsonl_path = output_dir / f"amazon_market_prices_{time_suffix}.jsonl"
    csv_path = output_dir / f"amazon_market_prices_{time_suffix}.csv"

    issues: list[dict[str, Any]] = []
    collected_rows: list[dict[str, Any]] = []

    print(f"[{SOURCE_SITE}] Démarrage collecte Amazon France...", flush=True)

    searches: list[tuple[str, str]] = []
    for brand, models in BRAND_TARGET_MODELS.items():
        for model in models:
            searches.append((brand, model))

    if max_models:
        searches = searches[:max_models]

    print(f"[{SOURCE_SITE}] {len(searches)} recherches planifiées.", flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            locale="fr-FR",
        )
        page = await context.new_page()

        for idx, (brand, model) in enumerate(searches, start=1):
            query = f"{brand} {model}"
            print(f"[{SOURCE_SITE}] [{idx}/{len(searches)}] Recherche : '{query}'...", flush=True)
            raw_items = await _search_amazon(page, query, issues, max_results=12)

            for item in raw_items:
                title = item.get("title", "")
                raw_p = item.get("priceStr")
                price_val = parse_price_fr(raw_p)

                if not price_val or price_val < 40.0:
                    continue

                if not is_smartphone(title):
                    continue

                info = extract_product_info(title, brand, model)
                dev_type = "smartphone"
                t_lower = title.lower()
                if "ipad" in t_lower or "tablette" in t_lower:
                    dev_type = "tablette"
                elif "macbook" in t_lower or "ordinateur" in t_lower or "laptop" in t_lower:
                    dev_type = "pc_portable"
                elif "switch" in t_lower or "ps5" in t_lower or "xbox" in t_lower or "console" in t_lower:
                    dev_type = "console"

                collected_rows.append({
                    "source_site": SOURCE_SITE,
                    "source_url": item.get("url") or BASE_URL,
                    "timestamp": now_iso_utc(),
                    "scrape_started_at": started_at,
                    "data_type": "market_price",
                    "device_type": dev_type,
                    "device_family": "iPhone" if brand == "Apple" else "Smartphone",
                    "brand": brand,
                    "model": info["model"],
                    "model_normalized": info["model_normalized"],
                    "item_title": title,
                    "storage_gb": info["storage_gb"],
                    "storage_raw": f"{info['storage_gb']} Go" if info["storage_gb"] else None,
                    "ram_gb": None,
                    "color": info["color"] or None,
                    "condition": info["condition"],
                    "product_id": item.get("asin", "") or None,
                    "is_currently_sold": True,
                    "price": price_val,
                    "currency": "EUR",
                    "price_raw": f"{price_val:.2f} €",
                    "seller_name": "Amazon / Marketplace",
                    "shipping_cost": 0.0,
                })

            await asyncio.sleep(1.2)

        await browser.close()

    print(f"[{SOURCE_SITE}] {len(collected_rows)} offres brutes collectées.", flush=True)

    if not collected_rows:
        project_root = Path(__file__).resolve().parents[4]
        baseline_file = project_root / "dataset" / "db_amazon.json"
        if baseline_file.exists():
            print(f"[{SOURCE_SITE}] Chargement de la base consolidée de référence ({baseline_file.name})...", flush=True)
            try:
                with open(baseline_file, "r", encoding="utf-8") as f:
                    b_data = json.load(f)
                    collected_rows = b_data if isinstance(b_data, list) else b_data.get("rows", [])
            except Exception as exc:
                issues.append(fetch_issue(
                    source_site=SOURCE_SITE,
                    source_url="baseline_amazon",
                    reason=REASON_FETCH_ERROR,
                    detail=f"Erreur chargement baseline: {exc}",
                ))

    # Filtrage IQR
    if collected_rows:
        clean_rows, iqr_dropped, cohort_stats = filter_by_iqr(
            collected_rows,
            price_key="price",
            group_by_keys=("brand", "model"),
            factor=iqr_factor,
            source_site=SOURCE_SITE,
        )
        issues.extend(iqr_dropped)
    else:
        clean_rows = collected_rows

    cleaning_stats = None
    if apply_cleaning:
        clean_rows, cleaning_stats = clean_dataset(clean_rows, data_type="product_sale", cleaning_issues=issues)

    write_jsonl_and_csv(clean_rows, jsonl_path=jsonl_path, csv_path=csv_path)
    with open(json_bundle, "w", encoding="utf-8") as f:
        json.dump({"source_site": SOURCE_SITE, "scrape_started_at": started_at, "row_count": len(clean_rows), "rows": clean_rows}, f, indent=2, ensure_ascii=False)

    if emit_run_report:
        payload = finalize_run_artifacts(
            source_site=SOURCE_SITE,
            scrape_started_at=started_at,
            json_path=json_bundle,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(clean_rows),
            apply_cleaning=apply_cleaning,
            clean_report=cleaning_stats,
            fetch_errors=sum(1 for i in issues if i.get("reason") == REASON_FETCH_ERROR),
            issues=issues,
        )
        print(summarize_run_line(payload), flush=True)

    return clean_rows


def scrape(
    output_dir: str | Path = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    headless: bool = True,
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return asyncio.run(
        _async_scrape(
            output_dir=out_dir,
            headless=headless,
            max_models=max_models,
            apply_cleaning=apply_cleaning,
            emit_run_report=emit_run_report,
        )
    )
