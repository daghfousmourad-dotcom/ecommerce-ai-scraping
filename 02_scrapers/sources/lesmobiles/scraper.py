"""LesMobiles.com Scraper (https://www.lesmobiles.com).

Comparateur et observatoire de référence des prix smartphones neufs et reconditionnés en France :
- Univers : product_sale (Prix de marché observés Neuf et Reconditionné par modèle)
- URL officielle : https://www.lesmobiles.com/telephones/
- Données : Marque, modèle, prix de référence neuf, prix de référence reconditionné, URL fiche.
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

SOURCE_NAME = "lesmobiles"
SOURCE_DOMAIN = "lesmobiles.com"
BASE_URL = "https://www.lesmobiles.com"
TARGET_URL = "https://www.lesmobiles.com/telephones/"

HUB_URLS = [
    "https://www.lesmobiles.com/telephones/",
    "https://www.lesmobiles.com/telephones/mobiles_apple.html",
    "https://www.lesmobiles.com/telephones/mobiles_samsung.html",
    "https://www.lesmobiles.com/telephones/mobiles_xiaomi.html",
    "https://www.lesmobiles.com/telephones/mobiles_google.html",
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def extract_price_value(element) -> float | None:
    if not element:
        return None
    m = re.search(r"(\d+[\d\s]*[\.,]?\d*)\s*€", element.get_text())
    if m:
        try:
            return float(m.group(1).replace(" ", "").replace(" ", "").replace(",", "."))
        except ValueError:
            pass
    return None


def parse_page_cards(html: str, scraped_at: str, seen: set) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not (href.startswith("/telephones/") and href.endswith(".html") and "mobiles_" not in href):
            continue

        p_texts = [p.get_text(strip=True) for p in a.find_all("p")]
        if len(p_texts) < 2:
            continue

        brand = p_texts[0].strip()
        model = p_texts[1].strip()
        if not brand or not model:
            continue

        full_url = f"{BASE_URL}{href}"

        p_neuf = a.find("p", class_="jsPrixNeuf")
        p_recond = a.find("p", class_="jsPrixReconditionne")
        p_std = a.find("p", class_="jsPrixStandard")

        pn = extract_price_value(p_neuf)
        pr = extract_price_value(p_recond)
        ps = extract_price_value(p_std)

        def make_row(cond: str, price_val: float) -> dict[str, Any]:
            full_title = f"{brand} {model}".strip()
            item_title = f"{full_title} ({cond})"
            return {
                "source_site": SOURCE_DOMAIN,
                "source_url": full_url,
                "source": full_url,
                "timestamp": scraped_at,
                "scrape_started_at": scraped_at,
                "data_type": "product_sale",
                "device_type": "smartphone",
                "device_family": "Smartphone",
                "brand": brand,
                "model": model,
                "model_normalized": full_title,
                "item_title": item_title,
                "item_name": item_title,
                "condition": cond,
                "price": price_val,
                "price_raw": f"{price_val:.2f} €".replace(".", ","),
                "currency": "EUR",
                "currency_symbol": "€",
                "seller_name": "LesMobiles (Comparateur Marché)",
                "warranty": "Garantie selon marchand partenaire (12-24 mois)",
                "scraped_at": scraped_at,
            }

        if pn and (full_url, "Neuf", pn) not in seen:
            seen.add((full_url, "Neuf", pn))
            results.append(make_row("Neuf", pn))

        if pr and (full_url, "Reconditionné", pr) not in seen:
            seen.add((full_url, "Reconditionné", pr))
            results.append(make_row("Reconditionné", pr))

        if not pn and not pr and ps and (full_url, "Marché standard", ps) not in seen:
            seen.add((full_url, "Marché standard", ps))
            results.append(make_row("Neuf ou reconditionné", ps))

    return results


def scrape(
    output_dir: str | Path | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
) -> list[dict[str, Any]]:
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir) if output_dir else (_SCRAPERS_ROOT / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp_clean = scrape_started_at.replace(":", "").replace("-", "").replace("+", "")
    prefix = f"{SOURCE_NAME}_market_prices_{timestamp_clean}"
    jsonl_path = out_dir / f"{prefix}.jsonl"
    csv_path = out_dir / f"{prefix}.csv"
    json_path = out_dir / f"{prefix}.json"

    convenience_jsonl = out_dir / f"db_{SOURCE_NAME}.jsonl"
    convenience_csv = out_dir / f"db_{SOURCE_NAME}.csv"
    convenience_json = out_dir / f"db_{SOURCE_NAME}.json"

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    seen: set = set()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"})

    print(f"[{SOURCE_NAME}] Téléchargement des pages comparateur ({len(HUB_URLS)} hubs)...", flush=True)
    t0 = time.time()

    for url in HUB_URLS:
        try:
            resp = session.get(url, timeout=15)
            if resp.status_code == 200:
                page_rows = parse_page_cards(resp.text, scrape_started_at, seen)
                rows.extend(page_rows)
                print(f"[{SOURCE_NAME}] {url} -> {len(page_rows)} offres extraites (Total: {len(rows)})", flush=True)
            else:
                issues.append(
                    fetch_issue(
                        source_site=SOURCE_NAME,
                        url=url,
                        reason=REASON_FETCH_ERROR,
                        message=f"HTTP {resp.status_code}",
                    )
                )
        except Exception as e:
            issues.append(
                fetch_issue(
                    source_site=SOURCE_NAME,
                    url=url,
                    reason=REASON_FETCH_ERROR,
                    message=str(e),
                )
            )

    print(f"[{SOURCE_NAME}] {len(rows)} offres brutes collectées en {time.time()-t0:.1f}s.", flush=True)

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_url", "model", "condition", "price"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}", flush=True)

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning and rows:
        cleaning_cfg = CleaningConfig(
            price_min=15.0,
            price_max=4000.0,
            dedupe_keys_product_sale=("source_url", "model", "condition", "price_raw"),
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

    print(f"[{SOURCE_NAME.upper()}] ✅ Fin d'exécution : {len(rows)} offres de marché exportées dans {out_dir}", flush=True)

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
