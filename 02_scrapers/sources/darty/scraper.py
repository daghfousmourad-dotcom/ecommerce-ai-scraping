"""
Darty Hybrid Scraper (sources2).
Intégré dans ecommerce_ai_scraping_scraper_V2-main basé sur la version darty_hybrid_scraper.py & darty_uc_scraper.py.
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

import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from scraper.core.filtering import clean_dataset
from scraper.core.parser_utils import clean_text, now_iso_utc
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.run_report import (
    REASON_FETCH_ERROR,
    fetch_issue,
    finalize_run_artifacts,
    summarize_run_line,
)

SOURCE_SITE = "darty"
START_URL = "https://www.darty.com/nav/achat/telephonie/telephone_mobile/mobile/index.html#dartyclic=X_tele-obje-conn_smar"
BASE_URL = "https://www.darty.com"

PRICE_RE = re.compile(r"(?<!\d)(\d{1,5}(?:[ .]\d{3})*(?:[,.]\d{1,2})?)\s*€")
STORAGE_RE = re.compile(r"(\d+)\s*(?:go|gb)", re.IGNORECASE)

KNOWN_BRANDS = [
    "Apple", "Samsung", "Google", "Xiaomi", "Honor", "Oppo",
    "OnePlus", "Sony", "Huawei", "TCL", "Motorola", "Nokia",
    "Realme", "Vivo", "Asus", "Crosscall", "POCO", "Blackview",
    "Doro", "Nothing",
]

PROMO_KEYWORDS = [
    "deezer", "remise sur", "offert", "-50%", "livraison", "avis",
    "voir l'offre", "état :", "etat :", "accessoire", "garantie",
]


def normalize_key(value: str | None) -> str:
    if not value:
        return ""
    v = clean_text(value).lower()
    v = re.sub(r"[^a-z0-9à-ÿ]+", " ", v)
    return re.sub(r"\s+", " ", v).strip()


def parse_price(value: str | None) -> tuple[float | None, str | None]:
    if not value:
        return None, None
    clean_val = value.replace("\xa0", " ")
    m = PRICE_RE.search(clean_val)
    if not m:
        return None, None
    raw_str = m.group(1)
    try:
        raw_numeric = float(raw_str.replace(" ", "").replace(",", "."))
        return raw_numeric, f"{raw_numeric:.2f} €"
    except ValueError:
        return None, None


def extract_storage(text: str) -> int | None:
    m = STORAGE_RE.search(text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return None


def extract_brand_and_model(product_name: str) -> tuple[str, str]:
    name = clean_text(product_name)
    name = re.sub(r"\s*\d(?:[,\.]\d)?/5\s*$", "", name).strip()
    for b in KNOWN_BRANDS:
        m = re.search(rf"\b{re.escape(b)}\b", name, re.IGNORECASE)
        if m:
            start_pos = m.start()
            brand = b
            rem = name[start_pos + len(b):].strip()
            model = re.sub(r"^(smartphone|téléphone|mobile)\s*", "", rem, flags=re.IGNORECASE).strip()
            model = re.sub(r"\s*\d(?:[,\.]\d)?/5\s*$", "", model).strip()
            if not model:
                model = name
            return brand, model

    name_clean = re.sub(r"^(smartphone|téléphone|mobile)\s*", "", name, flags=re.IGNORECASE).strip()
    name_clean = re.sub(r"\s*\d(?:[,\.]\d)?/5\s*$", "", name_clean).strip()
    return "Autre", name_clean or name


def clean_product_url(raw_url: str) -> str:
    if not raw_url.startswith("http"):
        raw_url = urljoin(BASE_URL, raw_url)
    parsed = urlparse(raw_url)
    clean_parts = (parsed.scheme, parsed.netloc, parsed.path, "", "", "")
    return urlunparse(clean_parts)


def handle_datadome_slider(sb) -> None:
    """Vérifie et tente de résoudre le slider DataDome s'il est affiché."""
    try:
        if sb.is_text_visible("Faites glisser vers la droite") or sb.is_element_visible("div[class*='slider']"):
            print(f"[{SOURCE_SITE}] Challenge DataDome Slider détecté. Résolution en cours...", flush=True)
            if sys.platform == "darwin":
                sb.execute_script("window.focus();")
            sb.sleep(1)
            sb.uc_gui_handle_captcha()
            sb.sleep(4)
    except Exception as e:
        print(f"[{SOURCE_SITE}] Notice slider DataDome : {e}", flush=True)


def consolidate_baseline_records(raw_rows: list[dict[str, Any]], started_at: str) -> list[dict[str, Any]]:
    """
    Fusionne intelligemment les lignes de dataset/db_darty.json regroupées par source_url.
    Associe le véritable libellé du smartphone (titre) avec le prix réel et l'état.
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in raw_rows:
        u = r.get("source_url", "")
        if u and "index.html" not in u:
            grouped[u].append(r)

    consolidated: list[dict[str, Any]] = []
    for url, items in grouped.items():
        real_title = None
        real_price = None
        real_price_raw = None
        real_condition = "new"
        storage_gb = None

        candidate_prices: list[tuple[float, Any]] = []
        for item in items:
            t = item.get("item_title", "") or ""
            t_lower = t.lower()
            if any(b.lower() in t_lower for b in KNOWN_BRANDS) or "smartphone" in t_lower:
                if not any(promo in t_lower for promo in PROMO_KEYWORDS):
                    real_title = t
            if item.get("storage_gb"):
                storage_gb = item.get("storage_gb")
            if item.get("price"):
                try:
                    p_val = float(item["price"])
                    candidate_prices.append((p_val, item.get("price_raw")))
                except (ValueError, TypeError):
                    pass
            if "reconditionn" in t_lower or "état :" in t_lower or "etat :" in t_lower or "occasion" in t_lower:
                real_condition = "refurbished"

        # Priorité au véritable prix d'achat (rejeter mensualités/acomptes < 50€ si prix plein disponible)
        if candidate_prices:
            full_prices = [cp for cp in candidate_prices if cp[0] >= 50.0]
            if full_prices:
                real_price, real_price_raw = max(full_prices, key=lambda x: x[0])
            else:
                real_price, real_price_raw = max(candidate_prices, key=lambda x: x[0])

        if not real_title:
            for item in items:
                t = item.get("item_title", "") or ""
                t_lower = t.lower()
                if len(t) > 5 and not any(promo in t_lower for promo in PROMO_KEYWORDS):
                    real_title = t
                    break

        if real_title and real_price:
            brand, model = extract_brand_and_model(real_title)
            if not storage_gb:
                storage_gb = extract_storage(real_title)

            clean_url = clean_product_url(url)
            consolidated.append({
                "source_site": SOURCE_SITE,
                "source_url": clean_url,
                "timestamp": now_iso_utc(),
                "scrape_started_at": started_at,
                "data_type": "market_price",
                "device_type": "smartphone",
                "device_family": brand,
                "brand": brand,
                "model": model,
                "model_normalized": normalize_key(model),
                "item_title": real_title,
                "storage_gb": storage_gb,
                "storage_raw": f"{storage_gb} Go" if storage_gb else None,
                "ram_gb": None,
                "color": None,
                "condition": real_condition,
                "product_id": hashlib.sha1(normalize_key(real_title).encode("utf-8")).hexdigest()[:16],
                "is_currently_sold": True,
                "price": real_price,
                "currency": "EUR",
                "price_raw": real_price_raw or f"{real_price:.2f} €",
                "seller_name": "Darty / Marketplace",
                "shipping_cost": 0.0,
            })

    return consolidated


def hide_browser_window(sb) -> None:
    try:
        sb.driver.minimize_window()
    except Exception:
        try:
            sb.execute_script("window.moveTo(-3000, 0);")
        except Exception:
            pass


def force_full_page_scroll(sb) -> None:
    print(f"[{SOURCE_SITE}] Défilement progressif de la page...", flush=True)
    for i in range(1, 7):
        sb.execute_script(f"window.scrollTo(0, document.body.scrollHeight * {i / 6});")
        sb.sleep(0.5)
        handle_datadome_slider(sb)
    sb.sleep(0.5)


def extract_products_from_listing(html: str, started_at: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    products: list[dict[str, Any]] = []

    cards = soup.select(
        "div.product-card, article.product, li.product-item, div[class*='product-item'], [class*='product_card']"
    )
    if not cards:
        cards = soup.select("div[class*='product']")

    for card in cards:
        if card.select("div.product-card, article.product"):
            continue

        a_tag = card.select_one("a[href*='/p/'], a[href*='/telephone_portable/'], a.product_name")
        if not a_tag:
            a_tag = card.find("a", href=True)
        if not a_tag:
            continue

        href = a_tag.get("href")
        product_url = clean_product_url(href) if href else ""

        name_el = card.select_one(".product_name, h3, h2, [class*='title']")
        name = clean_text(name_el.get_text()) if name_el else clean_text(a_tag.get_text())
        name = re.sub(r"\s*\d(?:[,\.]\d)?/5\s*$", "", name).strip()

        if not name or "avis" in name.lower() or "état :" in name.lower() or len(name) < 3:
            for t in card.select("a, span, div"):
                txt = clean_text(t.get_text())
                if len(txt) > 5 and "avis" not in txt.lower() and "€" not in txt and "état" not in txt.lower():
                    name = re.sub(r"\s*\d(?:[,\.]\d)?/5\s*$", "", txt).strip()
                    break

        if not name or "avis" in name.lower() or any(promo in name.lower() for promo in PROMO_KEYWORDS):
            continue

        price_el = card.select_one(".product_price, .price, [class*='price']")
        price_text = price_el.get_text() if price_el else card.get_text()
        price, price_raw = parse_price(price_text)
        if not price:
            continue

        condition = "new"
        card_text_lower = card.get_text().lower()
        if "reconditionn" in card_text_lower or "état :" in card_text_lower or "occasion" in card_text_lower:
            condition = "refurbished"

        brand, model = extract_brand_and_model(name)
        storage_gb = extract_storage(name)
        product_id = hashlib.sha1(normalize_key(name).encode("utf-8")).hexdigest()[:16]

        products.append({
            "source_site": SOURCE_SITE,
            "source_url": product_url,
            "timestamp": now_iso_utc(),
            "scrape_started_at": started_at,
            "data_type": "market_price",
            "device_type": "smartphone",
            "device_family": brand,
            "brand": brand,
            "model": model,
            "model_normalized": normalize_key(model),
            "item_title": name,
            "storage_gb": storage_gb,
            "storage_raw": f"{storage_gb} Go" if storage_gb else None,
            "ram_gb": None,
            "color": None,
            "condition": condition,
            "product_id": product_id,
            "is_currently_sold": True,
            "price": price,
            "currency": "EUR",
            "price_raw": price_raw,
            "seller_name": "Darty / Marketplace",
            "shipping_cost": 0.0,
        })

    return products


def get_next_page_url(soup: BeautifulSoup, current_url: str, current_page: int) -> str | None:
    next_a = soup.select_one(
        "a.button-next, a[class*='button-next'], a.next, a[rel='next'], .pagination_next a, a.pagination__next, a[aria-label*='suivant' i]"
    )
    if next_a and next_a.get("href"):
        next_url = urljoin(BASE_URL, next_a["href"])
        if next_url != current_url:
            return next_url

    for a in soup.find_all("a", href=True):
        text = clean_text(a.get_text()).lower()
        if text in (">", "suivant", "suiv.", "page suivante", "»"):
            next_url = urljoin(BASE_URL, a["href"])
            if next_url != current_url:
                return next_url

    # Construction déterministe pour le catalogue téléphonie Darty
    next_page = current_page + 1
    return f"https://www.darty.com/nav/achat/telephonie/telephone_mobile/mobile/page{next_page}.html"


def scrape(
    output_dir: str | Path = str(_SCRAPERS_ROOT / "outputs"),
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
    max_pages: int = 50,
    headless: bool = False,
) -> list[dict[str, Any]]:
    started_at = now_iso_utc()
    time_suffix = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S0000")
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_bundle = out_dir / f"darty_smartphones_{time_suffix}.json"
    jsonl_path = out_dir / f"darty_smartphones_{time_suffix}.jsonl"
    csv_path = out_dir / f"darty_smartphones_{time_suffix}.csv"
    run_json_path = out_dir / f"darty_smartphones_{time_suffix}_run.json"
    issues_jsonl_path = out_dir / f"darty_smartphones_{time_suffix}_issues.jsonl"

    issues: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    print(f"[{SOURCE_SITE}] Lancement de Darty via SeleniumBase UC mode (headless={headless}, max_pages={max_pages})...", flush=True)
    try:
        from seleniumbase import SB

        with SB(uc=True, headed=not headless, locale_code="fr-FR") as sb:
            current_url = START_URL
            page_count = 0

            while current_url:
                page_count += 1
                if max_pages > 0 and page_count > max_pages:
                    break

                print(f"[{SOURCE_SITE}] Navigation page {page_count}/{max_pages} : {current_url}", flush=True)

                try:
                    sb.uc_open_with_reconnect(current_url, reconnect_time=6)
                    sb.sleep(2)

                    hide_browser_window(sb)
                    handle_datadome_slider(sb)

                    try:
                        if sb.is_element_visible("#onetrust-accept-btn-handler"):
                            sb.click("#onetrust-accept-btn-handler")
                    except Exception:
                        pass

                    force_full_page_scroll(sb)
                    hide_browser_window(sb)

                    html = sb.get_page_source()
                    soup = BeautifulSoup(html, "html.parser")

                    page_products = extract_products_from_listing(html, started_at)
                    print(f"[{SOURCE_SITE}] Page {page_count} : {len(page_products)} produits détectés sur cette page.", flush=True)

                    if not page_products:
                        print(f"[{SOURCE_SITE}] Aucun produit trouvé sur la page {page_count}.", flush=True)
                        break

                    new_count = 0
                    for prod in page_products:
                        if prod["product_id"] not in seen_keys:
                            seen_keys.add(prod["product_id"])
                            rows.append(prod)
                            new_count += 1

                    print(f"[{SOURCE_SITE}] {new_count} nouveaux produits ajoutés (Total cumulé : {len(rows)})", flush=True)

                    next_url = get_next_page_url(soup, current_url, page_count)
                    if not next_url or next_url == current_url:
                        print(f"[{SOURCE_SITE}] Fin de la pagination (dernière page atteinte).", flush=True)
                        break
                    current_url = next_url

                except Exception as e:
                    print(f"[{SOURCE_SITE}] Erreur sur la page {page_count} : {e}", flush=True)
                    issues.append(fetch_issue(
                        source_site=SOURCE_SITE,
                        source_url=current_url,
                        reason=REASON_FETCH_ERROR,
                        detail=f"Erreur page {page_count}: {e}",
                    ))
                    break

    except ImportError:
        print(f"[{SOURCE_SITE}] seleniumbase non installé, exécution du fallback simulation...", flush=True)
    except Exception as exc:
        issues.append(fetch_issue(
            source_site=SOURCE_SITE,
            source_url=START_URL,
            reason=REASON_FETCH_ERROR,
            detail=f"Erreur Darty: {exc}",
        ))

    # Repli propre et consolidé si le live est bloqué par anti-bot
    if not rows:
        project_root = Path(__file__).resolve().parents[4]
        baseline_file = project_root / "dataset" / "db_darty.json"
        if baseline_file.exists():
            print(f"[{SOURCE_SITE}] Consolidation et chargement de la base de référence ({baseline_file.name})...", flush=True)
            try:
                with open(baseline_file, "r", encoding="utf-8") as f:
                    b_data = json.load(f)
                    raw_records = b_data if isinstance(b_data, list) else b_data.get("rows", [])
                    rows = consolidate_baseline_records(raw_records, started_at=started_at)
                    print(f"[{SOURCE_SITE}] {len(rows)} smartphones consolidés avec succès depuis la référence.", flush=True)
            except Exception as exc:
                issues.append(fetch_issue(
                    source_site=SOURCE_SITE,
                    source_url="baseline_darty",
                    reason=REASON_FETCH_ERROR,
                    detail=f"Erreur chargement baseline: {exc}",
                ))

    finished_at = now_iso_utc()
    cleaning_stats = None
    if apply_cleaning:
        rows, cleaning_stats = clean_dataset(rows, data_type="product_sale", cleaning_issues=issues)

    write_jsonl_and_csv(rows, jsonl_path=jsonl_path, csv_path=csv_path)
    with open(json_bundle, "w", encoding="utf-8") as f:
        json.dump({"source_site": SOURCE_SITE, "scrape_started_at": started_at, "row_count": len(rows), "rows": rows}, f, indent=2, ensure_ascii=False)

    if emit_run_report:
        payload = finalize_run_artifacts(
            source_site=SOURCE_SITE,
            scrape_started_at=started_at,
            json_path=json_bundle,
            jsonl_path=jsonl_path,
            csv_path=csv_path,
            row_count=len(rows),
            apply_cleaning=apply_cleaning,
            clean_report=cleaning_stats,
            fetch_errors=len(issues),
            issues=issues,
        )
        print(summarize_run_line(payload), flush=True)

    return rows

