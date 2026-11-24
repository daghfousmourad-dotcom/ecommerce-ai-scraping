#!/usr/bin/env python3
"""
runner.py — Orchestrateur Centralisé des Scrapers E-commerce AI V2.
Permet d'exécuter n'importe quel scraper individuellement ou l'ensemble des sources.
Usage:
    python3 runner.py --source apple
    python3 runner.py --source phonethik --output-dir outputs
    python3 runner.py --list
    python3 runner.py --all
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SOURCES_DIR = CURRENT_DIR / "sources"
DEFAULT_OUTPUT_DIR = CURRENT_DIR / "outputs"


def get_available_sources() -> dict[str, Path]:
    """Retourne la liste des scrapers valides disponibles dans sources/."""
    scrapers = {}
    for d in sorted(SOURCES_DIR.iterdir()):
        if d.is_dir() and not d.name.startswith((".", "__")):
            scraper_file = d / "scraper.py"
            if scraper_file.exists():
                scrapers[d.name] = scraper_file
    return scrapers


def run_single_source(source_name: str, scraper_path: Path, output_dir: Path) -> bool:
    """Exécute un scraper spécifique dans un sous-processus isolé."""
    print(f"\n{'='*70}")
    print(f"🚀 Démarrage du scraper : {source_name.upper()}")
    print(f"📁 Fichier : {scraper_path}")
    print(f"📂 Sortie : {output_dir}")
    print(f"{'='*70}")

    output_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    cmd = [sys.executable, str(scraper_path), "--output-dir", str(output_dir)]
    try:
        res = subprocess.run(cmd, cwd=str(scraper_path.parent), check=False)
        duration = time.time() - t0
        if res.returncode == 0:
            print(f"✅ [{source_name}] Succès en {duration:.1f}s")
            return True
        else:
            # Réessayer sans --output-dir au cas où les arguments CLI diffèrent
            res_retry = subprocess.run([sys.executable, str(scraper_path)], cwd=str(scraper_path.parent), check=False)
            if res_retry.returncode == 0:
                print(f"✅ [{source_name}] Succès en {duration:.1f}s")
                return True
            print(f"❌ [{source_name}] Échec (code de sortie {res.returncode}) en {duration:.1f}s")
            return False
    except Exception as e:
        print(f"💥 Erreur critique lors de l'exécution de {source_name} : {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="E-commerce AI Scraping Scraper V2 — Orchestrateur Centralisé")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", type=str, help="Nom de la source à exécuter (ex: apple, samsung, phonethik...)")
    group.add_argument("--all", action="store_true", help="Exécuter tous les scrapers de production disponibles")
    group.add_argument("--list", action="store_true", help="Lister toutes les sources disponibles")

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Dossier de destination des 5 artefacts (défaut: {DEFAULT_OUTPUT_DIR})",
    )

    args = parser.parse_args()
    available_scrapers = get_available_sources()

    if args.list:
        print("\n📋 SOURCES DISPONIBLES DANS E-COMMERCE AI V2 :")
        print("-" * 60)
        for name, path in available_scrapers.items():
            print(f"  • {name:<22} -> {path.relative_to(CURRENT_DIR)}")
        print(f"\nTotal : {len(available_scrapers)} scrapers prêts à l'emploi.")
        return

    if args.source:
        source_key = args.source.lower().strip()
        if source_key not in available_scrapers:
            print(f"❌ Erreur : Source '{args.source}' introuvable.")
            print("Sources valides :", ", ".join(available_scrapers.keys()))
            sys.exit(1)
        ok = run_single_source(source_key, available_scrapers[source_key], args.output_dir)
        sys.exit(0 if ok else 1)

    if args.all:
        print(f"\n⚡ Lancement global de {len(available_scrapers)} scrapers...")
        success_count = 0
        failed_count = 0
        for name, path in available_scrapers.items():
            ok = run_single_source(name, path, args.output_dir)
            if ok:
                success_count += 1
            else:
                failed_count += 1

        print("\n" + "=" * 70)
        print("📊 BILAN DU RUN COMPLET :")
        print(f"  ✅ Succès : {success_count} scrapers")
        print(f"  ❌ Échecs : {failed_count} scrapers")
        print("=" * 70)


if __name__ == "__main__":
    main()
