import React from "react";
import { Smartphone, RefreshCw, Sparkles } from "lucide-react";

export function Navbar({ onReset, onOpenPitch, selectedBrand, selectedModel, selectedRepair }) {
  return (
    <header className="navbar-header">
      <div className="navbar-inner">
        {/* Logo & Brand */}
        <div className="navbar-brand-group" onClick={onReset} role="button" tabIndex={0}>
          <div className="brand-icon-wrapper">
            <Smartphone className="w-5 h-5 text-white" />
          </div>
          <div className="brand-titles">
            <div className="brand-name">
              <span className="font-bold text-blue-600">E-commerce AI Scraping</span>
              <span className="beta-pill-badge">(Bêta)</span>
            </div>
            <div className="brand-subtitle">Comparateur Réparation Smartphones</div>
          </div>
        </div>

        {/* Actions */}
        <div className="navbar-actions">
          <button 
            type="button" 
            className="btn-pitch"
            onClick={onOpenPitch}
            title="Ouvrir l'argumentaire de vente E-commerce AI Scraping"
          >
            <Sparkles className="w-4 h-4 text-amber-500" />
            <span>Pitch Commercial</span>
          </button>

          {(selectedBrand || selectedModel || selectedRepair) && (
            <button 
              type="button" 
              className="btn-reset"
              onClick={onReset}
              title="Revenir au choix des marques"
            >
              <RefreshCw className="w-4 h-4" />
              <span>Réinitialiser</span>
            </button>
          )}
        </div>
      </div>
    </header>
  );
}
