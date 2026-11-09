/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#08090b",
        surface: "#0d0f12",
        panel: "#111317",
        elevated: "#15181d",
        hairline: "#1e2126",
        "hairline-strong": "#282c33",
        ink: "#e8eaed",
        muted: "#9aa3ae",
        faint: "#6b7480",
        accent: "#5b8cff",
        "accent-soft": "#1a2540",
        ok: "#3ecf8e",
        warn: "#e0b341",
        danger: "#e5654f",
        info: "#57a5f5",
      },
      fontFamily: {
        sans: [
          "Inter",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        mono: ["JetBrains Mono", "Cascadia Code", "Consolas", "ui-monospace", "monospace"],
      },
      fontSize: {
        "2xs": ["10.5px", "14px"],
        xs: ["11.5px", "16px"],
        sm: ["12.5px", "18px"],
        base: ["13.5px", "20px"],
        md: ["14.5px", "22px"],
        lg: ["16px", "24px"],
        xl: ["19px", "26px"],
        "2xl": ["23px", "30px"],
      },
      borderRadius: {
        sm: "5px",
        DEFAULT: "7px",
        md: "9px",
        lg: "12px",
        xl: "16px",
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(255,255,255,0.03) inset, 0 8px 24px -12px rgba(0,0,0,0.8)",
        pop: "0 24px 60px -20px rgba(0,0,0,0.9), 0 0 0 1px #282c33",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(2px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        pulseDot: {
          "0%,100%": { opacity: "1" },
          "50%": { opacity: "0.35" },
        },
      },
      animation: {
        "fade-in": "fade-in 140ms ease-out",
        "pulse-dot": "pulseDot 1.8s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
