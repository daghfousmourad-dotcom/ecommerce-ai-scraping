"""Scraper pour Refurbed France (refurbed.fr).

Place de marché leader d'appareils électroniques reconditionnés.
Univers : product_sale (smartphones Apple, Samsung, Google, Xiaomi...).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

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

SOURCE_NAME = "refurbed"
SOURCE_DOMAIN = "refurbed.fr"
BASE_URL = "https://www.refurbed.fr/c/telephones-portables/"

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
        "Accept-Language": "fr-FR,fr;q=0.9",
    })
    return s


def infer_brand(text: str) -> str:
    low = text.lower()
    if "iphone" in low or "apple" in low:
        return "Apple"
    if "galaxy" in low or "samsung" in low:
        return "Samsung"
    if "pixel" in low or "google" in low:
        return "Google"
    if "xiaomi" in low or "redmi" in low or "poco" in low:
        return "Xiaomi"
    if "huawei" in low:
        return "Huawei"
    return "Autre"


def parse_page(html: str, page_url: str, scraped_at: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    articles = soup.find_all("article")
    results = []

    for art in articles:
        text = art.get_text(separator=" | ", strip=True)
        m = re.search(r"(\d+[\.,]?\d*)\s*€", text)
        if not m:
            continue
        try:
            price_val = float(m.group(1).replace(",", "."))
        except ValueError:
            continue
        if price_val <= 0:
            continue

        a = art.find("a", href=True)
        prod_url = urljoin("https://www.refurbed.fr", a["href"]) if a else page_url

        # Model name
        title_el = art.find(["h2", "h3", "h4", "p"])
        model_name = title_el.get_text(strip=True) if title_el else ""
        if not model_name or len(model_name) < 4:
            parts = [p.strip() for p in text.split("|")]
            for p in parts:
                if any(b in p.lower() for b in ["iphone", "galaxy", "pixel", "xiaomi", "huawei"]):
                    model_name = p
                    break

        if not model_name:
            continue

        brand = infer_brand(model_name)
        full_title = f"{brand} {model_name}".strip() if not model_name.lower().startswith(brand.lower()) else model_name

        row = {
            "source_site": SOURCE_DOMAIN,
            "source_url": prod_url,
            "source": prod_url,
            "timestamp": scraped_at,
            "scrape_started_at": scraped_at,
            "data_type": "product_sale",
            "device_type": "smartphone",
            "device_family": "Smartphone",
            "brand": brand,
            "model": model_name,
            "model_normalized": full_title,
            "item_title": full_title,
            "item_name": full_title,
            "condition": "Reconditionné",
            "price": price_val,
            "price_raw": f"{price_val:.2f} €".replace(".", ","),
            "currency": "EUR",
            "currency_symbol": "€",
            "seller_name": "Refurbed France",
            "warranty": "Garantie 12 mois minimum",
            "scraped_at": scraped_at,
        }
        results.append(row)

    return results


def scrape(
    output_dir: str | Path | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_pages: int | None = 15,
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
    num_pages = max_pages or 15
    print(f"[{SOURCE_NAME}] Crawl de {num_pages} pages de téléphones reconditionnés ({BASE_URL})...")

    for p in range(1, num_pages + 1):
        url = BASE_URL if p == 1 else f"{BASE_URL}{p}/"
        try:
            resp = session.get(url, timeout=12)
            if resp.status_code == 200:
                sub = parse_page(resp.text, url, scrape_started_at)
                rows.extend(sub)
                if p % 5 == 0 or p == num_pages:
                    print(f"[{SOURCE_NAME}] {p}/{num_pages} pages crawlées ({len(rows)} offres cumulées)...")
            else:
                issues.append(fetch_issue(source_site=SOURCE_NAME, url=url, reason=REASON_FETCH_ERROR, message=f"HTTP {resp.status_code}"))
            time.sleep(0.1)
        except Exception as e:
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=url, reason=REASON_FETCH_ERROR, message=str(e)))

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "source_url", "item_title", "price_raw"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}")

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning and rows:
        cleaning_cfg = CleaningConfig(
            price_min=15.0,
            price_max=3000.0,
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

    print(f"[{SOURCE_NAME.upper()}] ✅ Fin d'exécution : {len(rows)} offres exportées dans {out_dir}", flush=True)

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
    parser.add_argument("--max-pages", type=int, default=15)
    args = parser.parse_args()
    scrape(output_dir=args.output_dir, max_pages=args.max_pages)


if __name__ == "__main__":
    main()
