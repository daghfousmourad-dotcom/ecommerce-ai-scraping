import React, { useState, useMemo } from "react";
import { Search, ChevronRight, Smartphone } from "lucide-react";
import { PageHeaderBanner } from "../components/PageHeaderBanner";

export function BrandSelectionView({ brands, onSelectBrand }) {
  const [search, setSearch] = useState("");
  const [filterCategory, setFilterCategory] = useState("all");

  const LEADERS = ["Apple", "Samsung", "Xiaomi", "Google", "Huawei", "Honor", "Oppo", "Sony", "Oneplus", "Realme"];

  const filteredBrands = useMemo(() => {
    return brands.filter((b) => {
      const matchSearch = b.name.toLowerCase().includes(search.toLowerCase().trim());
      if (!matchSearch) return false;
      if (filterCategory === "leaders") return LEADERS.includes(b.name);
      if (filterCategory === "others") return !LEADERS.includes(b.name);
      return true;
    });
  }, [brands, search, filterCategory]);

  return (
    <div className="view-container animate-fade-in">
      {/* Grand Titre E-commerce AI Scraping (Bêta) */}
      <PageHeaderBanner />

      {/* Intro descriptive sans les mentions supprimées */}
      <div className="brand-intro-section">
        <h2 className="brand-section-heading">Sélectionnez la Marque de Smartphone</h2>
        <p className="brand-section-subheading">
          Explorez les grilles tarifaires parmi <strong>{brands.length} marques officielles</strong> répertoriées.
        </p>
      </div>

      {/* Search & Filter Bar */}
      <div className="search-filter-card">
        <div className="search-input-wrapper">
          <Search className="w-5 h-5 text-slate-400" />
          <input
            type="text"
            placeholder="Rechercher une marque (Apple, Samsung, Xiaomi, Google, Oppo, Sony...)"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="search-input"
          />
          {search && (
            <button 
              type="button" 
              onClick={() => setSearch("")} 
              className="search-clear-btn"
            >
              ✕
            </button>
          )}
        </div>

        <div className="filter-pills-row">
          <button
            type="button"
            className={`pill-btn ${filterCategory === "all" ? "active" : ""}`}
            onClick={() => setFilterCategory("all")}
          >
            Toutes les marques ({brands.length})
          </button>
          <button
            type="button"
            className={`pill-btn ${filterCategory === "leaders" ? "active" : ""}`}
            onClick={() => setFilterCategory("leaders")}
          >
            Marques Principales ({brands.filter(b => LEADERS.includes(b.name)).length})
          </button>
          <button
            type="button"
            className={`pill-btn ${filterCategory === "others" ? "active" : ""}`}
            onClick={() => setFilterCategory("others")}
          >
            Autres Constructeurs ({brands.filter(b => !LEADERS.includes(b.name)).length})
          </button>
        </div>
      </div>

      {/* Brands Grid (Grandes images nettes pour toutes les marques sans exception, pas de TOP VENTE) */}
      <div className="brands-grid">
        {filteredBrands.map((brand) => {
          return (
            <div
              key={brand.slug}
              className="brand-card brand-card-large"
              onClick={() => onSelectBrand(brand)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === "Enter" && onSelectBrand(brand)}
            >
              {/* Grand conteneur logo officiel de la marque */}
              <div className="brand-logo-large-box">
                <img 
                  src={`/logos/${brand.slug}.svg`} 
                  alt={`Logo officiel ${brand.name}`} 
                  className="brand-logo-img-large"
                  loading="lazy"
                />
              </div>

              <div className="brand-info-block">
                <h3 className="brand-name-title">{brand.name}</h3>
                
                <div className="brand-stats-row">
                  <span className="stat-pill models-pill">
                    <Smartphone className="w-3.5 h-3.5" />
                    <strong>{brand.models_count}</strong> modèles
                  </span>
                  <span className="stat-pill offers-pill">
                    <strong>{brand.total_offers.toLocaleString()}</strong> devis
                  </span>
                </div>
              </div>

              <div className="brand-card-footer">
                <span className="explore-text">Voir les modèles</span>
                <ChevronRight className="w-4 h-4 chevron-icon" />
              </div>
            </div>
          );
        })}
      </div>

      {filteredBrands.length === 0 && (
        <div className="empty-state-card">
          <p className="empty-state-title">Aucune marque trouvée pour "{search}"</p>
          <p className="empty-state-desc">Essayez un autre mot-clé ou réinitialisez la recherche.</p>
          <button type="button" className="btn-secondary" onClick={() => { setSearch(""); setFilterCategory("all"); }}>
            Réinitialiser les filtres
          </button>
        </div>
      )}
    </div>
  );
}
