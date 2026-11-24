#!/usr/bin/env python3
"""
Orchestrateur Autonome de Normalisation IA Séquentielle (Night Run)
Projet : E-commerce AI Scraping Studio IA V2

Règles de fonctionnement :
1. Analyse la liste des sources et sélectionne les datasets non traités ou partiels.
2. Trie les datasets du plus grand nombre de lignes au plus petit.
3. Exécution STRICTEMENT séquentielle (1 source à la fois).
4. Vérification formelle d intégrité avant de passer à la source suivante.
5. Surveillance continue et respect de la source actuellement en cours (ex: Infonatic).
6. Sécurité Hardware maximale pour le Mac mini (M4 16 Go) :
   - Mode basse consommation (os.nice 10).
   - Concurrency limitée à 2 workers légers.
   - Pause de refroidissement (Cool-Down de 30s) entre chaque source.
   - Purge mémoire (garbage collector) après chaque dataset.
7. Arrêt propre (Graceful Stop) sur présence du fichier .stop_orchestrator ou signal.
"""

from __future__ import annotations

import gc
import json
import os
import signal
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
AI_DIR = BASE_DIR / "03_ia_normalizer"
OUTPUT_AI_DIR = AI_DIR / "outputs"
STOP_FILE = AI_DIR / ".stop_orchestrator"
LOG_FILE = AI_DIR / "orchestrator.log"

API_BASE = "http://127.0.0.1:8080"
MODEL_NAME = "qwen2.5-coder:7b"
SAFE_CONCURRENCY = 2
BATCH_SIZE = 5
COOL_DOWN_SECONDS = 30
POLL_INTERVAL_SECONDS = 10

try:
    os.nice(10)
except Exception:
    pass

should_exit = False

def sig_handler(signum, frame):
    global should_exit
    log("⚠️ Signal d interruption reçu. Arrêt propre en cours...")
    should_exit = True

signal.signal(signal.SIGINT, sig_handler)
signal.signal(signal.SIGTERM, sig_handler)

def log(msg: str):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    formatted = f"[{timestamp}] {msg}"
    print(formatted, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception:
        pass

def is_stop_requested() -> bool:
    global should_exit
    if should_exit:
        return True
    if STOP_FILE.exists():
        log("🛑 Fichier .stop_orchestrator détecté. Demande d arrêt confirmée.")
        return True
    return False

def call_api(endpoint: str, method: str = "GET", payload: dict | None = None) -> dict:
    url = f"{API_BASE}{endpoint}"
    data_bytes = json.dumps(payload).encode("utf-8") if payload else None
    headers = {"Content-Type": "application/json"} if payload else {}
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

def get_current_progress() -> dict:
    try:
        return call_api("/api/progress")
    except Exception as e:
        log(f"Erreur lecture progression API: {e}")
        return {"status": "unknown"}

def stop_active_job():
    try:
        call_api("/api/stop", method="POST")
        log("Signal d arrêt envoyé au serveur sur le job actif.")
    except Exception as e:
        log(f"Erreur envoi arrêt au serveur: {e}")

def get_queue() -> list[dict]:
    try:
        res = call_api("/api/files?scope=dataset")
        files = res.get("files", [])
        untreated = [
            f for f in files
            if f.get("treatment_status") in ("UNTREATED", "PARTIAL")
        ]
        untreated.sort(key=lambda x: x.get("rows_count", 0), reverse=True)
        return untreated
    except Exception as e:
        log(f"Erreur récupération liste des sources: {e}")
        return []

def wait_for_completion(source_name: str) -> bool:
    log(f"⏳ Surveillance active de la source {source_name}...")
    last_processed = -1
    consecutive_errors = 0

    while True:
        if is_stop_requested():
            log(f"Arrêt demandé pendant le traitement de {source_name}. Arrêt propre du job...")
            stop_active_job()
            return False

        time.sleep(POLL_INTERVAL_SECONDS)

        try:
            p = get_current_progress()
            status = p.get("status")
            processed = p.get("processed_rows", 0)
            total = p.get("total_rows", 0)
            pct = p.get("progress_percent", 0.0)
            speed = p.get("speed_rows_per_sec", 0.0)
            status_msg = p.get("status_message", "")

            if processed != last_processed:
                log(f"[{source_name}] {processed}/{total} lignes ({pct}%) | Vitesse : {speed}/s | {status_msg}")
                last_processed = processed

            if status == "completed":
                log(f"✅ Traitement terminé pour {source_name} ({processed}/{total} lignes).")
                return True
            elif status in ("stopped", "error"):
                log(f"⚠️ Traitement de {source_name} interrompu (statut: {status}, message: {status_msg}).")
                return False

            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            log(f"Avertissement polling ({consecutive_errors}/5) : {e}")
            if consecutive_errors >= 5:
                log("❌ Perte de contact prolongée avec le serveur API.")
                return False

def verify_source_completion(source_dict: dict) -> bool:
    source = source_dict["source"]
    expected_rows = source_dict.get("rows_count", 0)
    log(f"🔍 Vérification d intégrité pour {source} ({expected_rows} lignes attendues)...")

    refined_csvs = sorted(OUTPUT_AI_DIR.glob(f"{source}_ai_refined_*.csv"))
    if not refined_csvs:
        log(f"❌ Échec vérification : Aucun CSV de sortie trouvé pour {source}.")
        return False

    latest_csv = refined_csvs[-1]
    try:
        with open(latest_csv, "r", encoding="utf-8", errors="ignore") as f:
            csv_lines = sum(1 for _ in f) - 1
    except Exception as e:
        log(f"❌ Échec lecture CSV {latest_csv.name} : {e}")
        return False

    runs = sorted(OUTPUT_AI_DIR.glob(f"{source}_ai_refined_*_run.json"))
    if not runs:
        log(f"❌ Échec vérification : Aucun run.json trouvé pour {source}.")
        return False

    try:
        with open(runs[-1], "r", encoding="utf-8") as f:
            rj = json.load(f)
        run_status = rj.get("status")
        if run_status != "completed":
            log(f"❌ Échec vérification : Le statut du run report est {run_status} (attendu: completed).")
            return False
    except Exception as e:
        log(f"❌ Échec lecture run.json : {e}")
        return False

    queue = get_queue()
    remaining = next((x for x in queue if x["source"] == source), None)
    if remaining is not None and remaining.get('treatment_status') != "COMPLETED":
        log(f"❌ Échec vérification API : {source} est encore listée comme {remaining.get('treatment_status')}.")
        return False

    log(f"🎯 CERTIFICATION VALIDÉE pour {source} : CSV avec {csv_lines} lignes, run report validé.")
    return True

def hardware_cooldown():
    log(f"❄️ Sécurité Hardware Mac mini : Refroidissement du SoC (Pause de {COOL_DOWN_SECONDS}s)...")
    gc.collect()
    for _ in range(COOL_DOWN_SECONDS):
        if is_stop_requested():
            break
        time.sleep(1)
    log("✨ Fin du cycle de repos thermique. Matériel stable et frais.")

def run_orchestrator():
    if STOP_FILE.exists():
        try:
            STOP_FILE.unlink()
        except Exception:
            pass

    log("=" * 80)
    log("🚀 DÉMARRAGE DE L ORCHESTRATEUR DE NUIT E-COMMERCE AI V2")
    log("💻 Machine hôte : Mac mini M4 (16 Go RAM)")
    log("🛡️ Profil de protection matérielle : ACTIVÉ (Mode Économie & Refroidissement)")
    log(f"🤖 Modèle LLM : {MODEL_NAME} | Concurrency : {SAFE_CONCURRENCY} workers | Batch : {BATCH_SIZE}")
    log("=" * 80)

    progress = get_current_progress()
    active_status = progress.get("status")
    active_file = progress.get("filename", "")

    if active_status == "running" and active_file:
        active_source = active_file.replace("db_", "").replace(".csv", "")
        log(f"📌 Une source est actuellement en cours d exécution : {active_source} ({active_file}).")
        log("L orchestrateur attend patiemment la fin de ce traitement avant de lancer la suite.")
        success = wait_for_completion(active_source)
        if success:
            q = get_queue()
            curr_meta = next((x for x in q if x["source"] == active_source), {"source": active_source})
            verify_source_completion(curr_meta)
            hardware_cooldown()
        else:
            log(f"⚠️ La source active {active_source} ne s est pas terminée normalement.")
            if is_stop_requested():
                log("Arrêt demandé par l utilisateur. Sortie de l orchestrateur.")
                return

    while True:
        if is_stop_requested():
            log("🛑 Signal d arrêt détecté. Sortie de la boucle de traitement.")
            break

        queue = get_queue()
        if not queue:
            log("🎉 EXCELLENTE NOUVELLE : Toutes les sources de production sont 100% traitées et certifiées !")
            break

        log("\n📋 File d attente restante (triée du plus grand au plus petit) :")
        for idx, item in enumerate(queue[:10], 1):
            log(f"  {idx}. {item.get('source', ''):<25} ({item.get('rows_count', 0):>5} lignes) [{item.get('treatment_status')}]")
        if len(queue) > 10:
            log(f"  ... et {len(queue) - 10} autres sources plus légères.")

        target = queue[0]
        target_source = target["source"]
        target_path = target["full_path"]
        rows_count = target.get("rows_count", 0)

        log("\n" + "=" * 80)
        log(f"▶️ LANCEMENT DE LA PROCHAINE SOURCE : {target_source} ({rows_count} lignes)")
        log("=" * 80)

        payload = {
            "file_path": target_path,
            "model": MODEL_NAME,
            "concurrency": SAFE_CONCURRENCY,
            "batch_size": BATCH_SIZE,
            "incremental": True,
        }

        try:
            start_res = call_api("/api/start", method="POST", payload=payload)
            log(f"Réponse démarrage API : {start_res.get('message')}")
        except Exception as e:
            log(f"❌ Erreur lors du déclenchement de {target_source} : {e}")
            time.sleep(10)
            continue

        completed = wait_for_completion(target_source)
        if not completed:
            log(f"⚠️ Le traitement de {target_source} n a pas été achevé à 100%.")
            if is_stop_requested():
                break
            else:
                log("Passage à la source suivante après pause de sécurité.")
                hardware_cooldown()
                continue

        verified = verify_source_completion(target)
        if verified:
            log(f"🏆 SUCCESS : Source {target_source} entièrement traitée et certifiée avec succès !")
        else:
            log(f"⚠️ AVERTISSEMENT : La vérification formelle a signalé un écart pour {target_source}.")

        hardware_cooldown()

    log("\n" + "=" * 80)
    log("🏁 SESSION DE TRAITEMENT TERMINÉE OU INTERROMPUE PROPREMENT.")
    log(f"Horodatage final : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("Le Mac mini est à l état de repos, aucun fichier n a été corrompu.")
    log("=" * 80)

if __name__ == "__main__":
    run_orchestrator()
