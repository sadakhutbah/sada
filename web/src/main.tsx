import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./i18n";
// Polices de la charte servies avec le site : sans elles, Windows prend Segoe UI, plus large, et l'en-tête casse.
import "@fontsource-variable/source-sans-3";
import "@fontsource/eb-garamond/400.css";
import "./styles.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* The front end is served at the root: no URL prefix to strip. */}
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
