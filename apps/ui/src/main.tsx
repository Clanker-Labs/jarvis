import "@fontsource-variable/inter";
import "./index.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { connect } from "./lib/connection";
import { runDemo } from "./lib/demo";

if (new URLSearchParams(location.search).has("demo")) runDemo();
else void connect();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
