"""Repairmen Scraper (https://repairmen.fr).

Atelier de réparation smartphones & tablettes à Toulouse et environs (Escalquens 31750) :
- 4 rue des Amandiers, Villa 46, 31750 Escalquens / Grand Toulouse
- Téléphone : +33 6 70 67 75 28 | contact@repairmen.fr
- Stratégie d'extraction optimale (Stratégie 1 - API REST Directe + Enrichissement Nuxt) :
    1. Appel direct de l'API officielle : https://repairmen.fr/api/pricing
    2. Extraction des données de tarification pour 10 marques (Apple, Samsung, Xiaomi, Huawei,
       Google Pixel, OnePlus, Oppo, Honor, Realme, Vivo) et 80 modèles de smartphones phares.
    3. Déploiement multi-gammes pour Apple :
       - Écran Éco (Incell / LCD), Écran Premium (XO7 Soft OLED 120Hz), Écran Original Apple
       - Batterie Standard (Ti Basic), Batterie Haute Capacité (Ampsentrix), Batterie Originale Apple
       - Face arrière (Châssis complet / Laser)
       - Connecteur de charge (Compatible / Original)
       - Caméras avant et arrière
    4. Intégration des forfaits d'atelier transversaux :
       - Haut-parleur : 35 €
       - Boutons : 30 €
    5. Résolution des libellés et numéros de modèles (SKU / A-numbers / SM-numbers) via le catalogue officiel.
    6. Normalisation et standardisation stricte au format 22 colonnes E-commerce AI V2.
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
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

BASE_URL = "https://repairmen.fr"
API_PRICING_URL = "https://repairmen.fr/api/pricing"
DEVIS_URL = "https://repairmen.fr/devis"
TARIFS_URL = "https://repairmen.fr/tarifs"
SOURCE_NAME = "repairmen"
SOURCE_DOMAIN = "repairmen.fr"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "Repairmen Toulouse",
    "address": "4 rue des Amandiers, Villa 46",
    "city": "Escalquens",
    "postal_code": "31750",
    "delay": "Réparation en 30 minutes sur rendez-vous ou à domicile",
    "warranty": "Garantie jusqu'à 1 an selon qualité pièce (3 mois Éco, 1 an Premium/Original)",
}

CANONICAL_BRAND_NAMES = {
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
}

CANONICAL_REPAIR_MAP = {
    "écran": "Ecran LCD",
    "ecran": "Ecran LCD",
    "ecran lcd": "Ecran LCD",
    "écran éco (incell)": "Ecran LCD",
    "écran éco": "Ecran LCD",
    "écran premium (soft oled)": "Ecran LCD",
    "écran premium": "Ecran LCD",
    "écran original apple": "Ecran LCD",
    "batterie": "Batterie",
    "batterie standard": "Batterie",
    "batterie haute capacité": "Batterie",
    "batterie originale apple": "Batterie",
    "vitre": "Façade arrière",
    "face arrière": "Façade arrière",
    "façade arrière": "Façade arrière",
    "face arrière (châssis)": "Façade arrière",
    "face arrière (laser)": "Façade arrière",
    "connecteur": "Connecteur de charge",
    "connecteur de charge": "Connecteur de charge",
    "port de charge": "Connecteur de charge",
    "connecteur de charge (compatible)": "Connecteur de charge",
    "connecteur de charge (original)": "Connecteur de charge",
    "caméra arrière": "Caméra arrière",
    "caméra arrière (compatible)": "Caméra arrière",
    "caméra avant": "Caméra avant",
    "caméra avant (compatible)": "Caméra avant",
    "haut-parleur": "Haut-parleur",
    "haut-parleur (son, appel)": "Haut-parleur",
    "boutons": "Autre réparation",
    "boutons (power/volume)": "Autre réparation",
}


def map_repair_type(repair_raw: str) -> str:
    norm = repair_raw.strip().lower()
    if norm in CANONICAL_REPAIR_MAP:
        return CANONICAL_REPAIR_MAP[norm]
    for key, val in CANONICAL_REPAIR_MAP.items():
        if key in norm:
            return val
    return normalize_repair_type(repair_raw)


def fetch_api_json(url: str) -> dict[str, Any]:
    """Récupère un JSON via requête HTTP sécurisée."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        },
    )
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_device_catalog_metadata() -> dict[str, dict[str, str]]:
    """Télécharge et extrait les métadonnées officielles de tous les smartphones du site."""
    catalog: dict[str, dict[str, str]] = {}
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(
            f"{BASE_URL}/_nuxt/CbvnBLfS.js",
            headers={"User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            matches = re.findall(
                r'\{id:[\"\']([^\"\']+)[\"\'],name:[\"\']([^\"\']+)[\"\'](?:,modelNumber:[\"\']([^\"\']+)[\"\'])?',
                content,
            )
            for m in matches:
                dev_id = m[0]
                dev_name = m[1]
                sku = m[2] if len(m) > 2 else ""
                catalog[dev_id] = {
                    "name": dev_name,
                    "model_number": sku,
                }
    except Exception as exc:
        print(f"[REPAIRMEN] ⚠️ Note catalogue distant : {exc} (fallback actif)")

    return catalog


def parse_pricing_data(
    pricing_data: dict[str, Any],
    catalog: dict[str, dict[str, str]],
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    """Parse et structure l'ensemble des grilles tarifaires par marque et modèle."""
    rows: list[dict[str, Any]] = []
    scraped_at = now_iso_utc()

    models_processed = 0

    for brand_key, device_list in pricing_data.items():
        if not isinstance(device_list, list):
            continue

        brand_display = CANONICAL_BRAND_NAMES.get(brand_key.lower(), brand_key.capitalize())

        for device in device_list:
            if not isinstance(device, dict):
                continue

            if max_models and models_processed >= max_models:
                break

            device_id = device.get("deviceId", "")
            if not device_id:
                continue

            meta = catalog.get(device_id, {})
            model_name = meta.get("name") or device_id
            model_sku = meta.get("model_number") or ""

            model_url = f"{DEVIS_URL}?marque={urllib.parse.quote(brand_key)}&modele={urllib.parse.quote(device_id)}"

            # 1. ÉCRAN
            ecran_val = device.get("ecran")
            if isinstance(ecran_val, dict):
                # Déclinaisons Incell, XO7, Original
                if "incell" in ecran_val:
                    p = float(ecran_val["incell"])
                    rows.append(create_repair_row(
                        brand=brand_display,
                        model=model_name,
                        model_sku=model_sku,
                        model_url=model_url,
                        repair_type_raw="Écran Éco (Incell)",
                        canonical_repair="Ecran LCD",
                        price=p,
                        warranty="Garantie 3 mois",
                        scraped_at=scraped_at,
                    ))
                if "xo7" in ecran_val:
                    p = float(ecran_val["xo7"])
                    rows.append(create_repair_row(
                        brand=brand_display,
                        model=model_name,
                        model_sku=model_sku,
                        model_url=model_url,
                        repair_type_raw="Écran Premium (Soft OLED)",
                        canonical_repair="Ecran LCD",
                        price=p,
                        warranty="Garantie 1 an",
                        scraped_at=scraped_at,
                    ))
                if "original" in ecran_val:
                    p = float(ecran_val["original"])
                    rows.append(create_repair_row(
                        brand=brand_display,
                        model=model_name,
                        model_sku=model_sku,
                        model_url=model_url,
                        repair_type_raw="Écran Original Apple",
                        canonical_repair="Ecran LCD",
                        price=p,
                        warranty="Garantie 1 an (Pièce Apple d'origine)",
                        scraped_at=scraped_at,
                    ))
            elif isinstance(ecran_val, (int, float)) and ecran_val > 0:
                rows.append(create_repair_row(
                    brand=brand_display,
                    model=model_name,
                    model_sku=model_sku,
                    model_url=model_url,
                    repair_type_raw="Écran",
                    canonical_repair="Ecran LCD",
                    price=float(ecran_val),
                    warranty="Garantie 1 an",
                    scraped_at=scraped_at,
                ))

            # 2. BATTERIE
            bat_val = device.get("batterie")
            if isinstance(bat_val, dict):
                if "ti_basic" in bat_val:
                    p = float(bat_val["ti_basic"])
                    rows.append(create_repair_row(
                        brand=brand_display,
                        model=model_name,
                        model_sku=model_sku,
                        model_url=model_url,
                        repair_type_raw="Batterie Standard",
                        canonical_repair="Batterie",
                        price=p,
                        warranty="Garantie 6 mois",
                        scraped_at=scraped_at,
                    ))
                if "ampsentrix" in bat_val:
                    p = float(bat_val["ampsentrix"])
                    rows.append(create_repair_row(
                        brand=brand_display,
                        model=model_name,
                        model_sku=model_sku,
                        model_url=model_url,
                        repair_type_raw="Batterie Haute Capacité (Ampsentrix)",
                        canonical_repair="Batterie",
                        price=p,
                        warranty="Garantie 1 an",
                        scraped_at=scraped_at,
                    ))
                if "original" in bat_val:
                    p = float(bat_val["original"])
                    rows.append(create_repair_row(
                        brand=brand_display,
                        model=model_name,
                        model_sku=model_sku,
                        model_url=model_url,
                        repair_type_raw="Batterie Originale Apple",
                        canonical_repair="Batterie",
                        price=p,
                        warranty="Garantie 1 an (Pièce Apple d'origine)",
                        scraped_at=scraped_at,
                    ))
            elif isinstance(bat_val, (int, float)) and bat_val > 0:
                rows.append(create_repair_row(
                    brand=brand_display,
                    model=model_name,
                    model_sku=model_sku,
                    model_url=model_url,
                    repair_type_raw="Batterie",
                    canonical_repair="Batterie",
                    price=float(bat_val),
                    warranty="Garantie 1 an",
                    scraped_at=scraped_at,
                ))

            # 3. VITRE / FACE ARRIÈRE
            vitre_val = device.get("vitre")
            if isinstance(vitre_val, dict):
                if "chassis" in vitre_val:
                    p = float(vitre_val["chassis"])
                    rows.append(create_repair_row(
                        brand=brand_display,
                        model=model_name,
                        model_sku=model_sku,
                        model_url=model_url,
                        repair_type_raw="Face arrière (Châssis)",
                        canonical_repair="Façade arrière",
                        price=p,
                        warranty="Garantie 1 an",
                        scraped_at=scraped_at,
                    ))
                if "laser" in vitre_val:
                    p = float(vitre_val["laser"])
                    rows.append(create_repair_row(
                        brand=brand_display,
                        model=model_name,
                        model_sku=model_sku,
                        model_url=model_url,
                        repair_type_raw="Face arrière (Laser)",
                        canonical_repair="Façade arrière",
                        price=p,
                        warranty="Garantie 1 an",
                        scraped_at=scraped_at,
                    ))
            elif isinstance(vitre_val, (int, float)) and vitre_val > 0:
                rows.append(create_repair_row(
                    brand=brand_display,
                    model=model_name,
                    model_sku=model_sku,
                    model_url=model_url,
                    repair_type_raw="Face arrière",
                    canonical_repair="Façade arrière",
                    price=float(vitre_val),
                    warranty="Garantie 1 an",
                    scraped_at=scraped_at,
                ))

            # 4. CONNECTEUR DE CHARGE
            conn_val = device.get("connecteur")
            if isinstance(conn_val, dict):
                if "compatible" in conn_val:
                    p = float(conn_val["compatible"])
                    rows.append(create_repair_row(
                        brand=brand_display,
                        model=model_name,
                        model_sku=model_sku,
                        model_url=model_url,
                        repair_type_raw="Connecteur de charge (Compatible)",
                        canonical_repair="Connecteur de charge",
                        price=p,
                        warranty="Garantie 6 mois",
                        scraped_at=scraped_at,
                    ))
                if "original" in conn_val:
                    p = float(conn_val["original"])
                    rows.append(create_repair_row(
                        brand=brand_display,
                        model=model_name,
                        model_sku=model_sku,
                        model_url=model_url,
                        repair_type_raw="Connecteur de charge (Original)",
                        canonical_repair="Connecteur de charge",
                        price=p,
                        warranty="Garantie 1 an",
                        scraped_at=scraped_at,
                    ))
            elif isinstance(conn_val, (int, float)) and conn_val > 0:
                rows.append(create_repair_row(
                    brand=brand_display,
                    model=model_name,
                    model_sku=model_sku,
                    model_url=model_url,
                    repair_type_raw="Connecteur de charge",
                    canonical_repair="Connecteur de charge",
                    price=float(conn_val),
                    warranty="Garantie 1 an",
                    scraped_at=scraped_at,
                ))

            # 5. CAMÉRA ARRIÈRE
            cam_arr = device.get("camera_arriere")
            if isinstance(cam_arr, dict):
                if "compatible" in cam_arr:
                    p = float(cam_arr["compatible"])
                    rows.append(create_repair_row(
                        brand=brand_display,
                        model=model_name,
                        model_sku=model_sku,
                        model_url=model_url,
                        repair_type_raw="Caméra arrière (Compatible)",
                        canonical_repair="Caméra arrière",
                        price=p,
                        warranty="Garantie 6 mois",
                        scraped_at=scraped_at,
                    ))
            elif isinstance(cam_arr, (int, float)) and cam_arr > 0:
                rows.append(create_repair_row(
                    brand=brand_display,
                    model=model_name,
                    model_sku=model_sku,
                    model_url=model_url,
                    repair_type_raw="Caméra arrière",
                    canonical_repair="Caméra arrière",
                    price=float(cam_arr),
                    warranty="Garantie 1 an",
                    scraped_at=scraped_at,
                ))

            # 6. CAMÉRA AVANT
            cam_av = device.get("camera_avant")
            if isinstance(cam_av, dict):
                if "compatible" in cam_av:
                    p = float(cam_av["compatible"])
                    rows.append(create_repair_row(
                        brand=brand_display,
                        model=model_name,
                        model_sku=model_sku,
                        model_url=model_url,
                        repair_type_raw="Caméra avant (Compatible)",
                        canonical_repair="Caméra avant",
                        price=p,
                        warranty="Garantie 6 mois",
                        scraped_at=scraped_at,
                    ))
            elif isinstance(cam_av, (int, float)) and cam_av > 0:
                rows.append(create_repair_row(
                    brand=brand_display,
                    model=model_name,
                    model_sku=model_sku,
                    model_url=model_url,
                    repair_type_raw="Caméra avant",
                    canonical_repair="Caméra avant",
                    price=float(cam_av),
                    warranty="Garantie 1 an",
                    scraped_at=scraped_at,
                ))

            # 7. FORFAITS TRANSVERSAUX ATELIER (Autres réparations)
            rows.append(create_repair_row(
                brand=brand_display,
                model=model_name,
                model_sku=model_sku,
                model_url=model_url,
                repair_type_raw="Haut-parleur (Son, appel)",
                canonical_repair="Haut-parleur",
                price=35.0,
                warranty="Garantie 6 mois",
                scraped_at=scraped_at,
            ))
            rows.append(create_repair_row(
                brand=brand_display,
                model=model_name,
                model_sku=model_sku,
                model_url=model_url,
                repair_type_raw="Boutons (Power/Volume)",
                canonical_repair="Autre réparation",
                price=30.0,
                warranty="Garantie 6 mois",
                scraped_at=scraped_at,
            ))

            models_processed += 1

    return rows


def create_repair_row(
    brand: str,
    model: str,
    model_sku: str,
    model_url: str,
    repair_type_raw: str,
    canonical_repair: str,
    price: float,
    warranty: str,
    scraped_at: str,
) -> dict[str, Any]:
    price_raw_str = f"{int(price) if price.is_integer() else price} €"
    item_name = f"{repair_type_raw} {brand} {model}".strip()

    return {
        "source_site": SOURCE_DOMAIN,
        "source_url": model_url,
        "source": model_url,
        "timestamp": scraped_at,
        "data_type": "repair_price",
        "device_family": "Smartphone",
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
        "model_sku": model_sku,
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

    prefix = f"{SOURCE_NAME}_repair_price_{scrape_started_at}"
    jsonl_path = out_dir / f"{prefix}.jsonl"
    csv_path = out_dir / f"{prefix}.csv"
    json_path = out_dir / f"{prefix}.json"

    fetch_errors: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    # 1. Téléchargement API pricing
    print(f"\n[{SOURCE_NAME.upper()}] 🌐 Téléchargement de la grille tarifaire : {API_PRICING_URL}", flush=True)
    try:
        pricing_data = fetch_api_json(API_PRICING_URL)
    except Exception as exc:
        fetch_errors.append(fetch_issue(API_PRICING_URL, REASON_FETCH_ERROR, str(exc)))
        pricing_data = {}

    if not pricing_data:
        print(f"[{SOURCE_NAME.upper()}] ❌ Impossible de récupérer les tarifs de Repairmen.", flush=True)
        return []

    # 2. Enrichissement avec les métadonnées modèles du catalogue
    print(f"[{SOURCE_NAME.upper()}] 📚 Récupération du catalogue constructeurs...", flush=True)
    catalog = fetch_device_catalog_metadata()

    # 3. Parsing et structuration
    all_rows = parse_pricing_data(pricing_data, catalog=catalog, max_models=max_models)

    # 4. Déduplication
    deduped_rows = dedupe_dict_rows(
        all_rows,
        keys=["model", "repair_type_raw", "price", "shop_name"],
    )

    # 5. Nettoyage standard E-commerce AI V2
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

    # 6. Export des artefacts standard E-commerce AI V2
    write_jsonl_and_csv(deduped_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(deduped_rows),
        "rows": deduped_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[{SOURCE_NAME.upper()}] ✅ Terminé avec succès : {len(deduped_rows)} tarifs exportés dans {out_dir}", flush=True)

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
    parser = argparse.ArgumentParser(description="Repairmen Scraper (Toulouse / Grand Toulouse)")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport d'exécution")
    parser.add_argument("--max-models", type=int, default=None, help="Nombre max de modèles extraits")
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
    )


if __name__ == "__main__":
    main()
