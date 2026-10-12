import React from "react";
import ReactDOM from "react-dom/client";
import "@fontsource-variable/ibm-plex-sans";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import { Showcase } from "./workshop/Showcase";
import "./kinetic.css";
import "./workshop/showcase.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><Showcase /></React.StrictMode>,
);
