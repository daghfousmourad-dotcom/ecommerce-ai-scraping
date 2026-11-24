# Scraper : PIXOU_PHONE

## 📋 Présentation
scraper_pixou_phone.py
======================
Scraper haute performance pour Pixou Phone Marseille (171 Cours Lieutaud, 13006).
Interroge directement l'API JSON WordPress REST/AJAX du simulateur de devis en ligne.

Conforme aux standards E-commerce AI V2 :
- Univers : repair_and_parts (22 colonnes standardisées)
- 5 fichiers d'artefacts générés dans ./outputs/ :
  1. db_pixou_phone.json
  2. db_pixou_phone.jsonl
  3. db_pixou_phone.csv
  4. db_pixou_phone_run.json
  5. db_pixou_phone_issues.jsonl

- **Nom de la source** : `pixou_phone`
- **Domaine de données** : `product_sales`
- **URL cible** : https://pixouphone.fr/devis-en-ligne/
- **Technologie principale** : `API Directe / JSON State (Haute performance)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/pixou_phone
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/pixou_phone/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_pixou_phone.csv` : Fichier tabulaire complet
2. `db_pixou_phone.json` : Export JSON structuré
3. `db_pixou_phone.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_pixou_phone_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_pixou_phone_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
PIXOU_PHONE_API_KEY=
PIXOU_PHONE_PROXY=
```
