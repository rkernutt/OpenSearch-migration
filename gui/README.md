# Migration Wizard GUI

A browser-based wizard for the OpenSearch → Elastic Cloud migration toolkit. Built with **React 18**, **TypeScript**, **Vite**, and **Elastic UI (EUI)**.

## Purpose

Provides a Kibana-style step-by-step interface for operators who prefer a GUI over the CLI tools. The wizard walks through:

1. Environment setup (source/destination credentials)
2. Preflight checks
3. Migration method selection (Remote Reindex / Logstash / Proxy)
4. Progress monitoring
5. Validation

## Development

```bash
cd gui
npm install
npm run dev       # Vite dev server at http://localhost:5173
```

## Code quality

```bash
npm run lint          # ESLint (typescript-eslint + react-hooks + react-refresh)
npm run lint:fix      # Auto-fix ESLint issues
npm run format        # Prettier — write formatting changes
npm run format:check  # Prettier — CI-safe check (no writes)
npm run typecheck     # TypeScript type-check (tsc --noEmit)
```

## Build

```bash
npm run build    # Outputs to dist/
npm run preview  # Preview the production build locally
```

## Docker

A `Dockerfile` and `docker-compose.yml` are provided for containerised deployment (Nginx + the built app):

```bash
docker compose up --build
# App available at http://localhost:3000
```

## Configuration

The GUI connects to the Python CLI tools via the backend proxy defined in `proxy.cjs` (used by Vite in development). In production, configure the Nginx reverse proxy in `nginx.conf` to point at the running CLI endpoints.
