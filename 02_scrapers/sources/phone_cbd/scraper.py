"""
Phone CBD repair scraper (sources2).
Intégré dans ecommerce_ai_scraping_scraper_V2-main selon les conventions de l'architecture V2.
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
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from scraper.core.filtering import clean_dataset
from scraper.core.http_client import create_session
from scraper.core.normalization import normalize_repair_type
from scraper.core.parser_utils import now_iso_utc
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.run_report import (
    REASON_FETCH_ERROR,
    fetch_issue,
    finalize_run_artifacts,
    summarize_run_line,
)

SOURCE_SITE = "phone_cbd"
BASE_URL = "https://phoneandcbd.fr"
REPAIR_HUB_URL = f"{BASE_URL}/pages/remise-en-etat-telephone"

SHOP_CITY = "Marseille"
SHOP_POSTAL = "13006"
SHOP_WARRANTY = "Garantie 6 mois"
SHOP_DELAY = "30 minutes à 1h"

# Consoles et tablettes sont désormais autorisées
EXCLUDED_COLLECTION_KEYWORDS = [
    "accessoire", "accessoires", "coque", "verre-trempe", "cable", "chargeur"
]

KNOWN_BRANDS = {
    "apple": "Apple",
    "iphone": "Apple",
    "samsung": "Samsung",
    "galaxy": "Samsung",
    "huawei": "Huawei",
    "xiaomi": "Xiaomi",
    "mi": "Xiaomi",
    "redmi": "Xiaomi",
    "google": "Google",
    "pixel": "Google",
    "oppo": "Oppo",
    "honor": "Honor",
    "vivo": "Vivo",
    "realme": "Realme",
    "oneplus": "OnePlus",
    "sony": "Sony",
    "motorola": "Motorola",
    "nokia": "Nokia",
    "nintendo": "Nintendo",
    "playstation": "Sony PlayStation",
    "xbox": "Microsoft Xbox",
}


def normalize_name(name: str) -> str:
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name.strip())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"(?i)\b(réparation|reparation|forfait|remise en etat)\b", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_brand(brand_raw: str, handle: str = "", title: str = "") -> str:
    target_str = f"{brand_raw} {handle} {title}".lower()
    for key, val in KNOWN_BRANDS.items():
        if re.search(rf"\b{re.escape(key)}\b", target_str):
            return val
    b = re.sub(r"(?i)\b(réparation|reparation|ecran|telephone|téléphone|smartphone|forfait)s?\b", "", brand_raw).strip()
    if not b and handle:
        b = re.sub(r"(?i)\b(reparation|reparation-de|ecran)s?\b", "", handle).replace("-", " ").strip().title()
    return b.title() if b else "Autre"


def extract_brand_collections(session: requests.Session) -> list[dict[str, str]]:
    try:
        resp = session.get(REPAIR_HUB_URL, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[{SOURCE_SITE}] Erreur accès page hub {REPAIR_HUB_URL}: {e}", file=sys.stderr)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    collections = []
    seen = set()

    for a in soup.select('a[href*="/collections/"]'):
        href = a.get("href", "")
        m = re.search(r"/collections/([^/?#]+)", href)
        if not m:
            continue
        handle = m.group(1).lower()
        if any(ex in handle for ex in EXCLUDED_COLLECTION_KEYWORDS):
            continue

        title = a.get_text(strip=True) or handle.replace("-", " ").title()
        if handle not in seen:
            seen.add(handle)
            collections.append({"handle": handle, "title": title})

    return collections


def scrape(
    output_dir: str | Path = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_collections: int | None = None,
) -> list[dict[str, Any]]:
    """Exécute la collecte des offres de réparation Phone & CBD Marseille."""
    started_at = now_iso_utc()
    time_suffix = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S0000")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_bundle = out_dir / f"phone_cbd_repair_prices_{time_suffix}.json"
    jsonl_path = out_dir / f"phone_cbd_repair_prices_{time_suffix}.jsonl"
    csv_path = out_dir / f"phone_cbd_repair_prices_{time_suffix}.csv"
    run_json_path = out_dir / f"phone_cbd_repair_prices_{time_suffix}_run.json"
    issues_jsonl_path = out_dir / f"phone_cbd_repair_prices_{time_suffix}_issues.jsonl"

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    session = create_session()
    print(f"[{SOURCE_SITE}] Découverte des marques sur Phone & CBD Marseille...", flush=True)
    collections = extract_brand_collections(session)
    if max_collections:
        collections = collections[:max_collections]

    print(f"[{SOURCE_SITE}] {len(collections)} collections identifiées.", flush=True)

    for coll in collections:
        handle = coll["handle"]
        brand_name = clean_brand(coll["title"], handle)
        api_url = f"{BASE_URL}/collections/{handle}/products.json?limit=250"

        try:
            time.sleep(1.0)
            r = session.get(api_url, timeout=15)
            r.raise_for_status()
            products = r.json().get("products", [])
        except Exception as e:
            issues.append(fetch_issue(
                source_site=SOURCE_SITE,
                source_url=api_url,
                reason=REASON_FETCH_ERROR,
                detail=f"Erreur API collection {handle}: {e}",
            ))
            continue

        for prod in products:
            prod_title = prod.get("title", "").strip()
            handle_prod = prod.get("handle", "")
            prod_url = f"{BASE_URL}/products/{handle_prod}"

            vendor = prod.get("vendor", "").strip()
            if vendor and vendor.lower() not in ("phone & cbd", "phone and cbd", "phone&cbd", "default"):
                effective_brand = clean_brand(vendor, handle, prod_title)
            else:
                effective_brand = clean_brand(brand_name, handle, prod_title)

            model_name = re.sub(r"(?i)\b(réparation|reparation|forfait|remise en etat)\b", "", prod_title).strip()
            model_norm = normalize_name(model_name)
            model_clean = re.sub(rf"(?i)\b{re.escape(effective_brand)}\b", "", model_norm).strip()
            model = model_clean or model_norm

            # Détection device_type
            dev_type = "smartphone"
            t_lower = prod_title.lower()
            if "ipad" in t_lower or "tablette" in t_lower or "tab" in t_lower:
                dev_type = "tablette"
            elif "switch" in t_lower or "ps5" in t_lower or "ps4" in t_lower or "xbox" in t_lower or "console" in t_lower:
                dev_type = "console"
            elif "macbook" in t_lower or "pc" in t_lower or "ordinateur" in t_lower:
                dev_type = "pc_portable"

            variants = prod.get("variants", [])
            for var in variants:
                var_title = var.get("title", "").strip()
                raw_price = var.get("price")
                try:
                    price_val = float(raw_price) if raw_price is not None else None
                except (ValueError, TypeError):
                    price_val = None

                if price_val is None or price_val <= 0:
                    continue

                if not var_title or var_title.lower() in ("default title", "default", "titre par défaut"):
                    raw_repair = prod_title
                else:
                    raw_repair = var_title

                repair_label, repair_matched = normalize_repair_type(raw_repair)
                if not repair_matched and raw_repair != prod_title:
                    repair_from_prod, matched_prod = normalize_repair_type(prod_title)
                    if matched_prod:
                        repair_label = repair_from_prod
                        repair_matched = True

                row = {
                    "source_site": SOURCE_SITE,
                    "source_url": prod_url,
                    "timestamp": now_iso_utc(),
                    "scrape_started_at": started_at,
                    "data_type": "repair_price",
                    "device_type": dev_type,
                    "device_family": effective_brand,
                    "brand": effective_brand,
                    "model": model,
                    "model_normalized": model.lower(),
                    "product_or_part_name": f"{model} - {raw_repair}",
                    "repair_type_raw": raw_repair,
                    "repair_type": repair_label or raw_repair,
                    "repair_type_matched": repair_matched,
                    "price": price_val,
                    "currency": "EUR",
                    "price_raw": f"{price_val:.2f} €",
                    "warranty": SHOP_WARRANTY,
                    "delay": SHOP_DELAY,
                    "shop_name": f"Phone & CBD ({SHOP_CITY})",
                    "city": SHOP_CITY,
                    "postal_code": SHOP_POSTAL,
                }
                rows.append(row)

    finished_at = now_iso_utc()
    cleaning_stats = None
    if apply_cleaning:
        rows, cleaning_stats = clean_dataset(rows, data_type="repair_price", cleaning_issues=issues)

    write_jsonl_and_csv(rows, jsonl_path=jsonl_path, csv_path=csv_path)
    with open(json_bundle, "w", encoding="utf-8") as f:
        json.dump({"source_site": SOURCE_SITE, "scrape_started_at": started_at, "row_count": len(rows), "rows": rows}, f, indent=2, ensure_ascii=False)

    if emit_run_report:
        payload = finalize_run_artifacts(
            source_site=SOURCE_SITE,
            scrape_started_at=started_at,
            json_path=json_bundle,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(rows),
            apply_cleaning=apply_cleaning,
            clean_report=cleaning_stats,
            fetch_errors=len(issues),
            issues=issues,
        )
        print(summarize_run_line(payload), flush=True)

    return rows

