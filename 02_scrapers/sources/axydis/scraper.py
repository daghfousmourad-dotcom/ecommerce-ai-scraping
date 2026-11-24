"""Scraper pour AxyDis / AxyTel (axydis.fr).

Grossiste et distributeur B2B de terminaux mobiles professionnels (flottes d'entreprises).
Univers : product_sale / B2B (Apple, Samsung, Google, Xiaomi, Honor, Crosscall...).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_SCRAPERS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRAPERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT))
if str(_SCRAPERS_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT.parent))
_CORE_DIR = _SCRAPERS_ROOT / "core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

try:
    from scraper.core.filtering import CleaningConfig, clean_dataset
    from scraper.core.parser_utils import dedupe_dict_rows, now_iso_utc
    from scraper.core.row_export import write_jsonl_and_csv
    from scraper.core.run_report import (
        REASON_FETCH_ERROR,
        fetch_issue,
        finalize_run_artifacts,
        summarize_run_line,
    )
except ImportError:
    from core.filtering import CleaningConfig, clean_dataset
    from core.parser_utils import dedupe_dict_rows, now_iso_utc
    from core.row_export import write_jsonl_and_csv
    from core.run_report import (
        REASON_FETCH_ERROR,
        fetch_issue,
        finalize_run_artifacts,
        summarize_run_line,
    )

SOURCE_NAME = "axydis"
SOURCE_DOMAIN = "axydis.fr"
TARGET_URL = "https://www.axydis.fr/"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


def get_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(pool_connections=5, pool_maxsize=5, max_retries=retries))
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9",
    })
    return s


def infer_brand(text: str) -> str:
    low = text.lower()
    if "apple" in low or "iphone" in low:
        return "Apple"
    if "samsung" in low or "galaxy" in low:
        return "Samsung"
    if "google" in low or "pixel" in low:
        return "Google"
    if "xiaomi" in low or "redmi" in low:
        return "Xiaomi"
    if "honor" in low:
        return "Honor"
    if "crosscall" in low:
        return "Crosscall"
    if "fairphone" in low:
        return "Fairphone"
    return "Autre"


def parse_terminals(html: str, scraped_at: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".tcard")
    results = []

    for c in cards:
        name_el = c.select_one(".tname")
        if not name_el:
            continue
        full_name = name_el.get_text(" ", strip=True)
        if not full_name:
            continue

        brand = infer_brand(full_name)
        
        st_match = re.search(r"(\d+)\s*Go", full_name, re.IGNORECASE)
        storage_gb = int(st_match.group(1)) if st_match else None
        
        model_name = re.sub(r"\b\d+\s*Go\b", "", full_name, flags=re.IGNORECASE).strip()
        if model_name.lower().startswith(brand.lower()):
            model_clean = model_name[len(brand):].strip()
        else:
            model_clean = model_name

        price_without_plan_el = c.select_one(".teco")
        price_with_plan_el = c.select_one(".tprice")

        p_without = None
        if price_without_plan_el:
            m = re.search(r"(\d+[\.,]?\d*)\s*€", price_without_plan_el.get_text())
            if m:
                p_without = float(m.group(1).replace(",", "."))

        p_with = None
        if price_with_plan_el:
            m = re.search(r"(\d+[\.,]?\d*)\s*€", price_with_plan_el.get_text())
            if m:
                p_with = float(m.group(1).replace(",", "."))

        chosen_price_ht = p_without or p_with
        if not chosen_price_ht or chosen_price_ht <= 0:
            continue

        price_ttc = round(chosen_price_ht * 1.20, 2)

        badge_el = c.select_one(".tbadge")
        badge = badge_el.get_text(strip=True) if badge_el else "Neuf"
        condition = "Reconditionné" if "reconditionné" in badge.lower() or "reconditionné" in full_name.lower() else "Neuf"

        row = {
            "source_site": SOURCE_DOMAIN,
            "source_url": TARGET_URL,
            "source": TARGET_URL,
            "timestamp": scraped_at,
            "scrape_started_at": scraped_at,
            "data_type": "product_sale",
            "device_type": "smartphone",
            "device_family": "Smartphone",
            "brand": brand,
            "model": model_clean,
            "model_normalized": f"{brand} {model_clean}".strip(),
            "item_title": full_name,
            "item_name": full_name,
            "storage_gb": storage_gb,
            "storage_raw": f"{storage_gb} Go" if storage_gb else None,
            "condition": condition,
            "price": price_ttc,
            "price_raw": f"{price_ttc:.2f} €".replace(".", ","),
            "price_ht": chosen_price_ht,
            "price_with_plan_ht": p_with,
            "price_without_plan_ht": p_without,
            "currency": "EUR",
            "currency_symbol": "€",
            "seller_name": "AxyDis B2B",
            "channel": "B2B",
            "scraped_at": scraped_at,
        }
        results.append(row)

    return results


def scrape(
    output_dir: str | Path | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir) if output_dir else (_SCRAPERS_ROOT / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp_clean = scrape_started_at.replace(":", "").replace("-", "").replace("+", "")
    prefix = f"{SOURCE_NAME}_product_sales_{timestamp_clean}"
    jsonl_path = out_dir / f"{prefix}.jsonl"
    csv_path = out_dir / f"{prefix}.csv"
    json_path = out_dir / f"{prefix}.json"

    convenience_jsonl = out_dir / f"db_{SOURCE_NAME}.jsonl"
    convenience_csv = out_dir / f"db_{SOURCE_NAME}.csv"
    convenience_json = out_dir / f"db_{SOURCE_NAME}.json"

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    session = get_session()
    print(f"[{SOURCE_NAME}] Téléchargement du catalogue B2B ({TARGET_URL})...")
    try:
        resp = session.get(TARGET_URL, timeout=15)
        if resp.status_code == 200:
            resp.encoding = "utf-8"
            rows = parse_terminals(resp.text, scrape_started_at)
            print(f"[{SOURCE_NAME}] {len(rows)} terminaux B2B extraits.")
        else:
            print(f"[{SOURCE_NAME}] Erreur HTTP {resp.status_code}")
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=TARGET_URL, reason=REASON_FETCH_ERROR, message=f"HTTP {resp.status_code}"))
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur réseau: {e}")
        issues.append(fetch_issue(source_site=SOURCE_NAME, url=TARGET_URL, reason=REASON_FETCH_ERROR, message=str(e)))

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "price_raw"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}")

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning and rows:
        cleaning_cfg = CleaningConfig(
            price_min=15.0,
            price_max=4000.0,
            dedupe_keys_product_sale=("source_url", "model", "price_raw"),
        )
        rows, clean_report = clean_dataset(
            rows,
            data_type="product_sale",
            config=cleaning_cfg,
            cleaning_issues=issues if emit_run_report else None,
        )
        if clean_report:
            print(clean_report.summary_line(), flush=True)

    for row in rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    write_jsonl_and_csv(rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(rows),
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        shutil.copy(jsonl_path, convenience_jsonl)
        shutil.copy(csv_path, convenience_csv)
        shutil.copy(json_path, convenience_json)
    except Exception:
        pass

    print(f"[{SOURCE_NAME.upper()}] ✅ Fin d'exécution : {len(rows)} terminaux exportés dans {out_dir}", flush=True)

    if emit_run_report:
        payload_report = finalize_run_artifacts(
            source_site=SOURCE_NAME,
            scrape_started_at=scrape_started_at,
            json_path=json_path,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(rows),
            apply_cleaning=apply_cleaning,
            clean_report=clean_report,
            fetch_errors=fetch_errors,
            issues=issues,
        )
        print(summarize_run_line(payload_report), flush=True)

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Scraper {SOURCE_NAME}")
    parser.add_argument("--output-dir", type=str, default=None)
    args = parser.parse_args()
    scrape(output_dir=args.output_dir)


if __name__ == "__main__":
    main()
