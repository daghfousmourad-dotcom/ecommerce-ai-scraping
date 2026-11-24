# E-commerce AI Scraping (V2)

> **Moteur de Web Scraping & Plateforme d'Intelligence Tarifaire — Version Production V2**  
> Collecte automatisée multi-sources, normalisation intelligente par IA locale et consolidation du catalogue 360° pour le marché des smartphones (Vente Neuf/Reconditionné/Occasion, Réparations Ateliers et Pièces Détachées B2B).

---

> 🏆 **CATALOGUE CONSOLIDÉ UNIFIÉ 360° (GOLDEN MASTER) :**  
> **Fichier de référence officiel :** [`04_merge_pipeline/outputs/latest/unified_catalog_database.json`](04_merge_pipeline/outputs/latest/unified_catalog_database.json)  
> **Index de recherche instantané :** [`04_merge_pipeline/outputs/latest/catalog_index.json`](04_merge_pipeline/outputs/latest/catalog_index.json)  
> **Fiches modèles unitaires :** [`04_merge_pipeline/outputs/latest/devices/`](04_merge_pipeline/outputs/latest/devices/)  
> **Statut & Couverture :** **6 400+ SKUs smartphones uniques**, 100% normalisés (Marques officielles, Modèles canoniques, Capacités Go, Couleurs, Offres de vente multi-états et Grilles complètes de pannes d'ateliers).

---

## 📌 Présentation du Projet

**E-commerce AI Scraping V2** est la plateforme centralisée d'acquisition, de traitement et d'uniformisation de données tarifaires de l'écosystème E-commerce AI Scraping. 

Elle orchestre un pipeline complet et étanche en 4 étapes clés :
1. **Extraction Web (Scraping)** sur plus de 35 sources de référence (constructeurs, marketplaces, distributeurs spécialisés, réseaux nationaux et ateliers indépendants, grossistes B2B).
2. **Nettoyage statistique & Anti-Outliers** (déduplication stricte, planchers de prix, filtrage IQR des valeurs aberrantes).
3. **Normalisation Intelligente par IA locale (Ollama / Qwen 2.5 3B)** avec résolution canonique des modèles, extraction du stockage/couleurs et catégorisation standardisée des pannes.
4. **Consolidation 360° (Merge Pipeline)** générant le Golden Master unifié et structuré par référence de smartphone.

Le moteur opère sur **deux univers métiers étanches et standardisés** :
* **Domaine Réparations & Pièces Détachées** (22 colonnes standardisées) : forfaits ateliers physiques, pièces détachées, garanties, délais et coûts d'achat grossistes HT.
* **Domaine Vente de Produits Neufs, Reconditionnés & Occasion** (23 colonnes standardisées) : prix constructeurs, prix marché, reconditionné certifié multi-grades et cotes de l'occasion C2C.

---

## 📂 Contenu et Structure du Dépôt

Le projet est structuré autour de **4 piliers modulaires et étanches**, complétés par un point d'entrée unique (`main.py`) à la racine :

```text
ecommerce_ai_scraping/
│
├── 🚀 main.py                         # CLI Centrale unifiée pour piloter tout le système
├── ⚙️ config.py                       # Registre centralisé des chemins (Pathlib)
├── 📄 requirements.txt                # Dépendances Python certifiées
├── 🔒 .env.example                    # Modèle des variables d'environnement
├── 📄 README.md                       # Guide complet d'architecture et d'utilisation
│
├── 📁 01_dashboard/                   # 🖥️ PILIER 1 : Interface Web & Studio de Contrôle (Port 8080)
│   ├── run_dashboard.py               # Script de lancement rapide du serveur Web
│   ├── server.py                      # Serveur asynchrone (aiohttp) & API de staging/production
│   ├── config.py                      # Paramétrage de l'interface et des sources
│   └── static/                        # Application Web moderne (Glassmorphism, diffs, live runs)
│
├── 📁 02_scrapers/                    # 🕷️ PILIER 2 : Moteur d'Extraction & Staging
│   ├── runner.py                      # Orchestrateur CLI multi-scrapers
│   ├── core/                          # Socle partagé (HTTP client, anti-bot, export standardisé)
│   ├── sources/                       # 35+ dossiers de scrapers autonomes (1 dossier par site)
│   │   ├── apple/                     # Scraper Apple officiel (Vente)
│   │   ├── apple_repair/              # Scraper Apple réparations officielles
│   │   ├── clinique_phone/            # Scraper Clinique Phone Rosny-sous-Bois
│   │   ├── go_smart/                  # Scraper GO SMART Boulogne-Billancourt
│   │   ├── mon_reparateur_pro/        # Scraper Mon Réparateur Pro Clermont-Ferrand
│   │   ├── prosmobile/                # Scraper ProsMobile Strasbourg
│   │   ├── wefix/                     # Scraper WeFix Fnac/Darty
│   │   ├── save/                      # Scraper Save.co Réseau national
│   │   ├── docteur_it/                # Scraper Docteur IT
│   │   ├── iriparo/                   # Scraper iRiparo France
│   │   ├── pixel_store/               # Scraper Pixel Store Salaise
│   │   ├── utopya/                    # Scraper B2B Grossiste pièces détachées Utopya
│   │   └── ...                        # (Boulanger, Darty, Easy Cash, eBay, Amazon, etc.)
│   └── outputs/                       # Sas Staging : les 5 artefacts générés par extraction
│
├── 📁 03_ia_normalizer/               # 🤖 PILIER 3 : Moteur de Normalisation par IA Locale
│   ├── run_normalizer.py              # Script d'exécution autonome de l'agent IA
│   ├── engine/                        # Cœur algorithmique (Ollama, prompts, anti-collision, slugs)
│   │   ├── engine.py                  # Normalisateur asynchrone par lots (batchs concurrents)
│   │   ├── model_normalizer/          # Moteur de résolution déterministe & dictionnaires de marques
│   │   └── prompts.py                 # Prompts système stricts et schémas JSON
│   └── outputs/                       # Données certifiées raffinées par l'IA (*_ai_refined_*)
│
├── 📁 04_merge_pipeline/              # 🏛️ PILIER 4 : Consolidation 360° du Master Dataset
│   ├── run_merge.py                   # Script de déclenchement du merge
│   ├── src/                           # Algorithme d'unification et calculs statistiques
│   │   └── merge_unified_dataset.py   # Script de fusion stricte multi-sources (Option A)
│   └── outputs/
│       └── latest/                    # 🏆 Base consolidée active
│           ├── unified_catalog_database.json # Base unifiée 360° complète
│           ├── catalog_index.json            # Index de recherche rapide multi-critères
│           └── devices/                      # Fiches JSON par smartphone
│
└── 📁 docs/                           # 📚 Documentation & Suivi
    └── sites_a_scraper.csv            # Matrice d'audit et suivi des sites cibles (priorités P0/P1/P2)
```

---

## 🌐 Sources Intégrées (35+ Sources Web & Plateformes Opérationnelles)

La plateforme centralise et unifie en continu les données tarifaires à travers 5 grands univers métiers :

* **1. Réparations en Atelier & Réseaux Spécialisés :** WeFix *(leader national Fnac/Darty)*, Save.co, Docteur IT, iRiparo France, Pixel Store, Phon'Ethik, GO SMART, Mon Réparateur Pro, Clinique Phone, ProsMobile, Smart Repair 63, Corner Mobile, Phone & CBD, Phone Express, Pixou Phone.
* **2. Grossistes Pièces Détachées (B2B) :** Utopya *(leader grossiste écrans, batteries, connecteurs)*, Xiaomi France Service.
* **3. Constructeurs Officiels (Référence Neuf & Réparation) :** Apple Store & Calculateur barème officiel hors garantie, Samsung Shop & Smart Repair, Google Pixel Repair, Honor, Oppo, Xiaomi.
* **4. Marketplaces & Distribution Grand Public :** Amazon France *(avec filtrage statistique IQR)*, eBay France *(Browse API OAuth 2.0)*, Boulanger *(neuf & 2nd Life)*, Darty, Cdiscount, LDLC, Rakuten.
* **5. Reconditionné Spécialisé & Occasion :** Back Market *(multi-grades certifiés)*, Easy Cash *(réseau achat-vente)*, SMAAART *(reconditionnement 100% français)*, Electro Dépôt, Leboncoin *(cotes C2C)*, 123Comparer *(métamoteur comparatif)*.

---

## 🤖 Normalisation Intelligente (Module IA)

Le moteur intègre un pipeline asynchrone avec LLM local garantissant une **confidentialité totale (zéro fuite cloud)** et une **standardisation industrielle** :

* **Modèle sous-jacent** : `qwen2.5:3b` opéré 100% en local via **Ollama**.
* **Résolution hybride (Déterministe + IA)** : 
  1. Résolution canonique instantanée par dictionnaire certifié (`BrandVerifier` et `CanonicalResolver`) pour identifier sans erreur le constructeur officiel et le slug du smartphone (`canonical_slug`).
  2. Inférence LLM par lots concurrents optimisés (`DEFAULT_CONCURRENCY=3`, `DEFAULT_BATCH_SIZE=5`) pour dépolluer les intitulés complexes, extraire le stockage (Go) et les couleurs, et écarter les faux positifs (coques, chargeurs, boîtes vides).
* **Sécurité Anti-Collision de Clé de Cache** :  
  Chaque enregistrement possède une empreinte composite unique :
  ```python
  key = f"{source_url}::{brand}::{model}::{part_or_title}::{price}"
  ```
  Cette clé empêche rigoureusement toute collision de cache sur les sites regroupant des centaines de modèles sur une seule page web.
* **Les 9 Catégories Canoniques de Réparation E-commerce AI Scraping** :  
  Toutes les prestations sont standardisées vers les 9 catégories officielles :
  `Ecran LCD`, `Batterie`, `Façade arrière`, `Connecteur de charge`, `Caméra avant`, `Caméra arrière`, `Lentille caméra`, `Haut-parleur`, `Désoxydation`.
* **Studio Web Interactif** : Interface de suivi avec visualisation des diffs en direct, alertes de quarantaine et métriques de débit (offres/seconde).

---

## 🚀 Démarrage Rapide

### 1. Prérequis Système
* **OS** : macOS (Apple Silicon M1/M2/M3/M4 ou Intel) ou Linux Ubuntu 22.04+.
* **Python** : Version 3.9 à 3.11.
* **Ollama** : Installé localement ([ollama.com](https://ollama.com)).

### 2. Installation pas-à-pas
```bash
# 1. Cloner le dépôt
git clone https://github.com/daghfousmourad-dotcom/ecommerce-ai-scraping.git
cd ecommerce_ai_scraping

# 2. Créer et activer l'environnement virtuel
python3 -m venv .venv
source .venv/bin/activate

# 3. Installer les dépendances Python
pip install --upgrade pip
pip install -r requirements.txt

# 4. Installer les navigateurs Playwright (nécessaire pour les scrapers dynamiques)
playwright install chromium

# 5. Démarrer le serveur Ollama et télécharger le modèle de normalisation
ollama pull qwen2.5:3b
```

---

## 🛠️ Guide d'Utilisation

### 1. Piloter la plateforme via la CLI `main.py` (Recommandé)

Le script racine `main.py` est le chef d'orchestre de la plateforme :

```bash
# === SCRAPERS ===
# 1. Lister l'ensemble des scrapers disponibles
python3 main.py --scrapers-list

# 2. Exécuter un scraper spécifique (ex: Go Smart, Clinique Phone, Mon Réparateur Pro...)
python3 main.py --scraper go_smart
python3 main.py --scraper clinique_phone
python3 main.py --scraper mon_reparateur_pro

# 3. Exécuter tous les scrapers de production
python3 main.py --scrapers-all

# === NORMALISATION IA ===
# Normaliser une source spécifique
python3 main.py --normalize go_smart

# Normaliser toutes les sources certifiées en production
python3 main.py --normalize-all

# === CONSOLIDATION 360° (MERGE) ===
# Fusionner les données normalisées et reconstruire le Golden Master
python3 main.py --merge

# === DASHBOARD & STUDIO WEB ===
# Démarrer le Studio de contrôle Web (accessible sur http://127.0.0.1:8080)
python3 main.py --dashboard
```

---

### 2. Exécution Autonome des Scrapers

Chaque scraper situé dans `02_scrapers/sources/<nom>/` est conçu pour fonctionner de manière **100% autonome et isolée** :

```bash
# Exemple : Exécution directe du scraper GO SMART Boulogne
cd 02_scrapers/sources/go_smart
python3 scraper.py

# Exemple : Exécution directe du scraper Clinique Phone
cd 02_scrapers/sources/clinique_phone
python3 scraper.py
```

#### Les 5 Artefacts Certifiés (`02_scrapers/outputs/`) :
Chaque run de scraping génère systématiquement **5 fichiers standardisés** garantissant traçabilité et reproductibilité :
1. `[source]_[type]_[timestamp].csv` : Données tabulaires nettoyées (22 ou 23 colonnes).
2. `[source]_[type]_[timestamp].jsonl` : Données au format flux (1 ligne = 1 objet JSON).
3. `[source]_[type]_[timestamp].json` : Format dictionnaire complet avec métadonnées d'exécution.
4. `[source]_[type]_[timestamp]_run.json` : Rapport d'exécution (durée, nombre de lignes, statut de propreté).
5. `[source]_[type]_[timestamp]_issues.jsonl` : Journal d'audit des rejets ou erreurs éventuelles.

---

### 3. Utilisation de l'Interface Web (Dashboard & Studio IA)

Pour piloter les opérations graphiquement :
```bash
python3 main.py --dashboard
# ou : python3 01_dashboard/run_dashboard.py --port 8080
```
Puis accédez à l'URL : **`http://127.0.0.1:8080`**.

Fonctionnalités du Dashboard :
* **Tableau de bord Staging & Production** : Comparaison en direct entre les derniers runs du dossier `outputs/` et les données de production.
* **Promotion en Production** : Valider et promouvoir une source d'un simple clic.
* **Studio de Normalisation IA** : Lancer l'agent IA, observer la progression en temps réel, analyser les rejets d'accessoires et arrêter le traitement si nécessaire.
* **Explorateur du Catalogue Unifié 360°** : Rechercher n'importe quel modèle de smartphone et visualiser ses offres de vente et forfaits de réparation.

---

## 🔒 Confidentialité & Bonnes Pratiques

* **Confidentialité absolue** : Aucune donnée tarifaire ni requête d'inférence n'est transmise à des tiers ; le modèle de langage tourne en local sur la puce de votre machine via Ollama.
* **Secrets et configurations** : Les éventuelles clés d'API (ex: eBay Browse API) doivent être renseignées dans un fichier local `.env` (non versionné, basé sur `.env.example`).
