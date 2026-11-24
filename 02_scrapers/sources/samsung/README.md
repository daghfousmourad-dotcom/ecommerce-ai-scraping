# Scraper : SAMSUNG

## 📋 Présentation
Samsung Smart Repair Scraper (sources2).

Extrait les tarifs officiels de réparation constructeur Samsung France :
  URL: https://www.samsung.com/fr/support/smart-repair-service/mobile-devices/
  Couvre les smartphones Galaxy (Z Fold, Z Flip, S Series, A Series, Note),
  les tablettes Galaxy Tab, les montres Galaxy Watch et Galaxy Ring.

- **Nom de la source** : `samsung`
- **Domaine de données** : `repair_and_parts`
- **URL cible** : https://www.samsung.com/fr/support/smart-repair-service/mobile-devices/
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/samsung
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/samsung/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_samsung.csv` : Fichier tabulaire complet
2. `db_samsung.json` : Export JSON structuré
3. `db_samsung.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_samsung_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_samsung_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
SAMSUNG_API_KEY=
SAMSUNG_PROXY=
```
