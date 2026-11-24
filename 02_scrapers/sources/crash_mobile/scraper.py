"""Crash Mobile Scraper (https://www.crashmobile.fr).

Atelier de réparation smartphone et multimédia en Île-de-France (Essonne) :
- Adresse : 30 Avenue de Provence, 91170 Viry-Châtillon (01 88 93 69 42)
- Réparation express en 30-45 minutes sans rendez-vous

Stratégie d'extraction (Niveau 1 — API REST Supabase PostgREST directe) :
    1. Interrogation directe de l'API Supabase officielle :
       URL : https://veapeuoigmrxkfxkbbfm.supabase.co/rest/v1/
    2. Récupération des tables relationnelles normalisées :
       - /brands : marques (Apple, Samsung, Xiaomi, Huawei, Google, etc.)
       - /models : 770 modèles
       - /repair_types : types de pannes (Écran, Batterie, Connecteur, Vitre arrière...)
       - /repair_prices : 3 800+ tarifs au centime d'euro avec durées en minutes
    3. Normalisation standard 22 colonnes E-commerce AI V2.
    4. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.parse
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

BASE_URL = "https://www.crashmobile.fr"
SOURCE_NAME = "crash_mobile"
SOURCE_DOMAIN = "crashmobile.fr"

SUPABASE_URL = "https://veapeuoigmrxkfxkbbfm.supabase.co/rest/v1"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZlYXBldW9pZ21yeGtmeGtiYmZtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzQwMTM1OTIsImV4cCI6MjA0OTU4OTU5Mn0."
    "QnVCSRjCtRD4cqCQE59y2VF5IlaYXMP0uFEZuhDky3I"
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "Crash Mobile Viry-Châtillon & Essonne",
    "address": "30 Avenue de Provence",
    "city": "Viry-Châtillon",
    "postal_code": "91170",
    "delay": "Réparation express 30 à 45 minutes en atelier sans rendez-vous",
    "warranty": "Garantie pièces et main-d'œuvre atelier",
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
    "nintendo": "Nintendo",
    "microsoft": "Microsoft",
}

CANONICAL_REPAIR_MAP: dict[str, str] = {
    "écran cassé": "Ecran LCD",
    "batterie": "Batterie",
    "connecteur de charge": "Connecteur de charge",
    "vitre arrière": "Façade arrière",
    "caméra": "Caméra arrière",
    "appareil photo": "Caméra arrière",
}


def map_repair_type(raw_name: str) -> str:
    norm = raw_name.strip().lower()
    if norm in CANONICAL_REPAIR_MAP:
        return CANONICAL_REPAIR_MAP[norm]
    for k, v in CANONICAL_REPAIR_MAP.items():
        if k in norm:
            return v
    canon, matched = normalize_repair_type(raw_name)
    return canon if matched else "Autre réparation"


def fetch_supabase_table(
    table: str,
    select: str = "*",
    extra_params: str = "",
    batch_size: int = 1000,
) -> list[dict[str, Any]]:
    headers = {
        "apikey": SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    all_rows: list[dict[str, Any]] = []
    offset = 0

    while True:
        query_parts = [f"select={select}", f"limit={batch_size}", f"offset={offset}"]
        if extra_params:
            query_parts.append(extra_params)
        url = f"{SUPABASE_URL}/{table}?{'&'.join(query_parts)}"

        req = urllib.request.Request(url, headers=headers)
        success = False
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
                    batch = json.loads(resp.read().decode("utf-8"))
                    all_rows.extend(batch)
                    if len(batch) < batch_size:
                        return all_rows
                    offset += batch_size
                    success = True
                    break
            except Exception as exc:
                if attempt == 2:
                    print(f"⚠️ [CRASH MOBILE] Erreur sur {url}: {exc}")
                    return all_rows
                time.sleep(1.0)
        if not success:
            break

    return all_rows


def create_repair_row(
    brand: str,
    model: str,
    model_slug: str,
    brand_slug: str,
    device_family: str,
    repair_type_raw: str,
    canonical_repair: str,
    price: float,
    duration_minutes: int | None,
    scraped_at: str,
) -> dict[str, Any]:
    price_raw_str = f"{int(price) if price.is_integer() else price} €"
    item_name = f"{repair_type_raw} {brand} {model}".strip()
    model_url = f"{BASE_URL}/reparer/smartphones/{brand_slug}/{model_slug}" if (brand_slug and model_slug) else f"{BASE_URL}/reparer"

    delay_str = f"Réparation en {duration_minutes} minutes" if duration_minutes else SHOP_INFO["delay"]

    return {
        "source_site": SOURCE_DOMAIN,
        "source_url": model_url,
        "source": model_url,
        "timestamp": scraped_at,
        "data_type": "repair_price",
        "device_family": device_family,
        "brand": brand,
        "model": model,
        "item_name": item_name,
        "repair_type_raw": repair_type_raw,
        "repair_type": canonical_repair,
        "price": price,
        "price_raw": price_raw_str,
        "currency": "EUR",
        "currency_symbol": "€",
        "shop_name": SHOP_INFO["shop_name"],
        "address": SHOP_INFO["address"],
        "city": SHOP_INFO["city"],
        "postal_code": SHOP_INFO["postal_code"],
        "delay": delay_str,
        "warranty": SHOP_INFO["warranty"],
        "model_sku": model_slug,
        "repair_category": "Réparation en atelier",
        "scraped_at": scraped_at,
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

    fetch_errors: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    print(f"\n[{SOURCE_NAME.upper()}] 🌐 Interrogation API Supabase PostgREST : {SUPABASE_URL}", flush=True)

    # 1. Brands
    try:
        raw_brands = fetch_supabase_table("brands", select="id,name,slug")
        brands_map = {b["id"]: b for b in raw_brands}
        print(f"[{SOURCE_NAME.upper()}] 🏷️  Marques : {len(brands_map)}")
    except Exception as exc:
        fetch_errors.append(fetch_issue(f"{SUPABASE_URL}/brands", REASON_FETCH_ERROR, str(exc)))
        brands_map = {}

    # 2. Repair types
    try:
        raw_types = fetch_supabase_table("repair_types", select="id,name,description")
        repairs_map = {r["id"]: r for r in raw_types}
        print(f"[{SOURCE_NAME.upper()}] 🛠️  Types de pannes : {len(repairs_map)}")
    except Exception as exc:
        fetch_errors.append(fetch_issue(f"{SUPABASE_URL}/repair_types", REASON_FETCH_ERROR, str(exc)))
        repairs_map = {}

    # 3. Models
    try:
        raw_models = fetch_supabase_table("models", select="id,brand_id,name,slug,device_type")
        models_map = {m["id"]: m for m in raw_models}
        print(f"[{SOURCE_NAME.upper()}] 📱 Modèles : {len(models_map)}")
    except Exception as exc:
        fetch_errors.append(fetch_issue(f"{SUPABASE_URL}/models", REASON_FETCH_ERROR, str(exc)))
        models_map = {}

    # 4. Repair prices
    try:
        raw_prices = fetch_supabase_table("repair_prices", select="id,model_id,repair_type_id,price,duration_minutes", extra_params="price=gt.0")
        print(f"[{SOURCE_NAME.upper()}] 💰 Tarifs bruts : {len(raw_prices)}")
    except Exception as exc:
        fetch_errors.append(fetch_issue(f"{SUPABASE_URL}/repair_prices", REASON_FETCH_ERROR, str(exc)))
        raw_prices = []

    if not raw_prices:
        print(f"[{SOURCE_NAME.upper()}] ❌ Aucun tarif récupéré.")
        return []

    rows: list[dict[str, Any]] = []
    models_seen: set[str] = set()

    for p in raw_prices:
        model_id = p.get("model_id")
        repair_id = p.get("repair_type_id")
        price_val = p.get("price")
        if not model_id or not repair_id or price_val is None:
            continue

        model_info = models_map.get(model_id)
        if not model_info:
            continue

        dev_type = (model_info.get("device_type") or "phone").lower()
        if max_models and len(models_seen) >= max_models and model_id not in models_seen:
            continue
        models_seen.add(model_id)

        repair_info = repairs_map.get(repair_id)
        if not repair_info:
            continue

        brand_id = model_info.get("brand_id")
        brand_info = brands_map.get(brand_id, {})

        brand_raw = brand_info.get("name") or "Inconnu"
        brand_slug = brand_info.get("slug") or ""
        brand_clean = CANONICAL_BRAND_NAMES.get(brand_raw.lower(), brand_raw.capitalize())

        model_name = model_info.get("name") or "Modèle inconnu"
        model_slug = model_info.get("slug") or ""

        repair_name_raw = repair_info.get("name") or "Réparation"
        canonical_repair = map_repair_type(repair_name_raw)

        device_family = "Smartphone" if dev_type in ["phone", "smartphone"] else ("Tablette" if dev_type in ["tablet", "tablette"] else "Console")

        row = create_repair_row(
            brand=brand_clean,
            model=model_name,
            model_slug=model_slug,
            brand_slug=brand_slug,
            device_family=device_family,
            repair_type_raw=repair_name_raw,
            canonical_repair=canonical_repair,
            price=float(price_val),
            duration_minutes=p.get("duration_minutes"),
            scraped_at=scrape_started_at,
        )
        rows.append(row)

    print(f"[{SOURCE_NAME.upper()}] 🔄 Assemblage terminé : {len(rows)} lignes sur {len(models_seen)} modèles uniques")

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
    parser = argparse.ArgumentParser(description="Crash Mobile Scraper (Supabase REST)")
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
