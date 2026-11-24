import React, { useState, useEffect } from "react";
import { Navbar } from "./components/Navbar";
import { Breadcrumb } from "./components/Breadcrumb";
import { PitchModal } from "./components/PitchModal";
import { BrandSelectionView } from "./views/BrandSelectionView";
import { ModelSelectionView } from "./views/ModelSelectionView";
import { RepairSelectionView } from "./views/RepairSelectionView";
import { PriceComparisonView } from "./views/PriceComparisonView";
import { Loader2 } from "lucide-react";
import "./App.css";

export default function App() {
  const [brands, setBrands] = useState([]);
  const [loadingBrands, setLoadingBrands] = useState(true);
  
  // Navigation & Selection States
  const [selectedBrand, setSelectedBrand] = useState(null);
  const [brandRepairsMap, setBrandRepairsMap] = useState(null);
  const [loadingRepairs, setLoadingRepairs] = useState(false);
  
  const [selectedModel, setSelectedModel] = useState(null);
  const [modelRepairsList, setModelRepairsList] = useState([]);
  
  const [selectedRepair, setSelectedRepair] = useState(null);
  const [isPitchOpen, setIsPitchOpen] = useState(false);

  // 1. Load brands on mount
  useEffect(() => {
    fetch("/data/brands.json")
      .then((res) => {
        if (!res.ok) throw new Error("Erreur de chargement des marques");
        return res.json();
      })
      .then((data) => {
        setBrands(data);
        setLoadingBrands(false);
      })
      .catch((err) => {
        console.error("Failed to load brands:", err);
        setLoadingBrands(false);
      });
  }, []);

  // 2. Select Brand -> Load that brand's repair file in background
  const handleSelectBrand = (brand) => {
    setSelectedBrand(brand);
    setSelectedModel(null);
    setSelectedRepair(null);
    setLoadingRepairs(true);

    fetch(`/data/repairs/${brand.slug}.json`)
      .then((res) => {
        if (!res.ok) throw new Error("Erreur de chargement des réparations de la marque");
        return res.json();
      })
      .then((repData) => {
        setBrandRepairsMap(repData);
        setLoadingRepairs(false);
      })
      .catch((err) => {
        console.error("Failed to load brand repairs:", err);
        setBrandRepairsMap({});
        setLoadingRepairs(false);
      });
  };

  // 3. Select Model -> Extract repairs for that specific model
  const handleSelectModel = (model) => {
    setSelectedModel(model);
    setSelectedRepair(null);

    if (brandRepairsMap) {
      const repairs = brandRepairsMap[model.model] || brandRepairsMap[model.slug] || [];
      setModelRepairsList(repairs);
    } else {
      setModelRepairsList([]);
    }
  };

  // 4. Select Repair -> Go to Comparison View
  const handleSelectRepair = (repair) => {
    setSelectedRepair(repair);
  };

  // Navigation callbacks
  const handleGoToBrands = () => {
    setSelectedBrand(null);
    setSelectedModel(null);
    setSelectedRepair(null);
    setBrandRepairsMap(null);
  };

  const handleGoToModels = () => {
    setSelectedModel(null);
    setSelectedRepair(null);
  };

  const handleGoToRepairs = () => {
    setSelectedRepair(null);
  };

  return (
    <div className="app-container">
      {/* Top Navbar */}
      <Navbar 
        onReset={handleGoToBrands} 
        onOpenPitch={() => setIsPitchOpen(true)}
        selectedBrand={selectedBrand}
        selectedModel={selectedModel}
        selectedRepair={selectedRepair}
      />

      {/* Breadcrumb Navigation */}
      <Breadcrumb
        selectedBrand={selectedBrand}
        selectedModel={selectedModel}
        selectedRepair={selectedRepair}
        onGoToBrands={handleGoToBrands}
        onGoToModels={handleGoToModels}
        onGoToRepairs={handleGoToRepairs}
      />

      {/* Main View Router */}
      <main className="main-content">
        {loadingBrands ? (
          <div className="loading-state-box">
            <Loader2 className="w-8 h-8 text-blue-600 animate-spin" />
            <p>Chargement du catalogue unifié E-commerce AI Scraping...</p>
          </div>
        ) : !selectedBrand ? (
          // PAGE 1 : Choix de la Marque (imobstore inspired)
          <BrandSelectionView 
            brands={brands} 
            onSelectBrand={handleSelectBrand} 
          />
        ) : !selectedModel ? (
          // PAGE 2 : Choix du Modèle (Images + Titre, SANS réparations)
          <ModelSelectionView 
            brand={selectedBrand} 
            onSelectModel={handleSelectModel}
            onBack={handleGoToBrands}
          />
        ) : !selectedRepair ? (
          // PAGE 3 : Choix du Type de Réparation
          <RepairSelectionView 
            brand={selectedBrand}
            model={selectedModel}
            repairs={modelRepairsList}
            onSelectRepair={handleSelectRepair}
            onBack={handleGoToModels}
          />
        ) : (
          // PAGE 4 : Comparatif Tarifs avec Défloutage individuel et général
          <PriceComparisonView 
            brand={selectedBrand}
            model={selectedModel}
            repair={selectedRepair}
            onBack={handleGoToRepairs}
          />
        )}
      </main>

      {/* Commercial Pitch Modal */}
      <PitchModal 
        isOpen={isPitchOpen} 
        onClose={() => setIsPitchOpen(false)} 
      />

      {/* Footer */}
      <footer className="app-footer">
        <div className="footer-content">
          <span>E-commerce AI Scraping Intelligence Commerciale © 2026</span>
          <span className="footer-dot">•</span>
          <span>Démonstration Réparation Smartphone - Top 10 Réseaux Nationaux</span>
        </div>
      </footer>
    </div>
  );
}
