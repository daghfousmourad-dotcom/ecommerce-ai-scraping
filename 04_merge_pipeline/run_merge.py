#!/usr/bin/env python3
"""
run_merge.py — Lanceur du Pipeline de Consolidation & Fusion E-commerce AI V2.
Usage:
    python3 run_merge.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR / "src"
MERGE_SCRIPT = SRC_DIR / "merge_unified_dataset.py"


def main() -> None:
    print("\n" + "=" * 70)
    print("🔄 DÉMARRAGE DU PIPELINE DE MERGE E-COMMERCE AI V2")
    print(f"📁 Script : {MERGE_SCRIPT}")
    print("=" * 70 + "\n")

    if not MERGE_SCRIPT.exists():
        print(f"❌ Erreur : Script de merge introuvable à {MERGE_SCRIPT}")
        sys.exit(1)

    cmd = [sys.executable, str(MERGE_SCRIPT)]
    res = subprocess.run(cmd, cwd=str(CURRENT_DIR))
    sys.exit(res.returncode)


if __name__ == "__main__":
    main()
