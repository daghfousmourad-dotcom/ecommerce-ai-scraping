"""Scraper pour iFixit France (ifixit.com/fr-fr/Store).

Boutique officielle de pièces détachées et kits de réparation certifiés (écrans, batteries, connecteurs...).
Univers : part_cost (Apple iPhone, iPad, Google Pixel, Samsung Galaxy...).
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

SOURCE_NAME = "ifixit"
SOURCE_DOMAIN = "ifixit.com"
TARGET_URL = "https://www.ifixit.com/Parts/iPhone"

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
                "Accept-Language": "fr-FR,fr;q=0.9",
    })
    return s


def categorize_part(name: str) -> tuple[str, str]:
    low = name.lower()
    if "origine" in low or "original" in low or "constructeur" in low:
        piece = "Originale"
    elif "fix kit" in low or "kit" in low or "premium" in low:
        piece = "Premium"
    else:
        piece = "Standard"

    if "écran" in low or "ecran" in low or "display" in low or "dalle" in low:
        cat = "Écran"
    elif "batterie" in low or "battery" in low:
        cat = "Batterie"
    elif "connecteur" in low or "charge" in low or "lightning" in low or "usb-c" in low:
        cat = "Connecteur de charge"
    elif "vitre arrière" in low or "dos" in low or "arriere" in low:
        cat = "Façade arrière"
    elif "caméra avant" in low or "frontale" in low:
        cat = "Caméra avant"
    elif "caméra" in low or "lentille" in low:
        cat = "Caméra arrière"
    elif "haut-parleur" in low or "écouteur" in low:
        cat = "Haut-parleur"
    elif "nappe" in low or "adhésif" in low:
        cat = "Composant interne"
    else:
        cat = "Autre pièce détachée"

    return cat, piece


def scrape_model_page(session: requests.Session, model_url: str, scraped_at: str) -> list[dict[str, Any]]:
    resp = session.get(model_url, timeout=12)
    if resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    slug = model_url.rstrip("/").split("/")[-1]
    model_name = slug.replace("iPhone_", "iPhone ").replace("_", " ")
    brand = "Apple"
    results = []

    articles = soup.find_all("article")
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
        part_url = urljoin("https://www.ifixit.com", a["href"]) if a else model_url
        
        # Name
        h_el = art.find(["h2", "h3", "h4", "span"])
        name = h_el.get_text(strip=True) if h_el else ""
        if not name or len(name) < 4:
            # Fallback to text before price
            name = text.split("|")[0].strip()

        cat, piece = categorize_part(name)

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
            "model_normalized": f"{brand} {model_name}".strip() if not model_name.startswith(brand) else model_name,
            "product_or_part_name": name,
            "item_name": name,
            "repair_type_raw": name,
            "repair_type": cat,
            "repair_category": cat,
            "piece_type": piece,
            "price": price_val,
            "price_raw": f"{price_val:.2f} €".replace(".", ","),
            "currency": "EUR",
            "currency_symbol": "€",
            "seller_name": "iFixit France",
            "warranty": "Garantie à vie iFixit",
            "scraped_at": scraped_at,
        }
        results.append(row)

    return results


def scrape(
    output_dir: str | Path | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = 40,
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
    print(f"[{SOURCE_NAME}] Découverte des modèles iPhone ({TARGET_URL})...")
    model_links = []
    try:
        resp = session.get(TARGET_URL, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/Parts/iPhone_" in href and "?" not in href:
                    if not href.startswith("/fr-fr/"):
                        href = "/fr-fr" + href
                    full = urljoin("https://www.ifixit.com", href)
                    if full not in model_links:
                        model_links.append(full)
            print(f"[{SOURCE_NAME}] {len(model_links)} pages modèles trouvées.")
        else:
            print(f"[{SOURCE_NAME}] Erreur HTTP {resp.status_code}")
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=TARGET_URL, reason=REASON_FETCH_ERROR, message=f"HTTP {resp.status_code}"))
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur réseau: {e}")
        issues.append(fetch_issue(source_site=SOURCE_NAME, url=TARGET_URL, reason=REASON_FETCH_ERROR, message=str(e)))

    if max_models and max_models > 0:
        model_links = model_links[:max_models]

    print(f"[{SOURCE_NAME}] Extraction parallèle sur {len(model_links)} modèles (max_workers=5)...")

    def worker_fetch(u: str) -> tuple[str, list[dict[str, Any]], str | None]:
        w_session = get_session()
        try:
            sub = scrape_model_page(w_session, u, scrape_started_at)
            return u, sub, None
        except Exception as exc:
            return u, [], str(exc)

    done_count = 0
    with ThreadPoolExecutor(max_workers=5) as pool:
        future_map = {pool.submit(worker_fetch, u): u for u in model_links}
        for fut in as_completed(future_map):
            done_count += 1
            u, sub, err = fut.result()
            if err:
                issues.append(fetch_issue(source_site=SOURCE_NAME, url=u, reason=REASON_FETCH_ERROR, message=err))
            else:
                rows.extend(sub)

            if done_count % 10 == 0 or done_count == len(model_links):
                print(f"[{SOURCE_NAME}] {done_count}/{len(model_links)} modèles analysés ({len(rows)} pièces cumulées)...")

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
    parser.add_argument("--max-models", type=int, default=40)
    args = parser.parse_args()
    scrape(output_dir=args.output_dir, max_models=args.max_models)


if __name__ == "__main__":
    main()
