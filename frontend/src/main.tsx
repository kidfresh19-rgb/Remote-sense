import React from "react";
import ReactDOM from "react-dom/client";

import "@fontsource-variable/outfit";
import "@fontsource-variable/jetbrains-mono";
import "maplibre-gl/dist/maplibre-gl.css";
import "./index.css";

import { App } from "./App";

const root = document.getElementById("root");
if (!root) throw new Error("root element missing");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
