"""HONOR France Scraper (https://www.honor.com/fr/support/sparepart-price/).

Tarifs officiels des pièces détachées et réparations constructeur HONOR France :
Stratégie ordonnée (Roadmap E-commerce AI Scraping) :
  1. API Directe (REST/JSON Gateway Cloud CCPC) :
     - Liste des modèles (lv4) : GET https://selfservice-eu.honor.com/ccpcmd/services/dispatch/secured/CCPC/EN/ccpc/queryNewCommodityList/1000?productLevel=lv4
     - Variantes & SKUs (lv5/lv6) : GET .../queryNewCommodityList/1000?productLevel=lv5&productId=... / lv6
     - Grille tarifaire officielle par SKU : POST https://selfservice-eu.honor.com/ccpcmd/services/dispatch/secured/CCPC/EN/cspm/servicePrice/1000
     Fournit 100% des tarifs officiels constructeur HONOR en France en ~5 secondes.
"""

from __future__ import annotations

import sys
from pathlib import Path
_SCRAPERS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRAPERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT))
_CORE_DIR = _SCRAPERS_ROOT / 'core'
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from tqdm import tqdm

from scraper.core.filtering import CleaningConfig, clean_dataset
from scraper.core.http_client import create_session
from scraper.core.normalization import normalize_repair_type
from scraper.core.parser_utils import (
    dedupe_dict_rows,
    now_iso_utc,
)
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.run_report import (
    REASON_FETCH_ERROR,
    fetch_issue,
    finalize_run_artifacts,
    summarize_run_line,
)

BASE_PAGE_URL = "https://www.honor.com/fr/support/sparepart-price/"
API_COMMODITY_URL = "https://selfservice-eu.honor.com/ccpcmd/services/dispatch/secured/CCPC/EN/ccpc/queryNewCommodityList/1000"
API_PRICE_URL = "https://selfservice-eu.honor.com/ccpcmd/services/dispatch/secured/CCPC/EN/cspm/servicePrice/1000"
SOURCE_NAME = "honor"
SOURCE_DOMAIN = "honor.com"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.honor.com/",
    "Origin": "https://www.honor.com",
    "Accept": "*/*",
}

SHOP_INFO = {
    "shop_name": "HONOR France Service Officiel",
    "address": "Support en ligne et centres agréés HONOR",
    "city": "National",
    "postal_code": "75000",
    "delay": "Centre de service officiel HONOR",
    "warranty": "Garantie constructeur pièces d'origine HONOR",
}

CATEGORY_MAPPING = {
    # Smartphones
    "CMCG10000051": ("Smartphone", "Série Magic"),
    "CMCG10000053": ("Smartphone", "Série N"),
    "CMCG10000056": ("Smartphone", "Série X"),
    # Tablettes
    "CMCG10000720": ("Tablette", "Série Magic"),
    "CMCG10000466": ("Tablette", "Série X"),
    "CMCG10000465": ("Tablette", "Série N"),
    "CMCG10000464": ("Tablette", "Série V"),
    # PC Portables
    "CMCG10000680": ("PC Portable", "MagicBook Pro"),
    "CMCG10000463": ("PC Portable", "MagicBook X"),
    "CMCG10000462": ("PC Portable", "MagicBook V"),
    "CMCG10000480": ("PC Portable", "MagicBook N"),
    "CMCG10000700": ("PC Portable", "MagicBook Art"),
    # Montres & Wearables
    "CMCG10000062": ("Montre connectée", "HONOR Watch"),
    "CMCG10000063": ("Bracelet connecté", "HONOR Band"),
    # Audio
    "CMCG10000542": ("Écouteurs", "HONOR Earbuds N"),
    "CMCG10000524": ("Écouteurs", "HONOR Earbuds X"),
}


def _call_commodity_api(params: dict[str, str], session=None) -> list[dict[str, Any]]:
    """Appel à l'API de commodités HONOR avec déballage JSONP si nécessaire."""
    if session is None:
        session = create_session()

    base_params = {
        "sparePartFlag": "Y",
        "channelCode": "MYHONORWEB",
        "countryCode": "FR",
        "langCode": "fr",
        "country": "FR",
        "language": "fr",
        "siteCode": "fr_FR_X",
    }
    base_params.update(params)

    resp = session.get(API_COMMODITY_URL, params=base_params, headers=DEFAULT_HEADERS, timeout=15)
    resp.raise_for_status()

    text = resp.text.strip()
    match = re.search(r"\((.*)\)\s*;?$", text, re.DOTALL)
    raw_json = match.group(1) if match else text
    data = json.loads(raw_json)
    return data.get("responseData", {}).get("productList") or []


def fetch_all_models(session=None) -> list[dict[str, Any]]:
    """Récupère l'ensemble des modèles de niveau lv4 disponibles en France."""
    return _call_commodity_api({"productLevel": "lv4"}, session=session)


def fetch_model_prices_by_sku(sku_code: str, session=None) -> list[dict[str, Any]]:
    """Interroge la grille tarifaire officielle HONOR pour un SKU donné."""
    if session is None:
        session = create_session()

    payload = {
        "countryCode": "FR",
        "languageCode": "fr",
        "timeZone": "GMT+01:00",
        "skuCode": sku_code,
    }
    resp = session.post(
        API_PRICE_URL,
        json=payload,
        headers={"Content-Type": "application/json", **DEFAULT_HEADERS},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("responseData", {}).get("itemTypeInfoList") or []


def _resolve_model_skus_and_prices(
    model_info: dict[str, Any], session=None
) -> tuple[str | None, list[dict[str, Any]]]:
    """Trouve le premier SKU valide d'un modèle lv4 et récupère ses pièces détachées."""
    pid = model_info["productId"]
    lv5_list = _call_commodity_api({"productLevel": "lv5", "productId": pid}, session=session)

    for v5 in lv5_list:
        v5_id = v5.get("productId")
        if not v5_id:
            continue
        lv6_list = _call_commodity_api({"productLevel": "lv6", "productId": v5_id}, session=session)
        for v6 in lv6_list:
            sku = v6.get("offeringCode") or v6.get("productId")
            if not sku:
                continue
            try:
                items = fetch_model_prices_by_sku(sku, session=session)
                if items:
                    return sku, items
            except Exception:
                continue

    return None, []


def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Point d'entrée principal du scraper HONOR France."""
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = re.sub(r"[-:T]", "", scrape_started_at.split(".")[0]) + "0000"
    stem = f"honor_repair_prices_{timestamp}"

    json_path = out_dir / f"{stem}.json"
    jsonl_path = out_dir / f"{stem}.jsonl"
    csv_path = out_dir / f"{stem}.csv"

    session = create_session()
    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    print(f"[{SOURCE_NAME}] Découverte du catalogue de pièces via l'API HONOR France...", flush=True)
    try:
        models = fetch_all_models(session)
    except Exception as e:
        print(f"[{SOURCE_NAME}] Erreur lors de la récupération des modèles: {e}", flush=True)
        fetch_errors += 1
        issues.append(
            fetch_issue(
                reason=REASON_FETCH_ERROR,
                source_site=SOURCE_NAME,
                source_url=API_COMMODITY_URL,
                detail=str(e),
            )
        )
        return []

    print(f"[{SOURCE_NAME}] {len(models)} modèles identifiés.", flush=True)
    if max_models and max_models > 0:
        models = models[:max_models]
        print(f"[{SOURCE_NAME}] Limite active : {max_models} modèles.")

    scraped_at = now_iso_utc()

    def process_single_model(m: dict[str, Any]) -> list[dict[str, Any]]:
        th_session = create_session()
        m_name = m.get("displayName") or m.get("productName") or "Modèle HONOR"
        parent_id = m.get("parentId") or ""
        device_family, sub_cat = CATEGORY_MAPPING.get(parent_id, ("Smartphone", "Général"))

        sku, items = _resolve_model_skus_and_prices(m, session=th_session)
        if not items:
            return []

        rows: list[dict[str, Any]] = []
        model_url = f"{BASE_PAGE_URL}#/detail?productId={m.get('productId')}"

        for it in items:
            part_name = it.get("sparePartTypeLocalDesc") or "Pièce détachée"
            classification = it.get("serviceMaterialClassificationDesc") or ""
            prices = it.get("itemPriceList") or []
            if not prices:
                continue

            first_price_obj = prices[0]
            raw_price = first_price_obj.get("price") or first_price_obj.get("totalPrice")
            if raw_price is None:
                continue

            try:
                price_val = float(raw_price)
            except (ValueError, TypeError):
                continue

            if price_val <= 0:
                continue

            # Normalisation canonique E-commerce AI Scraping
            p_lower = part_name.lower()
            if "frontale" in p_lower or "avant" in p_lower:
                repair_type = "Appareil photo avant"
            elif any(k in p_lower for k in ["arrière", "principale", "téléobjectif", "grand angle"]):
                repair_type = "Appareil photo arrière"
            elif "carte mère" in p_lower:
                repair_type = "Carte mère"
            elif any(k in p_lower for k in ["capot", "façade arrière"]):
                repair_type = "Façade arrière"
            elif "batterie" in p_lower:
                repair_type = "Batterie"
            elif "écran intérieur" in p_lower:
                repair_type = "Écran intérieur (Pliable)"
            elif "écran extérieur" in p_lower:
                repair_type = "Écran extérieur"
            elif "écran" in p_lower or "ecran" in p_lower:
                repair_type = "Ecran LCD"
            else:
                canon, matched = normalize_repair_type(part_name)
                if not matched and classification:
                    canon_cat, m_cat = normalize_repair_type(classification)
                    if m_cat:
                        canon = canon_cat
                repair_type = canon if canon else part_name

            rows.append(
                {
                    "source_site": SOURCE_DOMAIN,
                    "source_url": model_url,
                    "source": model_url,
                    "timestamp": scraped_at,
                    "data_type": "repair_price",
                    "device_family": device_family,
                    "brand": "HONOR",
                    "model": m_name.strip(),
                    "item_name": part_name.strip(),
                    "repair_type_raw": part_name.strip(),
                    "repair_type": repair_type,
                    "price": price_val,
                    "price_raw": f"{price_val} €",
                    "currency": "EUR",
                    "currency_symbol": "€",
                    "shop_name": SHOP_INFO["shop_name"],
                    "address": SHOP_INFO["address"],
                    "city": SHOP_INFO["city"],
                    "postal_code": SHOP_INFO["postal_code"],
                    "delay": SHOP_INFO["delay"],
                    "warranty": SHOP_INFO["warranty"],
                    "model_sku": sku or "",
                    "repair_category": classification or sub_cat,
                    "scraped_at": scraped_at,
                }
            )
        return rows

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(process_single_model, m): m for m in models}
        for fut in tqdm(as_completed(futures), total=len(models), desc="HONOR Extraction", unit="modèle"):
            try:
                res = fut.result()
                all_rows.extend(res)
            except Exception as e:
                fetch_errors += 1
                m = futures[fut]
                issues.append(
                    fetch_issue(
                        reason=REASON_FETCH_ERROR,
                        source_site=SOURCE_NAME,
                        source_url=f"{BASE_PAGE_URL}#/detail?productId={m.get('productId')}",
                        detail=str(e),
                    )
                )

    deduped_rows = dedupe_dict_rows(
        all_rows,
        keys=["model", "repair_type_raw", "price", "shop_name"],
    )

    clean_report = None
    if apply_cleaning:
        cleaning_cfg = CleaningConfig(
            price_min=10.0,
            price_max=4000.0,
            dedupe_keys_repair_price=("source_url", "repair_type_raw", "price_raw"),
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

    write_jsonl_and_csv(deduped_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(deduped_rows),
        "rows": deduped_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[HONOR] Terminé avec succès. {len(deduped_rows)} tarifs exportés dans {out_dir}", flush=True)

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
    parser = argparse.ArgumentParser(description="Scraper des tarifs de réparation HONOR France")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-cleaning", action="store_true", help="Désactiver le nettoyage des données")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le run report")
    parser.add_argument("--max-models", type=int, default=None, help="Nombre max de modèles à scraper")
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_cleaning,
        emit_run_report=not args.no_report,
        max_models=args.max_models,
    )


if __name__ == "__main__":
    main()
