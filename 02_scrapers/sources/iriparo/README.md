# Scraper : IRIPARO

## 📋 Présentation
iRiparo scraper: discover devices and repair prices via official JSON API.

Flux de découverte et scraping :
  1. GET https://parislecourbe.iriparo.com/iriparo_dev/get_brands.php (Liste des 48 marques)
  2. GET https://parislecourbe.iriparo.com/iriparo_dev/get_devices.php?brand=<brand> (Modèles d'appareils)
  3. GET https://parislecourbe.iriparo.com/iriparo_dev/get_interventions.php?organization_id=2&device_id=<id> (Tarifs officiels en France)

- **Nom de la source** : `iriparo`
- **Domaine de données** : `product_sales`
- **URL cible** : https://parislecourbe.iriparo.com/iriparo_dev/get_brands.php
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/iriparo
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/iriparo/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_iriparo.csv` : Fichier tabulaire complet
2. `db_iriparo.json` : Export JSON structuré
3. `db_iriparo.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_iriparo_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_iriparo_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
IRIPARO_API_KEY=
IRIPARO_PROXY=
```
