# Scraper : PHONE_CBD

## 📋 Présentation
Phone CBD repair scraper (sources2).
Intégré dans ecommerce_ai_scraping_scraper_V2-main selon les conventions de l'architecture V2.

- **Nom de la source** : `phone_cbd`
- **Domaine de données** : `product_sales`
- **URL cible** : https://phoneandcbd.fr
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/phone_cbd
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/phone_cbd/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_phone_cbd.csv` : Fichier tabulaire complet
2. `db_phone_cbd.json` : Export JSON structuré
3. `db_phone_cbd.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_phone_cbd_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_phone_cbd_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
PHONE_CBD_API_KEY=
PHONE_CBD_PROXY=
```
