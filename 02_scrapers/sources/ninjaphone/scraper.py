"""NinjaPhone Scraper (https://ninjaphone.fr).

Atelier de réparation smartphones et multimédia à Annemasse / Genève :
- Univers : repair_price (Grilles tarifaires complètes smartphones Apple, Samsung, Xiaomi, Google Pixel)
- URL source : https://ninjaphone.fr/tarifs/
- Données : prix fixes TTC par modèle et par panne (Écran, Batterie, Connecteur, Vitre arrière, Caméra, Désoxydation, Micro/Haut-parleur)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
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

BASE_URL = "https://ninjaphone.fr"
TARIFS_URL = "https://ninjaphone.fr/tarifs/"
SOURCE_NAME = "ninjaphone"
SOURCE_DOMAIN = "ninjaphone.fr"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

CANONICAL_REPAIR_MAP = {
    "écran": "Écran",
    "batterie": "Batterie",
    "connecteur de charge": "Connecteur de charge",
    "vitre arrière": "Façade arrière",
    "caméra": "Caméra arrière",
    "désoxydation": "Désoxydation",
    "micro / haut-parleur": "Audio / Haut-parleur",
}

TABLE_BRANDS = {
    1: "Apple",
    2: "Apple",
    3: "Samsung",
    4: "Samsung",
    5: "Xiaomi",
    6: "Xiaomi",
    7: "Google",
    8: "Google",
}


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

    print(f"[{SOURCE_NAME}] Téléchargement de la grille tarifaire ({TARIFS_URL})...")
    resp_text = ""
    try:
        resp = requests.get(TARIFS_URL, headers={"User-Agent": USER_AGENT}, timeout=15)
        if resp.status_code == 200:
            resp_text = resp.text
        else:
            issues.append(fetch_issue(source_site=SOURCE_NAME, url=TARIFS_URL, reason=REASON_FETCH_ERROR, message=f"HTTP {resp.status_code}"))
    except Exception as e:
        issues.append(fetch_issue(source_site=SOURCE_NAME, url=TARIFS_URL, reason=REASON_FETCH_ERROR, message=str(e)))

    if not resp_text:
        print(f"[{SOURCE_NAME}] ❌ Impossible de charger {TARIFS_URL}")
        return []

    soup = BeautifulSoup(resp_text, "html.parser")
    tables = soup.select("table")
    print(f"[{SOURCE_NAME}] Trouvé {len(tables)} tables tarifaires.")

    for idx, brand in TABLE_BRANDS.items():
        if idx >= len(tables):
            continue
        table = tables[idx]
        tr_list = table.select("tr")
        if not tr_list:
            continue
        headers = [th.get_text(strip=True) for th in tr_list[0].select("th, td")]

        for tr in tr_list[1:]:
            cells = tr.select("th, td")
            if not cells:
                continue
            model_raw = cells[0].get_text(strip=True)
            if not model_raw or "modèle" in model_raw.lower():
                continue

            for col_idx, cell in enumerate(cells[1:], start=1):
                if col_idx >= len(headers):
                    continue
                repair_type_raw = headers[col_idx]
                price_text = cell.get_text(strip=True).replace(" ", " ").strip()
                m = re.search(r"(\d+[\.,]?\d*)\s*€", price_text)
                if not m:
                    continue
                try:
                    price_val = float(m.group(1).replace(",", "."))
                except ValueError:
                    continue

                if price_val <= 0:
                    continue

                canonical_cat = CANONICAL_REPAIR_MAP.get(repair_type_raw.lower(), repair_type_raw)
                item_name = f"{repair_type_raw} {brand} {model_raw}".strip()

                row = {
                    "source_site": SOURCE_DOMAIN,
                    "source_url": TARIFS_URL,
                    "source": TARIFS_URL,
                    "timestamp": scrape_started_at,
                    "scrape_started_at": scrape_started_at,
                    "data_type": "repair_price",
                    "device_type": "smartphone",
                    "device_family": "Smartphone",
                    "brand": brand,
                    "model": model_raw,
                    "model_normalized": f"{brand} {model_raw}",
                    "item_name": item_name,
                    "repair_category": canonical_cat,
                    "repair_type": canonical_cat,
                    "repair_type_raw": repair_type_raw,
                    "price": price_val,
                    "price_raw": f"{price_val:.2f} €".replace(".", ","),
                    "currency": "EUR",
                    "currency_symbol": "€",
                    "shop_name": "NinjaPhone Annemasse",
                    "seller_name": "NinjaPhone",
                    "workshop_name": "NinjaPhone Annemasse",
                    "delay": "30 à 60 minutes en atelier",
                    "warranty": "Garantie pièces et main-d'œuvre",
                    "scraped_at": scrape_started_at,
                }
                rows.append(row)

    if max_models:
        rows = rows[:max_models]

    initial_count = len(rows)
    rows = dedupe_dict_rows(rows, keys=("source_site", "brand", "model", "repair_type_raw", "price"))
    print(f"[{SOURCE_NAME}] Total lignes brutes: {initial_count}, après déduplication: {len(rows)}")

    clean_report = None
    fetch_errors = len(issues)
    if apply_cleaning and rows:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=2500.0,
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
        import shutil
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
    parser = argparse.ArgumentParser(description=f"{SOURCE_NAME} Scraper")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport")
    parser.add_argument("--max-models", type=int, default=None, help="Max modèles")
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
    )


if __name__ == "__main__":
    main()
