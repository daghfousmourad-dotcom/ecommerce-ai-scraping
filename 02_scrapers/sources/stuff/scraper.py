"""STUFF Scraper (https://stuff-reparation.fr).

Réseau de boutiques de réparation smartphones et multimédia en région PACA :
1. STUFF Marseille Saint-Ferréol : 78 Rue Saint-Ferréol, 13006 Marseille (04 91 33 01 68)
2. STUFF Aubagne : CC Auchan Aubagne, Route de Gémenos, 13400 Aubagne (04 42 73 88 59)
3. STUFF Aix-en-Provence : 36 Rue Espariat, 13100 Aix-en-Provence (04 42 93 31 54)
4. STUFF Nice Lingostière : CC Carrefour Nice Lingostière, 606 Bd du Mercantour, 06200 Nice (06 99 77 06 06)
5. STUFF Antibes : CC Carrefour Antibes, Chem. de Saint-Claude, 06600 Antibes (06 67 11 77 77)

Garantie 6 mois pièces et main-d'œuvre | Réparation express 30 minutes | Certifié QualiRépar

Stratégie d'extraction optimale (Stratégie 1 - API / Dataset Hiboutik `stuff-quote-data.js`) :
    1. Récupération directe du bundle officiel de tarification Hiboutik :
       URL : https://stuffmarseille.fr/stuff-quote/stuff-quote-data.js (ou miroir Aubagne/Aix)
    2. Extraction des 2 940 modèles référencés et des 26 constructeurs avec codes SKU / A-numbers.
    3. Traitement des 318 modèles tarifés en atelier (Samsung, Apple, Xiaomi, Huawei, Google, etc.).
    4. Déploiement géolocalisé pour les 5 ateliers PACA (Marseille, Aubagne, Aix, Nice, Antibes).
    5. Normalisation standard au format 22 colonnes E-commerce AI V2.
    6. Exportation certifiée des 5 artefacts standard (.jsonl, .csv, .json, _run.json, _issues.jsonl).
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
from pathlib import Path
from typing import Any
import urllib.parse
import urllib.request

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

SOURCE_NAME = "stuff"
SOURCE_DOMAIN = "stuff-reparation.fr"
DATA_SOURCE_URL = "https://stuffmarseille.fr/stuff-quote/stuff-quote-data.js"
FALLBACK_DATA_URL = "https://stuffaubagne.fr/stuff-quote/stuff-quote-data.js"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

STORES_PACA = [
    {
        "id": "marseille",
        "shop_name": "STUFF - Marseille Saint-Ferréol",
        "address": "78 Rue Saint-Ferréol",
        "city": "Marseille",
        "postal_code": "13006",
        "phone": "04 91 33 01 68",
        "url": "https://stuffmarseille.fr/",
    },
    {
        "id": "aubagne",
        "shop_name": "STUFF - Aubagne",
        "address": "CC Auchan Aubagne, Rte de Gémenos",
        "city": "Aubagne",
        "postal_code": "13400",
        "phone": "04 42 73 88 59",
        "url": "https://stuffaubagne.fr/",
    },
    {
        "id": "aix",
        "shop_name": "STUFF - Aix-en-Provence",
        "address": "36 Rue Espariat",
        "city": "Aix-en-Provence",
        "postal_code": "13100",
        "phone": "04 42 93 31 54",
        "url": "https://stuffaix.fr/",
    },
    {
        "id": "nice",
        "shop_name": "STUFF - Nice Lingostière",
        "address": "CC Carrefour Nice Lingostière, 606 Bd du Mercantour",
        "city": "Nice",
        "postal_code": "06200",
        "phone": "06 99 77 06 06",
        "url": "https://stuffnice.fr/",
    },
    {
        "id": "antibes",
        "shop_name": "STUFF - Antibes",
        "address": "CC Carrefour Antibes, Chem. de Saint-Claude",
        "city": "Antibes",
        "postal_code": "06600",
        "phone": "06 67 11 77 77",
        "url": "https://stuffantibes.fr/",
    },
]

CANONICAL_BRAND_NAMES: dict[str, str] = {
    "apple": "Apple",
    "samsung": "Samsung",
    "xiaomi": "Xiaomi",
    "huawei": "Huawei",
    "google": "Google",
    "oppo": "Oppo",
    "honor": "Honor",
    "oneplus": "OnePlus",
    "realme": "Realme",
    "sony": "Sony",
    "motorola": "Motorola",
    "nokia": "Nokia",
    "asus": "Asus",
    "alcatel": "Alcatel",
    "blackberry": "BlackBerry",
    "cat": "CAT",
    "fairphone": "Fairphone",
    "htc": "HTC",
    "lenovo": "Lenovo",
    "lg": "LG",
    "microsoft": "Microsoft",
    "nintendo": "Nintendo",
}

REPAIR_LABEL_MAPPING: dict[str, tuple[str, str]] = {
    # key: (raw_label, canonical_ecommerce_ai_scraping)
    "ecran": ("Écran", "Ecran LCD"),
    "ecran-tablette": ("Écran Tablette", "Ecran LCD"),
    "ecran-tactile": ("Écran tactile", "Ecran LCD"),
    "ecran-tactile-tablette": ("Écran tactile", "Ecran LCD"),
    "batterie": ("Batterie", "Batterie"),
    "batterie-tablette": ("Batterie", "Batterie"),
    "port-de-charge": ("Connecteur de charge", "Connecteur de charge"),
    "port-de-charge-tablette": ("Connecteur de charge", "Connecteur de charge"),
    "coque-arriere": ("Vitre arrière / Coque", "Façade arrière"),
    "chassis-midframe": ("Châssis complet", "Façade arrière"),
    "chassis-midframe-tablette": ("Châssis complet", "Façade arrière"),
    "camera-arriere": ("Caméra arrière", "Caméra arrière"),
    "objectif-de-camera": ("Lentille caméra arrière", "Caméra arrière"),
    "camera-frontale": ("Caméra avant", "Caméra avant"),
    "haut-parleur": ("Haut-parleur", "Haut-parleur"),
    "ecouteurs": ("Écouteur interne", "Haut-parleur"),
    "micro-appel-tablette": ("Microphone", "Microphone"),
    "bouton-d-alimentation": ("Bouton Power", "Autre réparation"),
    "bouton-accueil": ("Bouton Home", "Autre réparation"),
    "vibreur": ("Vibreur", "Autre réparation"),
    "lecteur-de-carte-sim": ("Lecteur SIM", "Autre réparation"),
    "antenne-wifi": ("Antenne Wi-Fi", "Autre réparation"),
    "antenne-nfc": ("Antenne NFC", "Autre réparation"),
    "ecran-console": ("Écran Console", "Ecran LCD"),
    "batterie-console": ("Batterie Console", "Batterie"),
    "bouton-d-alimentation-console": ("Bouton Power Console", "Autre réparation"),
    "ecran-montre-connectee": ("Écran Montre", "Ecran LCD"),
}


def fetch_stuff_quote_data() -> dict[str, Any] | None:
    """Télécharge et extrait l'objet STUFF_QUOTE_DATA depuis stuff-quote-data.js."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for target_url in [DATA_SOURCE_URL, FALLBACK_DATA_URL]:
        try:
            req = urllib.request.Request(target_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                content = resp.read().decode("utf-8", errors="ignore")
                idx = content.find("window.STUFF_QUOTE_DATA = ")
                if idx != -1:
                    start_json = idx + len("window.STUFF_QUOTE_DATA = ")
                    decoder = json.JSONDecoder()
                    data, _ = decoder.raw_decode(content[start_json:])
                    return data
        except Exception as exc:
            print(f"[{SOURCE_NAME.upper()}] ⚠️ Erreur sur {target_url}: {exc}")

    return None


def create_repair_row(
    store: dict[str, str],
    brand: str,
    model: str,
    model_sku: str,
    device_family: str,
    repair_type_raw: str,
    canonical_repair: str,
    price: float,
    scraped_at: str,
) -> dict[str, Any]:
    price_raw_str = f"{int(price) if price.is_integer() else price} €"
    item_name = f"{repair_type_raw} {brand} {model}".strip()
    source_url = f"{store['url']}#devis"

    return {
        "source_site": SOURCE_DOMAIN,
        "source_url": source_url,
        "source": source_url,
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
        "shop_name": store["shop_name"],
        "address": store["address"],
        "city": store["city"],
        "postal_code": store["postal_code"],
        "delay": "Réparation express en 30 minutes sans rendez-vous",
        "warranty": "Garantie 6 mois pièces et main-d'œuvre, certifié QualiRépar",
        "model_sku": model_sku,
        "repair_category": "Réparation en atelier",
        "scraped_at": scraped_at,
    }


def scrape(
    output_dir: str | Path | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
    stores_filter: list[str] | None = None,
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

    print(f"\n[{SOURCE_NAME.upper()}] 🌐 Récupération du dataset officiel Hiboutik : {DATA_SOURCE_URL}", flush=True)

    data = fetch_stuff_quote_data()
    if not data:
        fetch_errors.append(fetch_issue(DATA_SOURCE_URL, REASON_FETCH_ERROR, "Échec téléchargement stuff-quote-data.js"))
        print(f"[{SOURCE_NAME.upper()}] ❌ Impossible de charger les données STUFF.", flush=True)
        return []

    pricing = data.get("pricing", {})
    models_dict = {m["id"]: m for m in data.get("models", [])}
    brands_dict = {b["id"]: b for b in data.get("brands", [])}

    print(f"[{SOURCE_NAME.upper()}] 📦 Dataset chargé : {len(pricing)} modèles tarifés sur {len(models_dict)} modèles au catalogue", flush=True)

    # Filtrage des boutiques
    stores = STORES_PACA
    if stores_filter:
        stores = [s for s in STORES_PACA if s["id"] in stores_filter or s["city"].lower() in stores_filter]

    print(f"[{SOURCE_NAME.upper()}] 📍 Déploiement sur {len(stores)} ateliers PACA : {', '.join(s['city'] for s in stores)}", flush=True)

    all_rows: list[dict[str, Any]] = []
    models_processed = 0

    for model_id, repairs_dict in pricing.items():
        if not isinstance(repairs_dict, dict) or not repairs_dict:
            continue

        if max_models and models_processed >= max_models:
            break
        models_processed += 1

        model_info = models_dict.get(model_id, {})
        model_name = model_info.get("name") or model_id
        brand_id = model_info.get("brandId", "")
        brand_info = brands_dict.get(brand_id, {})
        brand_raw = brand_info.get("name") or brand_id
        brand_clean = CANONICAL_BRAND_NAMES.get(brand_raw.lower(), brand_raw.capitalize())

        type_id = (model_info.get("typeId") or "smartphone").lower()
        device_family = "Smartphone"
        if "tablette" in type_id:
            device_family = "Tablette"
        elif "console" in type_id:
            device_family = "Console"
        elif "montre" in type_id:
            device_family = "Montre connectée"

        # Code SKU / A-numbers
        codes_list = model_info.get("codes", [])
        model_sku = ", ".join(codes_list) if isinstance(codes_list, list) else str(codes_list)

        for rep_key, price_raw in repairs_dict.items():
            try:
                price_val = float(price_raw)
            except (ValueError, TypeError):
                continue

            if price_val <= 0:
                continue

            raw_label, canon_rep = REPAIR_LABEL_MAPPING.get(rep_key, (rep_key.replace("-", " ").capitalize(), "Autre réparation"))

            # Génération d'une ligne pour chaque atelier PACA
            for store in stores:
                row = create_repair_row(
                    store=store,
                    brand=brand_clean,
                    model=model_name,
                    model_sku=model_sku,
                    device_family=device_family,
                    repair_type_raw=raw_label,
                    canonical_repair=canon_rep,
                    price=price_val,
                    scraped_at=scrape_started_at,
                )
                all_rows.append(row)

    print(f"[{SOURCE_NAME.upper()}] 🔄 Données assemblées : {len(all_rows)} tarifs sur {models_processed} modèles", flush=True)

    # Déduplication
    deduped_rows = dedupe_dict_rows(
        all_rows,
        keys=["model", "repair_type_raw", "price", "shop_name"],
    )

    # Nettoyage E-commerce AI V2
    clean_report = None
    if apply_cleaning:
        cleaning_cfg = CleaningConfig(
            price_min=1.0,
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

    # Exportation des artefacts E-commerce AI V2
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
    parser = argparse.ArgumentParser(description="STUFF Scraper (Marseille, Aubagne, Aix, Nice, Antibes)")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport d'exécution")
    parser.add_argument("--max-models", type=int, default=None, help="Nombre max de modèles extraits")
    parser.add_argument(
        "--stores",
        nargs="+",
        default=None,
        help="Filtrer les boutiques (ex: marseille aubagne aix)",
    )
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
        stores_filter=args.stores,
    )


if __name__ == "__main__":
    main()
