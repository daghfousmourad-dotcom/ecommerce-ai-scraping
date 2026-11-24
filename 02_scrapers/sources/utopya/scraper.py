"""Utopya.fr scraper: parts supplier — login, brand/model discovery, part costs."""

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
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import Page, sync_playwright
from tqdm import tqdm

from scraper.core.filtering import clean_dataset
from scraper.core.normalization import normalize_device_name, normalize_repair_type
from scraper.core.run_report import (
    REASON_FETCH_ERROR,
    fetch_issue,
    finalize_run_artifacts,
    summarize_run_line,
)
from scraper.core.row_export import write_jsonl_and_csv
from scraper.core.parser_utils import (
    dedupe_dict_rows,
    now_iso_utc,
    parse_price_and_currency,
)

LOGIN_URL = "https://www.utopya.fr/customer/account/login/"
HOME_URL = "https://www.utopya.fr/"

# Override with UTOPYA_EMAIL / UTOPYA_PASSWORD (preferred for production).
_DEFAULT_EMAIL = "info@t-scop.com"
_DEFAULT_PASSWORD = "Tscop@2025"


def _credentials() -> tuple[str, str]:
    email = os.environ.get("UTOPYA_EMAIL", _DEFAULT_EMAIL).strip()
    password = os.environ.get("UTOPYA_PASSWORD", _DEFAULT_PASSWORD)
    if not email or not password:
        raise ValueError("Utopya credentials missing: set UTOPYA_EMAIL and UTOPYA_PASSWORD")
    return email, password


def _wait_page_ready(page: Page, settle_ms: int = 400) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=60000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=60000)
    except Exception:
        pass
    page.wait_for_timeout(settle_ms)


def _dismiss_cookie_banner_if_any(page: Page) -> None:
    selectors = [
        "#axeptio_btn_acceptAll",
        'button[aria-label="Accepter les cookies"]',
        'button:has-text("OK pour moi")',
        "#axeptio_btn_dismiss",
        'button:has-text("Tout accepter")',
        'button:has-text("Accepter")',
        "#onetrust-accept-btn-handler",
        'button[id*="accept"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=5000)
                page.wait_for_timeout(400)
                return
        except Exception:
            continue


def _visible_login_form(page: Page):
    """
    Utopya duplicates #form-popup-login in the DOM (e.g. template + live modal).
    Playwright's :first resolves to a hidden node — we need the visible instance.
    """
    for sel in ('#form-popup-login', "#customer-login-form", 'form[action*="loginPost"]'):
        loc = page.locator(sel)
        try:
            n = loc.count()
        except Exception:
            continue
        for i in range(n):
            form = loc.nth(i)
            try:
                if form.is_visible():
                    return form
            except Exception:
                continue
    return None


def _wait_for_visible_login_form(page: Page, timeout_ms: int = 35000) -> None:
    """Wait until at least one login form matching Utopya's markup is actually visible."""

    def _any_visible() -> bool:
        return _visible_login_form(page) is not None

    try:
        page.wait_for_function(
            """() => {
                const selectors = ['#form-popup-login', '#customer-login-form'];
                for (const s of selectors) {
                    for (const f of document.querySelectorAll(s)) {
                        const r = f.getBoundingClientRect();
                        const cs = window.getComputedStyle(f);
                        if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') continue;
                        if (r.width < 2 || r.height < 2) continue;
                        return true;
                    }
                }
                for (const f of document.querySelectorAll('form[action*="loginPost"]')) {
                    const r = f.getBoundingClientRect();
                    const cs = window.getComputedStyle(f);
                    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
                    if (r.width < 2 || r.height < 2) continue;
                    const u = f.querySelector('input[name="login[username]"]');
                    if (u) return true;
                }
                return false;
            }""",
            timeout=timeout_ms,
        )
    except Exception:
        if not _any_visible():
            raise


def _fill_popup_or_inline_form(page: Page, user: str, pwd: str) -> bool:
    """Fill credentials into the visible login form (handles duplicate #form-popup-login)."""
    form = _visible_login_form(page)
    scope = form if form is not None else page
    user_selectors = [
        'input[name="login[username]"]',
        "#email",
        'input[type="email"]',
        'input[name*="user"]',
    ]
    pwd_selectors = ['input[name="login[password]"]', "#pass", 'input[type="password"]']
    submit_selectors = [
        "#send2",
        "button[type='submit'].action.login",
        "button[type='submit']",
        'button:has-text("Se connecter")',
    ]

    try:
        user_in = scope.locator(", ".join(user_selectors)).first
        pwd_in = scope.locator(", ".join(pwd_selectors)).first
        user_in.wait_for(state="visible", timeout=12000)
        pwd_in.wait_for(state="visible", timeout=12000)
        user_in.fill(user)
        pwd_in.fill(pwd)
        submit = scope.locator(", ".join(submit_selectors)).first
        submit.click(timeout=12000)
        return True
    except Exception:
        return False


def _username_field_visible(page: Page) -> bool:
    """True if any username field is visible (Utopya may duplicate hidden fields in the DOM)."""
    loc = page.locator(
        'input[name="login[username]"], #email, input[type="email"], input[name*="user"]'
    )
    try:
        for i in range(loc.count()):
            if loc.nth(i).is_visible():
                return True
    except Exception:
        pass
    return False


def _open_login_popup_if_needed(page: Page, timeout_ms: int) -> None:
    """Click header login when the form is not already visible (e.g. homepage)."""
    if _visible_login_form(page) is not None:
        return
    triggers = [
        "a.button-plain.login",
        'a:has-text("Se connecter")',
        'header a[href*="login"]',
        'a[href*="customer/account/login"]',
        'a[href*="account/login"]',
        'a[title*="connect" i]',
    ]
    last_exc: Exception | None = None
    per = max(12000, timeout_ms // max(len(triggers), 1))
    for sel in triggers:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="attached", timeout=per)
            try:
                loc.scroll_into_view_if_needed(timeout=5000)
            except Exception:
                pass
            loc.click(timeout=15000)
            return
        except Exception as exc:
            last_exc = exc
            try:
                loc = page.locator(sel).first
                if loc.count():
                    loc.click(timeout=12000, force=True)
                    return
            except Exception as force_exc:
                last_exc = force_exc
                continue
    # Last fallback: click likely login links from JS when overlays/visibility checks interfere.
    try:
        clicked = page.evaluate(
            """() => {
                const candidates = [
                  'a.button-plain.login',
                  'a[href*="customer/account/login"]',
                  'a[href*="account/login"]',
                  'header a[href*="login"]',
                  'a[title*="connect" i]'
                ];
                for (const s of candidates) {
                    const el = document.querySelector(s);
                    if (!el) continue;
                    el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                    return true;
                }
                for (const a of document.querySelectorAll('a')) {
                    const txt = (a.textContent || '').toLowerCase();
                    const href = (a.getAttribute('href') || '').toLowerCase();
                    if (txt.includes('se connecter') || href.includes('account/login')) {
                        a.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                        return true;
                    }
                }
                return false;
            }"""
        )
        if clicked:
            return
    except Exception:
        pass
    if last_exc:
        raise last_exc


def login_utopya(
    page: Page,
    *,
    email: str | None = None,
    password: str | None = None,
    login_url: str = LOGIN_URL,
    timeout_ms: int = 90000,
) -> bool:
    """
    Log in via the popup opened from the login page (Se connecter).

    Flow: customer/account/login/ -> click a.button-plain.login -> #form-popup-login -> submit.
    """
    user, pwd = (email, password) if email and password else _credentials()

    print("[Utopya] Opening login page...", flush=True)
    page.goto(login_url, wait_until="domcontentloaded", timeout=timeout_ms)
    _wait_page_ready(page)
    _dismiss_cookie_banner_if_any(page)

    # Some builds use an inline Magento form on /customer/account/login/; others use a popup from the header.
    if not _username_field_visible(page):
        print("[Utopya] Opening connection popup...", flush=True)
        try:
            _open_login_popup_if_needed(page, timeout_ms=min(25000, timeout_ms))
        except Exception as exc:
            print(f"[Utopya] Could not open login UI: {exc}", flush=True)
            return False
        _dismiss_cookie_banner_if_any(page)

    try:
        _wait_for_visible_login_form(page, timeout_ms=35000)
    except Exception as exc:
        print(f"[Utopya] Login form did not appear: {exc}", flush=True)
        return False

    try:
        page.locator(".login-popup .loader").wait_for(state="hidden", timeout=15000)
    except Exception:
        pass

    print("[Utopya] Submitting credentials...", flush=True)
    if not _fill_popup_or_inline_form(page, user, pwd):
        form = _visible_login_form(page)
        if form is None:
            print("[Utopya] No visible login form to fill.", flush=True)
            return False
        form.locator('input[name="login[username]"]').fill(user)
        form.locator('input[name="login[password]"]').fill(pwd)
        try:
            form.locator("#send2").click(timeout=10000)
        except Exception:
            form.locator("button[type='submit'].action.login").click(timeout=10000)

    _wait_page_ready(page, settle_ms=600)

    # Success: account / logout / Mon compte
    success_selectors = [
        'a[href*="customer/account/logout"]',
        'a[href*="logout"]',
        'a:has-text("Mon compte")',
        ".customer-name",
    ]
    for sel in success_selectors:
        try:
            page.wait_for_selector(sel, timeout=8000)
            print("[Utopya] Login successful.", flush=True)
            return True
        except Exception:
            continue

    # Popup error visible?
    try:
        err = page.locator(".login-popup .message-error, .error-message:not(.hide)")
        if err.count() and err.first.is_visible():
            print("[Utopya] Login failed: error message shown on page.", flush=True)
            return False
    except Exception:
        pass

    print("[Utopya] Login outcome unclear (no success marker found).", flush=True)
    return False


# --- Brand / model discovery (main menu "Pièces détachées" tree, same as legacy linksProviderV4) ---

_MENU_TREE_JS = r"""
() => {
  const base = "https://www.utopya.fr";
  const abs = (u) => {
    if (!u) return u;
    try { return new URL(u, base + "/").href; } catch (e) { return u; }
  };
  // Exclusion des catégories accessoires purs et consommables
  const EXCLUDED_CATEGORIES = [
    "outillage", "accessoire", "accessoires", "consommable", "consommables",
    "outil", "outils", "trottinette", "protection", "verre trempe", "verre trempé"
  ];
  // Exclusion uniquement des objets connectés / écouteurs (les tablettes, PC portables et consoles sont conservés)
  const MODEL_EXCLUDED_KEYWORDS = [
    "airpod", "airpods", "apple watch", "apple tv", "watch"
  ];
  const isExcluded = (txt) => {
    if (!txt) return false;
    const lower = txt.toLowerCase();
    return EXCLUDED_CATEGORIES.some(exc => lower.includes(exc));
  };
  const isModelExcluded = (txt) => {
    if (!txt) return false;
    const lower = txt.toLowerCase();
    return MODEL_EXCLUDED_KEYWORDS.some(kw => lower.includes(kw));
  };
  const menu_data = {};
  const processed_urls = new Set();
  const brandItems = document.querySelectorAll("ul.main-menu > li.category-item.parent");
  brandItems.forEach((brand) => {
    const brandLink = brand.querySelector("a");
    const brandSpan = brand.querySelector("a span");
    if (!brandLink || !brandSpan) return;
    const brand_name = brandSpan.innerText.trim();
    const brand_url = abs(brandLink.getAttribute("href"));
    if (!brand_name || !brand_url || isExcluded(brand_name)) return;
    const submenu = brand.querySelector("ul.level0.submenu");
    if (!submenu) return;
    const series_data = {};
    const allSeriesItems = submenu.querySelectorAll("li.category-item");
    allSeriesItems.forEach((series) => {
      const seriesNameEl = series.querySelector("a span");
      const series_name = seriesNameEl ? seriesNameEl.innerText.trim() : null;
      const seriesLink = series.querySelector("a");
      const series_url = seriesLink ? abs(seriesLink.getAttribute("href")) : null;
      if (!series_name || !series_url || isExcluded(series_name) || isModelExcluded(series_name)) return;
      if (processed_urls.has(series_url)) return;
      const models = [];
      const models_submenu = series.querySelector("ul.submenu");
      if (models_submenu) {
        const modelItems = models_submenu.querySelectorAll("li.category-item");
        modelItems.forEach((model) => {
          const modelSpan = model.querySelector("a span");
          const model_link = model.querySelector("a");
          const model_name = modelSpan ? modelSpan.innerText.trim() : null;
          const model_url = model_link ? abs(model_link.getAttribute("href")) : null;
          // Filtre secondaire : exclure les modèles non-téléphone par mot-clé
          if (model_name && model_url && !processed_urls.has(model_url) && !isModelExcluded(model_name)) {
            models.push({ name: model_name, url: model_url });
            processed_urls.add(model_url);
          }
        });
        if (models.length && !(series_name in series_data)) {
          series_data[series_name] = models;
          processed_urls.add(series_url);
        }
      } else {
        if (!processed_urls.has(series_url) && !(series_name in series_data)) {
          series_data[series_name] = [{ name: series_name, url: series_url }];
          processed_urls.add(series_url);
        }
      }
    });
    if (Object.keys(series_data).length) {
      menu_data[brand_name] = series_data;
    }
  });
  return menu_data;
}
"""


def _hover_main_menu_brands(page: Page, *, max_items: int = 60) -> None:
    """Hover top-level menu parents so megamenu sub-trees load if needed."""
    try:
        parents = page.locator("ul.main-menu > li.category-item.parent")
        n = parents.count()
        for i in range(min(n, max_items)):
            try:
                parents.nth(i).hover(timeout=2500)
                page.wait_for_timeout(120)
            except Exception:
                continue
    except Exception:
        pass


def discover_brand_models_tree(page: Page, *, timeout_ms: int = 90000) -> dict[str, Any]:
    """
    After login, open the homepage and read the nested brand → series → model URLs from the main menu.

    Returns the same nested shape as legacy ``all_phone_links.json``:
    ``{ brand: { series: [ {name, url}, ... ] } }``.
    """
    print("[Utopya] Discovering brands/models from main menu...", flush=True)
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    _wait_page_ready(page)
    _dismiss_cookie_banner_if_any(page)

    page.wait_for_selector("ul.main-menu", timeout=min(60000, timeout_ms))
    page.wait_for_timeout(400)
    _hover_main_menu_brands(page)

    tree: dict[str, Any] = page.evaluate(_MENU_TREE_JS)
    if not tree:
        print("[Utopya] Menu tree empty — retrying after extra hover...", flush=True)
        _hover_main_menu_brands(page, max_items=80)
        page.wait_for_timeout(500)
        tree = page.evaluate(_MENU_TREE_JS)

    total_series = sum(len(v) for v in tree.values()) if isinstance(tree, dict) else 0
    print(
        f"[Utopya] Menu discovery: {len(tree)} brands, {total_series} series groups.",
        flush=True,
    )
    return tree if isinstance(tree, dict) else {}


def flatten_brand_model_tree(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Nested menu tree -> flat rows for CSV / pipeline."""
    rows: list[dict[str, Any]] = []
    ts = now_iso_utc()
    # Catégories de menu à exclure (niveau marque / série) - accessoires et consommables
    EXCLUDED_CATEGORIES = (
        "outillage", "accessoire", "accessoires", "consommable", "consommables",
        "outil", "outils", "trottinette", "protection", "verre trempe",
    )
    # Mots-clés dans les noms de modèles à exclure : uniquement objets connectés / écouteurs
    # (tablettes, PC portables et consoles sont conservés conformément aux consignes)
    MODEL_EXCLUDED_KEYWORDS = (
        "airpod", "airpods", "apple watch", "apple tv", "watch",
    )
    for brand, series_map in tree.items():
        if not isinstance(series_map, dict):
            continue
        brand_clean = brand.strip()
        if any(exc in brand_clean.lower() for exc in EXCLUDED_CATEGORIES):
            continue
        for series_name, models in series_map.items():
            if not isinstance(models, list):
                continue
            series_clean = (series_name or "").strip()
            if any(exc in series_clean.lower() for exc in EXCLUDED_CATEGORIES):
                continue
            # Filtre secondaire : exclure les séries non-téléphone par mot-clé dans le nom
            if any(kw in series_clean.lower() for kw in MODEL_EXCLUDED_KEYWORDS):
                continue
            for m in models:
                if not isinstance(m, dict):
                    continue
                name = (m.get("name") or "").strip()
                raw_url = (m.get("url") or "").strip()
                if not name or not raw_url:
                    continue
                # Filtre secondaire : exclure les modèles non-téléphone par mot-clé dans le nom
                if any(kw in name.lower() for kw in MODEL_EXCLUDED_KEYWORDS):
                    continue
                url = urljoin(HOME_URL, raw_url)
                rows.append(
                    {
                        "brand": brand.strip(),
                        "series": (series_name or "").strip(),
                        "model": name,
                        "url": url,
                        "source_site": "utopya",
                        "data_type": "part_listing_url",
                        "discovered_at": ts,
                    }
                )
    return dedupe_dict_rows(rows, ("url",))


def export_discovery_json(tree: dict[str, Any], flat_rows: list[dict[str, Any]], output_path: Path) -> None:
    payload = {
        "source_site": "utopya",
        "discovered_at": now_iso_utc(),
        "nested_menu": tree,
        "flat_models": flat_rows,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def export_discovery_csv(rows: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        output_path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with output_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


# --- Listing page scraping -> part_cost rows ---


def _scrape_listing_page(
    page: Page,
    *,
    brand: str,
    series: str,
    model: str,
    url: str,
    on_row: Any | None = None,
    scrape_started_at: str | None = None,
) -> list[dict[str, Any]]:
    """
    Scrape one Utopya listing page (for a specific device model) into part_cost rows.
    """
    page.goto(url, timeout=60000)
    page.wait_for_load_state("domcontentloaded", timeout=60000)
    try:
        page.wait_for_selector("#layer-product-list, .products.list.items", timeout=60000)
    except Exception:
        # Some pages may be empty or redirect; best effort.
        page.wait_for_timeout(1000)

    # Scroll to load lazy products
    page.evaluate(
        """
        () => {
            let totalHeight = 0;
            const distance = 250;
            const timer = setInterval(() => {
                window.scrollBy(0, distance);
                totalHeight += distance;
                if (totalHeight >= document.body.scrollHeight) {
                    clearInterval(timer);
                }
            }, 200);
        }
        """
    )
    page.wait_for_timeout(3500)

    items = page.query_selector_all(".item.product-item.listing-item, .item.product.product-item")

    rows: list[dict[str, Any]] = []
    ts = now_iso_utc()

    for product in items:
        try:
            name_el = product.query_selector(".product-item-link.name, a.product-item-link")
            name = name_el.inner_text().strip() if name_el else ""
            product_url = name_el.get_attribute("href") if name_el else url

            price_el = product.query_selector(".price-box.price-final_price span.price")
            if not price_el:
                price_el = product.query_selector(".price-box span.price")
            raw_price = price_el.inner_text().strip() if price_el else ""
            price_value, currency = parse_price_and_currency(raw_price)

            if not name or price_value is None:
                continue

            repair_norm, repair_matched = normalize_repair_type(name)
            dev = normalize_device_name(None, brand=brand, model=model, series=series)
            model_val = dev.get("model") or model
            model_norm = (model_val or "").lower().strip()
            dev_family = dev.get("device_family") or series or "Smartphone"

            # Detect device_type
            dev_type = "smartphone"
            name_lower = name.lower()
            if "ipad" in name_lower or "tablette" in name_lower or "tab" in name_lower:
                dev_type = "tablette"
            elif "macbook" in name_lower or "pc" in name_lower or "ordinateur" in name_lower:
                dev_type = "pc_portable"
            elif "switch" in name_lower or "ps5" in name_lower or "ps4" in name_lower or "xbox" in name_lower or "console" in name_lower:
                dev_type = "console"

            row: dict[str, Any] = {
                "source_site": "utopya",
                "source_url": product_url or url,
                "timestamp": ts,
                "scrape_started_at": scrape_started_at,
                "data_type": "part_cost",
                "device_type": dev_type,
                "device_family": dev_family,
                "brand": dev.get("brand") or brand,
                "model": model_val,
                "model_normalized": model_norm,
                "product_or_part_name": name,
                "repair_type_raw": name,
                "repair_type": repair_norm,
                "repair_type_matched": repair_matched,
                "price": price_value,
                "currency": currency or "EUR",
                "price_raw": raw_price,
                "warranty": None,
                "delay": None,
                "shop_name": "Utopya Grossiste B2B",
                "city": None,
                "postal_code": None,
            }
            if on_row is not None:
                try:
                    on_row(row)
                except Exception:
                    # Streaming is best-effort; do not break the scrape if sink fails once.
                    pass
            rows.append(row)
        except Exception:
            continue

    return rows


def scrape(
    *,
    output_dir: str = str(_SCRAPERS_ROOT / "outputs"),
    headless: bool = False,
    email: str | None = None,
    password: str | None = None,
    apply_cleaning: bool = True,
    emit_run_report: bool = True,
) -> list[dict[str, Any]]:
    """
    Log in, discover brand/series/model listing URLs from the main menu,
    then scrape listing pages into part_cost rows (Utopya is a parts supplier).

    Data is streamed to disk as each product is scraped, and also returned as a list.
    JSONL/CSV are rewritten at the end so they match the final deduped/cleaned dataset.

    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    scrape_started_at = now_iso_utc()
    # Safe suffix for filenames, derived from the scrape start time.
    ts_suffix = (
        scrape_started_at.replace(":", "")
        .replace("-", "")
        .replace("+", "")
        .replace("T", "_")
    )

    all_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    fetch_errors = 0

    jsonl_path = out_dir / f"utopya_part_costs_{ts_suffix}.jsonl"
    json_path = out_dir / f"utopya_part_costs_{ts_suffix}.json"
    csv_path = out_dir / f"utopya_part_costs_{ts_suffix}.csv"

    # Open sinks up-front so we can stream each product row in real time.
    jsonl_file = jsonl_path.open("w", encoding="utf-8")
    csv_file = csv_path.open("w", encoding="utf-8", newline="")
    csv_writer: csv.DictWriter | None = None

    def _stream_row(row: dict[str, Any]) -> None:
        # Enrich with global scrape start time so each row carries it.
        row_with_meta = dict(row)
        row_with_meta.setdefault("scrape_started_at", scrape_started_at)

        all_rows.append(row_with_meta)

        # JSONL: one object per line, flushed immediately.
        jsonl_file.write(json.dumps(row_with_meta, ensure_ascii=False) + "\n")
        jsonl_file.flush()

        # CSV: write header once, then append rows.
        nonlocal csv_writer
        if csv_writer is None:
            fieldnames = list(row_with_meta.keys())
            csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            csv_writer.writeheader()
        csv_writer.writerow(row_with_meta)
        csv_file.flush()

    # Single Playwright session + browser for login, discovery, and listing scraping.
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="fr-FR")
        page = context.new_page()
        try:
            if not login_utopya(page, email=email, password=password):
                raise RuntimeError("Utopya login failed. Check credentials, selectors, or anti-bot.")

            tree = discover_brand_models_tree(page)
            flat = flatten_brand_model_tree(tree)
            print(f"[Utopya] {len(flat)} listing page(s) to scrape", flush=True)

            with tqdm(
                total=len(flat),
                desc="[Utopya] Scraping",
                unit="page",
                dynamic_ncols=True,
            ) as pbar:
                for idx, meta in enumerate(flat, start=1):
                    brand = meta.get("brand", "")
                    series = meta.get("series", "")
                    model = meta.get("model", "")
                    url = meta.get("url", "")
                    if not url:
                        fetch_errors += 1
                        if emit_run_report:
                            issues.append(
                                fetch_issue(
                                    reason=REASON_FETCH_ERROR,
                                    source_site="utopya",
                                    detail="empty_listing_url",
                                    brand=brand,
                                    series=series,
                                    model=model,
                                )
                            )
                        pbar.update(1)
                        continue
                    try:
                        rows = _scrape_listing_page(
                            page,
                            brand=brand,
                            series=series,
                            model=model,
                            url=url,
                            on_row=_stream_row,
                            scrape_started_at=scrape_started_at,
                        )
                        hint = f"{brand} {model}".strip()
                        if len(hint) > 32:
                            hint = hint[:29] + "..."
                        pbar.set_postfix(rows=len(rows), model=hint or "—", refresh=False)
                    except Exception as exc:
                        tqdm.write(f"[Utopya] Page {idx}/{len(flat)} failed: {exc}")
                        fetch_errors += 1
                        if emit_run_report:
                            issues.append(
                                fetch_issue(
                                    reason=REASON_FETCH_ERROR,
                                    source_site="utopya",
                                    source_url=url,
                                    brand=brand,
                                    series=series,
                                    model=model,
                                    page_index=idx,
                                    detail=str(exc),
                                )
                            )
                    pbar.update(1)
        finally:
            browser.close()
            jsonl_file.close()
            csv_file.close()

    all_rows = dedupe_dict_rows(all_rows, ("source_url", "product_or_part_name", "price_raw"))
    clean_report = None
    if apply_cleaning:
        all_rows, clean_report = clean_dataset(
            all_rows,
            data_type="part_cost",
            cleaning_issues=issues if emit_run_report else None,
        )
        print(clean_report.summary_line(), flush=True)
    for row in all_rows:
        row.setdefault("scrape_started_at", scrape_started_at)

    write_jsonl_and_csv(all_rows, jsonl_path=jsonl_path, csv_path=csv_path)

    json_payload = {
        "source_site": "utopya",
        "scrape_started_at": scrape_started_at,
        "row_count": len(all_rows),
        "rows": all_rows,
    }
    json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[Utopya] Exported {len(all_rows)} part_cost rows", flush=True)
    print(f"[Utopya] JSON : {json_path}", flush=True)
    print(f"[Utopya] JSONL: {jsonl_path}", flush=True)
    print(f"[Utopya] CSV  : {csv_path}", flush=True)
    if emit_run_report:
        payload = finalize_run_artifacts(
            source_site="utopya",
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
        print(summarize_run_line(payload), flush=True)
        if payload.paths:
            print(f"[Utopya] Run   : {payload.paths.run_json}", flush=True)
            print(f"[Utopya] Issues: {payload.paths.issues_jsonl}", flush=True)
    return all_rows


if __name__ == "__main__":
    scrape(headless=False)
