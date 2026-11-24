#!/usr/bin/env python3
"""
Script d arrêt propre pour l orchestrateur de normalisation IA E-commerce AI Scraping.
"""
from pathlib import Path
import urllib.request

stop_file = Path("/Volumes/MonSSD/scraping/ecommerce-ai-scraping/03_ia_normalizer/.stop_orchestrator")
stop_file.touch()

print("🛑 Fichier .stop_orchestrator créé.")
print("Envoi du signal d arrêt au dashboard...")

try:
    req = urllib.request.Request("http://127.0.0.1:8080/api/stop", method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        print("Réponse serveur :", resp.read().decode())
except Exception as e:
    print("Erreur ou serveur non joignable :", e)

print("✅ L orchestrateur s arrêtera proprement dès la fin du lot en cours, avec sauvegarde intégrale du cache.")
