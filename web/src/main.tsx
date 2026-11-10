import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./i18n";
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
