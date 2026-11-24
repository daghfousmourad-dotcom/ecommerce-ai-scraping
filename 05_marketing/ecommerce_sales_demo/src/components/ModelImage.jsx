import React, { useState, useEffect } from "react";

// In-memory cache for scraped model images
let cachedModelImages = null;
let fetchPromise = null;

export function ModelImage({ brand, model, slug, className = "model-product-img" }) {
  const [modelImagesMap, setModelImagesMap] = useState(cachedModelImages);
  const [useFallback, setUseFallback] = useState(false);

  useEffect(() => {
    if (cachedModelImages) {
      setModelImagesMap(cachedModelImages);
      return;
    }
    if (!fetchPromise) {
      fetchPromise = fetch("/data/model_images_map.json")
        .then((res) => res.json())
        .then((data) => {
          cachedModelImages = data;
          return data;
        })
        .catch((err) => {
          console.error("Failed to load model images map:", err);
          return {};
        });
    }
    fetchPromise.then((data) => setModelImagesMap(data));
  }, []);

  const bName = (brand || "").toLowerCase().trim();
  const mName = (model || "").trim().toLowerCase();
  const sSlug = (slug || "").toLowerCase().trim();

  // Look up image from scraped sites map
  let realImgUrl = null;
  if (modelImagesMap && !useFallback) {
    realImgUrl = 
      modelImagesMap[sSlug] ||
      modelImagesMap[`${bName}_${mName.replace(/ /g, "_")}`] ||
      modelImagesMap[mName] ||
      modelImagesMap[`${bName} ${mName}`] ||
      modelImagesMap[mName.replace(/ 5g| 4g/g, "").trim()] ||
      modelImagesMap[`${bName} ${mName.replace(/ 5g| 4g/g, "").trim()}`];
  }

  // If a real scraped image is found, render it
  if (realImgUrl && !useFallback) {
    return (
      <div className={`model-photo-wrapper ${className}`}>
        <img
          src={realImgUrl}
          alt={`${brand} ${model}`}
          className="model-real-photo"
          loading="lazy"
          onError={() => setUseFallback(true)}
        />
      </div>
    );
  }

  // Fallback: High-Fidelity Device Mockup with Brand Emblem
  let bodyColor = "#1e293b";
  let edgeColor = "#334155";
  let screenGrad = "linear-gradient(135deg, #0f172a 0%, #1e293b 100%)";
  let accentColor = "#3b82f6";
  let cameraType = "dual";

  if (bName.includes("apple")) {
    bodyColor = "#262626";
    edgeColor = "#404040";
    screenGrad = "linear-gradient(145deg, #1c1917 0%, #292524 100%)";
    accentColor = "#a8a29e";
    cameraType = mName.includes("pro") ? "triple" : "dual";
  } else if (bName.includes("samsung")) {
    bodyColor = "#0f172a";
    edgeColor = "#1e293b";
    screenGrad = "linear-gradient(145deg, #020617 0%, #0f172a 100%)";
    accentColor = "#38bdf8";
    cameraType = "vertical";
  } else if (bName.includes("google")) {
    bodyColor = "#18181b";
    edgeColor = "#27272a";
    screenGrad = "linear-gradient(145deg, #18181b 0%, #27272a 100%)";
    accentColor = "#4ade80";
    cameraType = "visor";
  } else if (bName.includes("xiaomi") || bName.includes("redmi") || bName.includes("poco")) {
    bodyColor = "#172554";
    edgeColor = "#1e3a8a";
    screenGrad = "linear-gradient(145deg, #0f172a 0%, #1e1b4b 100%)";
    accentColor = "#fb923c";
    cameraType = "square";
  }

  return (
    <div className={`smartphone-device-frame ${className}`}>
      <div 
        className="device-chassis"
        style={{
          background: bodyColor,
          borderColor: edgeColor,
          boxShadow: "0 8px 16px -2px rgba(0,0,0,0.2), inset 0 0 0 1px rgba(255,255,255,0.15)"
        }}
      >
        <div className="device-screen" style={{ background: screenGrad }}>
          {cameraType === "triple" || bName.includes("apple") ? (
            <div className="device-dynamic-island">
              <span className="camera-dot"></span>
            </div>
          ) : (
            <div className="device-punch-hole"></div>
          )}

          <div className="screen-inner-branding">
            <img 
              src={`/logos/${brand.toLowerCase().replace(/ /g, "_")}.svg`}
              alt={brand}
              className="screen-brand-mini-logo"
              onError={(e) => { e.target.style.display = "none"; }}
            />
            <div className="screen-model-name-text" title={model}>
              {model.length > 20 ? model.substring(0, 18) + "..." : model}
            </div>
            <div className="screen-status-dots">
              <span className="dot" style={{ background: accentColor }}></span>
              <span className="signal-bar"></span>
            </div>
          </div>

          <div className="glass-reflection-shine"></div>
        </div>
      </div>
    </div>
  );
}
