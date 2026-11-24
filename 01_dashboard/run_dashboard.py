#!/usr/bin/env python3
"""
run_dashboard.py — Point d'entrée pour démarrer l'interface et le Dashboard Web E-commerce AI V2.
Usage:
    python3 run_dashboard.py
    python3 run_dashboard.py --port 8080 --host 127.0.0.1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent

sys.path.insert(0, str(PROJECT_ROOT / "03_ia_normalizer" / "engine"))
sys.path.insert(0, str(PROJECT_ROOT / "02_scrapers"))
sys.path.insert(0, str(CURRENT_DIR))

from server import run_dashboard


def main() -> None:
    parser = argparse.ArgumentParser(description="E-commerce AI V2 — Dashboard & Interface Web")
    parser.add_argument("--port", type=int, default=8080, help="Port d'écoute (défaut: 8080)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Hôte d'écoute (défaut: 127.0.0.1)")
    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("🌟 DÉMARRAGE DU DASHBOARD & STUDIO E-COMMERCE AI V2")
    print(f"🌐 URL : http://{args.host}:{args.port}")
    print(f"📁 Dossier statique : {CURRENT_DIR / 'static'}")
    print("=" * 70 + "\n")

    run_dashboard(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
