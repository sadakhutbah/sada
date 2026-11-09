import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./i18n";
import "./styles.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* Le front est servi à la racine : pas de préfixe d'URL à retirer. */}
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
