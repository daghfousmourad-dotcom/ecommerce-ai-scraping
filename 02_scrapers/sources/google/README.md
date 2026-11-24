# Scraper : GOOGLE

## 📋 Présentation
Google Pixel Official Repair Cost Scraper (https://store.google.com/fr/repair-cost-estimator?hl=fr).

Tarifs officiels des réparations constructeur Google Pixel en France :
Stratégie ordonnée (Roadmap E-commerce AI Scraping) :
  1. API Directe (Google Store RPC Batchexecute - rpcid: QQr9wd) :
     - Découverte des modèles Pixel (lv4/ISE) : extraction directe depuis le bundle initial HTML
     - Interrogation du endpoint RPC batchexecute par modèle
     - Extraction précise au centime d'euro des 16 catégories officielles de panne constructeur
     Fournit 100% des tarifs officiels constructeur Google Pixel en France en ~3 secondes.

- **Nom de la source** : `google`
- **Domaine de données** : `repair_and_parts`
- **URL cible** : https://store.google.com/fr/repair-cost-estimator?hl=fr).
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/google
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/google/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_google.csv` : Fichier tabulaire complet
2. `db_google.json` : Export JSON structuré
3. `db_google.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_google_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_google_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
GOOGLE_API_KEY=
GOOGLE_PROXY=
```
