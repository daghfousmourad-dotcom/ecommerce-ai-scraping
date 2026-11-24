# Guide d'Exploitation du Sous-Catalogue de Démonstration E-commerce AI Scraping (Top 10 Réparation Smartphone)

**Emplacement :** `/Volumes/MonSSD/scraping/ecommerce-ai-scraping/05_marketing/`  
**Contexte :** Démonstration officielle de l'application E-commerce AI Scraping pour la réparation de smartphones devant le management et les parties prenantes.  
**Intégrité garantie :** Aucun fichier source d'origine n'a été altéré. Tous les livrables sont dérivés de manière 100% autonome et isolés dans le dossier `05_marketing`.

---

## 1. Vue d'Ensemble des Fichiers Générés

| Nom du Fichier | Format | Taille | Volume de Données | Cas d'Usage Principal |
|:---|:---:|:---:|:---:|:---|
| **`subcatalog_smartphone_repairs_top10.csv`** | CSV | ~22.5 Mo | 87 272 lignes de devis | Analyse tabulaire Excel / Google Sheets, filtres rapides, benchmarks |
| **`subcatalog_smartphone_repairs_top10.json`** | JSON Plat | ~46.4 Mo | 87 272 objets | Ingestion dans une base de données, pipeline Pandas ou script Python |
| **`unified_subcatalog_database_top10.json`** | JSON Unifié | ~57.4 Mo | 4 468 modèles enrichis | Alimentation directe de l'application frontend E-commerce AI Scraping (format natif) |
| **`top_10_repair_sites_analysis.json`** | JSON | ~21 Ko | Top 10 comparatif | Métriques détaillées sur la représentativité des 10 sites |
| **`RAPPORT_ANALYSE_TOP10_SITES_REPARATION.md`** | Markdown | ~11 Ko | Document de synthèse | Support de présentation stratégique pour le manager |

---

## 2. Structure et Schéma des Données

### A. Fichiers Tabulaires (`subcatalog_smartphone_repairs_top10.csv` et `.json`)
Chaque ligne correspond à une **offre unitaire de réparation** proposée par l'un des 10 réparateurs partenaires :

| Champ | Type | Description | Exemple |
|:---|:---|:---|:---|
| `source_site` | Chaîne | Identifiant unique du scraper | `iriparo`, `wefix`, `feexo`... |
| `shop_name` | Chaîne | Nom commercial de l'enseigne / atelier | `WeFix Point de Vente / Fnac-Darty` |
| `city` | Chaîne | Ville d'implantation | `Paris`, `Lyon`, `Marseille`... |
| `postal_code` | Chaîne | Code postal | `75015`, `69003`... |
| `brand` | Chaîne | Marque normalisée du smartphone | `Apple`, `Samsung`, `Xiaomi`... |
| `model` | Chaîne | Modèle commercial du smartphone | `iPhone 13`, `Galaxy S23 5G`... |
| `canonical_slug` | Chaîne | Clé unique du modèle dans le catalogue E-commerce AI Scraping | `apple_iphone_13_128gb` |
| `repair_category` | Chaîne | Famille de panne normalisée | `screen`, `battery`, `charging_port`... |
| `repair_label` | Chaîne | Libellé exact de la prestation | `Remplacement Écran OLED`, `Batterie`... |
| `price_eur` | Float | Tarif TTC en euros | `129.0`, `79.0`, `199.9` |
| `delay` | Chaîne | Délai d'intervention garanti | `20 min`, `30 à 60 minutes`, `24h-48h` |
| `warranty` | Chaîne | Durée de garantie de la pièce/réparation | `1 an`, `4 mois`, `Garantie à vie` |
| `source_url` | Chaîne | URL directe du devis sur le site source | `https://www.wefix.net/...` |
| `scraped_at` | Horodatage | Date et heure de collecte de la donnée | `2026-09-30T19:36:15+00:00` |

---

### B. Catalogue Unifié E-commerce AI Scraping (`unified_subcatalog_database_top10.json`)
Ce fichier adopte la structure native requise par le moteur E-commerce AI Scraping :
- Chaque clé est un `canonical_slug` de smartphone (ex: `apple_iphone_14_128gb`).
- Pour chaque type d'intervention (`ecran`, `batterie`, `connecteur`, etc.), un résumé statistique (`summary`) est **recalculé dynamiquement à partir des 10 réparateurs sélectionnés** :
  - `min_price` : Meilleur prix disponible pour la démo.
  - `avg_price` : Prix moyen du marché.
  - `max_price` : Prix le plus élevé.
  - `median_price` : Médiane statistique.
  - `total_providers` : Nombre d'offres concurrentes disponibles pour ce modèle.
  - `all_providers` : Liste détaillée des devis avec enseignes, délais, garanties et liens directs.

---

## 3. Chiffres Clés à Présenter au Manager

- **87 272 offres de réparation** consolidées et dédoublées.
- **4 468 modèles distincts de smartphones** couverts.
- **49 marques représentées** (des leaders Apple, Samsung, Xiaomi jusqu'aux marques émergentes comme Honor, Google Pixel, Nothing, Crosscall, Blackview).
- **10 réseaux et ateliers majeurs** :
  1. `iRiparo` (40 marques, 2 018 modèles)
  2. `Feexo` (19 marques, 1 917 modèles)
  3. `WeFix` (20 marques, 1 046 modèles)
  4. `Pixel Store` (18 marques, 725 modèles, 11.6 réparations/modèle)
  5. `Crash Mobile` (16 marques, 730 modèles)
  6. `Phon'Ethik` (18 marques, 693 modèles)
  7. `Docteur IT` (22 marques, 458 modèles)
  8. `Smart Repair 63` (13 marques, 480 modèles)
  9. `GO SMART` (17 marques, 475 modèles)
  10. `Save.co` (14 marques, 451 modèles)

---

## 4. Scénario Recommandé pour la Démo

1. **Introduction :** Montrer `subcatalog_smartphone_repairs_top10.csv` dans Excel avec un filtre sur `Apple` -> `iPhone 13` pour montrer la comparaison instantanée des prix entre WeFix, Save, iRiparo et Pixel Store.
2. **Preuve de Profondeur :** Filtrer sur `repair_category = screen` versus `repair_category = battery` pour démontrer la variance tarifaire et la clarté du positionnement prix.
3. **Intégration Applicative :** Charger `unified_subcatalog_database_top10.json` directement dans l'interface E-commerce AI Scraping pour afficher la fiche produit avec la jauge de prix min / moyen / max et les badges de garantie.
