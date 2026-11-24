"""Scraper pour SOSav (sosav.fr).

Fournisseur de pièces détachées pour smartphones (Apple, Samsung, Huawei, Xiaomi, Google...).
Univers : part_cost (écrans, batteries, connecteurs, châssis, caméras...).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

SOURCE_NAME = "sosav"
SOURCE_DOMAIN = "sosav.fr"
INDEX_URL = "https://www.sosav.fr/"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


def get_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=retries))
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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


def categorize_part(name: str) -> tuple[str, str]:
    low = name.lower()
    if "officiel" in low or "original" in low or "origine" in low:
        piece = "Originale"
    elif "prime" in low or "premium" in low or "reconditionné" in low:
        piece = "Reconditionné"
    elif "basic" in low or "compatible" in low or "itc" in low or "incell" in low:
        piece = "Compatible"
    else:
        piece = "Standard"

    if "écran" in low or "ecran" in low or "dalle" in low or "lcd" in low or "oled" in low:
        cat = "Écran"
    elif "batterie" in low:
        cat = "Batterie"
    elif "connecteur" in low or "charge" in low:
        cat = "Connecteur de charge"
    elif "vitre arrière" in low or "façade arrière" in low or "châssis" in low or "dos" in low:
        cat = "Façade arrière"
    elif "caméra avant" in low or "frontale" in low:
        cat = "Caméra avant"
    elif "caméra arrière" in low or "lentille" in low or "caméra" in low:
        cat = "Caméra arrière"
    elif "haut-parleur" in low or "écouteur" in low:
        cat = "Haut-parleur"
    elif "nappe" in low:
        cat = "Nappe interne"
    else:
        cat = "Autre pièce détachée"

    return cat, piece


def scrape_model_page(session: requests.Session, model_url: str, model_name: str, scraped_at: str) -> list[dict[str, Any]]:
    resp = session.get(model_url, timeout=12)
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    brand = infer_brand(model_name)
    results = []

    cards = soup.select("article.product-miniature, .product-miniature")
    for c in cards:
        title_el = c.select_one(".product-title, h3, h2, .name, a[itemprop='url']")
        name = title_el.get_text(strip=True) if title_el else ""
        if not name:
            continue

        price_el = c.select_one(".price, [itemprop='price']")
        price_text = price_el.get_text(strip=True) if price_el else ""
        m = re.search(r"(\d+[\.,]?\d*)\s*€", price_text)
        if not m:
            continue

        try:
            price_val = float(m.group(1).replace(",", "."))
        except ValueError:
            continue

        if price_val <= 0:
            continue

        a = c.find("a", href=True)
        part_url = urljoin(model_url, a["href"]) if a else model_url

        cat, piece_type = categorize_part(name)

        row = {
            "source_site": SOURCE_DOMAIN,
            "source_url": part_url,
            "source": part_url,
            "timestamp": scraped_at,
            "scrape_started_at": scraped_at,
            "data_type": "part_cost",
            "device_type": "smartphone",
            "device_family": "Smartphone",
            "brand": brand,
            "model": model_name,
            "model_normalized": f"{brand} {model_name}".strip() if not model_name.lower().startswith(brand.lower()) else model_name,
            "product_or_part_name": name,
            "item_name": name,
            "repair_type_raw": name,
            "repair_type": cat,
            "repair_category": cat,
            "piece_type": piece_type,
            "price": price_val,
            "price_raw": f"{price_val:.2f} €".replace(".", ","),
            "currency": "EUR",
            "currency_symbol": "€",
            "stock_status": "En stock",
            "seller_name": "SOSav",
            "warranty": "Garantie pièces détachées",
            "scraped_at": scraped_at,
        }
        results.append(row)

    return results


def scrape(
    output_dir: str | Path | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = 60,
) -> list[dict[str, Any]]:
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir) if output_dir else (_SCRAPERS_ROOT / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp_clean = scrape_started_at.replace(":", "").replace("-", "").replace("+", "")
    prefix = f"{SOURCE_NAME}_part_costs_{timestamp_clean}"
    jsonl_path = out_dir / f"{prefix}.jsonl"
    csv_path = out_dir / f"{prefix}.csv"
    json_path = out_dir / f"{prefix}.json"

    convenience_jsonl = out_dir / f"db_{SOURCE_NAME}.jsonl"
    convenience_csv = out_dir / f"db_{SOURCE_NAME}.csv"
    convenience_json = out_dir / f"db_{SOURCE_NAME}.json"

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    session = get_session()
    print(f"[{SOURCE_NAME}] Découverte des modèles smartphones sur ({INDEX_URL})...")
    model_urls: dict[str, str] = {}
    try:
        resp = session.get(INDEX_URL, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(strip=True)
                if "/store/" in href and any(b in text.lower() for b in ["iphone", "galaxy", "pixel", "xiaomi", "huawei"]) and len(text) < 40:
                    full = urljoin(INDEX_URL, href)
                    if full not in model_urls and not full.endswith(".html"):
                        model_urls[full] = text
            print(f"[{SOURCE_NAME}] {len(model_urls)} modèles smartphones trouvés.")
        else:
            print(f"[{SOURCE_NAME}] Erreur HTTP {resp.status_code} sur l'accueil")
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=INDEX_URL, reason=REASON_FETCH_ERROR, message=f"HTTP {resp.status_code}"))
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur réseau: {e}")
        issues.append(fetch_issue(source_site=SOURCE_NAME, url=INDEX_URL, reason=REASON_FETCH_ERROR, message=str(e)))

    items = list(model_urls.items())
    if max_models and max_models > 0:
        items = items[:max_models]

    print(f"[{SOURCE_NAME}] Extraction parallèle sur {len(items)} modèles (max_workers=5)...")

    def worker_fetch(it: tuple[str, str]) -> tuple[str, list[dict[str, Any]], str | None]:
        url, m_name = it
        w_session = get_session()
        try:
            sub = scrape_model_page(w_session, url, m_name, scrape_started_at)
            return url, sub, None
        except Exception as exc:
            return url, [], str(exc)

    done_count = 0
    with ThreadPoolExecutor(max_workers=5) as pool:
        future_map = {pool.submit(worker_fetch, it): it for it in items}
        for fut in as_completed(future_map):
            done_count += 1
            u, sub, err = fut.result()
            if err:
                issues.append(fetch_issue(source_site=SOURCE_NAME, url=u, reason=REASON_FETCH_ERROR, message=err))
            else:
                rows.extend(sub)

            if done_count % 15 == 0 or done_count == len(items):
                print(f"[{SOURCE_NAME}] {done_count}/{len(items)} modèles analysés ({len(rows)} pièces cumulées)...")

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "source_url", "product_or_part_name", "price_raw"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}")

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning and rows:
        cleaning_cfg = CleaningConfig(
            price_min=0.5,
            price_max=1500.0,
            dedupe_keys_part_cost=("source_url", "product_or_part_name", "price_raw"),
        )
        rows, clean_report = clean_dataset(
            rows,
            data_type="part_cost",
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

    print(f"[{SOURCE_NAME.upper()}] ✅ Fin d'exécution : {len(rows)} pièces exportées dans {out_dir}", flush=True)

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
    parser.add_argument("--max-models", type=int, default=60)
    args = parser.parse_args()
    scrape(output_dir=args.output_dir, max_models=args.max_models)


if __name__ == "__main__":
    main()
