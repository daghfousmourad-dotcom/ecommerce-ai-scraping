"""Capitaine Repair Scraper (https://capitainerepair.com).

Service national de réparation smartphone et informatique par expédition et atelier :
- Diagnostic en ligne et grilles tarifaires complètes
- Prise en charge Apple, Samsung Galaxy, Oppo, Huawei

Stratégie d'extraction (Niveau 2 — Extraction HTML Statique Directe) :
    1. Téléchargement direct de https://capitainerepair.com/pages/tarifs
    2. Parsing des 6 grandes tables tarifaires par marque et gamme :
       - iPhone (iPhone 6 jusqu'à iPhone 17 Pro Max)
       - Samsung Galaxy A, Galaxy S, Galaxy Note
       - Oppo gamme A
       - Huawei gamme P
    3. Extraction des 4 pannes canoniques : Écran/LCD, Batterie, Connecteur de charge, Vitre arrière
    4. Normalisation standard 22 colonnes E-commerce AI V2.
    5. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.request
from pathlib import Path
from typing import Any
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
    from scraper.core.normalization import normalize_repair_type
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
    from core.normalization import normalize_repair_type
    from core.parser_utils import dedupe_dict_rows, now_iso_utc
    from core.row_export import write_jsonl_and_csv
    from core.run_report import (
        REASON_FETCH_ERROR,
        fetch_issue,
        finalize_run_artifacts,
        summarize_run_line,
    )

BASE_URL = "https://capitainerepair.com"
SOURCE_NAME = "capitaine_repair"
SOURCE_DOMAIN = "capitainerepair.com"
TARGET_URL = f"{BASE_URL}/pages/tarifs"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "Capitaine Repair (Service National)",
    "address": "Atelier National",
    "city": "National",
    "postal_code": "75000",
    "delay": "Intervention express en atelier ou expédition sécurisée",
    "warranty": "Garantie pièces et main-d'œuvre Capitaine Repair",
}

CANONICAL_REPAIR_MAP: dict[str, str] = {
    "ecran": "Ecran LCD",
    "lcd": "Ecran LCD",
    "batterie": "Batterie",
    "connecteur": "Connecteur de charge",
    "vitre arriere": "Façade arrière",
}


def parse_price(val_str: str) -> float | None:
    m = re.search(r"(\d+[\.,]?\d*)\s*€?", val_str.replace(" ", "").replace("\xa0", ""))
    if m:
        try:
            return float(m.group(1).replace(",", "."))
        except ValueError:
            return None
    return None


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

    fetch_errors: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    print(f"\n[{SOURCE_NAME.upper()}] 🌐 Téléchargement des grilles tarifaires : {TARGET_URL}", flush=True)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(TARGET_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        print(f"❌ [{SOURCE_NAME.upper()}] Erreur fetch {TARGET_URL}: {exc}")
        fetch_errors.append(fetch_issue(TARGET_URL, REASON_FETCH_ERROR, str(exc)))
        return []

    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    print(f"[{SOURCE_NAME.upper()}] 📊 Tables trouvées : {len(tables)}")

    rows: list[dict[str, Any]] = []
    models_seen: set[str] = set()

    for idx, table in enumerate(tables):
        trs = table.find_all("tr")
        if not trs or len(trs) < 2:
            continue

        header_tds = [td.get_text(strip=True).upper() for td in trs[0].find_all(["th", "td"])]
        if not header_tds:
            continue

        first_header = header_tds[0]
        # Déterminer la marque et la gamme
        if "IPHONE" in first_header:
            brand = "Apple"
            model_prefix = "iPhone "
        elif "OPPO" in first_header:
            brand = "Oppo"
            model_prefix = "Oppo "
        elif "SAMSUNG A" in first_header:
            brand = "Samsung"
            model_prefix = "Galaxy "
        elif "SAMSUNG S" in first_header:
            brand = "Samsung"
            model_prefix = "Galaxy "
        elif "SAMSUNG NOTE" in first_header:
            brand = "Samsung"
            model_prefix = "Galaxy Note "
        elif "HUAWEI" in first_header:
            brand = "Huawei"
            model_prefix = "Huawei "
        else:
            continue

        columns = header_tds[1:]

        for tr in trs[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all(["th", "td"])]
            if len(cells) < 2:
                continue

            raw_model = cells[0].strip()
            if not raw_model or any(k in raw_model.upper() for k in ["TOTAL", "PRIX", "DEVIS"]):
                continue

            if brand == "Apple":
                full_model = raw_model if raw_model.lower().startswith("iphone") else f"iPhone {raw_model}"
            elif brand == "Samsung":
                full_model = raw_model if raw_model.lower().startswith("galaxy") else f"Galaxy {raw_model}"
            elif brand == "Oppo":
                full_model = raw_model if raw_model.lower().startswith("oppo") else f"Oppo {raw_model}"
            elif brand == "Huawei":
                full_model = raw_model if raw_model.lower().startswith("huawei") else f"Huawei {raw_model}"
            else:
                full_model = f"{brand} {raw_model}"

            if max_models and len(models_seen) >= max_models and full_model not in models_seen:
                continue
            models_seen.add(full_model)

            for col_idx, col_name in enumerate(columns):
                val_idx = col_idx + 1
                if val_idx >= len(cells):
                    break

                val_str = cells[val_idx].strip()
                price = parse_price(val_str)
                if price is None or price <= 0:
                    continue

                col_clean = col_name.lower().replace("é", "e").replace("è", "e")
                canon_rep = "Autre réparation"
                rep_type_raw = col_name
                for k, v in CANONICAL_REPAIR_MAP.items():
                    if k in col_clean:
                        canon_rep = v
                        if v == "Ecran LCD":
                            rep_type_raw = "Remplacement Écran"
                        elif v == "Batterie":
                            rep_type_raw = "Remplacement Batterie"
                        elif v == "Connecteur de charge":
                            rep_type_raw = "Connecteur de charge"
                        elif v == "Façade arrière":
                            rep_type_raw = "Vitre arrière"
                        break

                price_raw_str = f"{int(price) if price.is_integer() else price} €"
                item_name = f"{rep_type_raw} {brand} {full_model}".strip()

                row = {
                    "source_site": SOURCE_DOMAIN,
                    "source_url": TARGET_URL,
                    "source": TARGET_URL,
                    "timestamp": scrape_started_at,
                    "data_type": "repair_price",
                    "device_family": "Smartphone",
                    "brand": brand,
                    "model": full_model,
                    "item_name": item_name,
                    "repair_type_raw": rep_type_raw,
                    "repair_type": canon_rep,
                    "price": price,
                    "price_raw": price_raw_str,
                    "currency": "EUR",
                    "currency_symbol": "€",
                    "shop_name": SHOP_INFO["shop_name"],
                    "address": SHOP_INFO["address"],
                    "city": SHOP_INFO["city"],
                    "postal_code": SHOP_INFO["postal_code"],
                    "delay": SHOP_INFO["delay"],
                    "warranty": SHOP_INFO["warranty"],
                    "model_sku": full_model.lower().replace(" ", "_"),
                    "repair_category": "Réparation en atelier",
                    "scraped_at": scrape_started_at,
                }
                rows.append(row)

    print(f"[{SOURCE_NAME.upper()}] 🔄 Données extraites : {len(rows)} tarifs sur {len(models_seen)} modèles")

    # Déduplication
    deduped_rows = dedupe_dict_rows(rows, keys=["model", "repair_type_raw", "price", "shop_name"])

    # Nettoyage
    clean_report = None
    if apply_cleaning:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=4000.0,
            dedupe_keys_repair_price=("source_url", "model", "repair_type_raw", "price_raw"),
        )
        deduped_rows, clean_report = clean_dataset(
            deduped_rows,
            data_type="repair_price",
            config=cleaning_cfg,
            cleaning_issues=issues if emit_run_report else None,
        )
        if clean_report:
            print(clean_report.summary_line(), flush=True)

    for row in deduped_rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    # Export des 5 artefacts
    write_jsonl_and_csv(deduped_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(deduped_rows),
        "rows": deduped_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[{SOURCE_NAME.upper()}] ✅ Succès : {len(deduped_rows)} tarifs exportés dans {out_dir}", flush=True)

    if emit_run_report:
        payload_report = finalize_run_artifacts(
            source_site=SOURCE_NAME,
            scrape_started_at=scrape_started_at,
            json_path=json_path,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(deduped_rows),
            apply_cleaning=apply_cleaning,
            clean_report=clean_report,
            fetch_errors=fetch_errors,
            issues=issues,
        )
        print(summarize_run_line(payload_report), flush=True)

    return deduped_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Capitaine Repair Scraper (HTML Tables)")
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
