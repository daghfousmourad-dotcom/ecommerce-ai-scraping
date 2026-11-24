#!/usr/bin/env python3
"""
main.py — Point d'Entrée Global & Orchestrateur du Projet E-commerce AI V2.
Permet de piloter les 4 grands piliers du projet en une seule commande :
  1. Scrapers (individuel ou global)
  2. Normalisation IA
  3. Merge unifié 360°
  4. Dashboard Web & Studio

Exemples d'utilisation :
  python3 main.py --scraper apple
  python3 main.py --scraper phonethik
  python3 main.py --scrapers-list
  python3 main.py --scrapers-all
  python3 main.py --normalize apple
  python3 main.py --normalize-all
  python3 main.py --merge
  python3 main.py --dashboard
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from config import (
    DASHBOARD_DIR,
    MERGE_DIR,
    NORMALIZER_DIR,
    PROJECT_ROOT,
    SCRAPERS_DIR,
)


def run_cmd(cmd: list[str], cwd: Path) -> int:
    try:
        res = subprocess.run(cmd, cwd=str(cwd))
        return res.returncode
    except KeyboardInterrupt:
        print("\nArrêt par l'utilisateur.")
        return 130


def main() -> None:
    parser = argparse.ArgumentParser(description="E-commerce AI V2 — Orchestrateur Global")
    group = parser.add_mutually_exclusive_group(required=True)

    # 1. Scrapers
    group.add_argument("--scraper", type=str, help="Lancer un scraper individuel (ex: apple, samsung, phonethik...)")
    group.add_argument("--scrapers-all", action="store_true", help="Lancer l'ensemble des scrapers")
    group.add_argument("--scrapers-list", action="store_true", help="Lister tous les scrapers disponibles")

    # 2. Normalisation IA
    group.add_argument("--normalize", type=str, help="Normaliser par IA une source spécifique")
    group.add_argument("--normalize-all", action="store_true", help="Normaliser toutes les sources par IA")

    # 3. Merge
    group.add_argument("--merge", action="store_true", help="Exécuter la fusion consolidée du catalogue E-commerce AI Scraping")

    # 4. Dashboard
    group.add_argument("--dashboard", action="store_true", help="Lancer l'interface Web et le Dashboard")

    parser.add_argument("--port", type=int, default=8080, help="Port Web pour le Dashboard (défaut: 8080)")

    args = parser.parse_args()

    if args.scrapers_list:
        sys.exit(run_cmd([sys.executable, "runner.py", "--list"], cwd=SCRAPERS_DIR))

    if args.scraper:
        sys.exit(run_cmd([sys.executable, "runner.py", "--source", args.scraper], cwd=SCRAPERS_DIR))

    if args.scrapers_all:
        sys.exit(run_cmd([sys.executable, "runner.py", "--all"], cwd=SCRAPERS_DIR))

    if args.normalize:
        sys.exit(run_cmd([sys.executable, "run_normalizer.py", "--source", args.normalize], cwd=NORMALIZER_DIR))

    if args.normalize_all:
        sys.exit(run_cmd([sys.executable, "run_normalizer.py", "--all"], cwd=NORMALIZER_DIR))

    if args.merge:
        sys.exit(run_cmd([sys.executable, "run_merge.py"], cwd=MERGE_DIR))

    if args.dashboard:
        sys.exit(run_cmd([sys.executable, "run_dashboard.py", "--port", str(args.port)], cwd=DASHBOARD_DIR))


if __name__ == "__main__":
    main()
