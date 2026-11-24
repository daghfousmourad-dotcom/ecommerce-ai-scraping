# Scraper : APPLE

## 📋 Présentation
Apple iPhone Repair Official Scraper (sources2).

Extrait les tarifs officiels de réparation constructeur Apple France :
  API: https://support.apple.com/ols/api/pricing/products/services/pricing-estimate
  Page: https://support.apple.com/fr-fr/iphone/repair
  Type de tarification: OOW (Out Of Warranty / Hors Garantie France)

- **Nom de la source** : `apple`
- **Domaine de données** : `repair_and_parts`
- **URL cible** : https://support.apple.com/ols/api/pricing/products/services/pricing-estimate
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/apple
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/apple/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_apple.csv` : Fichier tabulaire complet
2. `db_apple.json` : Export JSON structuré
3. `db_apple.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_apple_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_apple_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
APPLE_API_KEY=
APPLE_PROXY=
```
