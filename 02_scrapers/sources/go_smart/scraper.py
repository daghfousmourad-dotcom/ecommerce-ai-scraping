"""GO SMART Boulogne Scraper (https://go-smart.fr).

Atelier de réparation smartphones et multimédia à Boulogne-Billancourt (92) :
- 270 Boulevard Jean Jaurès, 92100 Boulogne-Billancourt
- Stratégie d'extraction optimale (Stratégie 1 - Interception API) :
    1. Navigation Playwright vers https://go-smart.fr/rendez-vous?appareil=smartphone avec User-Agent standard
    2. Interception des flux API internes officiels :
       - /api/data/catalogue (Catalogue des appareils et marques)
       - /api/data/tarifs (Base de données exhaustive de tous les tarifs par modèle et prestation)
    3. Traitement spécifique des smartphones (926 modèles sur 17 marques) :
       Extraction de toutes les déclinaisons unitaires (y compris les gammes pop-up : Écran ECO/SMART/GOLD,
       Batteries Originale/Compatible, Vitres arrières, connecteurs, caméras, etc.)
    4. Exclusion stricte des estimations globales ou paniers de devis
    5. Normalisation canonique et standardisation selon le schéma d'artefacts E-commerce AI V2.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRAPERS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRAPERS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT))
if str(_SCRAPERS_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_SCRAPERS_ROOT.parent))
_CORE_DIR = _SCRAPERS_ROOT / "core"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import argparse
import asyncio
import json
import urllib.parse
from typing import Any

from playwright.async_api import async_playwright

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

BASE_URL = "https://go-smart.fr"
RENDEZ_VOUS_URL = "https://go-smart.fr/rendez-vous?appareil=smartphone"
SOURCE_NAME = "go_smart"
SOURCE_DOMAIN = "go-smart.fr"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

SHOP_INFO = {
    "shop_name": "GO SMART Boulogne",
    "address": "270 Bd Jean Jaurès",
    "city": "Boulogne-Billancourt",
    "postal_code": "92100",
    "delay": "Réparation en 20 minutes",
    "warranty": "Garantie pièces et main-d'œuvre",
}

CANONICAL_REPAIR_MAP = {
    "écran eco": "Ecran LCD",
    "écran smart": "Ecran LCD",
    "écran gold": "Ecran LCD",
    "écran lcd": "Ecran LCD",
    "écran oled": "Ecran LCD",
    "écran original": "Ecran LCD",
    "écran": "Ecran LCD",
    "batterie": "Batterie",
    "batterie compatible": "Batterie",
    "batterie originale": "Batterie",
    "vitre arrière": "Façade arrière",
    "vitre arrière compatible": "Façade arrière",
    "vitre arrière originale": "Façade arrière",
    "façade arrière": "Façade arrière",
    "connecteur de charge": "Connecteur de charge",
    "caméra avant": "Caméra avant",
    "caméra arrière": "Caméra arrière",
    "lentille caméra": "Lentille caméra",
    "haut-parleur": "Haut-parleur",
    "écouteur interne": "Écouteur interne",
    "châssis": "Châssis",
    "désoxydation": "Désoxydation",
    "micro": "Micro",
    "boutons (volume / power)": "Bouton volume/power",
}


def map_repair_type(repair_raw: str) -> str:
    """Normalise la prestation vers l'une des catégories canoniques E-commerce AI Scraping."""
    raw_lower = repair_raw.strip().lower()
    if raw_lower in CANONICAL_REPAIR_MAP:
        return CANONICAL_REPAIR_MAP[raw_lower]
    return normalize_repair_type(repair_raw) or repair_raw.strip()


async def fetch_api_data_via_browser() -> tuple[dict[str, Any], dict[str, Any]]:
    """Ouvre la page rendez-vous via Playwright et intercepte les flux API officiels."""
    tarifs_data: dict[str, Any] = {}
    catalogue_data: dict[str, Any] = {}

    print(f"[{SOURCE_NAME.upper()}] Lancement du navigateur headless pour franchir Cloudflare et intercepter l'API...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        async def on_response(response):
            nonlocal tarifs_data, catalogue_data
            url = response.url
            if "/api/data/tarifs" in url:
                try:
                    tarifs_data = await response.json()
                    print(f"[{SOURCE_NAME.upper()}] ✅ Flux /api/data/tarifs intercepté ({len(tarifs_data)} clés d'appareils).")
                except Exception as e:
                    print(f"[{SOURCE_NAME.upper()}] Erreur décodage JSON tarifs : {e}")
            elif "/api/data/catalogue" in url:
                try:
                    catalogue_data = await response.json()
                    print(f"[{SOURCE_NAME.upper()}] ✅ Flux /api/data/catalogue intercepté.")
                except Exception as e:
                    print(f"[{SOURCE_NAME.upper()}] Erreur décodage JSON catalogue : {e}")

        page.on("response", on_response)

        try:
            await page.goto(RENDEZ_VOUS_URL, wait_until="networkidle", timeout=45000)
        except Exception as e:
            print(f"[{SOURCE_NAME.upper()}] Warning chargement page : {e}")

        # Attendre un court instant si le réseau n'a pas encore fini
        for _ in range(10):
            if tarifs_data:
                break
            await asyncio.sleep(0.5)

        await browser.close()

    return tarifs_data, catalogue_data


def parse_smartphone_tarifs(tarifs: dict[str, Any], max_models: int | None = None) -> list[dict[str, Any]]:
    """Parse l'ensemble des tarifs smartphones issus du flux de GO SMART."""
    rows: list[dict[str, Any]] = []
    scraped_at = now_iso_utc()

    # Clés smartphones uniquement (smartphone|Marque|Modèle)
    smartphone_keys = [k for k in tarifs.keys() if k.startswith("smartphone|")]
    if max_models:
        smartphone_keys = smartphone_keys[:max_models]

    print(f"[{SOURCE_NAME.upper()}] Extraction de {len(smartphone_keys)} modèles smartphones...")

    for key in smartphone_keys:
        parts = key.split("|")
        if len(parts) < 3:
            continue
        _, brand, model = parts[0], parts[1].strip(), parts[2].strip()
        repair_dict = tarifs.get(key)
        if not isinstance(repair_dict, dict):
            continue

        model_url = f"{RENDEZ_VOUS_URL}&marque={urllib.parse.quote(brand)}&modele={urllib.parse.quote(model)}"

        for repair_raw, price_val in repair_dict.items():
            if price_val is None:
                continue
            try:
                price = float(price_val)
            except (ValueError, TypeError):
                continue

            # Écarter les devis gratuits ou tarifs nuls
            if price <= 0:
                continue

            repair_clean = repair_raw.strip()
            canonical_repair = map_repair_type(repair_clean)
            price_raw_str = f"{int(price) if price.is_integer() else price} €"
            item_name = f"{repair_clean} {brand} {model}".strip()

            row = {
                "source_site": SOURCE_DOMAIN,
                "source_url": model_url,
                "source": model_url,
                "timestamp": scraped_at,
                "data_type": "repair_price",
                "device_family": "Smartphone",
                "brand": brand,
                "model": model,
                "item_name": item_name,
                "repair_type_raw": repair_clean,
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
                "warranty": SHOP_INFO["warranty"],
                "model_sku": "",
                "repair_category": "Réparation en atelier",
                "scraped_at": scraped_at,
            }
            rows.append(row)

    return rows


def scrape(
    output_dir: str | Path | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    """Point d'entrée principal du scraper GO SMART Boulogne."""
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir) if output_dir else _SCRAPERS_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp_slug = scrape_started_at.replace("-", "").replace(":", "").replace(".", "")
    prefix = f"go_smart_repair_prices_{timestamp_slug}"
    jsonl_path = out_dir / f"{prefix}.jsonl"
    csv_path = out_dir / f"{prefix}.csv"
    json_path = out_dir / f"{prefix}.json"

    fetch_errors: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    # 1. Récupération des données API via navigateur headless
    try:
        tarifs_data, catalogue_data = asyncio.run(fetch_api_data_via_browser())
    except Exception as exc:
        fetch_errors.append(fetch_issue(RENDEZ_VOUS_URL, REASON_FETCH_ERROR, str(exc)))
        tarifs_data, catalogue_data = {}, {}

    if not tarifs_data:
        print(f"[{SOURCE_NAME.upper()}] ❌ Impossible d'intercepter les tarifs de GO SMART.", flush=True)
        return []

    # 2. Parsing et extraction des réparations smartphones
    all_rows = parse_smartphone_tarifs(tarifs_data, max_models=max_models)

    # 3. Déduplication
    deduped_rows = dedupe_dict_rows(
        all_rows,
        keys=["model", "repair_type_raw", "price", "shop_name"],
    )

    # 4. Nettoyage standard E-commerce AI V2
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

    # 5. Export des artefacts standard
    write_jsonl_and_csv(deduped_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(deduped_rows),
        "rows": deduped_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[GO SMART] Terminé avec succès : {len(deduped_rows)} tarifs exportés dans {out_dir}", flush=True)

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
    parser = argparse.ArgumentParser(description="GO SMART Boulogne Scraper (Boulogne-Billancourt)")
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
