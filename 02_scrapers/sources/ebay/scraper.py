"""
eBay Browse API scraper (sources2).
Intégré dans ecommerce_ai_scraping_scraper_V2-main.
Supporte à la fois la collecte temps réel via l'API officielle eBay (OAuth 2.0)
et le chargement sécurisé de la base certifiée de production.
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

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from scraper.core.filtering import clean_dataset
from scraper.core.parser_utils import now_iso_utc
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.run_report import (
    REASON_FETCH_ERROR,
    fetch_issue,
    finalize_run_artifacts,
    summarize_run_line,
)
from .ebay_api import EbayAPIError, EbayClient
from .normalize import normalize_item

SOURCE_SITE = "ebay"
logger = logging.getLogger("ebay_scraper")


def _row_from_normalized(norm: dict[str, Any], started_at: str) -> dict[str, Any]:
    price_val = float(norm["price"]) if norm.get("price") is not None else None
    currency = norm.get("currency") or "EUR"
    st_gb = norm.get("storage_gb")
    p_raw = f"{price_val:.2f} {currency}" if price_val is not None else ""

    return {
        "source_site": SOURCE_SITE,
        "source_url": norm.get("item_url"),
        "timestamp": norm.get("extracted_at") or now_iso_utc(),
        "scrape_started_at": started_at,
        "data_type": "market_price",
        "device_type": "smartphone",
        "device_family": norm.get("variant") or norm.get("model"),
        "brand": norm.get("brand"),
        "model": norm.get("model"),
        "model_normalized": (norm.get("model") or "").strip().lower(),
        "item_title": norm.get("title"),
        "storage_gb": int(st_gb) if st_gb is not None else None,
        "storage_raw": f"{int(st_gb)} Go" if st_gb is not None else None,
        "ram_gb": int(norm["ram_gb"]) if norm.get("ram_gb") is not None else None,
        "color": norm.get("color"),
        "condition": norm.get("condition_class") or norm.get("condition_name") or "NEW",
        "product_id": norm.get("ebay_item_id"),
        "is_currently_sold": True,
        "price": price_val,
        "currency": currency,
        "price_raw": p_raw,
        "seller_name": "eBay Vendeur",
        "shipping_cost": 0.0,
    }


def _collect_live_api(
    client_id: str,
    client_secret: str,
    max_items: int | None = None,
    limit_pages: int | None = None,
    started_at: str = "",
) -> list[dict[str, Any]]:
    client = EbayClient(
        client_id=client_id,
        client_secret=client_secret,
        api_base=os.getenv("EBAY_API_BASE", "https://api.ebay.com").rstrip("/"),
        marketplace_id=os.getenv("EBAY_MARKETPLACE_ID", "EBAY_FR"),
        accept_language=os.getenv("EBAY_ACCEPT_LANGUAGE", "fr-FR"),
        timeout=int(os.getenv("REQUEST_TIMEOUT", "30")),
        retry_attempts=2,
        request_delay=0.1,
        logger=logger,
    )

    print(f"[{SOURCE_SITE}] Authentification OAuth 2.0 auprès d'eBay...", flush=True)
    client.authenticate()
    print(f"[{SOURCE_SITE}] Authentification OAuth 2.0 réussie.", flush=True)

    query = os.getenv("EBAY_QUERY", "smartphone")
    category_id = os.getenv("EBAY_CATEGORY_ID", "9355")
    page_size = min(max_items or 50, 50)
    target_count = max_items or 50

    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    offset = 0
    pages_done = 0
    max_p = limit_pages or 50

    while len(rows) < target_count and pages_done < max_p:
        print(f"[{SOURCE_SITE}] Requête Browse API offset={offset}, limit={page_size}...", flush=True)
        data = client.search(
            query=query,
            category_id=category_id,
            offset=offset,
            limit=page_size,
        )
        items = data.get("itemSummaries", []) or []
        pages_done += 1
        if not items:
            break

        for summary in items:
            item_id = summary.get("itemId")
            if not item_id or item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            try:
                detailed = client.get_item(item_id)
            except Exception:
                detailed = summary

            norm = normalize_item(detailed)
            if norm:
                rows.append(_row_from_normalized(norm, started_at=started_at))

            if len(rows) >= target_count:
                break

        offset += page_size
        if len(items) < page_size:
            break

    print(f"[{SOURCE_SITE}] Collecte temps réel terminée : {len(rows)} annonces extraites.", flush=True)
    return rows


def scrape(
    output_dir: str | Path = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    limit_pages: int | None = None,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    started_at = now_iso_utc()
    time_suffix = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S0000")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_bundle = out_dir / f"ebay_market_prices_{time_suffix}.json"
    jsonl_path = out_dir / f"ebay_market_prices_{time_suffix}.jsonl"
    csv_path = out_dir / f"ebay_market_prices_{time_suffix}.csv"
    run_json_path = out_dir / f"ebay_market_prices_{time_suffix}_run.json"
    issues_jsonl_path = out_dir / f"ebay_market_prices_{time_suffix}_issues.jsonl"

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    # 1. Charger les variables d'environnement (.env dans ecommerce_ai_scraping_scraper_V2-main ou root)
    current_file = Path(__file__).resolve()
    v2_root = current_file.parents[3]
    project_root = current_file.parents[4]
    load_dotenv(v2_root / ".env")
    load_dotenv(project_root / ".env")

    client_id = os.getenv("EBAY_CLIENT_ID", "MouradDa-Mourad-PRD-6a217ea81-ab7e9006").strip()
    client_secret = os.getenv("EBAY_CLIENT_SECRET", "PRD-a217ea8122d4-fd25-405b-b070-322b").strip()

    # 2. Si identifiants présents, tentative de collecte temps réel via l'API Browse
    if client_id and client_secret:
        try:
            rows = _collect_live_api(
                client_id=client_id,
                client_secret=client_secret,
                max_items=max_items,
                limit_pages=limit_pages,
                started_at=started_at,
            )
        except Exception as exc:
            print(f"[{SOURCE_SITE}] Erreur API live eBay : {exc}. Bascule vers la base de référence...", flush=True)
            issues.append(fetch_issue(
                source_site=SOURCE_SITE,
                source_url="ebay_browse_api",
                reason=REASON_FETCH_ERROR,
                detail=f"Erreur API eBay: {exc}",
            ))

    # 3. Repli autonome si pas d'identifiants ou si l'API a échoué
    if not rows:
        baseline_candidates = [
            project_root / "dataset" / "db_ebay.json",
            v2_root / "dataset" / "db_ebay.json",
        ]
        for cpath in baseline_candidates:
            if cpath.exists():
                print(f"[{SOURCE_SITE}] Chargement de la base certifiée de référence ({cpath.name})...", flush=True)
                try:
                    with open(cpath, "r", encoding="utf-8") as f:
                        raw_data = json.load(f)

                    raw_rows = raw_data if isinstance(raw_data, list) else raw_data.get("rows", [])
                    # Si c'est déjà standardisé dans db_ebay.json
                    if raw_rows and "source_site" in raw_rows[0]:
                        for r in raw_rows:
                            r_copy = dict(r)
                            r_copy["scrape_started_at"] = started_at
                            rows.append(r_copy)
                    else:
                        for r in raw_rows:
                            norm_dummy = {
                                "item_url": r.get("item_url"),
                                "extracted_at": r.get("extracted_at"),
                                "variant": r.get("variant"),
                                "model": r.get("model"),
                                "brand": r.get("brand"),
                                "title": r.get("title"),
                                "storage_gb": r.get("storage_gb"),
                                "ram_gb": r.get("ram_gb"),
                                "color": r.get("color"),
                                "condition_class": r.get("condition_class"),
                                "condition_name": r.get("condition_name"),
                                "ebay_item_id": r.get("ebay_item_id"),
                                "price": r.get("price"),
                                "currency": r.get("currency"),
                            }
                            rows.append(_row_from_normalized(norm_dummy, started_at=started_at))

                    print(f"[{SOURCE_SITE}] {len(rows)} smartphones chargés depuis la référence.", flush=True)
                    break
                except Exception as exc:
                    issues.append(fetch_issue(
                        source_site=SOURCE_SITE,
                        source_url=str(cpath),
                        reason=REASON_FETCH_ERROR,
                        detail=f"Erreur chargement baseline {cpath.name}: {exc}",
                    ))

        if max_items and len(rows) > max_items:
            rows = rows[:max_items]

    finished_at = now_iso_utc()
    cleaning_stats = None
    if apply_cleaning:
        rows, cleaning_stats = clean_dataset(rows, data_type="product_sale", cleaning_issues=issues)

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
