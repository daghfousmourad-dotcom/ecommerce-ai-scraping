"""Feexo Scraper (https://feexo.fr).

Réseau de boutiques de réparation smartphones et multimédia en Occitanie :
- Toulouse Centre : 1 Rue Antoine Idrac, 31000 Toulouse (07 56 80 80 09)
- Montpellier Centre : 3 Rue du Pont de Lattes, 34000 Montpellier (06 41 97 71 75)
- Pérols : Centre Commercial Auchan Plein Sud, 34470 Pérols (07 56 80 80 09)
- Certifié QualiRépar | Écrans garantis 1 an | Pièces et autres réparations garanties 6 mois

Stratégie d'extraction optimale (Stratégie 1 - API REST Supabase PostgREST directe) :
    1. Interrogation directe de l'API Supabase officielle hébergeant le catalogue complet :
       URL : https://pdukjmjrelfkulspvcft.supabase.co/rest/v1/
    2. Récupération des tables relationnelles normalisées :
       - /brands : 24 constructeurs (Apple, Samsung, Xiaomi, Huawei, Google, Oppo, OnePlus, Honor, etc.)
       - /models : 2 366 modèles avec type d'appareil (smartphone, tablette, etc.)
       - /repair_types : 43 types de prestations standardisées
       - /prices : 20 000+ tarifs actifs (avec filtres price > 0 et is_active = true)
    3. Traitement ciblé des smartphones et tablettes (device_type in ['smartphone', 'tablette'])
    4. Mapping canonique selon les 9 grandes catégories E-commerce AI Scraping et normalisation standard 22 colonnes E-commerce AI V2.
    5. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
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

BASE_URL = "https://feexo.fr"
SOURCE_NAME = "feexo"
SOURCE_DOMAIN = "feexo.fr"

# Supabase PostgREST API
SUPABASE_URL = "https://pdukjmjrelfkulspvcft.supabase.co/rest/v1"
SUPABASE_ANON_KEY = "sb_publishable_-LkOsln8faS62wYZOW_rtQ_pWJ2rLlC"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "Feexo Occitanie (Toulouse / Montpellier / Pérols)",
    "address": "1 Rue Antoine Idrac",
    "city": "Toulouse",
    "postal_code": "31000",
    "delay": "Réparation express 30 à 45 min en atelier sans rendez-vous",
    "warranty": "Écran garanti 1 an, batterie et autres réparations 6 mois, certifié QualiRépar",
}

CANONICAL_BRAND_NAMES: dict[str, str] = {
    "apple": "Apple",
    "samsung": "Samsung",
    "xiaomi": "Xiaomi",
    "huawei": "Huawei",
    "google": "Google",
    "oneplus": "OnePlus",
    "oppo": "Oppo",
    "honor": "Honor",
    "realme": "Realme",
    "vivo": "Vivo",
    "motorola": "Motorola",
    "sony": "Sony",
    "asus": "Asus",
    "nokia": "Nokia",
    "nothing": "Nothing",
    "cmf": "CMF",
    "fairphone": "Fairphone",
    "crosscall": "Crosscall",
    "lenovo": "Lenovo",
    "zte": "ZTE",
    "nubia": "Nubia",
    "microsoft": "Microsoft",
}

CANONICAL_REPAIR_MAP: dict[str, str] = {
    # Écrans
    "écran classic": "Ecran LCD",
    "écran premium": "Ecran LCD",
    "écran original": "Ecran LCD",
    "écran lcd": "Ecran LCD",
    "bloc écran + tactile original": "Ecran LCD",
    "bloc écran + tactile premium": "Ecran LCD",
    "bloc écran seul": "Ecran LCD",
    "écran tactile": "Ecran LCD",
    "vitre tactile": "Ecran LCD",
    # Batteries
    "batterie premium": "Batterie",
    "batterie original": "Batterie",
    "batterie macbook": "Batterie",
    "batterie console": "Batterie",
    # Connecteurs
    "connecteur de charge": "Connecteur de charge",
    "port usb": "Connecteur de charge",
    # Caméras
    "appareil photo arrière": "Caméra arrière",
    "lentilles appareil photo": "Caméra arrière",
    "appareil photo avant": "Caméra avant",
    # Vitres arrière & Châssis
    "vitre arrière": "Façade arrière",
    "vitre arrière + charge induction": "Façade arrière",
    "châssis complet avec boutons": "Façade arrière",
    # Diagnostics & Forfaits services
    "diagnostic": "Diagnostic",
    "autre panne": "Diagnostic",
    "protection hydrogel 7h sur-mesure": "Autre réparation",
    "protection hydrogel 9h": "Autre réparation",
    "protection hydrogel privacy": "Autre réparation",
    "sauvegarde / transfert de donnée": "Autre réparation",
    "réinstallation système": "Autre réparation",
    "nettoyage complet (poussière)": "Autre réparation",
    "restauration": "Autre réparation",
    "paramétrage de l'appareil": "Autre réparation",
}


def map_repair_type(repair_raw: str) -> str:
    norm = repair_raw.strip().lower()
    if norm in CANONICAL_REPAIR_MAP:
        return CANONICAL_REPAIR_MAP[norm]
    for key, val in CANONICAL_REPAIR_MAP.items():
        if key in norm:
            return val
    canon, matched = normalize_repair_type(repair_raw)
    return canon if matched else "Autre réparation"


def get_repair_warranty(repair_raw: str) -> str:
    norm = repair_raw.strip().lower()
    if "écran" in norm or "ecran" in norm or "tactile" in norm:
        if "original" in norm:
            return "Garantie 1 an (Pièce originale constructeur - QualiRépar)"
        return "Garantie 1 an (Pièce certifiée haute qualité - QualiRépar)"
    elif "batterie" in norm:
        if "original" in norm:
            return "Garantie 6 mois (Batterie originale constructeur)"
        return "Garantie 6 mois (Batterie haute capacité)"
    elif "diagnostic" in norm:
        return "Devis gratuit sans engagement"
    elif "hydrogel" in norm:
        return "Garantie pose immédiate"
    else:
        return "Garantie 6 mois (Pièce & main-d'œuvre)"


def fetch_supabase_table(
    endpoint: str,
    select: str = "*",
    extra_params: str = "",
    batch_size: int = 1000,
) -> list[dict[str, Any]]:
    """Récupère l'intégralité d'une table Supabase via pagination PostgREST offset/limit."""
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
        qs = "&".join(query_parts)
        url = f"{SUPABASE_URL}/{endpoint}?{qs}"

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
                    print(f"⚠️ [FEEXO API] Erreur sur {url}: {exc}")
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
    warranty: str,
    scraped_at: str,
) -> dict[str, Any]:
    price_raw_str = f"{int(price) if price.is_integer() else price} €"
    item_name = f"{repair_type_raw} {brand} {model}".strip()
    model_url = f"{BASE_URL}/reparation/{brand_slug}/{model_slug}/" if (brand_slug and model_slug) else f"{BASE_URL}/reparation/"

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
        "delay": SHOP_INFO["delay"],
        "warranty": warranty,
        "model_sku": model_slug,
        "repair_category": "Réparation en atelier",
        "scraped_at": scraped_at,
    }


def scrape(
    output_dir: str | Path | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
    allowed_device_types: set[str] | None = None,
) -> list[dict[str, Any]]:
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir) if output_dir else (_SCRAPERS_ROOT / "outputs")
    out_dir.mkdir(parents=True, exist_ok=True)

    prefix = f"{SOURCE_NAME}_repair_price_{scrape_started_at}"
    jsonl_path = out_dir / f"{prefix}.jsonl"
    csv_path = out_dir / f"{prefix}.csv"
    json_path = out_dir / f"{prefix}.json"

    fetch_errors: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    target_device_types = allowed_device_types or {"smartphone", "tablette"}

    print(f"\n[{SOURCE_NAME.upper()}] 🌐 Connexion API Supabase PostgREST : {SUPABASE_URL}", flush=True)

    # 1. Récupération des tables de lookup
    try:
        raw_brands = fetch_supabase_table("brands", select="id,name,slug,display_prices")
        brands_map = {b["id"]: b for b in raw_brands}
        print(f"[{SOURCE_NAME.upper()}] 🏷️  Marques chargées : {len(brands_map)} marques", flush=True)
    except Exception as exc:
        fetch_errors.append(fetch_issue(f"{SUPABASE_URL}/brands", REASON_FETCH_ERROR, str(exc)))
        brands_map = {}

    try:
        raw_repair_types = fetch_supabase_table("repair_types", select="id,name,is_staff_only")
        repairs_map = {r["id"]: r for r in raw_repair_types if not r.get("is_staff_only")}
        print(f"[{SOURCE_NAME.upper()}] 🛠️  Prestations chargées : {len(repairs_map)} types", flush=True)
    except Exception as exc:
        fetch_errors.append(fetch_issue(f"{SUPABASE_URL}/repair_types", REASON_FETCH_ERROR, str(exc)))
        repairs_map = {}

    try:
        raw_models = fetch_supabase_table("models", select="id,brand_id,name,slug,device_type,is_active")
        models_map = {m["id"]: m for m in raw_models if m.get("is_active", True)}
        print(f"[{SOURCE_NAME.upper()}] 📱 Modèles chargés : {len(models_map)} modèles", flush=True)
    except Exception as exc:
        fetch_errors.append(fetch_issue(f"{SUPABASE_URL}/models", REASON_FETCH_ERROR, str(exc)))
        models_map = {}

    # 2. Récupération des tarifs actifs avec prix positif
    print(f"[{SOURCE_NAME.upper()}] 💰 Téléchargement des tarifs actifs (price > 0)...", flush=True)
    try:
        raw_prices = fetch_supabase_table(
            "prices",
            select="id,model_id,repair_type_id,price,old_price,is_promo",
            extra_params="price=gt.0&is_active=eq.true",
        )
        print(f"[{SOURCE_NAME.upper()}] 📦 Tarifs bruts récupérés : {len(raw_prices)} lignes", flush=True)
    except Exception as exc:
        fetch_errors.append(fetch_issue(f"{SUPABASE_URL}/prices", REASON_FETCH_ERROR, str(exc)))
        raw_prices = []

    if not raw_prices:
        print(f"[{SOURCE_NAME.upper()}] ❌ Aucun tarif récupéré depuis Supabase.", flush=True)
        return []

    # 3. Assemblage & Jointure en mémoire
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

        dev_type = (model_info.get("device_type") or "").lower()
        if dev_type not in target_device_types:
            continue

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
        warranty = get_repair_warranty(repair_name_raw)

        device_family = "Smartphone" if dev_type == "smartphone" else "Tablette"

        row = create_repair_row(
            brand=brand_clean,
            model=model_name,
            model_slug=model_slug,
            brand_slug=brand_slug,
            device_family=device_family,
            repair_type_raw=repair_name_raw,
            canonical_repair=canonical_repair,
            price=float(price_val),
            warranty=warranty,
            scraped_at=scrape_started_at,
        )
        rows.append(row)

    print(f"[{SOURCE_NAME.upper()}] 🔄 Données assemblées : {len(rows)} tarifs sur {len(models_seen)} modèles uniques", flush=True)

    # 4. Déduplication stricte
    deduped_rows = dedupe_dict_rows(
        rows,
        keys=["model", "repair_type_raw", "price", "shop_name"],
    )

    # 5. Nettoyage E-commerce AI V2
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

    # 6. Exportation des artefacts E-commerce AI V2
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
    parser = argparse.ArgumentParser(description="Feexo Scraper (Toulouse / Montpellier / Pérols)")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport d'exécution")
    parser.add_argument("--max-models", type=int, default=None, help="Nombre max de modèles extraits")
    parser.add_argument(
        "--smartphones-only",
        action="store_true",
        help="Restreindre exclusivement aux smartphones (ignorer tablettes)",
    )
    args = parser.parse_args()

    allowed_types = {"smartphone"} if args.smartphones_only else {"smartphone", "tablette"}

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
        allowed_device_types=allowed_types,
    )


if __name__ == "__main__":
    main()
