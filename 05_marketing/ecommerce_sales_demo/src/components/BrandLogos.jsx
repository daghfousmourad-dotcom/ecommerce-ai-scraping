import React from "react";

export function BrandLogo({ brand, className = "w-8 h-8", color = "currentColor" }) {
  const b = (brand || "").toLowerCase().trim();

  if (b.includes("apple")) {
    return (
      <svg viewBox="0 0 170 170" className={className} fill={color}>
        <path d="M150.37 130.25c-2.45 5.66-5.35 10.87-8.71 15.66-4.58 6.53-8.33 11.05-11.22 13.56-4.48 4.12-9.28 6.23-14.42 6.35-3.69 0-8.14-1.05-13.32-3.18-5.19-2.12-9.97-3.17-14.34-3.17-4.58 0-9.49 1.05-14.75 3.17-5.26 2.13-9.5 3.24-12.74 3.35-4.35.13-9.16-1.9-14.42-6.08-3.69-3.07-7.69-7.85-12-14.34-6.28-9.49-11.28-20.25-15.01-32.27-3.72-12.02-5.58-23.75-5.58-35.19 0-14.51 3.52-26.69 10.56-36.54 7.04-9.85 16.32-14.88 27.84-15.08 5.12 0 10.97 1.38 17.56 4.14 6.59 2.76 10.74 4.2 12.45 4.33 1.95-.2 6.39-1.74 13.32-4.63 6.93-2.89 12.87-4.2 17.82-3.94 13.33.66 23.95 5.48 31.86 14.46-11.58 7.04-17.27 16.64-17.07 28.8.2 9.54 3.91 17.47 11.13 23.8 7.22 6.33 15.77 9.87 25.65 10.62-2.11 6.53-4.75 13.06-7.92 19.59zM119.22 31.95c0-7.39 2.66-14.36 7.98-20.91 5.32-6.55 12.03-10.56 20.13-12.04.13 1.05.2 2.04.2 2.97 0 7.39-2.76 14.49-8.28 21.3-5.52 6.81-12.28 10.78-20.29 11.91.07-1.12.26-2.19.26-3.23z"/>
      </svg>
    );
  }

  if (b.includes("samsung")) {
    return (
      <svg viewBox="0 0 200 60" className={className} fill={color}>
        <text x="50%" y="60%" textAnchor="middle" dominantBaseline="middle" fontFamily="var(--font-display)" fontWeight="800" fontSize="32" letterSpacing="2">SAMSUNG</text>
      </svg>
    );
  }

  if (b.includes("xiaomi") || b.includes("redmi") || b.includes("poco")) {
    return (
      <svg viewBox="0 0 100 100" className={className} fill={color}>
        <rect x="5" y="5" width="90" height="90" rx="24" fill="#ff6700" />
        <path d="M28 32 h12 v24 h10 v-24 h12 v36 h-12 v-16 h-10 v16 h-12 z" fill="#ffffff" />
        <rect x="66" y="32" width="6" height="36" fill="#ffffff" />
      </svg>
    );
  }

  if (b.includes("google")) {
    return (
      <svg viewBox="0 0 48 48" className={className}>
        <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
        <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
        <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
        <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
      </svg>
    );
  }

  if (b.includes("huawei")) {
    return (
      <svg viewBox="0 0 100 100" className={className} fill="#CF0A2C">
        <circle cx="50" cy="50" r="42" fill="none" stroke="#CF0A2C" strokeWidth="4" />
        <path d="M50 20 C47 35, 30 50, 50 65 C70 50, 53 35, 50 20 Z" fill="#CF0A2C"/>
        <path d="M35 32 C38 45, 26 58, 44 68 C58 55, 43 42, 35 32 Z" fill="#CF0A2C"/>
        <path d="M65 32 C62 45, 74 58, 56 68 C42 55, 57 42, 65 32 Z" fill="#CF0A2C"/>
      </svg>
    );
  }

  if (b.includes("honor")) {
    return (
      <svg viewBox="0 0 120 40" className={className} fill={color}>
        <text x="50%" y="65%" textAnchor="middle" dominantBaseline="middle" fontFamily="var(--font-display)" fontWeight="800" fontSize="24" letterSpacing="4">HONOR</text>
      </svg>
    );
  }

  if (b.includes("oppo")) {
    return (
      <svg viewBox="0 0 120 40" className={className} fill="#0F704D">
        <text x="50%" y="65%" textAnchor="middle" dominantBaseline="middle" fontFamily="var(--font-display)" fontWeight="900" fontSize="26" letterSpacing="3">oppo</text>
      </svg>
    );
  }

  if (b.includes("oneplus")) {
    return (
      <svg viewBox="0 0 100 100" className={className}>
        <rect x="10" y="10" width="80" height="80" rx="16" fill="#eb0029"/>
        <text x="42" y="65" fill="#fff" fontSize="42" fontWeight="bold" fontFamily="var(--font-sans)">1</text>
        <text x="68" y="45" fill="#fff" fontSize="28" fontWeight="bold" fontFamily="var(--font-sans)">+</text>
      </svg>
    );
  }

  if (b.includes("sony")) {
    return (
      <svg viewBox="0 0 120 40" className={className} fill={color}>
        <text x="50%" y="65%" textAnchor="middle" dominantBaseline="middle" fontFamily="var(--font-sans)" fontWeight="800" fontSize="24" letterSpacing="4">SONY</text>
      </svg>
    );
  }

  if (b.includes("motorola") || b.includes("moto")) {
    return (
      <svg viewBox="0 0 100 100" className={className} fill="#001489">
        <circle cx="50" cy="50" r="44" fill="#001489"/>
        <path d="M26 62 Q36 32 44 48 Q50 60 50 60 Q50 60 56 48 Q64 32 74 62 Q66 48 60 54 Q50 68 50 68 Q50 68 40 54 Q34 48 26 62 Z" fill="#ffffff"/>
      </svg>
    );
  }

  if (b.includes("nokia")) {
    return (
      <svg viewBox="0 0 120 40" className={className} fill="#124191">
        <text x="50%" y="65%" textAnchor="middle" dominantBaseline="middle" fontFamily="var(--font-display)" fontWeight="900" fontSize="22" letterSpacing="3">NOKIA</text>
      </svg>
    );
  }

  if (b.includes("realme")) {
    return (
      <svg viewBox="0 0 120 40" className={className}>
        <rect width="120" height="40" rx="8" fill="#FFC915"/>
        <text x="50%" y="65%" textAnchor="middle" dominantBaseline="middle" fontFamily="var(--font-display)" fontWeight="800" fontSize="20" fill="#000" letterSpacing="1">realme</text>
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect width="14" height="20" x="5" y="2" rx="2" ry="2"/>
      <path d="M12 18h.01"/>
    </svg>
  );
}
