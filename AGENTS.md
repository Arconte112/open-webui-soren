# Repository Guidelines

## Project Structure & Module Organization
Frontend code sits in `src/`: routes in `src/routes/(app)`, components in `src/lib/components`, locale bundles in `src/lib/i18n`, static assets in `static/`. Backend logic lives in `backend/open_webui`; `main.py` mounts routers from `backend/open_webui/routers`, data models under `backend/open_webui/models`, and migrations/utilities inside `backend/open_webui/internal`. Persistent data lives in `backend/data`, Cypress specs in `cypress/`, and deployment helpers at the repo root.

## Build, Test, and Development Commands
Install dependencies with `npm install`, then `npm run dev` (or `npm run dev:5050`) to serve the UI after Pyodide assets download. `npm run build` emits production bundles, and `npm run preview` smoke-tests them. Bring up the API via `pip install -r backend/requirements.txt` plus `python -m uvicorn open_webui.main:app --reload`, or run the stack with `docker compose up -d` / `make install`. Lint/format/typecheck with `npm run lint`, `npm run format`, and `npm run check`.

## Architecture & LLM Endpoints
`backend/open_webui/main.py` applies CORS, session, and audit middleware before exposing routers and serving static bundles. `routers/openai.py` and `routers/ollama.py` proxy chat, embedding, and image traffic, patching payload parameters, forwarding optional user headers, and streaming responses from OpenAI-compatible or Ollama targets. Model management (`routers/models.py`), retrieval orchestration (`routers/retrieval.py`), and realtime updates (`backend/open_webui/socket/main.py`) cover the remaining LLM and tool surface; Redis rooms coordinate websockets when enabled.

## Coding Style & Naming Conventions
Frontend files follow Prettier/ESLint defaults: two-space indents, camelCase state, PascalCase components, kebab-case filenames with CSS co-located. Backend modules honor Black (4 spaces) and Pylint, using snake_case for modules/functions and CapWords for classes; place reusable helpers in `backend/open_webui/utils` and new routers in `backend/open_webui/routers`.

## Testing Guidelines
Use `npm run test:frontend` for Vitest and `npm run cy:open` for Cypress coverage. Backend suites run with `pytest backend/open_webui/test`; heavier cases rely on `pytest-docker` fixtures for Redis or storage, so mark them accordingly. Store fixtures in `test/test_files` or `backend/open_webui/test/util`, and name new files `test_<feature>.py`.

## Commit & Pull Request Guidelines
Follow the concise history: prefix commits with `feat:`, `fix:`, `refac`, `doc:`, etc., keeping subjects under 72 characters. PRs should summarize changes, list added env/config knobs, link issues, and include command logs (`npm run lint`, `pytest`, etc.). Attach screenshots or API traces for UI or contract updates and request review before merging.

## Security & Configuration Tips
Protect secrets—`backend/start.sh` reads `WEBUI_SECRET_KEY` and JWT values from env or `.webui_secret_key`, so never commit real keys. Mount `backend/data` in Docker (`-v open-webui:/app/backend/data`) to preserve user content. Verify connectivity and scoped credentials before enabling optional engines (Ollama, Playwright, tool servers).

## Fork Maintenance & Upstream Sync
Active fork: `Arconte112/open-webui-soren`. The upstream remote is not kept attached by default. When a sync with `open-webui/open-webui` is requested, temporarily add it with `git remote add upstream https://github.com/open-webui/open-webui.git`, then `git fetch upstream`, merge or rebase `upstream/main` onto the local `main`, and finally `git remote remove upstream` once the update is complete. Always finish by pushing through `origin` only.

## Fork Policy
This repository is a personal fork of Open WebUI. Never open pull requests against the upstream project; all customizations stay private under this fork. Only reconnect to upstream when explicitly needed to pull changes, and remove the remote again immediately after the sync so the fork remains isolated.
