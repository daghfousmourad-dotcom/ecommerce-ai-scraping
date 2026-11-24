"""Scraper pour Infonatic Manosque (infonatic.fr).

Extraction des grilles tarifaires complètes multi-marques :
- Apple (iPhone 16, 15, 14, 13...)
- Samsung (Galaxy S24, S23, Z Flip/Fold, A-series...)
- Google (Pixel 10, 9, 8, 7...)
- Xiaomi (Xiaomi 14, Redmi...)
Prestations : Écrans (Originaux, Reconditionnés, Compatibles), Batteries, Connecteurs, Vitres arrières, Caméras...
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

SOURCE_NAME = "infonatic"
SOURCE_DOMAIN = "infonatic.fr"
INDEX_URL = "https://infonatic.fr/nos-tarifs"

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


def infer_brand_from_url(url: str) -> str:
    if "/apple/" in url:
        return "Apple"
    if "/samsung/" in url:
        return "Samsung"
    if "/google" in url:
        return "Google"
    if "/xiaomi" in url:
        return "Xiaomi"
    return "Autre"


def categorize_repair(text: str) -> tuple[str, str]:
    low = text.lower()
    
    # Piece type
    if "original" in low or "origine" in low or "💯" in text:
        piece = "Originale"
    elif "reconditionné" in low or "premium" in low or "soft oled" in low:
        piece = "Reconditionné"
    elif "compatible" in low or "hard oled" in low or "sans châssis" in low:
        piece = "Compatible"
    else:
        piece = "Standard"

    # Canonical repair category
    if "écran" in low or "ecran" in low or "dalle" in low or "oled" in low:
        cat = "Écran"
    elif "batterie" in low:
        cat = "Batterie"
    elif "connecteur" in low or "charge" in low:
        cat = "Connecteur de charge"
    elif "vitre arrière" in low or "dos" in low or "face arrière" in low:
        cat = "Façade arrière"
    elif "caméra avant" in low or "frontale" in low:
        cat = "Caméra avant"
    elif "caméra arrière" in low or "lentille" in low:
        cat = "Caméra arrière"
    elif "haut-parleur" in low or "écouteur" in low:
        cat = "Haut-parleur"
    elif "désoxydation" in low or "eau" in low:
        cat = "Désoxydation"
    elif "diagnostic" in low:
        cat = "Diagnostic"
    else:
        cat = "Autre réparation"

    return cat, piece


def scrape_single_model(session: requests.Session, url: str, model_name: str, scraped_at: str) -> list[dict[str, Any]]:
    resp = session.get(url, timeout=12)
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return []

    brand = infer_brand_from_url(url)
    results = []

    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue
        for r in rows:
            cells = [c.get_text(strip=True) for c in r.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            rep_raw = cells[0].strip()
            price_raw = cells[1].strip()

            if not rep_raw or "réparation" in rep_raw.lower() or "service" in rep_raw.lower():
                continue

            m = re.search(r"(\d+[\.,]?\d*)\s*€", price_raw)
            if not m:
                continue

            try:
                price_val = float(m.group(1).replace(",", "."))
            except ValueError:
                continue

            if price_val <= 0:
                continue

            cat, piece = categorize_repair(rep_raw)
            item_name = f"{rep_raw} {brand} {model_name}".strip()

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
                "model_normalized": f"{brand} {model_name}".strip() if not model_name.lower().startswith(brand.lower()) else model_name,
                "item_name": item_name,
                "repair_category": cat,
                "repair_type": cat,
                "repair_type_raw": rep_raw,
                "piece_type": piece,
                "price": price_val,
                "price_raw": f"{price_val:.2f} €".replace(".", ","),
                "currency": "EUR",
                "currency_symbol": "€",
                "workshop_name": "Infonatic Manosque (Alpes-de-Haute-Provence)",
                "seller_name": "Infonatic",
                "delay": "Réparation express en atelier à Manosque",
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
    print(f"[{SOURCE_NAME}] Découverte des modèles smartphones sur ({INDEX_URL})...")
    model_dict: dict[str, str] = {}
    try:
        resp = session.get(INDEX_URL, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/nos-tarifs/smartphones/" in href:
                    text = a.get_text(strip=True)
                    if text and href not in model_dict:
                        model_dict[href] = text
            print(f"[{SOURCE_NAME}] {len(model_dict)} modèles trouvés dans le menu tarifaire.")
        else:
            print(f"[{SOURCE_NAME}] Erreur HTTP {resp.status_code} sur l'index")
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=INDEX_URL, reason=REASON_FETCH_ERROR, message=f"HTTP {resp.status_code}"))
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur réseau: {e}")
        issues.append(fetch_issue(source_site=SOURCE_NAME, url=INDEX_URL, reason=REASON_FETCH_ERROR, message=str(e)))

    items = list(model_dict.items())
    if max_models and max_models > 0:
        items = items[:max_models]

    print(f"[{SOURCE_NAME}] Extraction parallèle sur {len(items)} pages modèles (max_workers=5)...")
    
    def worker_fetch(item: tuple[str, str]) -> tuple[str, list[dict[str, Any]], str | None]:
        url, m_name = item
        w_session = get_session()
        try:
            sub_rows = scrape_single_model(w_session, url, m_name, scrape_started_at)
            return url, sub_rows, None
        except Exception as exc:
            return url, [], str(exc)

    done_count = 0
    with ThreadPoolExecutor(max_workers=5) as pool:
        future_map = {pool.submit(worker_fetch, it): it for it in items}
        for fut in as_completed(future_map):
            done_count += 1
            url, sub_rows, err = fut.result()
            if err:
                issues.append(fetch_issue(source_site=SOURCE_NAME, url=url, reason=REASON_FETCH_ERROR, message=err))
            else:
                rows.extend(sub_rows)

            if done_count % 25 == 0 or done_count == len(items):
                print(f"[{SOURCE_NAME}] {done_count}/{len(items)} modèles analysés ({len(rows)} forfaits cumulés)...")

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
