import React from "react";
import { X, CheckCircle2, TrendingUp, ShieldAlert, Award, Zap } from "lucide-react";

export function PitchModal({ isOpen, onClose }) {
  if (!isOpen) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card animate-fade-in" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-group">
            <span className="modal-badge">Fiche Argumentaire Commercial</span>
            <h2 className="modal-title">Pourquoi E-commerce AI Scraping Réparation ?</h2>
          </div>
          <button type="button" className="btn-modal-close" onClick={onClose}>
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="modal-body">
          {/* 3 Value Pillars */}
          <div className="pitch-pillars-grid">
            <div className="pitch-pillar">
              <div className="pillar-icon bg-blue-50 text-blue-600">
                <TrendingUp className="w-5 h-5" />
              </div>
              <h3>Asymétrie Tarifaire Forte</h3>
              <p>
                Sur un même modèle et une même panne (ex: écran iPhone ou batterie Samsung), les écarts de prix constatés entre ateliers atteignent <strong>jusqu'à 45% à 60%</strong>. E-commerce AI Scraping permet au prospect d'optimiser son budget immédiatement.
              </p>
            </div>

            <div className="pitch-pillar">
              <div className="pillar-icon bg-emerald-50 text-emerald-600">
                <Award className="w-5 h-5" />
              </div>
              <h3>Exhaustivité & Précision</h3>
              <p>
                Contrairement aux comparateurs généralistes qui s'arrêtent à 2 ou 3 modèles phares, E-commerce AI Scraping référence <strong>4 962 modèles et 49 marques</strong>, y compris les modèles récents et de niche (Xiaomi, Pixel, Oppo, Crosscall).
              </p>
            </div>

            <div className="pitch-pillar">
              <div className="pillar-icon bg-amber-50 text-amber-600">
                <Zap className="w-5 h-5" />
              </div>
              <h3>Transparence Délais & Garanties</h3>
              <p>
                Le prix ne fait pas tout : E-commerce AI Scraping compare aussi la <strong>durée d'intervention</strong> (de 20 min à 48h) et la <strong>garantie de la pièce</strong> (de 3 mois à la garantie à vie).
              </p>
            </div>
          </div>

          {/* Demonstration tips */}
          <div className="pitch-advice-box">
            <h4>💡 Conseil pour la démonstration client :</h4>
            <p>
              Commencez toujours la démo en <strong>mode confidentiel (sources floutées)</strong> pour montrer la pertinence et les écarts de prix sans dévoiler les partenaires. Utilisez le bouton <em>"Révéler la source"</em> uniquement pour convaincre le prospect de la fiabilité de nos sources (WeFix, Save, iRiparo, Docteur IT...).
            </p>
          </div>
        </div>

        <div className="modal-footer">
          <button type="button" className="btn-primary" onClick={onClose}>
            Fermer l'aide commerciale
          </button>
        </div>
      </div>
    </div>
  );
}
