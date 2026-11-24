# Scraper : BOULANGER

## 📋 Présentation
scraper_boulanger.py
====================
Scraper haute performance pour Boulanger (Smartphones Neufs & Reconditionnés).
Cible : Enseigne N°20 (Priorité 1) de la cartographie E-commerce AI Scraping.
Présence locale stratégique :
  - Boulanger Marseille Prado (261 Avenue du Prado, 13008 Marseille)
  - Boulanger Aubagne (ZI des Paluds, 13400 Aubagne)
  - Boulanger Cabriès - Plan de Campagne (13480 Cabriès)
  + Desserte e-commerce livraison express Marseille & national.

Univers : product_sales (23 colonnes standardisées E-commerce AI V2)
Périmètre d extraction :
  - Catalogue complet Téléphonie / Smartphones :
    https://www.boulanger.com/c/smartphone-telephone-portable?numPage=N (Pages 1 à 49)
  - Extraction native des 2 univers : Neuf constructeur & Reconditionné "2nd Life"
  - Extraction hybride : Flux JSON analytics intégré + JSON-LD Schema.org + DOM

Génère 5 fichiers d artefacts standardisés dans ./outputs/ :
  1. db_boulanger.json
  2. db_boulanger.jsonl
  3. db_boulanger.csv
  4. db_boulanger_run.json
  5. db_boulanger_issues.jsonl

- **Nom de la source** : `boulanger`
- **Domaine de données** : `product_sales`
- **URL cible** : https://www.boulanger.com/c/smartphone-telephone-portable?numPage=N
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/boulanger
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/boulanger/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_boulanger.csv` : Fichier tabulaire complet
2. `db_boulanger.json` : Export JSON structuré
3. `db_boulanger.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_boulanger_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_boulanger_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
BOULANGER_API_KEY=
BOULANGER_PROXY=
```
