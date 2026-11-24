import React from "react";
import { 
  ArrowLeft, 
  Smartphone, 
  BatteryCharging, 
  Zap, 
  Camera, 
  Smile, 
  Shield, 
  Volume2, 
  Mic, 
  Droplet, 
  Search, 
  Wrench, 
  ChevronRight, 
  Sparkles
} from "lucide-react";
import { PageHeaderBanner } from "../components/PageHeaderBanner";
import { ModelImage } from "../components/ModelImage";

function getRepairIcon(iconName, className = "w-6 h-6") {
  switch (iconName) {
    case "Smartphone": return <Smartphone className={className} />;
    case "BatteryCharging": return <BatteryCharging className={className} />;
    case "Zap": return <Zap className={className} />;
    case "Camera": return <Camera className={className} />;
    case "Smile": return <Smile className={className} />;
    case "Shield": return <Shield className={className} />;
    case "Volume2": return <Volume2 className={className} />;
    case "Mic": return <Mic className={className} />;
    case "Droplet": return <Droplet className={className} />;
    case "Search": return <Search className={className} />;
    default: return <Wrench className={className} />;
  }
}

export function RepairSelectionView({ brand, model, repairs, onSelectRepair, onBack }) {
  return (
    <div className="view-container animate-fade-in">
      {/* Grand Titre E-commerce AI Scraping (Bêta) - Conservé sur toutes les pages */}
      <PageHeaderBanner />

      {/* Header & Back Button */}
      <div className="subpage-header">
        <button type="button" onClick={onBack} className="btn-back">
          <ArrowLeft className="w-4 h-4" />
          <span>Changer de Modèle</span>
        </button>

        {/* Selected Model Showcase Card */}
        <div className="selected-device-banner">
          <div className="device-banner-visual">
            <ModelImage 
              brand={brand.name} 
              model={model.model} 
              slug={model.slug} 
              className="mini-model-banner-img"
            />
          </div>
          <div className="device-banner-info">
            <div className="device-brand-badge">{brand.name}</div>
            <h2 className="device-model-name">{model.model}</h2>
            <p className="device-status-text">
              <Sparkles className="w-4 h-4 text-emerald-600 inline mr-1" />
              <strong>{repairs ? repairs.length : 0} types de réparations</strong> disponibles chez nos réparateurs partenaires
            </p>
          </div>
        </div>
      </div>

      {/* Section Title */}
      <div className="section-intro-bar">
        <h3 className="section-title">Sélectionnez le Type de Réparation à Comparer</h3>
        <p className="section-subtitle">
          Choisissez la prestation souhaitée pour afficher les grilles tarifaires en direct, délais et garanties.
        </p>
      </div>

      {/* Repairs Grid (SANS la mention Panne fréquente) */}
      <div className="repairs-grid">
        {repairs && repairs.map((repairItem) => {
          return (
            <div
              key={repairItem.key}
              className="repair-card"
              onClick={() => onSelectRepair(repairItem)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === "Enter" && onSelectRepair(repairItem)}
            >
              <div className="repair-card-header">
                <div className={`repair-icon-box repair-icon-${repairItem.key}`}>
                  {getRepairIcon(repairItem.icon, "w-6 h-6")}
                </div>

                <div className="repair-card-meta">
                  <h4 className="repair-card-title">{repairItem.name}</h4>
                  <p className="repair-card-desc">{repairItem.desc}</p>
                </div>
              </div>

              <div className="repair-card-pricing-row">
                <div className="repair-offers-pill">
                  <span className="pill-dot"></span>
                  <span>{repairItem.offers_count} devis disponibles</span>
                </div>

                {repairItem.min_price !== null && (
                  <div className="repair-price-indicator">
                    <span className="price-label">Dès</span>
                    <span className="price-val">{repairItem.min_price.toFixed(2)} €</span>
                  </div>
                )}
              </div>

              <div className="repair-card-footer">
                <span className="compare-cta">Voir les tarifs des réparateurs</span>
                <ChevronRight className="w-4 h-4 chevron-icon" />
              </div>
            </div>
          );
        })}
      </div>

      {(!repairs || repairs.length === 0) && (
        <div className="empty-state-card">
          <p className="empty-state-title">Aucune réparation répertoriée pour ce modèle.</p>
          <p className="empty-state-desc">Veuillez sélectionner un autre modèle dans le catalogue.</p>
          <button type="button" className="btn-secondary" onClick={onBack}>
            Retour au choix du modèle
          </button>
        </div>
      )}
    </div>
  );
}
