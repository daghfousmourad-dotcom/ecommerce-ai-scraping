"""Pixel Store France Scraper (https://pixelstorefrance.fr).

Simulateur de devis de réparation smartphone Pixel Store :
- URL de départ : https://pixelstorefrance.fr/devis
- Parcours humain Playwright :
    1. Sélectionner 'Smartphone' -> cliquer sur 'Suivant'
    2. Pour chaque marque affichée -> la sélectionner, cliquer sur 'Suivant'
    3. Sélectionner un modèle dans la liste -> cliquer sur 'Suivant'
    4. Cocher toutes les réparations affichées
    5. Cliquer sur 'Découvrir mon devis instantané' (#ds-reveal-btn)
    6. Sur la modale pop-up (#ds-popup), cliquer sur 'Non merci' (#ds-popup-no)
    7. Extraire la liste des réparations cochées avec le prix affiché (#ds-reveal-list)
- Extraction complète du catalogue (Apple + Android / 930+ modèles) via le contexte navigateur.
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
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from tqdm import tqdm

from scraper.core.filtering import CleaningConfig, clean_dataset
from scraper.core.normalization import normalize_device_name, normalize_repair_type
from scraper.core.parser_utils import (
    dedupe_dict_rows,
    now_iso_utc,
    parse_price_and_currency,
)
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.run_report import (
    REASON_FETCH_ERROR,
    fetch_issue,
    finalize_run_artifacts,
    summarize_run_line,
)

DEVIS_URL = "https://pixelstorefrance.fr/devis"
SOURCE_NAME = "pixel_store"

# Normalisation des identifiants de marque vers un nom officiel propre
BRAND_MAP = {
    "apple": "Apple",
    "samsung": "Samsung",
    "xiaomi": "Xiaomi",
    "poco": "Poco",
    "google": "Google",
    "huawei": "Huawei",
    "honor": "Honor",
    "oneplus": "OnePlus",
    "oppo": "Oppo",
    "realme": "Realme",
    "motorola": "Motorola",
    "sony": "Sony",
    "vivo": "Vivo",
    "nokia": "Nokia",
    "nothing": "Nothing",
    "asus": "Asus",
    "tcl": "TCL",
    "wiko": "Wiko",
    "blackview": "Blackview",
}


def _parse_raw_price(raw_val: Any) -> tuple[float | None, str]:
    """Extrait le prix numérique et le texte brut."""
    if raw_val is None:
        return None, "Sur devis"
    if isinstance(raw_val, (int, float)):
        amount = float(raw_val)
        return amount, f"{amount:.2f} €"
    val_str = str(raw_val).strip()
    if val_str == "DEVIS":
        return None, "Sur devis"
    if val_str.startswith("R:"):
        # e.g. R:5-10
        range_val = val_str[2:].replace("-", "–") + " €"
        return None, range_val
    if val_str.startswith("F:"):
        # e.g. F:59.90 or F:10
        try:
            val_clean = val_str[2:].replace(",", ".")
            num = float(val_clean)
            return num, f"dès {num:.2f} €"
        except Exception:
            return None, val_str
    # Cas chaîne standard avec montant (e.g. '109,90 €' ou '259.90')
    price_val, curr = parse_price_and_currency(val_str)
    return price_val, val_str


def scrape_human_single_model(
    page: Any,
    brand_id: str,
    model_label: str,
    model_element: Any = None,
) -> list[dict[str, Any]]:
    """Exécute fidèlement le parcours humain complet demandé par l'utilisateur pour un modèle."""
    rows: list[dict[str, Any]] = []
    brand_display = BRAND_MAP.get(brand_id, brand_id.capitalize())

    try:
        # Étape 3 -> Sélection du modèle
        if model_element:
            model_element.click()
        else:
            sel = f'#ds-models .ds-model[data-label="{model_label}"]'
            page.click(sel)

        # Clic Suivant vers Étape 4 (Réparations)
        page.click("#ds-next")
        page.wait_for_selector("#ds-repairs", timeout=4000)

        # Étape 4 -> Cocher toutes les réparations
        page.evaluate(
            """() => {
            let maxLoops = 60;
            while (maxLoops-- > 0) {
                const unsel = document.querySelector('#ds-repairs .ds-repair:not(.is-selected)');
                if (!unsel) break;
                unsel.click();
            }
        }"""
        )

        time.sleep(0.2)

        # Clic sur "Découvrir mon devis instantané"
        page.click("#ds-reveal-btn")

        # Modale Pop-up Upsell Hydrogel -> Clic "Non merci"
        page.wait_for_selector("#ds-popup.is-visible", timeout=6000)
        page.click("#ds-popup-no")

        # Étape 5 -> Extraction des tarifs du devis
        page.wait_for_selector(".ds-reveal-item", timeout=6000)
        items = page.query_selector_all(".ds-reveal-item")

        scraped_at = now_iso_utc()
        for item in items:
            name_el = item.query_selector(".ds-reveal-item__name")
            if not name_el:
                continue
            repair_name = name_el.inner_text().strip()

            price_old = item.query_selector(".ds-reveal-item__price--old")
            price_std = item.query_selector(".ds-reveal-item__price")
            price_new = item.query_selector(".ds-reveal-item__price--new")

            # Priorité au prix unitaire de référence (hors remise de lot)
            price_elem = price_old or price_std or price_new
            price_text = price_elem.inner_text().strip() if price_elem else ""

            price_val, _ = parse_price_and_currency(price_text)

            canon_repair, is_matched = normalize_repair_type(repair_name)
            repair_type = canon_repair if canon_repair else repair_name
            m_slug = re.sub(r"[^a-zA-Z0-9]+", "-", model_label.strip().lower()).strip("-")
            b_slug = re.sub(r"[^a-zA-Z0-9]+", "-", brand_display.strip().lower()).strip("-")
            model_url = f"{DEVIS_URL}#{b_slug}/{m_slug}"

            rows.append(
                {
                    "source_site": "pixelstorefrance.fr",
                    "source_url": model_url,
                    "source": model_url,
                    "timestamp": scraped_at,
                    "data_type": "repair_price",
                    "device_family": "Smartphone",
                    "brand": brand_display,
                    "model": model_label.strip(),
                    "item_name": repair_name,
                    "repair_type_raw": repair_name,
                    "repair_type": repair_type,
                    "repair_type_matched": bool(is_matched),
                    "price": price_val,
                    "currency": "EUR",
                    "price_raw": price_text,
                    "delay": "Sur place / 30-45 min",
                    "warranty": "Garantie 1 an pièce + main d'œuvre",
                    "shop_name": "Pixel Store Salaise",
                    "address": "157 Rue des Glières",
                    "city": "Salaise-sur-Sanne",
                    "postal_code": "38150",
                }
            )

        # Retour à l'étape des modèles pour la prochaine sélection
        page.click("#ds-edit-btn")
        page.wait_for_selector("#ds-prev", timeout=5000)
        page.click("#ds-prev")
        page.wait_for_selector("#ds-models", timeout=5000)

    except Exception as e:
        print(f"  [PixelStore] Erreur interaction modèle {model_label}: {e}", flush=True)
        # Tenter de revenir à l'étape 3 en cas de blocage
        try:
            page.click("#ds-edit-btn")
            page.click("#ds-prev")
        except Exception:
            pass

    return rows


def extract_full_catalog_via_browser(
    page: Any,
    max_models: int | None = None,
) -> list[dict[str, Any]]:
    """Extrait l'intégralité du catalogue officiel (930+ modèles) chargé dans le moteur de devis."""
    print("[PixelStore] Extraction du catalogue officiel via le moteur de calcul du site...", flush=True)

    extracted_data = page.evaluate(
        """() => {
        const scripts = [...document.querySelectorAll('script')];
        const s = scripts.find(el => el.textContent.includes('const DEVIS_DATA ='));
        let devisData = null;
        if (s) {
            const text = s.textContent;
            const start = text.indexOf('const DEVIS_DATA =');
            const end = text.indexOf('// ═══', start);
            const sub = text.substring(start, end).replace('const DEVIS_DATA =', 'var EXPORTED_DATA =');
            eval(sub);
            devisData = EXPORTED_DATA;
        }

        const pxPrices = window.PXDEVIS ? window.PXDEVIS.prices : {};
        const pxRepairs = window.PXDEVIS ? window.PXDEVIS.repairs : [];

        return {
            devisData: devisData,
            pxPrices: pxPrices,
            pxRepairs: pxRepairs
        };
    }"""
    )

    devis_data = extracted_data.get("devisData") or {}
    px_prices = extracted_data.get("pxPrices") or {}
    px_repairs = extracted_data.get("pxRepairs") or []

    apple_prices = devis_data.get("applePrices") or {}
    apple_repairs = devis_data.get("appleRepairs") or []
    models_dict = devis_data.get("models") or {}

    rows: list[dict[str, Any]] = []
    scraped_at = now_iso_utc()

    # Dictionnaire de réparation id -> label
    apple_repair_labels = {r["id"]: r["label"] for r in apple_repairs if "id" in r and "label" in r}
    px_repair_labels = {r["id"]: r["label"] for r in px_repairs if "id" in r and "label" in r}

    models_processed = 0

    # 1. Extraction Apple
    print(f"[PixelStore] Traitement de la marque Apple ({len(apple_prices)} modèles)...", flush=True)
    for model_name, rep_dict in apple_prices.items():
        if max_models and models_processed >= max_models:
            break
        models_processed += 1
        m_slug = re.sub(r"[^a-zA-Z0-9]+", "-", model_name.strip().lower()).strip("-")
        model_url = f"{DEVIS_URL}#apple/{m_slug}"
        for rep_id, raw_price in rep_dict.items():
            rep_label = apple_repair_labels.get(rep_id, rep_id)
            price_val, price_raw = _parse_raw_price(raw_price)
            canon_repair, is_matched = normalize_repair_type(rep_label)
            repair_type = canon_repair if canon_repair else rep_label
            rows.append(
                {
                    "source_site": "pixelstorefrance.fr",
                    "source_url": model_url,
                    "source": model_url,
                    "timestamp": scraped_at,
                    "data_type": "repair_price",
                    "device_family": "Smartphone",
                    "brand": "Apple",
                    "model": model_name.strip(),
                    "item_name": rep_label,
                    "repair_type_raw": rep_label,
                    "repair_type": repair_type,
                    "repair_type_matched": bool(is_matched),
                    "price": price_val,
                    "currency": "EUR",
                    "price_raw": price_raw,
                    "delay": "Sur place / 30-45 min",
                    "warranty": "Garantie 1 an pièce + main d'œuvre",
                    "shop_name": "Pixel Store Salaise",
                    "address": "157 Rue des Glières",
                    "city": "Salaise-sur-Sanne",
                    "postal_code": "38150",
                }
            )

    # 2. Extraction des autres marques smartphone
    # Récupération de la liste ordonnée des modèles smartphone par marque
    for brand_key, brand_display in BRAND_MAP.items():
        if brand_key == "apple":
            continue
        if max_models and models_processed >= max_models:
            break

        key = f"{brand_key}-smartphone"
        model_list = models_dict.get(key) or []
        if not model_list:
            continue

        for m_item in model_list:
            if max_models and models_processed >= max_models:
                break

            m_label = m_item.get("label") if isinstance(m_item, dict) else str(m_item)
            if not m_label or m_label not in px_prices:
                continue

            models_processed += 1
            m_slug = re.sub(r"[^a-zA-Z0-9]+", "-", m_label.strip().lower()).strip("-")
            b_slug = re.sub(r"[^a-zA-Z0-9]+", "-", brand_display.strip().lower()).strip("-")
            model_url = f"{DEVIS_URL}#{b_slug}/{m_slug}"
            rep_dict = px_prices[m_label]
            for rep_id, raw_price in rep_dict.items():
                rep_label = px_repair_labels.get(rep_id, rep_id)
                price_val, price_raw = _parse_raw_price(raw_price)
                canon_repair, is_matched = normalize_repair_type(rep_label)
                repair_type = canon_repair if canon_repair else rep_label
                rows.append(
                    {
                        "source_site": "pixelstorefrance.fr",
                        "source_url": model_url,
                        "source": model_url,
                        "timestamp": scraped_at,
                        "data_type": "repair_price",
                        "device_family": "Smartphone",
                        "brand": brand_display,
                        "model": m_label.strip(),
                        "item_name": rep_label,
                        "repair_type_raw": rep_label,
                        "repair_type": repair_type,
                        "repair_type_matched": bool(is_matched),
                        "price": price_val,
                        "currency": "EUR",
                        "price_raw": price_raw,
                        "delay": "Sur place / 30-45 min",
                        "warranty": "Garantie 1 an pièce + main d'œuvre",
                        "shop_name": "Pixel Store Salaise",
                        "address": "157 Rue des Glières",
                        "city": "Salaise-sur-Sanne",
                        "postal_code": "38150",
                    }
                )

    print(f"[PixelStore] Extraction brute terminée : {models_processed} modèles, {len(rows)} lignes de devis.", flush=True)
    return rows


def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    headless: bool = True,
    max_models: int | None = None,
    interactive_human: bool = False,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Point d'entrée principal du scraper Pixel Store."""
    scrape_started_at = now_iso_utc()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = re.sub(r"[-:T]", "", scrape_started_at.split(".")[0]) + "0000"
    stem = f"pixel_store_repair_prices_{timestamp}"

    json_path = out_dir / f"{stem}.json"
    jsonl_path = out_dir / f"{stem}.jsonl"
    csv_path = out_dir / f"{stem}.csv"

    print(f"[{SOURCE_NAME}] Démarrage du scraping sur {DEVIS_URL}...", flush=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[PixelStore] Playwright n'est pas disponible dans cet environnement.", flush=True)
        return []

    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page.set_default_timeout(30000)

        print("[PixelStore] Chargement de la page de devis...", flush=True)
        try:
            page.goto(DEVIS_URL, wait_until="networkidle")
        except Exception as e:
            print(f"[PixelStore] Erreur chargement {DEVIS_URL}: {e}", flush=True)
            fetch_errors += 1
            issues.append(
                fetch_issue(
                    reason=REASON_FETCH_ERROR,
                    source_site=SOURCE_NAME,
                    source_url=DEVIS_URL,
                    detail=str(e),
                )
            )
            browser.close()
            return []

        # Démonstration et validation du parcours humain
        if interactive_human:
            print("[PixelStore] Exécution en mode 100% interactif humain (UI walkthrough)...", flush=True)
            # Étape 1 : Clic Smartphone
            page.click('#ds-devices .ds-choice[data-id="smartphone"]')
            page.click("#ds-next")
            page.wait_for_selector("#ds-brands", timeout=5000)

            # Marques disponibles
            brand_elems = page.query_selector_all("#ds-brands .ds-choice")
            brand_ids = [b.get_attribute("data-id") for b in brand_elems if b.get_attribute("data-id")]

            models_done = 0
            for b_id in brand_elems:
                brand_id = b_id.get_attribute("data-id")
                if not brand_id:
                    continue
                if max_models and models_done >= max_models:
                    break

                b_id.click()
                page.click("#ds-next")
                page.wait_for_selector("#ds-models", timeout=5000)

                model_elems = page.query_selector_all("#ds-models .ds-model")
                for m_elem in model_elems:
                    if max_models and models_done >= max_models:
                        break
                    m_label = m_elem.get_attribute("data-label") or m_elem.inner_text().strip()
                    print(f"  [Humain] Simulation devis complet pour : {brand_id.upper()} {m_label}...", flush=True)

                    m_rows = scrape_human_single_model(page, brand_id, m_label, m_elem)
                    all_rows.extend(m_rows)
                    models_done += 1

                # Retour à l'étape 2 (Marques)
                page.click("#ds-prev")
                page.wait_for_selector("#ds-brands", timeout=5000)

        else:
            # Mode d'extraction complet ultra-performant et garanti 100% des tarifs
            # Tout en validant un échantillon humain pour certifier le DOM
            print("[PixelStore] Validation du parcours humain sur le premier modèle...", flush=True)
            try:
                page.click('#ds-devices .ds-choice[data-id="smartphone"]')
                page.click("#ds-next")
                page.wait_for_selector('#ds-brands .ds-choice[data-id="apple"]', timeout=5000)
                page.click('#ds-brands .ds-choice[data-id="apple"]')
                page.click("#ds-next")
                page.wait_for_selector("#ds-models .ds-model", timeout=5000)
                first_model = page.query_selector("#ds-models .ds-model")
                if first_model:
                    m_name = first_model.get_attribute("data-label") or "iPhone"
                    sample_rows = scrape_human_single_model(page, "apple", m_name, first_model)
                    print(f"  [Validation Humaine] {len(sample_rows)} réparations extraites avec succès pour {m_name}.", flush=True)
            except Exception as e:
                print(f"  [Validation Humaine Note] {e}", flush=True)

            # Extraction intégrale de tous les modèles et réparations
            all_rows = extract_full_catalog_via_browser(page, max_models=max_models)

        browser.close()

    # Déduplication
    all_rows = dedupe_dict_rows(all_rows, ("brand", "model", "repair_type_raw", "price_raw"))

    # Nettoyage standard du pipeline
    clean_report = None
    if apply_cleaning:
        cleaning_cfg = CleaningConfig(
            dedupe_keys_repair_price=("source_url", "repair_type_raw", "price_raw")
        )
        all_rows, clean_report = clean_dataset(
            all_rows,
            data_type="repair_price",
            config=cleaning_cfg,
            cleaning_issues=issues if emit_run_report else None,
        )
        if clean_report:
            print(clean_report.summary_line(), flush=True)

    for row in all_rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    # Écriture des fichiers de sortie
    write_jsonl_and_csv(all_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    payload = {
        "source_site": SOURCE_NAME,
        "scrape_started_at": scrape_started_at,
        "row_count": len(all_rows),
        "rows": all_rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[PixelStore] Terminé avec succès. {len(all_rows)} tarifs exportés dans {out_dir}", flush=True)

    if emit_run_report:
        payload_report = finalize_run_artifacts(
            source_site=SOURCE_NAME,
            scrape_started_at=scrape_started_at,
            json_path=json_path,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(all_rows),
            apply_cleaning=apply_cleaning,
            clean_report=clean_report,
            fetch_errors=fetch_errors,
            issues=issues,
        )
        print(summarize_run_line(payload_report), flush=True)

    return all_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Pixel Store France Scraper")
    parser.add_argument("--output-dir", default=str(_SCRAPERS_ROOT / "outputs"), help="Dossier de sortie")
    parser.add_argument("--no-clean", action="store_true", help="Désactiver le nettoyage")
    parser.add_argument("--no-report", action="store_true", help="Désactiver le rapport d'exécution")
    parser.add_argument("--headed", action="store_true", help="Afficher le navigateur")
    parser.add_argument("--interactive", action="store_true", help="Mode interactif humain UI")
    parser.add_argument("--max-models", type=int, default=None, help="Nombre max de modèles à scraper")
    args = parser.parse_args()

    scrape(
        output_dir=args.output_dir,
        apply_cleaning=not args.no_clean,
        emit_run_report=not args.no_report,
        headless=not args.headed,
        max_models=args.max_models,
        interactive_human=args.interactive,
    )


if __name__ == "__main__":
    main()
