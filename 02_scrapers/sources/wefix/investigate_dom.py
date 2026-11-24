"""
Script d'investigation DOM pour WeFix.
Ouvre le navigateur en mode visible pour voir la page et extrait les sélecteurs.
"""
import sys
from playwright.sync_api import sync_playwright

HOME_URL = "https://www.wefix.net/"
REPAIR_URL = "https://www.wefix.net/reparation/smartphone"

def investigate():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # visible pour debug
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()
        
        print("=== Navigating to WeFix home ===", flush=True)
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        
        # Try dismiss cookie banner
        for sel in ["#onetrust-accept-btn-handler", "button:has-text('Accepter')", "button:has-text('Accept')", "[id*='accept']"]:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    print(f"  Cookie banner dismissed via: {sel}", flush=True)
                    page.wait_for_timeout(1000)
                    break
            except Exception:
                pass
        
        page.wait_for_timeout(2000)
        
        # Save full HTML
        html = page.content()
        with open("/tmp/wefix_home.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  Home HTML saved ({len(html)} bytes) to /tmp/wefix_home.html", flush=True)
        
        # Look for device-related elements
        print("\n=== Searching for device selectors ===", flush=True)
        
        searches = [
            "#device-select-top",
            "[id*='device']",
            "[class*='device']",
            "sl-select",
            "sl-dropdown",
            "[data-device]",
            "select",
            "input[type='search']",
            "a[href*='/reparation/smartphone']",
            "a[href*='/reparation/']",
            "[class*='cursor-pointer']",
            "[class*='group block']",
        ]
        
        for sel in searches:
            try:
                els = page.query_selector_all(sel)
                if els:
                    print(f"\n  FOUND [{len(els)}]: {sel}", flush=True)
                    for i, el in enumerate(els[:5]):
                        try:
                            outer = page.evaluate("el => el.outerHTML.slice(0, 300)", el)
                            print(f"    [{i}] {outer}", flush=True)
                        except Exception as e:
                            print(f"    [{i}] Error: {e}", flush=True)
            except Exception as ex:
                print(f"  Error with {sel}: {ex}", flush=True)
        
        # Check all links containing /reparation/
        print("\n=== All /reparation/ links ===", flush=True)
        links = page.query_selector_all("a[href*='/reparation/']")
        print(f"  Found {len(links)} repair links", flush=True)
        hrefs = set()
        for link in links[:20]:
            try:
                href = page.evaluate("el => el.href", link)
                hrefs.add(href)
            except Exception:
                pass
        for href in sorted(hrefs):
            print(f"  {href}", flush=True)
        
        # Now go to /reparation/smartphone
        print("\n=== Navigating to /reparation/smartphone ===", flush=True)
        page.goto(REPAIR_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)
        
        html2 = page.content()
        with open("/tmp/wefix_smartphone.html", "w", encoding="utf-8") as f:
            f.write(html2)
        print(f"  Smartphone page HTML saved ({len(html2)} bytes) to /tmp/wefix_smartphone.html", flush=True)
        
        # Look for brand links
        print("\n=== Brand links on /reparation/smartphone ===", flush=True)
        brand_links = page.query_selector_all("a[href*='/reparation/smartphone/']")
        print(f"  Found {len(brand_links)} brand links", flush=True)
        brand_hrefs = set()
        for link in brand_links[:30]:
            try:
                href = page.evaluate("el => el.href", link)
                brand_hrefs.add(href)
            except Exception:
                pass
        for href in sorted(brand_hrefs)[:30]:
            print(f"  {href}", flush=True)
        
        # Look for brand cards/divs
        print("\n=== Brand card elements ===", flush=True)
        for sel in ["[class*='brand']", "[data-brand]", "article", ".card", "[class*='card']"]:
            try:
                els = page.query_selector_all(sel)
                if els:
                    print(f"\n  FOUND [{len(els)}]: {sel}", flush=True)
                    for i, el in enumerate(els[:3]):
                        try:
                            outer = page.evaluate("el => el.outerHTML.slice(0, 300)", el)
                            print(f"    [{i}] {outer}", flush=True)
                        except Exception:
                            pass
            except Exception:
                pass
        
        print("\n=== Done. Browser stays open for 20s for manual inspection ===", flush=True)
        page.wait_for_timeout(20000)
        browser.close()

investigate()
