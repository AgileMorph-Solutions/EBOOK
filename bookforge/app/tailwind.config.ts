import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // ── Forge — dark cinematic atelier. Warm charcoal surfaces, molten
        // ember accent, cool ash neutrals. Semantic token names preserved so
        // every component reflows into the dark theme automatically.
        background: "#0d0a08",
        surface: "#0d0a08",
        "surface-dim": "#080605",
        "surface-bright": "#1c1611",
        "surface-container-lowest": "#120e0b",
        "surface-container-low": "#17120e",
        "surface-container": "#1b1512",
        "surface-container-high": "#231b16",
        "surface-container-highest": "#2c221b",
        "surface-variant": "#2c221b",
        "surface-tint": "#ff8a4c",

        "on-background": "#f4ece3",
        "on-surface": "#f4ece3",
        "on-surface-variant": "#c2b1a4",

        outline: "#8a7268",
        "outline-variant": "#3a2e27",

        // Molten ember — the signature accent
        primary: "#ff8a4c",
        "on-primary": "#3a1400",
        "primary-container": "#c2521c",
        "on-primary-container": "#ffe6d8",
        "primary-fixed": "#ffdbcc",
        "primary-fixed-dim": "#ffb595",
        "on-primary-fixed": "#351000",
        "on-primary-fixed-variant": "#7c2e00",
        "inverse-primary": "#9c3f07",

        // Ash — muted warm neutrals for secondary chrome
        secondary: "#b6a89e",
        "on-secondary": "#2a221d",
        "secondary-container": "#2a221d",
        "on-secondary-container": "#ddccc0",
        "secondary-fixed": "#eae1da",
        "secondary-fixed-dim": "#cec5be",
        "on-secondary-fixed": "#1f1b17",
        "on-secondary-fixed-variant": "#4b4641",

        // Verdigris — success / grounded / QA-pass
        tertiary: "#79cf98",
        "on-tertiary": "#04340f",
        "tertiary-container": "#2a4433",
        "on-tertiary-container": "#c9ebd2",
        "tertiary-fixed": "#c9ebd2",
        "tertiary-fixed-dim": "#aeceb6",
        "on-tertiary-fixed": "#042111",
        "on-tertiary-fixed-variant": "#304d3b",

        error: "#ff8a80",
        "on-error": "#4a1410",
        "error-container": "#4a1512",
        "on-error-container": "#ffdad6",

        "inverse-surface": "#f4ece3",
        "inverse-on-surface": "#251d19",
      },
      borderRadius: {
        DEFAULT: "0.25rem",
        lg: "0.5rem",
        xl: "0.75rem",
        full: "9999px",
      },
      spacing: {
        gutter: "24px",
        "margin-mobile": "20px",
        "margin-desktop": "40px",
        unit: "8px",
        "container-max": "1440px",
      },
      fontFamily: {
        display: ["var(--font-display)", "Georgia", "serif"],
        body: ["var(--font-body)", "system-ui", "sans-serif"],
        label: ["var(--font-label)", "monospace"],
      },
      fontSize: {
        "display-lg": [
          "48px",
          { lineHeight: "56px", letterSpacing: "-0.02em", fontWeight: "500" },
        ],
        "display-lg-mobile": [
          "32px",
          { lineHeight: "40px", letterSpacing: "-0.01em", fontWeight: "500" },
        ],
        "headline-md": ["32px", { lineHeight: "40px", fontWeight: "500" }],
        "headline-sm": ["24px", { lineHeight: "32px", fontWeight: "500" }],
        "body-lg": ["18px", { lineHeight: "28px", fontWeight: "400" }],
        "body-md": ["16px", { lineHeight: "24px", fontWeight: "400" }],
        "body-sm": ["14px", { lineHeight: "20px", fontWeight: "400" }],
        "label-md": [
          "13px",
          { lineHeight: "16px", letterSpacing: "0.02em", fontWeight: "500" },
        ],
        "label-sm": [
          "11px",
          { lineHeight: "14px", letterSpacing: "0.05em", fontWeight: "500" },
        ],
      },
      maxWidth: {
        "container-max": "1440px",
      },
    },
  },
  plugins: [],
};

export default config;
