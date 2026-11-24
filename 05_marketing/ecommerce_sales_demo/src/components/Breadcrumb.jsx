import React from "react";
import { ChevronRight, Home, Smartphone, Wrench, CircleDollarSign } from "lucide-react";

export function Breadcrumb({ 
  selectedBrand, 
  selectedModel, 
  selectedRepair,
  onGoToBrands, 
  onGoToModels, 
  onGoToRepairs 
}) {
  return (
    <nav className="breadcrumb-nav" aria-label="Fil d'Ariane">
      <div className="breadcrumb-container">
        <button 
          type="button" 
          onClick={onGoToBrands}
          className={`breadcrumb-item ${!selectedBrand ? "active" : ""}`}
        >
          <Home className="w-4 h-4" />
          <span>Marques</span>
        </button>

        {selectedBrand && (
          <>
            <ChevronRight className="w-4 h-4 text-slate-400 breadcrumb-sep" />
            <button 
              type="button" 
              onClick={onGoToModels}
              className={`breadcrumb-item ${selectedBrand && !selectedModel ? "active" : ""}`}
            >
              <span className="font-semibold">{selectedBrand.name}</span>
            </button>
          </>
        )}

        {selectedModel && (
          <>
            <ChevronRight className="w-4 h-4 text-slate-400 breadcrumb-sep" />
            <button 
              type="button" 
              onClick={onGoToRepairs}
              className={`breadcrumb-item ${selectedModel && !selectedRepair ? "active" : ""}`}
            >
              <Smartphone className="w-4 h-4" />
              <span>{selectedModel.model}</span>
            </button>
          </>
        )}

        {selectedRepair && (
          <>
            <ChevronRight className="w-4 h-4 text-slate-400 breadcrumb-sep" />
            <div className="breadcrumb-item active">
              <Wrench className="w-4 h-4 text-blue-600" />
              <span className="font-semibold text-blue-700">{selectedRepair.name}</span>
            </div>
          </>
        )}
      </div>
    </nav>
  );
}
