# Scraper : ELECTRO_DEPOT

## 📋 Présentation
scraper_electro_depot.py
========================
Scraper haute performance et résilient pour Electro Dépôt (Smartphones Neufs & Reconditionnés).
Cible : Enseigne N°24 (Priorité 1) de la cartographie E-commerce AI Scraping, présent à Marseille La Valentine (13011).

Univers : product_sales (23 colonnes standardisées E-commerce AI V2)
Périmètre d'extraction :
  - Smartphones Neufs : https://www.electrodepot.fr/smartphone-mobilite/smartphone-telephone/smartphone.html
  - Smartphones Reconditionnés : https://www.electrodepot.fr/smartphone-mobilite/smartphone-reconditionne.html
  - Mobiles & Seniors (Optionnel) : https://www.electrodepot.fr/smartphone-mobilite/smartphone-telephone/telephone-mobile.html

Génère 5 fichiers d'artefacts dans ./outputs/ :
  1. db_electro_depot.json
  2. db_electro_depot.jsonl
  3. db_electro_depot.csv
  4. db_electro_depot_run.json
  5. db_electro_depot_issues.jsonl

- **Nom de la source** : `electro_depot`
- **Domaine de données** : `product_sales`
- **URL cible** : https://www.electrodepot.fr/smartphone-mobilite/smartphone-telephone/smartphone.html
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/electro_depot
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/electro_depot/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_electro_depot.csv` : Fichier tabulaire complet
2. `db_electro_depot.json` : Export JSON structuré
3. `db_electro_depot.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_electro_depot_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_electro_depot_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
ELECTRO_DEPOT_API_KEY=
ELECTRO_DEPOT_PROXY=
```
