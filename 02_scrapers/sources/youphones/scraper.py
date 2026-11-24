"""Scraper pour YouPhones Poitiers (youphones.fr).

Extraction des grilles tarifaires de réparation pour smartphones Apple iPhone :
écrans (Originaux, Standards), batteries, vitres arrière, connecteurs, caméras, haut-parleurs...
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

SOURCE_NAME = "youphones"
SOURCE_DOMAIN = "youphones.fr"
INDEX_URL = "https://www.youphones.fr/tarifs/"

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


def categorize_repair(title: str) -> tuple[str, str]:
    low = title.lower()
    if "écran" in low or "ecran" in low:
        cat = "Écran"
        if "original" in low:
            piece = "Originale"
        elif "standard" in low or "économique" in low:
            piece = "Compatible"
        else:
            piece = "Reconditionné"
    elif "batterie" in low:
        cat = "Batterie"
        piece = "Premium"
    elif "vitre arrière" in low or "dos" in low:
        cat = "Façade arrière"
        piece = "Compatible"
    elif "connecteur" in low or "charge" in low:
        cat = "Connecteur de charge"
        piece = "Compatible"
    elif "caméra avant" in low or "caméra frontale" in low:
        cat = "Caméra avant"
        piece = "Originale"
    elif "caméra arrière" in low or "lentille" in low:
        cat = "Caméra arrière"
        piece = "Originale"
    elif "haut-parleur" in low or "écouteur" in low:
        cat = "Haut-parleur"
        piece = "Standard"
    else:
        cat = "Autre réparation"
        piece = "Standard"
    return cat, piece


def scrape_model_page(session: requests.Session, url: str, scraped_at: str) -> list[dict[str, Any]]:
    resp = session.get(url, timeout=12)
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    h1 = soup.find("h1")
    model_name = h1.get_text(strip=True) if h1 else ""
    if not model_name:
        # Extract from URL
        slug = url.rstrip("/").split("/")[-1]
        model_name = slug.replace("-", " ").title()

    brand = "Apple"
    results = []

    cards = soup.select("article.repair-card")
    for c in cards:
        h3 = c.find("h3")
        if not h3:
            continue
        title = h3.get_text(strip=True)
        if not title:
            continue

        price_el = c.select_one(".price")
        if not price_el:
            continue
        price_text = price_el.get_text(strip=True)

        m = re.search(r"(\d+[\.,]?\d*)\s*€", price_text)
        if not m:
            continue

        try:
            price_val = float(m.group(1).replace(",", "."))
        except ValueError:
            continue

        if price_val <= 0:
            continue

        desc_el = c.find("p", class_=lambda cl: cl != "price")
        desc = desc_el.get_text(strip=True) if desc_el else ""

        canonical_cat, piece_type = categorize_repair(title)
        item_name = f"{title} {brand} {model_name}".strip()

        row = {
            "source_site": SOURCE_DOMAIN,
            "source_url": url,
            "source": url,
            "timestamp": scraped_at,
            "scrape_started_at": scraped_at,
            "data_type": "repair_price",
            "device_type": "smartphone",
            "device_family": "Smartphone",
            "brand": brand,
            "model": model_name,
            "model_normalized": f"{brand} {model_name}".strip() if not model_name.startswith(brand) else model_name,
            "item_name": item_name,
            "repair_category": canonical_cat,
            "repair_type": canonical_cat,
            "repair_type_raw": title,
            "piece_type": piece_type,
            "price": price_val,
            "price_raw": f"{price_val:.2f} €".replace(".", ","),
            "currency": "EUR",
            "currency_symbol": "€",
            "workshop_name": "YouPhones Poitiers",
            "seller_name": "YouPhones",
            "delay": "Réparation rapide en atelier",
            "warranty": "Garantie pièces et main-d'œuvre",
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
    prefix = f"{SOURCE_NAME}_repair_prices_{timestamp_clean}"
    jsonl_path = out_dir / f"{prefix}.jsonl"
    csv_path = out_dir / f"{prefix}.csv"
    json_path = out_dir / f"{prefix}.json"

    convenience_jsonl = out_dir / f"db_{SOURCE_NAME}.jsonl"
    convenience_csv = out_dir / f"db_{SOURCE_NAME}.csv"
    convenience_json = out_dir / f"db_{SOURCE_NAME}.json"

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    session = get_session()
    print(f"[{SOURCE_NAME}] Découverte des modèles sur ({INDEX_URL})...")
    model_urls = []
    try:
        resp = session.get(INDEX_URL, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/modele/" in href and href not in model_urls:
                    model_urls.append(href)
            print(f"[{SOURCE_NAME}] {len(model_urls)} modèles trouvés.")
        else:
            print(f"[{SOURCE_NAME}] Erreur HTTP {resp.status_code} sur l'index")
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=INDEX_URL, reason=REASON_FETCH_ERROR, message=f"HTTP {resp.status_code}"))
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur réseau: {e}")
        issues.append(fetch_issue(source_site=SOURCE_NAME, url=INDEX_URL, reason=REASON_FETCH_ERROR, message=str(e)))

    if max_models and max_models > 0:
        model_urls = model_urls[:max_models]

    for idx, m_url in enumerate(model_urls, start=1):
        try:
            sub_rows = scrape_model_page(session, m_url, scrape_started_at)
            rows.extend(sub_rows)
            if idx % 10 == 0 or idx == len(model_urls):
                print(f"[{SOURCE_NAME}] {idx}/{len(model_urls)} modèles explorés ({len(rows)} forfaits cumulés)...")
            time.sleep(0.05)
        except Exception as e:
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=m_url, reason=REASON_FETCH_ERROR, message=str(e)))

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "repair_type_raw", "price"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}")

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning and rows:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=3000.0,
            dedupe_keys_repair_price=("source_url", "model", "repair_type_raw", "price_raw"),
        )
        rows, clean_report = clean_dataset(
            rows,
            data_type="repair_price",
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

    print(f"[{SOURCE_NAME.upper()}] ✅ Fin d'exécution : {len(rows)} forfaits exportés dans {out_dir}", flush=True)

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
    parser.add_argument("--max-models", type=int, default=None)
    args = parser.parse_args()
    scrape(output_dir=args.output_dir, max_models=args.max_models)


if __name__ == "__main__":
    main()
