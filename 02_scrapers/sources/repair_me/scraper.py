"""Repair-ME Scraper (https://repair-me.fr).

Ateliers de réparation smartphones et tablettes en Pays de la Loire :
1. Repair-ME Laval (Mayenne 53) :
   - 34 Bis Rue du Val de Mayenne, 53000 Laval
   - Téléphone : +33 6 69 27 02 87 | laval@repair-me.fr
   - Site web : https://repair-me.fr/laval/
2. Repair-ME Nantes (Loire-Atlantique 44) :
   - 75 Rue des Hauts Pavés, 44000 Nantes
   - Téléphone : +33 6 60 90 28 47 | nantes@repair-me.fr
   - Site web : https://repair-me-nantes.fr/

Spécialiste de la réparation mobile depuis 2012, certifié QualiRépar.

Stratégie d'extraction optimale (Stratégie 1 - API WordPress admin-ajax.php directe) :
    1. Interrogation directe du backend AJAX du plugin Repairplugin-pro sur les deux sites :
       - action: rp_fe_get_categories -> Types d'appareils (Téléphone, Tablette)
       - action: rp_fe_get_brands -> Liste des constructeurs (Apple, Samsung, Xiaomi, Google, etc.)
       - action: rp_fe_get_models -> Modèles complets (419 Laval + 408 Nantes)
       - action: rp_fe_get_repairs -> Tarifs complets et déclinaisons multi-gammes
    2. Extraction des tarifs simples et des variantes multi-gammes (repair_attrs_from_db :
       Éco In-Cell, Original reconditionné, Premium OLED) avec durées et garanties associées.
    3. Normalisation stricte au format 22 colonnes E-commerce AI V2 avec distinction des boutiques.
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
from concurrent.futures import ThreadPoolExecutor, as_completed
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

SOURCE_NAME = "repair_me"
SOURCE_DOMAIN = "repair-me.fr"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

LOCATIONS_CONFIG = {
    "laval": {
        "shop_name": "Repair-ME Laval",
        "address": "34 Bis Rue du Val de Mayenne",
        "city": "Laval",
        "postal_code": "53000",
        "phone": "+33 6 69 27 02 87",
        "email": "laval@repair-me.fr",
        "base_url": "https://repair-me.fr",
        "tarifs_url": "https://repair-me.fr/laval/tarifs/",
        "ajax_url": "https://repair-me.fr/wp-admin/admin-ajax.php",
        "delay": "Réparation express en atelier en 30 à 60 minutes",
        "default_warranty": "Garantie 6 à 18 mois selon qualité pièce, certifié QualiRépar",
    },
    "nantes": {
        "shop_name": "Repair-ME Nantes",
        "address": "75 Rue des Hauts Pavés",
        "city": "Nantes",
        "postal_code": "44000",
        "phone": "+33 6 60 90 28 47",
        "email": "nantes@repair-me.fr",
        "base_url": "https://repair-me-nantes.fr",
        "tarifs_url": "https://repair-me-nantes.fr/tarifs/",
        "ajax_url": "https://repair-me-nantes.fr/wp-admin/admin-ajax.php",
        "delay": "Réparation express en atelier en 30 à 60 minutes",
        "default_warranty": "Garantie 6 à 18 mois selon qualité pièce, certifié QualiRépar",
    },
}

CANONICAL_BRAND_NAMES: dict[str, str] = {
    "apple": "Apple",
    "samsung": "Samsung",
    "xiaomi": "Xiaomi",
    "google": "Google",
    "huawei": "Huawei",
    "honor": "Honor",
    "oppo": "Oppo",
    "one plus": "OnePlus",
    "oneplus": "OnePlus",
    "realme": "Realme",
    "crosscall": "Crosscall",
    "motorola": "Motorola",
    "blackview": "Blackview",
    "nokia": "Nokia",
    "sony": "Sony",
    "vivo": "Vivo",
    "lg": "LG",
    "tcl": "TCL",
    "asus": "Asus",
    "wiko": "Wiko",
    "tecno": "Tecno",
}

CANONICAL_REPAIR_MAP: dict[str, str] = {
    "écran complet": "Ecran LCD",
    "écran": "Ecran LCD",
    "ecran": "Ecran LCD",
    "batterie": "Batterie",
    "connecteur de charge": "Connecteur de charge",
    "caméra avant": "Caméra avant",
    "caméra arrière": "Caméra arrière",
    "vitre arrière": "Façade arrière",
    "lentille caméra arrière": "Caméra arrière",
    "désoxydation": "Désoxydation",
    "récupération de données": "Autre réparation",
    "logiciel": "Autre réparation",
    "mot de passe oublié": "Autre réparation",
    "son": "Haut-parleur",
    "microphone": "Microphone",
    "antenne": "Autre réparation",
    "vibreur": "Autre réparation",
    "bouton(s)": "Autre réparation",
    "lecteur sim": "Autre réparation",
    "autre panne": "Diagnostic",
    "établir un devis": "Diagnostic",
}

IGNORE_BRANDS = {
    "je ne connais pas la marque",
    "prendre un rendez-vous",
    "autres marques",
    "autres",
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


def post_ajax(
    url: str,
    data: dict[str, str],
    referer: str,
    timeout: float = 12.0,
    retries: int = 2,
) -> dict[str, Any] | None:
    """Envoie une requête POST vers le point d'entrée admin-ajax.php."""
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": referer,
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    encoded_data = urllib.parse.urlencode(data).encode("utf-8")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=encoded_data, headers=headers)
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="ignore")
                return json.loads(text)
        except Exception:
            if attempt == retries:
                return None
            time.sleep(0.5 * (attempt + 1))

    return None


def fetch_brands_for_category(ajax_url: str, referer: str, category_name: str) -> list[str]:
    """Récupère la liste des marques pour une catégorie."""
    res = post_ajax(ajax_url, {"action": "rp_fe_get_brands", "c_name": category_name}, referer)
    if not res or not isinstance(res, dict):
        return []
    results = res.get("results", [])
    brands = []
    for b in results:
        b_name = b.get("b_name", "").strip()
        if b_name and b_name.lower() not in IGNORE_BRANDS:
            brands.append(b_name)
    return brands


def fetch_models_for_brand(ajax_url: str, referer: str, category_name: str, brand_name: str) -> list[dict[str, str]]:
    """Récupère la liste des modèles pour une marque et catégorie."""
    res = post_ajax(ajax_url, {"action": "rp_fe_get_models", "c_name": category_name, "b_name": brand_name}, referer)
    if not res or not isinstance(res, dict):
        return []
    results = res.get("results", [])
    models = []
    for m in results:
        # structure: {'A': model_id, 'B': model_name, ...}
        m_name = m.get("B", "").strip()
        m_id = m.get("A", "").strip()
        if m_name:
            models.append({"name": m_name, "id": m_id})
    return models


def fetch_model_repairs(ajax_url: str, referer: str, brand_name: str, model_name: str) -> dict[str, Any] | None:
    """Récupère la grille des prestations et tarifs pour un modèle spécifique."""
    return post_ajax(
        ajax_url,
        {"action": "rp_fe_get_repairs", "b_name": brand_name, "m_name": model_name},
        referer,
    )


def create_repair_row(
    loc_key: str,
    brand: str,
    model: str,
    device_family: str,
    repair_type_raw: str,
    canonical_repair: str,
    price: float,
    warranty: str,
    delay: str,
    scraped_at: str,
) -> dict[str, Any]:
    cfg = LOCATIONS_CONFIG[loc_key]
    price_raw_str = f"{int(price) if price.is_integer() else price} €"
    item_name = f"{repair_type_raw} {brand} {model}".strip()

    model_encoded = urllib.parse.quote(model)
    brand_encoded = urllib.parse.quote(brand)
    source_url = f"{cfg['tarifs_url']}?selected=repairs/{brand_encoded}/{model_encoded}"

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
        "shop_name": cfg["shop_name"],
        "address": cfg["address"],
        "city": cfg["city"],
        "postal_code": cfg["postal_code"],
        "delay": delay or cfg["delay"],
        "warranty": warranty or cfg["default_warranty"],
        "model_sku": "",
        "repair_category": "Réparation en atelier",
        "scraped_at": scraped_at,
    }


def parse_repairs_response(
    rep_data: dict[str, Any],
    loc_key: str,
    brand_clean: str,
    model_name: str,
    device_family: str,
    scraped_at: str,
) -> list[dict[str, Any]]:
    """Parse la réponse API rp_fe_get_repairs et extrait les tarifs simples et multi-gammes."""
    rows: list[dict[str, Any]] = []
    cfg = LOCATIONS_CONFIG[loc_key]

    all_repairs = rep_data.get("limited_repairs", []) + rep_data.get("remaining_repairs", [])
    attrs_db = rep_data.get("repair_attrs_from_db", {})
    if not isinstance(attrs_db, dict):
        attrs_db = {}

    for r in all_repairs:
        if not isinstance(r, dict):
            continue

        rid = str(r.get("r_id", ""))
        r_name = (r.get("r_name") or "").strip()
        if not r_name:
            continue

        # Durée de réparation
        dur = r.get("r_duration", "")
        dur_unit = r.get("r_duration_measure", "")
        delay_str = ""
        if dur:
            unit_fr = "minutes" if "min" in dur_unit else "heures"
            delay_str = f"Réparation en {dur} {unit_fr}"

        # Éligibilité QualiRépar / Badge
        badge = (r.get("r_badge") or "").strip()
        default_w = f"{badge} - {cfg['default_warranty']}" if badge else cfg["default_warranty"]

        attrs = attrs_db.get(rid, [])
        if attrs and isinstance(attrs, list):
            # Déclinaisons multi-gammes (ex: Éco, Original reconditionné, Premium)
            for a in attrs:
                if not isinstance(a, dict):
                    continue
                var_name = (a.get("B") or "").strip()
                p_str = a.get("D", "")
                try:
                    price_val = float(p_str)
                except (ValueError, TypeError):
                    continue

                if price_val <= 0:
                    continue

                var_warranty = (a.get("F") or "").strip() or default_w
                full_raw_name = f"{r_name} ({var_name})" if var_name else r_name
                canon_rep = map_repair_type(r_name)

                row = create_repair_row(
                    loc_key=loc_key,
                    brand=brand_clean,
                    model=model_name,
                    device_family=device_family,
                    repair_type_raw=full_raw_name,
                    canonical_repair=canon_rep,
                    price=price_val,
                    warranty=var_warranty,
                    delay=delay_str,
                    scraped_at=scraped_at,
                )
                rows.append(row)
        else:
            # Prestation à tarif unique direct
            p_str = r.get("r_price", "")
            try:
                price_val = float(p_str)
            except (ValueError, TypeError):
                continue

            if price_val <= 0:
                continue

            canon_rep = map_repair_type(r_name)
            row = create_repair_row(
                loc_key=loc_key,
                brand=brand_clean,
                model=model_name,
                device_family=device_family,
                repair_type_raw=r_name,
                canonical_repair=canon_rep,
                price=price_val,
                warranty=default_w,
                delay=delay_str,
                scraped_at=scraped_at,
            )
            rows.append(row)

    return rows


def scrape_location(
    loc_key: str,
    categories: list[str],
    max_models: int | None = None,
    max_workers: int = 6,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Scrape l'ensemble d'une implantation (Laval ou Nantes) via requêtes concurrentes."""
    cfg = LOCATIONS_CONFIG[loc_key]
    ajax_url = cfg["ajax_url"]
    referer = cfg["tarifs_url"]
    scraped_at = now_iso_utc()

    all_rows: list[dict[str, Any]] = []
    fetch_errors: list[dict[str, Any]] = []

    print(f"\n[{SOURCE_NAME.upper()} - {cfg['city'].upper()}] 🌐 Découverte catalogue : {ajax_url}", flush=True)

    # 1. Collecte de tous les couples (catégorie, marque, modèle)
    tasks: list[dict[str, str]] = []
    for cat_name in categories:
        dev_family = "Smartphone" if "téléphone" in cat_name.lower() else "Tablette"
        brands = fetch_brands_for_category(ajax_url, referer, cat_name)
        print(f"[{SOURCE_NAME.upper()} - {cfg['city'].upper()}] 🏷️  {cat_name} : {len(brands)} marques actives", flush=True)

        for b_raw in brands:
            brand_clean = CANONICAL_BRAND_NAMES.get(b_raw.lower(), b_raw.capitalize())
            models = fetch_models_for_brand(ajax_url, referer, cat_name, b_raw)
            for m in models:
                tasks.append({
                    "cat": cat_name,
                    "dev_family": dev_family,
                    "brand_raw": b_raw,
                    "brand_clean": brand_clean,
                    "model_name": m["name"],
                })

    print(f"[{SOURCE_NAME.upper()} - {cfg['city'].upper()}] 📱 Total modèles identifiés : {len(tasks)} modèles", flush=True)

    if max_models and len(tasks) > max_models:
        tasks = tasks[:max_models]
        print(f"[{SOURCE_NAME.upper()} - {cfg['city'].upper()}] ⚠️ Limitation active : {max_models} modèles", flush=True)

    # 2. Téléchargement concurrent des tarifs
    print(f"[{SOURCE_NAME.upper()} - {cfg['city'].upper()}] ⚡ Extraction concurrente ({max_workers} threads)...", flush=True)

    def _worker(t: dict[str, str]) -> list[dict[str, Any]]:
        rep_data = fetch_model_repairs(ajax_url, referer, t["brand_raw"], t["model_name"])
        if not rep_data or not rep_data.get("status"):
            return []
        return parse_repairs_response(
            rep_data=rep_data,
            loc_key=loc_key,
            brand_clean=t["brand_clean"],
            model_name=t["model_name"],
            device_family=t["dev_family"],
            scraped_at=scraped_at,
        )

    completed_count = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, t): t for t in tasks}
        for future in as_completed(futures):
            t_info = futures[future]
            try:
                model_rows = future.result()
                all_rows.extend(model_rows)
            except Exception as exc:
                fetch_errors.append(fetch_issue(f"{ajax_url}?b={t_info['brand_raw']}&m={t_info['model_name']}", REASON_FETCH_ERROR, str(exc)))
            completed_count += 1
            if completed_count % 100 == 0 or completed_count == len(tasks):
                print(f"  [{cfg['city']}] Progression : {completed_count}/{len(tasks)} modèles ({len(all_rows)} tarifs extraits en {time.time()-t0:.1f}s)", flush=True)

    print(f"[{SOURCE_NAME.upper()} - {cfg['city'].upper()}] ✅ Terminé : {len(all_rows)} tarifs bruts extraits", flush=True)
    return all_rows, fetch_errors


def scrape(
    output_dir: str | Path | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
    location: str = "all",
    include_tablets: bool = True,
    max_workers: int = 6,
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

    locs_to_scrape = ["laval", "nantes"] if location == "all" else [location]
    categories = ["Téléphone"]
    if include_tablets:
        categories.append("Tablette")

    all_rows: list[dict[str, Any]] = []
    for loc_key in locs_to_scrape:
        loc_rows, loc_errors = scrape_location(
            loc_key=loc_key,
            categories=categories,
            max_models=max_models,
            max_workers=max_workers,
        )
        all_rows.extend(loc_rows)
        fetch_errors.extend(loc_errors)

    if not all_rows:
        print(f"[{SOURCE_NAME.upper()}] ❌ Aucun tarif récupéré depuis Repair-ME.", flush=True)
        return []

    # Déduplication
    deduped_rows = dedupe_dict_rows(
        all_rows,
        keys=["model", "repair_type_raw", "price", "shop_name"],
    )

    # Nettoyage standard E-commerce AI V2
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

    # Export des artefacts E-commerce AI V2
    write_jsonl_and_csv(deduped_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(deduped_rows),
        "rows": deduped_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[{SOURCE_NAME.upper()}] ✅ Succès global : {len(deduped_rows)} tarifs exportés dans {out_dir}", flush=True)

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
    parser = argparse.ArgumentParser(description="Repair-ME Scraper (Laval & Nantes)")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport d'exécution")
    parser.add_argument("--max-models", type=int, default=None, help="Nombre max de modèles extraits par atelier")
    parser.add_argument(
        "--location",
        choices=["all", "laval", "nantes"],
        default="all",
        help="Implantation à scraper (défaut: all pour Laval et Nantes)",
    )
    parser.add_argument(
        "--smartphones-only",
        action="store_true",
        help="Restreindre exclusivement aux smartphones (ignorer tablettes)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="Nombre de threads concurrents (défaut: 6)",
    )
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
        location=args.location,
        include_tablets=not args.smartphones_only,
        max_workers=args.workers,
    )


if __name__ == "__main__":
    main()
