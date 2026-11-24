"""Phone Clinic Scraper (https://www.phone-clinic.fr).

Atelier de réparation smartphones et multimédia à Voiron (Isère) :
- Univers : repair_price (Grilles tarifaires Apple iPhone)
- URL officielle : https://www.phone-clinic.fr/tarifs
- Données : prestations détaillées (Écran Éco, Écran Perf, Batterie, Charge / Connecteur, Vitre arrière).
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
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
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

SOURCE_NAME = "phone_clinic"
SOURCE_DOMAIN = "phone-clinic.fr"
TARGET_URL = "https://www.phone-clinic.fr/tarifs"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

CANONICAL_REPAIR_MAP = {
    "écran": "Écran",
    "ecran": "Écran",
    "batterie": "Batterie",
    "charge": "Connecteur de charge",
    "connecteur": "Connecteur de charge",
    "vitre": "Façade arrière",
    "arrière": "Façade arrière",
    "arriere": "Façade arrière",
    "ar.": "Façade arrière",
}


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


def map_repair_category(text: str) -> str:
    low = text.lower()
    for kw, cat in CANONICAL_REPAIR_MAP.items():
        if kw in low:
            return cat
    return "Autre réparation"


def parse_tariffs(html: str, scraped_at: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []

    rows = table.find_all("tr")
    if not rows:
        return []

    headers = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
    results: list[dict[str, Any]] = []

    for r in rows[1:]:
        cells = [c.get_text(strip=True) for c in r.find_all(["td", "th"])]
        if not cells or len(cells) <= 1:
            continue
        model_raw = cells[0]
        if not model_raw or "modèle" in model_raw.lower():
            continue

        model = model_raw.strip()
        brand = "Apple"

        for col_idx, col_name in enumerate(headers[1:], start=1):
            if col_idx >= len(cells):
                continue

            val = cells[col_idx]
            if not val or val in ["—", "-", "N/A", ""]:
                continue

            m = re.search(r"(\d+[\.,]?\d*)\s*€", val)
            if not m:
                continue

            try:
                price_val = float(m.group(1).replace(",", "."))
            except ValueError:
                continue

            if price_val <= 0:
                continue

            repair_raw = col_name.strip()
            canonical_cat = map_repair_category(repair_raw)
            item_name = f"{repair_raw} {brand} {model}".strip()

            row = {
                "source_site": SOURCE_DOMAIN,
                "source_url": TARGET_URL,
                "source": TARGET_URL,
                "timestamp": scraped_at,
                "scrape_started_at": scraped_at,
                "data_type": "repair_price",
                "device_type": "smartphone",
                "device_family": "Smartphone",
                "brand": brand,
                "model": model,
                "model_normalized": f"{brand} {model}".strip() if not model.startswith(brand) else model,
                "item_name": item_name,
                "repair_category": canonical_cat,
                "repair_type": canonical_cat,
                "repair_type_raw": repair_raw,
                "price": price_val,
                "price_raw": f"{price_val:.2f} €".replace(".", ","),
                "currency": "EUR",
                "currency_symbol": "€",
                "workshop_name": "Phone Clinic Voiron (Isère)",
                "seller_name": "Phone Clinic",
                "delay": "Réparation immédiate en atelier",
                "warranty": "Garantie pièces et main-d'œuvre comprises",
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
    print(f"[{SOURCE_NAME}] Téléchargement de la grille tarifaire ({TARGET_URL})...")
    try:
        resp = session.get(TARGET_URL, timeout=15)
        if resp.status_code == 200:
            resp.encoding = "utf-8"
            rows = parse_tariffs(resp.text, scrape_started_at)
            print(f"[{SOURCE_NAME}] {len(rows)} prestations extraites.")
        else:
            print(f"[{SOURCE_NAME}] Erreur HTTP {resp.status_code}")
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=TARGET_URL, reason=REASON_FETCH_ERROR, message=f"HTTP {resp.status_code}"))
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur réseau: {e}")
        issues.append(fetch_issue(source_site=SOURCE_NAME, url=TARGET_URL, reason=REASON_FETCH_ERROR, message=str(e)))

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
    args = parser.parse_args()
    scrape(output_dir=args.output_dir)


if __name__ == "__main__":
    main()
