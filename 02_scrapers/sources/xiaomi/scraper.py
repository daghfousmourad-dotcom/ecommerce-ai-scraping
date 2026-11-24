"""
Xiaomi France Official Spare Parts Price Scraper (sources2).

Extrait les tarifs officiels de pièces détachées et réparations constructeur Xiaomi France :
  Portail officiel: https://www.mi.com/fr/support/spare-parts-price/
  API Catalogue: https://go.buy.mi.com/fr/v2/item/category/nav?from=tablet
  API Modèles: https://go.buy.mi.com/fr/search/product-list
  API Info Pièces: https://ams-go.buy.mi.com/fr/misc/getgoodsinformation
  API Tarifs Officiels: https://ams-api.buy.mi.com/fr/crm/api/XGetPrice (languageId=1036, country=fr)
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

import concurrent.futures
import csv
import json
import re
from pathlib import Path
from typing import Any

from tqdm import tqdm

from scraper.core.filtering import CleaningConfig, clean_dataset
from scraper.core.http_client import create_session
from scraper.core.normalization import normalize_device_name, normalize_repair_type
from scraper.core.parser_utils import (
    dedupe_dict_rows,
    now_iso_utc,
)
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.run_report import (
    finalize_run_artifacts,
    summarize_run_line,
)

BASE_SUPPORT_URL = "https://www.mi.com/fr/support/spare-parts-price/"
NAV_CATEGORY_URL = "https://go.buy.mi.com/fr/v2/item/category/nav?from=tablet"
PRODUCT_LIST_URL = "https://go.buy.mi.com/fr/search/product-list"
SEARCH_RESULT_URL = "https://go.buy.mi.com/fr/v2/search/result"
GOODS_INFO_URL = "https://ams-go.buy.mi.com/fr/misc/getgoodsinformation"
PRICE_API_URL = "https://ams-api.buy.mi.com/fr/crm/api/XGetPrice"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Referer": BASE_SUPPORT_URL,
    "Accept": "application/json, text/plain, */*",
}

RELEVANT_CAT_IDS = {
    1348: "Smartphones",
    1351: "Tablettes",
    1325: "Smart Watches",
    1328: "Smart Bands",
    1334: "Écouteurs TWS",
    2344: "TVs",
}


def detect_brand(model_name: str) -> str:
    """Détecte la marque commerciale Xiaomi / POCO / Redmi."""
    m_upper = model_name.upper()
    if "POCO" in m_upper:
        return "POCO"
    if "REDMI" in m_upper:
        return "Redmi"
    return "Xiaomi"


def detect_device_type(cat_name: str, model_name: str) -> str:
    """Détecte le type d'appareil (smartphone, tablet, smartwatch)."""
    text = f"{cat_name} {model_name}".lower()
    if any(k in text for k in ["pad", "tablette", "tablet"]):
        return "tablet"
    if any(k in text for k in ["watch", "band", "montre"]):
        return "smartwatch"
    if any(k in text for k in ["tv", "télé"]):
        return "tv"
    return "smartphone"


def discover_all_product_tags(session) -> dict[str, dict[str, Any]]:
    """
    Découvre l'ensemble des tags de produits Xiaomi France via les catégories et le moteur de recherche.
    Retourne un mapping tag -> {name, category_name, device_type}.
    """
    products: dict[str, dict[str, Any]] = {}

    # 1. Catégories ciblées de produits électroniques / mobiles
    for cat_id, cat_title in RELEVANT_CAT_IDS.items():
        page = 0
        while True:
            url = (
                f"{PRODUCT_LIST_URL}?version=v4&cacheable=1&page_size=20"
                f"&type=2&cat_id={cat_id}&page_index={page}&from=tablet"
            )
            try:
                res = session.get(url, headers=DEFAULT_HEADERS, timeout=8)
                if res.status_code != 200:
                    break
                dp = (res.json().get("data") or {}).get("data_provider") or {}
                items = (dp.get("data") or {}).get("product_list") or []
                if not items:
                    break

                for item in items:
                    p = item.get("product") or {}
                    name = p.get("name")
                    link = p.get("item_link") or ""
                    m = re.search(r"/product/([^/]+)/?", link)
                    tag = m.group(1) if m else None
                    if tag and tag not in products:
                        products[tag] = {
                            "name": name,
                            "category_name": cat_title,
                            "device_type": detect_device_type(cat_title, name or ""),
                            "tag": tag,
                        }

                total = dp.get("product_total_count", 0)
                page += 1
                if len(items) < 20 or (page * 20 >= total):
                    break
            except Exception:
                break

    # 2. Mots-clés de recherche complémentaires (smartphones / tablettes)
    keywords = ["xiaomi", "redmi", "poco"]
    for kw in keywords:
        page = 0
        while page < 3:  # limiter à 3 pages max par mot-clé
            url = (
                f"{SEARCH_RESULT_URL}?type=1&word={kw}&from=mobile"
                f"&page_index={page}&page_size=30"
            )
            try:
                res = session.get(url, headers=DEFAULT_HEADERS, timeout=8)
                if res.status_code != 200:
                    break
                dp = (res.json().get("data") or {}).get("data_provider") or {}
                items = (dp.get("data") or {}).get("product_list") or []
                if not items:
                    break

                for item in items:
                    p = item.get("product") or {}
                    name = p.get("name")
                    link = p.get("item_link") or ""
                    m = re.search(r"/product/([^/]+)/?", link)
                    tag = m.group(1) if m else None
                    if tag and tag not in products:
                        products[tag] = {
                            "name": name,
                            "category_name": kw.capitalize(),
                            "device_type": detect_device_type("", name or ""),
                            "tag": tag,
                        }

                total = dp.get("product_total_count", 0)
                page += 1
                if len(items) < 20 or (page * 30 >= total):
                    break
            except Exception:
                break

    return products


def fetch_spare_parts_for_sku(session, sku: str) -> list[dict[str, Any]]:
    """
    Interroge l'API officielle CRM Xiaomi France pour obtenir les tarifs des pièces d'un SKU.
    """
    payload = {
        "from": "pc",
        "goods_id": str(sku),
        "country": "fr",
        "languageId": 1036,  # 1036 = Français (France)
    }
    headers = dict(DEFAULT_HEADERS)
    headers["Content-Type"] = "application/json"

    try:
        resp = session.post(PRICE_API_URL, json=payload, headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("statusCode") == 200 and data.get("body"):
                return data["body"]
    except Exception:
        pass
    return []


def process_single_product(
    tag: str,
    p_info: dict[str, Any],
    timestamp: str,
    scrape_started_at: str,
) -> list[dict[str, Any]]:
    """
    Traite un modèle unique : résout ses SKUs et extrait les prix de pièces détachées.
    """
    session = create_session()
    model_name = p_info.get("name") or tag
    dev_type = p_info.get("device_type") or "smartphone"
    brand = detect_brand(model_name)

    dev = normalize_device_name(dev_type, brand=brand, model=model_name)
    model_str = dev.get("model") or model_name
    model_norm = (model_str or "").lower().strip()
    source_url = f"{BASE_SUPPORT_URL}?name={tag}"

    info_url = f"{GOODS_INFO_URL}?from=pc&tag={tag}"
    try:
        r = session.get(info_url, headers=DEFAULT_HEADERS, timeout=8)
        if r.status_code != 200:
            return []
        g_data = (r.json().get("data") or {}).get("goodsinformation") or []
    except Exception:
        return []

    if not g_data:
        return []

    rows: list[dict[str, Any]] = []
    queried_skus: set[str] = set()
    seen_specs: set[str] = set()

    for g in g_data:
        sku = str(g.get("sku") or "")
        if not sku or sku in queried_skus:
            continue

        gname = g.get("name") or ""
        m_spec = re.search(r"(\d+\s*(?:GO|GB)\s*\+\s*\d+\s*(?:GO|GB|TO|TB))", gname, re.IGNORECASE)
        spec_str = m_spec.group(1).upper() if m_spec else ""

        if spec_str and spec_str in seen_specs:
            continue
        if spec_str:
            seen_specs.add(spec_str)
        queried_skus.add(sku)

        parts = fetch_spare_parts_for_sku(session, sku)
        if not parts:
            continue

        for part in parts:
            raw_part_name = (part.get("materialName") or "").strip()
            price_ttc = part.get("price")
            price_ht = part.get("priceBeforeTax")
            currency = part.get("currencyCode") or "EUR"

            if not raw_part_name or price_ttc is None:
                continue

            try:
                price_val = float(price_ttc)
            except (ValueError, TypeError):
                continue

            repair_norm, repair_matched = normalize_repair_type(raw_part_name)

            part_label = raw_part_name
            if spec_str and "carte mère" in raw_part_name.lower():
                part_label = f"{raw_part_name} ({spec_str})"

            rows.append({
                "source_site": "xiaomi",
                "source_url": source_url,
                "timestamp": timestamp,
                "scrape_started_at": scrape_started_at,
                "data_type": "repair_price",
                "device_type": dev_type,
                "device_family": dev.get("device_family"),
                "brand": brand,
                "model": model_str,
                "model_normalized": model_norm,
                "product_or_part_name": f"{part_label} - {model_str}",
                "repair_type_raw": raw_part_name,
                "repair_type": repair_norm,
                "repair_type_matched": repair_matched,
                "price": price_val,
                "price_before_tax": float(price_ht) if price_ht is not None else None,
                "currency": currency,
                "price_raw": f"{price_val} {currency}",
                "warranty": "Pièces détachées d'origine constructeur Xiaomi",
                "delay": "Centre de service agréé Xiaomi ou envoi postal",
                "shop_name": "Xiaomi France Service Officiel",
                "city": "France",
                "postal_code": "",
            })

    return rows


def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
) -> list[dict[str, Any]]:
    """
    Exécute le scraping complet des tarifs officiels Xiaomi France Spare Parts Price.
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
    print("[Xiaomi] Découverte du catalogue produits...", flush=True)
    products = discover_all_product_tags(session)
    print(f"[Xiaomi] {len(products)} modèles uniques découverts.", flush=True)

    if not products:
        raise RuntimeError("Aucun produit Xiaomi découvert.")

    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    jsonl_path = out_dir / f"xiaomi_official_prices_{ts_suffix}.jsonl"
    json_path = out_dir / f"xiaomi_official_prices_{ts_suffix}.json"
    csv_path = out_dir / f"xiaomi_official_prices_{ts_suffix}.csv"

    jsonl_file = jsonl_path.open("w", encoding="utf-8")
    csv_file = csv_path.open("w", encoding="utf-8", newline="")
    csv_writer: csv.DictWriter | None = None

    def _stream_rows(rows: list[dict[str, Any]]) -> None:
        nonlocal csv_writer
        for row in rows:
            row_with_meta = dict(row)
            row_with_meta.setdefault("scrape_started_at", scrape_started_at)
            all_rows.append(row_with_meta)

            jsonl_file.write(json.dumps(row_with_meta, ensure_ascii=False) + "\n")
            jsonl_file.flush()

            if csv_writer is None:
                fieldnames = list(row_with_meta.keys())
                csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                csv_writer.writeheader()
            csv_writer.writerow(row_with_meta)
            csv_file.flush()

    timestamp = now_iso_utc()
    items = list(products.items())

    # Extraction concurrente avec 5 workers
    with tqdm(
        total=len(items),
        desc="[Xiaomi Repair] Extraction",
        unit="modèle",
        dynamic_ncols=True,
    ) as pbar:
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_tag = {
                executor.submit(
                    process_single_product,
                    tag,
                    p_info,
                    timestamp,
                    scrape_started_at,
                ): tag
                for tag, p_info in items
            }

            for future in concurrent.futures.as_completed(future_to_tag):
                try:
                    p_rows = future.result()
                    if p_rows:
                        _stream_rows(p_rows)
                except Exception:
                    fetch_errors += 1
                finally:
                    pbar.update(1)

    jsonl_file.close()
    csv_file.close()

    # Déduplication
    all_rows = dedupe_dict_rows(
        all_rows,
        ("source_url", "model_normalized", "product_or_part_name", "price_raw"),
    )

    clean_report = None
    if apply_cleaning:
        cfg = CleaningConfig(
            dedupe_keys_repair_price=("source_url", "product_or_part_name", "price_raw")
        )
        all_rows, clean_report = clean_dataset(
            all_rows,
            data_type="repair_price",
            config=cfg,
            cleaning_issues=issues if emit_run_report else None,
        )
        print(clean_report.summary_line(), flush=True)

    for row in all_rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    write_jsonl_and_csv(all_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": "xiaomi",
        "scrape_started_at": scrape_started_at,
        "row_count": len(all_rows),
        "rows": all_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[Xiaomi Repair] Terminé. Exporté {len(all_rows)} lignes", flush=True)
    if emit_run_report:
        payload_report = finalize_run_artifacts(
            source_site="xiaomi",
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
        print(summarize_run_line(payload_report), flush=True)

    return all_rows


if __name__ == "__main__":
    scrape()
