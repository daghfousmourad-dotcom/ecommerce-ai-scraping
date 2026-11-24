"""La Paraphonie Scraper (https://www.laparaphonie.fr).

Réseau national d'ateliers de réparation smartphones et multimédia au sein des centres E.Leclerc :
- Plus de 25 corners et ateliers en France
- Partenaire de confiance, réparation certifiée

Stratégie d'extraction (Niveau 1 — API REST Backend Directe) :
    1. Interrogation directe de l'API REST de gestion atelier :
       URL : http://intervention-laparaphonie.etiseo.fr/api-v2.php?service=get-price-detail&token=X6GavfciL0Km2cvvdrokS9qx8MwOs3Kn
    2. Récupération directe du JSON consolidé :
       - marques : 21 marques (Apple, Samsung, Xiaomi, Huawei, etc.)
       - categories : 705 modèles
       - products : 4 328 tarifs officiels avec montants TTC
    3. Normalisation standard 22 colonnes E-commerce AI V2.
    4. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
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

BASE_URL = "https://www.laparaphonie.fr"
SOURCE_NAME = "la_paraphonie"
SOURCE_DOMAIN = "laparaphonie.fr"

API_URL = "http://intervention-laparaphonie.etiseo.fr/api-v2.php?service=get-price-detail&token=X6GavfciL0Km2cvvdrokS9qx8MwOs3Kn"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "La Paraphonie E.Leclerc (Réseau National)",
    "address": "Centres E.Leclerc Nationaux",
    "city": "National",
    "postal_code": "75000",
    "delay": "Réparation express en atelier sans rendez-vous",
    "warranty": "Garantie pièces et main-d'œuvre La Paraphonie",
}

CANONICAL_BRAND_NAMES: dict[str, str] = {
    "apple": "Apple",
    "samsung": "Samsung",
    "xiaomi": "Xiaomi",
    "huawei": "Huawei",
    "google": "Google",
    "oppo": "Oppo",
    "oneplus": "OnePlus",
    "honor": "Honor",
    "sony": "Sony",
    "motorola": "Motorola",
    "asus": "Asus",
    "nokia": "Nokia",
    "wiko": "Wiko",
}

CANONICAL_REPAIR_MAP: dict[str, str] = {
    "écran": "Ecran LCD",
    "ecran": "Ecran LCD",
    "batterie": "Batterie",
    "connecteur": "Connecteur de charge",
    "charge": "Connecteur de charge",
    "vitre arrière": "Façade arrière",
    "arriere": "Façade arrière",
    "caméra": "Caméra arrière",
    "camera": "Caméra arrière",
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

    print(f"\n[{SOURCE_NAME.upper()}] 🌐 Appel API Backend La Paraphonie : {API_URL}", flush=True)

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    req = urllib.request.Request(API_URL, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"❌ [{SOURCE_NAME.upper()}] Échec appel API: {exc}")
        fetch_errors.append(fetch_issue(API_URL, REASON_FETCH_ERROR, str(exc)))
        return []

    res_data = data.get("data", {})
    marques_dict = res_data.get("marques", {})
    categories_dict = res_data.get("categories", {})
    products_dict = res_data.get("products", {})

    print(f"[{SOURCE_NAME.upper()}] 🏷️  Marques : {len(marques_dict)}")
    print(f"[{SOURCE_NAME.upper()}] 📱 Catégories / Modèles : {len(categories_dict)}")
    print(f"[{SOURCE_NAME.upper()}] 🛠️  Tarifs prestations : {len(products_dict)}")

    # Map marques
    brands_map: dict[str, str] = {}
    if isinstance(marques_dict, dict):
        for m_id, m_val in marques_dict.items():
            name = m_val.get("product_marque_name") if isinstance(m_val, dict) else str(m_val)
            brands_map[str(m_id)] = name
    elif isinstance(marques_dict, list):
        for m in marques_dict:
            brands_map[str(m.get("product_marque_id"))] = m.get("product_marque_name", "")

    # Map categories (models)
    models_map: dict[str, dict[str, Any]] = {}
    if isinstance(categories_dict, dict):
        for cat_id, cat_val in categories_dict.items():
            if isinstance(cat_val, dict):
                models_map[str(cat_id)] = cat_val
            else:
                models_map[str(cat_id)] = {"product_category_name": str(cat_val), "product_marque_id": ""}
    elif isinstance(categories_dict, list):
        for cat in categories_dict:
            models_map[str(cat.get("product_category_id"))] = cat

    # Iterate products
    rows: list[dict[str, Any]] = []
    models_seen: set[str] = set()

    products_items = products_dict.values() if isinstance(products_dict, dict) else products_dict

    for prod in products_items:
        amount = prod.get("amount")
        if amount is None or float(amount) <= 0:
            continue

        cat_id = str(prod.get("product_category_id", ""))
        model_info = models_map.get(cat_id, {})
        model_name = prod.get("product_category_name") or model_info.get("product_category_name") or "Modèle Inconnu"

        if max_models and len(models_seen) >= max_models and cat_id not in models_seen:
            continue
        models_seen.add(cat_id)

        marque_id = str(prod.get("product_marque_id") or model_info.get("product_marque_id", ""))
        brand_raw = brands_map.get(marque_id, "")
        if not brand_raw:
            # Essayer d'extraire la marque depuis le modèle
            first_word = model_name.split()[0] if model_name else "Inconnu"
            brand_raw = first_word

        brand_clean = CANONICAL_BRAND_NAMES.get(brand_raw.lower(), brand_raw.capitalize())

        repair_raw = prod.get("name") or "Réparation"
        canonical_repair = map_repair_type(repair_raw)
        price_val = float(amount)
        price_raw_str = f"{int(price_val) if price_val.is_integer() else price_val} €"

        is_tablet = any(k in model_name.lower() for k in ["ipad", "tab", "pad"])
        device_family = "Tablette" if is_tablet else "Smartphone"

        item_name = f"{repair_raw} {brand_clean} {model_name}".strip()
        model_url = f"{BASE_URL}/tarif?marque={marque_id}&modele={cat_id}"

        row = {
            "source_site": SOURCE_DOMAIN,
            "source_url": model_url,
            "source": model_url,
            "timestamp": scrape_started_at,
            "data_type": "repair_price",
            "device_family": device_family,
            "brand": brand_clean,
            "model": model_name,
            "item_name": item_name,
            "repair_type_raw": repair_raw,
            "repair_type": canonical_repair,
            "price": price_val,
            "price_raw": price_raw_str,
            "currency": "EUR",
            "currency_symbol": "€",
            "shop_name": SHOP_INFO["shop_name"],
            "address": SHOP_INFO["address"],
            "city": SHOP_INFO["city"],
            "postal_code": SHOP_INFO["postal_code"],
            "delay": SHOP_INFO["delay"],
            "warranty": SHOP_INFO["warranty"],
            "model_sku": cat_id,
            "repair_category": "Réparation en atelier",
            "scraped_at": scrape_started_at,
        }
        rows.append(row)

    print(f"[{SOURCE_NAME.upper()}] 🔄 Données assemblées : {len(rows)} lignes sur {len(models_seen)} modèles")

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
    parser = argparse.ArgumentParser(description="La Paraphonie Scraper (API Backend)")
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
