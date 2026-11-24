import React, { useState, useMemo } from "react";
import { Search, ArrowLeft, CheckCircle, ChevronRight } from "lucide-react";
import { PageHeaderBanner } from "../components/PageHeaderBanner";
import { ModelImage } from "../components/ModelImage";

export function ModelSelectionView({ brand, onSelectModel, onBack }) {
  const [search, setSearch] = useState("");
  const [selectedSeries, setSelectedSeries] = useState("all");

  const seriesList = useMemo(() => {
    if (!brand || !brand.models) return [];
    const bName = brand.name.toLowerCase();
    
    if (bName === "apple") {
      return ["iPhone 16", "iPhone 15", "iPhone 14", "iPhone 13", "iPhone 12", "iPhone 11", "iPhone SE"];
    }
    if (bName === "samsung") {
      return ["Galaxy S", "Galaxy Z", "Galaxy A", "Galaxy Note", "Galaxy M"];
    }
    if (bName === "xiaomi") {
      return ["Xiaomi 14", "Xiaomi 13", "Redmi Note", "Redmi", "Poco"];
    }
    if (bName === "google") {
      return ["Pixel 9", "Pixel 8", "Pixel 7", "Pixel 6", "Pixel 5"];
    }
    return [];
  }, [brand]);

  const filteredModels = useMemo(() => {
    if (!brand || !brand.models) return [];
    return brand.models.filter((m) => {
      const matchSearch = m.model.toLowerCase().includes(search.toLowerCase().trim());
      if (!matchSearch) return false;
      if (selectedSeries !== "all") {
        return m.model.toLowerCase().includes(selectedSeries.toLowerCase());
      }
      return true;
    });
  }, [brand, search, selectedSeries]);

  return (
    <div className="view-container animate-fade-in">
      {/* Grand Titre E-commerce AI Scraping (Bêta) - Conservé sur toutes les pages */}
      <PageHeaderBanner />

      {/* Top Header & Navigation */}
      <div className="subpage-header">
        <button type="button" onClick={onBack} className="btn-back">
          <ArrowLeft className="w-4 h-4" />
          <span>Changer de Marque</span>
        </button>

        <div className="subpage-title-box">
          <div className="brand-badge-pill-header">
            <img 
              src={`/logos/${brand.slug}.svg`} 
              alt={brand.name} 
              className="w-5 h-5 object-contain" 
            />
            <span>Catalogue Officiel : {brand.name}</span>
          </div>
          <h2 className="subpage-title">Sélectionnez le Modèle de Smartphone</h2>
          <p className="subpage-subtitle">
            {brand.models_count} modèles commercialisés répertoriés. Cliquez sur un modèle pour diagnostiquer les tarifs.
          </p>
        </div>
      </div>

      {/* Search & Series Filters */}
      <div className="search-filter-card">
        <div className="search-input-wrapper">
          <Search className="w-5 h-5 text-slate-400" />
          <input
            type="text"
            placeholder={`Rechercher un modèle ${brand.name} (ex: ${brand.models[0]?.model || "Pro, Ultra, Plus..."})`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="search-input"
            autoFocus
          />
          {search && (
            <button type="button" onClick={() => setSearch("")} className="search-clear-btn">
              ✕
            </button>
          )}
        </div>

        {seriesList.length > 0 && (
          <div className="filter-pills-row">
            <button
              type="button"
              className={`pill-btn ${selectedSeries === "all" ? "active" : ""}`}
              onClick={() => setSelectedSeries("all")}
            >
              Tous les modèles ({brand.models_count})
            </button>
            {seriesList.map((series) => (
              <button
                key={series}
                type="button"
                className={`pill-btn ${selectedSeries === series ? "active" : ""}`}
                onClick={() => setSelectedSeries(series)}
              >
                Série {series}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Result Counter */}
      <div className="results-meta-bar">
        <span>Affichage de <strong>{filteredModels.length}</strong> modèles disponibles</span>
        {search && <span className="search-tag">Filtre : "{search}"</span>}
      </div>

      {/* Models Grid (IMAGE POUR CHAQUE MODÈLE SANS EXCEPTION, SANS RÉPARATION) */}
      <div className="models-grid">
        {filteredModels.map((modelItem, idx) => (
          <div
            key={`${modelItem.slug}_${idx}`}
            className="model-card"
            onClick={() => onSelectModel(modelItem)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === "Enter" && onSelectModel(modelItem)}
          >
            {/* Visualisation réaliste du modèle (sans exception) */}
            <div className="model-visual-box">
              <ModelImage 
                brand={brand.name} 
                model={modelItem.model} 
                slug={modelItem.slug} 
              />
              <span className="model-brand-pill">{brand.name}</span>
            </div>

            {/* Model Title (strictement sans réparations) */}
            <div className="model-info-box">
              <h3 className="model-title" title={modelItem.model}>{modelItem.model}</h3>
              
              <div className="model-meta-row">
                <span className="verified-badge">
                  <CheckCircle className="w-3.5 h-3.5 text-emerald-600" />
                  <span>Modèle Référencé</span>
                </span>
              </div>
            </div>

            {/* Select Action Button */}
            <div className="model-card-action">
              <span className="select-model-label">Sélectionner ce modèle</span>
              <ChevronRight className="w-4 h-4 chevron-icon" />
            </div>
          </div>
        ))}
      </div>

      {filteredModels.length === 0 && (
        <div className="empty-state-card">
          <p className="empty-state-title">Aucun modèle trouvé pour "{search}"</p>
          <p className="empty-state-desc">Vérifiez l'orthographe ou réinitialisez la recherche.</p>
          <button 
            type="button" 
            className="btn-secondary" 
            onClick={() => { setSearch(""); setSelectedSeries("all"); }}
          >
            Réinitialiser la recherche
          </button>
        </div>
      )}
    </div>
  );
}
