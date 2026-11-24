"""EIS Phones & PC Scraper (https://www.eisphonesandpc.fr).

Atelier de réparation smartphone et informatique en Seine-et-Marne (Sud 77) :
- Secteurs : Moret-sur-Loing, Montereau, Fontainebleau
- Devis immédiat, intervention 30 à 60 minutes

Stratégie d'extraction (Niveau 2 — Extraction de State Déshydraté Directe) :
    1. Téléchargement direct de https://www.eisphonesandpc.fr/tarifs.php
    2. Extraction regex du catalogue complet structuré dans window.EIS_CATALOG :
       - 5 marques majeures (Apple, Samsung, Xiaomi/Redmi/Poco, Google, Huawei)
       - 348 modèles
       - 3 020 tarifs avec type de panne, qualité et délai d'intervention
    3. Normalisation standard 22 colonnes E-commerce AI V2.
    4. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
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

BASE_URL = "https://www.eisphonesandpc.fr"
SOURCE_NAME = "eis_phones_pc"
SOURCE_DOMAIN = "eisphonesandpc.fr"
TARGET_URL = f"{BASE_URL}/tarifs.php"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "EIS Phones & PC (Sud 77 Moret / Fontainebleau)",
    "address": "Atelier Sud 77",
    "city": "Moret-sur-Loing",
    "postal_code": "77250",
    "delay": "30 à 60 minutes en atelier",
    "warranty": "Garantie pièces et main-d'œuvre, certifié réparation",
}

CANONICAL_BRAND_NAMES: dict[str, str] = {
    "apple": "Apple",
    "samsung": "Samsung",
    "xiaomi": "Xiaomi",
    "google": "Google",
    "huawei": "Huawei",
}

CANONICAL_REPAIR_MAP: dict[str, str] = {
    "écran lcd": "Ecran LCD",
    "ecran lcd": "Ecran LCD",
    "écran": "Ecran LCD",
    "ecran": "Ecran LCD",
    "batterie": "Batterie",
    "connecteur de charge": "Connecteur de charge",
    "vitre arrière": "Façade arrière",
    "caméra": "Caméra arrière",
    "appareil photo": "Caméra arrière",
    "lentille": "Caméra arrière",
    "désoxydation": "Autre réparation",
}


def map_repair_type(raw_name: str) -> str:
    norm = raw_name.strip().lower()
    for k, v in CANONICAL_REPAIR_MAP.items():
        if k in norm:
            return v
    canon, matched = normalize_repair_type(raw_name)
    return canon if matched else "Autre réparation"


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

    print(f"\n[{SOURCE_NAME.upper()}] 🌐 Téléchargement de la page de tarifs : {TARGET_URL}", flush=True)

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

    # Extraction regex de window.EIS_CATALOG
    m = re.search(r"window\.EIS_CATALOG\s*=\s*(\{.*?\});\s*(?:window|<)", html, re.DOTALL)
    if not m:
        print(f"❌ [{SOURCE_NAME.upper()}] window.EIS_CATALOG non trouvé dans le code HTML.")
        fetch_errors.append(fetch_issue(TARGET_URL, REASON_FETCH_ERROR, "EIS_CATALOG_NOT_FOUND"))
        return []

    try:
        catalog = json.loads(m.group(1))
    except Exception as exc:
        print(f"❌ [{SOURCE_NAME.upper()}] Erreur parsing JSON: {exc}")
        fetch_errors.append(fetch_issue(TARGET_URL, REASON_FETCH_ERROR, str(exc)))
        return []

    brands = catalog.get("brands", [])
    print(f"[{SOURCE_NAME.upper()}] 🏷️  Marques trouvées : {len(brands)}")

    rows: list[dict[str, Any]] = []
    models_seen: set[str] = set()

    for b in brands:
        b_name_raw = b.get("name") or "Inconnu"
        b_clean = CANONICAL_BRAND_NAMES.get(b.get("id", "").lower(), b_name_raw.split("/")[0].strip())

        models = b.get("models", [])
        for m_info in models:
            m_id = m_info.get("id", "")
            m_name = m_info.get("name", "Modèle")

            if max_models and len(models_seen) >= max_models and m_id not in models_seen:
                continue
            models_seen.add(m_id)

            repairs = m_info.get("repairs", [])
            for rep in repairs:
                price_val = rep.get("price")
                if price_val is None or float(price_val) <= 0:
                    continue

                rep_name_raw = rep.get("name", "Réparation")
                quality = rep.get("quality", "").strip()
                if quality:
                    rep_name_raw = f"{rep_name_raw} ({quality})"

                canonical_repair = map_repair_type(rep_name_raw)
                price_float = float(price_val)
                price_raw_str = f"{int(price_float) if price_float.is_integer() else price_float} €"
                duration = rep.get("duration") or SHOP_INFO["delay"]

                is_tablet = any(k in m_name.lower() for k in ["ipad", "tab", "pad"])
                device_family = "Tablette" if is_tablet else "Smartphone"

                item_name = f"{rep_name_raw} {b_clean} {m_name}".strip()

                row = {
                    "source_site": SOURCE_DOMAIN,
                    "source_url": TARGET_URL,
                    "source": TARGET_URL,
                    "timestamp": scrape_started_at,
                    "data_type": "repair_price",
                    "device_family": device_family,
                    "brand": b_clean,
                    "model": m_name,
                    "item_name": item_name,
                    "repair_type_raw": rep_name_raw,
                    "repair_type": canonical_repair,
                    "price": price_float,
                    "price_raw": price_raw_str,
                    "currency": "EUR",
                    "currency_symbol": "€",
                    "shop_name": SHOP_INFO["shop_name"],
                    "address": SHOP_INFO["address"],
                    "city": SHOP_INFO["city"],
                    "postal_code": SHOP_INFO["postal_code"],
                    "delay": duration,
                    "warranty": SHOP_INFO["warranty"],
                    "model_sku": m_id,
                    "repair_category": "Réparation en atelier",
                    "scraped_at": scrape_started_at,
                }
                rows.append(row)

    print(f"[{SOURCE_NAME.upper()}] 🔄 Données assemblées : {len(rows)} tarifs sur {len(models_seen)} modèles")

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
    parser = argparse.ArgumentParser(description="EIS Phones & PC Scraper (JSON State)")
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
