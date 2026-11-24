# Scraper : RAKUTEN

## 📋 Présentation
scraper_rakuten.py
===================
Scraper haute performance et résilient pour Rakuten France (fr.shopping.rakuten.com).
Cible : Marketplace majeure Téléphonie / Smartphones Neufs, Reconditionnés & Occasion.

Architecture & Contournement Anti-Bot :
  - Rakuten France est protégé par DataDome (geo.captcha-delivery.com, status 403 sur requêtes directes).
  - Contournement maîtrisé : Playwright orchestrant Google Chrome officiel en mode off-screen
    (--disable-blink-features=AutomationControlled, --window-position=100,100).
    Zéro fenêtre visible pour l utilisateur, exécution discrète et fluide avec 100% de succès HTTP 200.

Périmètre d extraction :
  - Catalogue global Smartphones & Téléphones mobiles (/nav/Tel-PDA_Telephones-mobiles)
  - Catalogues marques leaders : Apple, Samsung, Xiaomi, Google, OnePlus, Honor, Huawei, Oppo, Sony, Realme, Motorola
  - Catalogues reconditionnés dédiés (?ft=r)
  - Extraction duale par fiche produit :
    * Offre Neuf constructeur ("Neuf dès X €")
    * Offre Occasion / Reconditionné ("Occasion dès Y €" ou "Reconditionné dès Z €")
  - Métadonnées techniques : Stockage (Go/To), RAM (Go), Couleur officielle, Marque normalisée, ID Produit (pid/mfp).

Conformité E-commerce AI V2 :
  - 23 colonnes standardisées (PRODUCT_SALE_KEYS)
  - Validation et canonicalisation via device_validator (is_valid_smartphone_device, is_valid_sale_price, canonicalize_model_name)
  - 5 artefacts standardisés générés dans ./outputs/ :
      1. db_rakuten.json
      2. db_rakuten.jsonl
      3. db_rakuten.csv
      4. db_rakuten_run.json
      5. db_rakuten_issues.jsonl
  - Synchronisation stricte vers staging uniquement (outputs/). AUCUN envoi automatique vers dataset/ ni GitHub.

- **Nom de la source** : `rakuten`
- **Domaine de données** : `product_sales`
- **URL cible** : https://fr.shopping.rakuten.com
- **Technologie principale** : `Playwright Headless Browser (Rendu JS & Contenu dynamique)`

---

## 🚀 Exécution autonome (Individuelle)

Pour exécuter ce scraper de façon totalement autonome et indépendante :

```bash
cd 02_scrapers/sources/rakuten
python3 scraper.py
```

Ou depuis la racine du projet :

```bash
python3 02_scrapers/sources/rakuten/scraper.py
```

---

## 📦 Fichiers générés (5 Artefacts standardisés)

Tous les résultats sont automatiquement enregistrés dans `02_scrapers/outputs/` :
1. `db_rakuten.csv` : Fichier tabulaire complet
2. `db_rakuten.json` : Export JSON structuré
3. `db_rakuten.jsonl` : Export JSON Lines pour ingestion streaming
4. `db_rakuten_run.json` : Métadonnées d'exécution (horodatage, durée, statut, colonnes)
5. `db_rakuten_issues.jsonl` : Journal d'audit et erreurs de parsing / anomalies

---

## 🔒 Configuration & Identifiants

Si des clés d'accès, proxies ou identifiants spécifiques sont nécessaires pour cette source, définissez-les dans le fichier central `.env` à la racine :

```env
RAKUTEN_API_KEY=
RAKUTEN_PROXY=
```
