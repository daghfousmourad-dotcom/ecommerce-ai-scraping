# Scraper : LEBONCOIN

## 📋 Présentation
scraper_leboncoin.py
====================
Scraper haute performance & furtif pour Leboncoin (Smartphones Occasion & Vente).
Basé sur la stratégie validée de Darty (SeleniumBase UC Mode + uc_open_with_reconnect).

Stratégie anti-bot :
- Undetected ChromeDriver (UC Mode) avec headed=True masqué
- Reconnexion furtive (uc_open_with_reconnect) pour contourner DataDome / CloudFront
- Résolution automatique du slider captcha DataDome (uc_gui_handle_captcha)
- Défilement progressif humain naturel (smooth scroll) avec micro-pauses aléatoires
- Pause aléatoire humaine inter-pages (anti-burst rate limit)
- Extraction directe des données structurées natives Next.js (__NEXT_DATA__)
- Sauvegarde incrémentale à chaque lot de pages (zéro perte de données)
- Univers : product_sales (23 colonnes standardisées E-commerce AI V2)

Génère 5 fichiers d'artefacts dans ./outputs/ :
  1. db_leboncoin.json
  2. db_leboncoin.jsonl
  3. db_leboncoin.csv
  4. db_leboncoin_run.json
  5. db_leboncoin_issues.jsonl

- **Nom de la source** : `leboncoin`
- **Domaine de données** : `product_sales`
- **URL cible** : https://www.leboncoin.fr
- **Technologie principale** : `Selenium Undetected-Chromedriver`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/leboncoin
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/leboncoin/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_leboncoin.csv` : Fichier tabulaire complet
2. `db_leboncoin.json` : Export JSON structuré
3. `db_leboncoin.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_leboncoin_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_leboncoin_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
LEBONCOIN_API_KEY=
LEBONCOIN_PROXY=
```
