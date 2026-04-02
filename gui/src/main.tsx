import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { EuiProvider } from "@elastic/eui";
import { ErrorBoundary } from "./components/ErrorBoundary";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <EuiProvider colorMode="light">
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </EuiProvider>
  </StrictMode>
);
