import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import "@fontsource-variable/cairo";
import "./index.css";
import App from "./App";

// Apply the saved theme before the first paint so dark mode never flashes white.
try {
  const saved = localStorage.getItem("zr.theme");
  const dark = saved ? saved === "dark" : window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "dark" : "light";
} catch {
  /* storage blocked */
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
