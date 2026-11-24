import React, { useState, useMemo } from "react";
import { 
  ArrowLeft, 
  Eye, 
  EyeOff, 
  Lock, 
  Unlock, 
  ExternalLink, 
  ShieldCheck, 
  Clock, 
  MapPin, 
  Globe, 
  Award, 
  ArrowUpDown, 
  Sparkles, 
  Check, 
  Info,
  DollarSign
} from "lucide-react";
import { BrandLogo } from "../components/BrandLogos";
import { PageHeaderBanner } from "../components/PageHeaderBanner";

export function PriceComparisonView({ brand, model, repair, onBack }) {
  // State for blur: set of unblurred offer IDs
  const [revealedIds, setRevealedIds] = useState(new Set());
  const [allRevealed, setAllRevealed] = useState(false);
  const [sortBy, setSortBy] = useState("price_asc");
  const [filterCity, setFilterCity] = useState("all");

  const offers = repair.offers || [];

  // Toggle individual reveal
  const toggleRevealItem = (id) => {
    setRevealedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  // Toggle ALL reveal
  const toggleRevealAll = () => {
    if (allRevealed) {
      setRevealedIds(new Set());
      setAllRevealed(false);
    } else {
      const allIds = new Set(offers.map((o) => o.id));
      setRevealedIds(allIds);
      setAllRevealed(true);
    }
  };

  // Extract cities for filter
  const cities = useMemo(() => {
    const setC = new Set();
    offers.forEach((o) => {
      if (o.city && o.city !== "France") setC.add(o.city);
    });
    return Array.from(setC).sort();
  }, [offers]);

  // Sort & Filter offers
  const processedOffers = useMemo(() => {
    let list = [...offers];
    if (filterCity !== "all") {
      list = list.filter((o) => o.city === filterCity);
    }

    if (sortBy === "price_asc") {
      list.sort((a, b) => a.price - b.price);
    } else if (sortBy === "price_desc") {
      list.sort((a, b) => b.price - a.price);
    } else if (sortBy === "delay") {
      list.sort((a, b) => (a.delay || "").localeCompare(b.delay || ""));
    } else if (sortBy === "warranty") {
      list.sort((a, b) => (b.warranty || "").localeCompare(a.warranty || ""));
    }
    return list;
  }, [offers, sortBy, filterCity]);

  // Calculate pricing statistics
  const minPrice = useMemo(() => {
    const valid = offers.map((o) => o.price).filter((p) => p > 0);
    return valid.length ? Math.min(...valid) : null;
  }, [offers]);

  const maxPrice = useMemo(() => {
    const valid = offers.map((o) => o.price).filter((p) => p > 0);
    return valid.length ? Math.max(...valid) : null;
  }, [offers]);

  const avgPrice = useMemo(() => {
    const valid = offers.map((o) => o.price).filter((p) => p > 0);
    return valid.length ? (valid.reduce((a, b) => a + b, 0) / valid.length) : null;
  }, [offers]);

  const maxSaving = minPrice && maxPrice ? (maxPrice - minPrice) : 0;

  return (
    <div className="view-container animate-fade-in">
      {/* Grand Titre E-commerce AI Scraping (Bêta) */}
      <PageHeaderBanner />

      {/* Top Header */}
      <div className="subpage-header">
        <button type="button" onClick={onBack} className="btn-back">
          <ArrowLeft className="w-4 h-4" />
          <span>Changer de Réparation</span>
        </button>

        <div className="comparison-banner">
          <div className="banner-context-col">
            <div className="banner-tags-row">
              <span className="banner-brand-tag">
                <BrandLogo brand={brand.name} className="w-4 h-4 inline mr-1" color="#2563eb" />
                {brand.name}
              </span>
              <span className="banner-model-tag">{model.model}</span>
              <span className="banner-repair-tag">{repair.name}</span>
            </div>
            <h1 className="comparison-title">Comparatif des Tarifs en Atelier</h1>
            <p className="comparison-subtitle">
              {offers.length} réparateurs référencés en temps réel avec devis certifiés, délais et garanties.
            </p>
          </div>

          {/* Pricing Highlight Box */}
          <div className="pricing-stat-cards">
            {minPrice !== null && (
              <div className="stat-card stat-best-price">
                <div className="stat-label">Meilleur Prix Constaté</div>
                <div className="stat-value text-emerald-600">{minPrice.toFixed(2)} €</div>
                <div className="stat-sub">TTC Pièce & Main-d'œuvre</div>
              </div>
            )}
            {avgPrice !== null && (
              <div className="stat-card">
                <div className="stat-label">Prix Moyen du Marché</div>
                <div className="stat-value">{avgPrice.toFixed(2)} €</div>
                <div className="stat-sub">Moyenne des 10 réseaux</div>
              </div>
            )}
            {maxSaving > 5 && (
              <div className="stat-card stat-saving">
                <div className="stat-label">Économie Potentielle</div>
                <div className="stat-value text-blue-600">jusqu'à {maxSaving.toFixed(0)} €</div>
                <div className="stat-sub">selon l'atelier sélectionné</div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Commercial Blur Control Bar (FEATURE MAJEURE) */}
      <div className="blur-control-card">
        <div className="blur-control-left">
          <div className="shield-icon-wrapper">
            {allRevealed ? (
              <Unlock className="w-5 h-5 text-emerald-600" />
            ) : (
              <Lock className="w-5 h-5 text-amber-600" />
            )}
          </div>
          <div>
            <div className="blur-status-title">
              {allRevealed ? "Mode Démonstration Complète (Sources visibles)" : "Mode Commercial Confidentiel (Sources floutées)"}
            </div>
            <p className="blur-status-desc">
              {allRevealed 
                ? "Toutes les enseignes sources et adresses sont visibles. Cliquez ci-contre pour reflouter." 
                : "Les sites web sources et adresses des réparateurs sont masqués pour protéger l'argumentaire commercial."}
            </p>
          </div>
        </div>

        {/* Global Action Button */}
        <div className="blur-control-actions">
          <button
            type="button"
            className={`btn-master-toggle ${allRevealed ? "revealed" : "blurred"}`}
            onClick={toggleRevealAll}
          >
            {allRevealed ? (
              <>
                <EyeOff className="w-4 h-4" />
                <span>Tout Reflouter (Mode Prospect)</span>
              </>
            ) : (
              <>
                <Eye className="w-4 h-4" />
                <span>Tout Déflouter (Action Générale)</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Filters and Sorting Toolbar */}
      <div className="offers-toolbar">
        <div className="toolbar-left">
          <span className="offers-count-badge">
            <strong>{processedOffers.length}</strong> offres trouvées
          </span>
          {cities.length > 0 && (
            <select
              value={filterCity}
              onChange={(e) => setFilterCity(e.target.value)}
              className="toolbar-select"
            >
              <option value="all">Toutes les villes ({offers.length})</option>
              {cities.map((city) => (
                <option key={city} value={city}>
                  {city} ({offers.filter(o => o.city === city).length})
                </option>
              ))}
            </select>
          )}
        </div>

        <div className="toolbar-right">
          <span className="sort-label">Trier par :</span>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            className="toolbar-select"
          >
            <option value="price_asc">Prix le plus bas (Recommandé)</option>
            <option value="price_desc">Prix le plus élevé</option>
            <option value="delay">Délai d'intervention</option>
            <option value="warranty">Garantie</option>
          </select>
        </div>
      </div>

      {/* Offers Cards List */}
      <div className="offers-list">
        {processedOffers.map((offer, index) => {
          const isRevealed = allRevealed || revealedIds.has(offer.id);
          const isBestPrice = offer.price === minPrice;

          return (
            <div 
              key={offer.id} 
              className={`offer-card ${isBestPrice ? "best-price-card" : ""}`}
            >
              {isBestPrice && (
                <div className="best-price-ribbon">
                  <Award className="w-3.5 h-3.5" />
                  <span>Meilleur Tarif</span>
                </div>
              )}

              {/* Price Column */}
              <div className="offer-col-price">
                <div className="offer-price-number">{offer.price.toFixed(2)} €</div>
                <div className="offer-price-tax">TTC tout inclus</div>
                {minPrice && offer.price > minPrice && (
                  <div className="offer-price-diff">
                    +{(offer.price - minPrice).toFixed(0)} € vs meilleur prix
                  </div>
                )}
              </div>

              {/* Prestation Details Column */}
              <div className="offer-col-details">
                <h3 className="offer-repair-name" title={offer.label}>{offer.label}</h3>
                
                <div className="offer-specs-badges">
                  {offer.delay && (
                    <span className="spec-badge delay-badge">
                      <Clock className="w-3.5 h-3.5 text-amber-600" />
                      <span>{offer.delay}</span>
                    </span>
                  )}
                  {offer.warranty && (
                    <span className="spec-badge warranty-badge">
                      <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
                      <span>{offer.warranty}</span>
                    </span>
                  )}
                  <span className="spec-badge part-badge">Pièce certifiée</span>
                </div>
              </div>

              {/* BLURRED ZONE: Source Website & Address */}
              <div className="offer-col-source">
                <div className="source-box">
                  <div className="source-label-row">
                    <span className="source-label">Atelier & Source Web :</span>
                    {!isRevealed && (
                      <span className="confidential-tag">
                        <Lock className="w-3 h-3 inline mr-1" />
                        Masqué
                      </span>
                    )}
                  </div>

                  {/* Content with blur condition */}
                  <div className={`source-content-wrapper ${isRevealed ? "revealed" : "blur-shield"}`}>
                    <div className="source-shop-name">
                      {offer.shop || offer.site}
                    </div>

                    <div className="source-web-url">
                      <Globe className="w-3.5 h-3.5 text-blue-500" />
                      <span>{offer.site ? `${offer.site}.fr / .com` : "Site officiel"}</span>
                    </div>

                    <div className="source-address">
                      <MapPin className="w-3.5 h-3.5 text-slate-500" />
                      <span>{offer.city}{offer.postal ? ` (${offer.postal})` : ""}</span>
                    </div>
                  </div>
                </div>

                {/* Individual Toggle Button */}
                <button
                  type="button"
                  className={`btn-single-toggle ${isRevealed ? "btn-hide" : "btn-reveal"}`}
                  onClick={() => toggleRevealItem(offer.id)}
                  title={isRevealed ? "Masquer ce réparateur" : "Révéler ce réparateur"}
                >
                  {isRevealed ? (
                    <>
                      <EyeOff className="w-3.5 h-3.5" />
                      <span>Reflouter</span>
                    </>
                  ) : (
                    <>
                      <Eye className="w-3.5 h-3.5" />
                      <span>Révéler la source</span>
                    </>
                  )}
                </button>
              </div>

              {/* External Link or Verified Badge */}
              <div className="offer-col-action">
                {isRevealed && offer.url ? (
                  <a
                    href={offer.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="btn-visit-source"
                  >
                    <span>Voir l'offre</span>
                    <ExternalLink className="w-3.5 h-3.5" />
                  </a>
                ) : (
                  <div className="quote-verified-pill">
                    <Check className="w-3.5 h-3.5 text-emerald-600" />
                    <span>Tarif Audité</span>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {processedOffers.length === 0 && (
        <div className="empty-state-card">
          <p className="empty-state-title">Aucune offre correspondant aux filtres.</p>
          <button type="button" className="btn-secondary" onClick={() => setFilterCity("all")}>
            Réinitialiser le filtre de ville
          </button>
        </div>
      )}
    </div>
  );
}
