# Scraper : LOCAL_SHOPS

## 📋 Présentation
Generic targets scraper for mixed local shop pages listed in targets.csv.

- **Nom de la source** : `local_shops`
- **Domaine de données** : `product_sales`
- **URL cible** : Voir la configuration interne
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/local_shops
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/local_shops/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_local_shops.csv` : Fichier tabulaire complet
2. `db_local_shops.json` : Export JSON structuré
3. `db_local_shops.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_local_shops_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_local_shops_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
LOCAL_SHOPS_API_KEY=
LOCAL_SHOPS_PROXY=
```
