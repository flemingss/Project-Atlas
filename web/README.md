# Atlas React UI (`web/`)

The React SPA that powers the Atlas Document Editor and (future) operator console.
Built with **Vite + React 18 + TypeScript + shadcn/ui + Tailwind CSS**.

> **Status (Mar 2026):** The editor page is feature-complete and replaces the
> previous standalone HTML/JS editor. The Streamlit operator console (`ui/`)
> remains live for non-editor workflows (Upload, Search, Review, Admin).
> Future phases will migrate those surfaces into this React app.

---

## Quick Start

### Local development (recommended)

```bash
cd web
npm install
npm run dev          # Vite dev server at http://localhost:5173
```

The Vite dev server proxies `/api/*` and `/health` to `http://127.0.0.1:18080`
(the running Atlas backend). Start the backend first:

```bash
# In the repo root
python -m atlas
```

### Production build

```bash
cd web
npm run build        # Outputs to ../static/editor/
```

The build output is served by FastAPI at `/editor` via `StaticFiles(html=True)`.
No separate web server is required.

If backend admin token auth is enabled, open the editor with `?token=<ATLAS_ADMIN_TOKEN>` once
(for example `/editor/?token=...` or `/editor/vlm-ingest?token=...`).
The SPA stores it in `localStorage` as `atlas_admin_token` and reuses it for API requests.

React page/layout conventions are documented in `web/STYLE_GUIDE.md`.

### Type checking

```bash
npm run typecheck    # tsc --noEmit
```

### VLM Configure preview fit behavior (standardized)

The VLM Ingest Configure step uses a standardized preview strategy to prevent clipping on common fullscreen resolutions:

- Default mode is **Fit Page** (no-scroll page visibility)
- Additional controls: **Fit Width** and **Actual Size**
- Manual zoom (`+/-`) switches mode to `manual`
- Fit modes auto-recompute on viewport resize (via `ResizeObserver`) so preview remains usable without bespoke page-level configs

This behavior is implemented in `web/src/pages/vlm-ingest/index.tsx` and should be kept consistent for future preview surfaces.

---

## Stack

| Layer | Library | Purpose |
|-------|---------|---------|
| Build | Vite 6 | Dev server, HMR, production bundler |
| Framework | React 18 | UI framework |
| Language | TypeScript 5 | Type safety |
| Routing | React Router 7 | Client-side routing (basename `/editor`) |
| Styling | Tailwind CSS 3 + CSS custom properties | Utility-first CSS with semantic design tokens |
| Components | shadcn/ui (Radix + CVA) | Accessible, composable UI primitives |
| State | Zustand 4 | Lightweight global store for editor state |
| Data fetching | TanStack React Query 5 | Async state with caching, retries, mutations |
| PDF viewer | pdfjs-dist 4.9 | PDF rendering to canvas |
| Markdown editor | CodeMirror 6 | Syntax-highlighted markdown editing |
| Split panes | react-resizable-panels 3 | Draggable panel layout |
| Notifications | Sonner | Toast notifications |
| Icons | Lucide React | Consistent icon set |

---

## Directory Structure

```
web/
├── index.html                         # Vite entry HTML
├── package.json                       # Dependencies & scripts
├── vite.config.ts                     # Vite config (proxy, build output)
├── tsconfig.json                      # TypeScript config
├── tailwind.config.js                 # Tailwind theme (design tokens)
├── postcss.config.js                  # PostCSS (Tailwind + Autoprefixer)
├── .gitignore                         # node_modules, dist
└── src/
    ├── main.tsx                       # React root mount
    ├── app.tsx                        # Router + QueryClient + Toaster
    ├── globals.css                    # Tailwind directives + CSS custom properties
    ├── vite-env.d.ts                  # Vite type declarations
    ├── lib/
    │   └── utils.ts                   # cn() utility (clsx + tailwind-merge)
    ├── layouts/
    │   └── app-layout.tsx             # Top bar + <Outlet> shell
    ├── pages/
    │   ├── home/
    │   │   └── index.tsx              # Doc ID input → navigate to editor
    │   ├── editor/
    │   │   └── index.tsx              # Split-pane editor page
    │   └── vlm-ingest/
    │       └── index.tsx              # 7-step VLM ingest wizard
    ├── components/
    │   ├── ui/                        # shadcn/ui primitives
    │   │   ├── badge.tsx
    │   │   ├── button.tsx
    │   │   ├── card.tsx
    │   │   ├── input.tsx
    │   │   ├── label.tsx
    │   │   ├── popover.tsx
    │   │   ├── separator.tsx
    │   │   ├── slider.tsx
    │   │   ├── switch.tsx
    │   │   └── tooltip.tsx
    │   └── editor/                    # Editor-specific components
    │       ├── index.ts               # Barrel exports
    │       ├── pdf-viewer.tsx         # PDF.js canvas viewer + nav + zoom + crop overlay
    │       ├── markdown-editor.tsx    # CodeMirror 6 editor (imperative ref)
    │       ├── editor-toolbar.tsx     # VLM Fix, LLM Refine, Strip, Re-Judge, Save, Undo
    │       ├── vlm-settings.tsx       # DPI + crop margin popover
    │       └── status-bar.tsx         # Connection dot + stats + model info
    ├── services/
    │   ├── api.ts                     # Typed fetch client for /api/editor/* endpoints
    │   └── vlm-ingest-api.ts          # Typed fetch client for /api/editor/vlm-ingest/* endpoints
    ├── stores/
    │   ├── editor-store.ts            # Zustand store (run, page, zoom, VLM settings)
    │   └── vlm-ingest-store.ts        # Zustand store (VLM ingest wizard state)
    └── hooks/
        ├── use-editor-api.ts          # React Query mutations (VLM, save, refine, judge)
        └── use-vlm-ingest.ts          # React Query hooks for VLM ingest workflow
```

---

## Design System

### Theme tokens

All colours use CSS custom properties defined in `globals.css` (dark-first)
and referenced in `tailwind.config.js` as semantic tokens:

| Token group | Examples | Source |
|-------------|----------|--------|
| Surfaces | `bg-base`, `bg-surface`, `bg-card`, `bg-overlay` | `--bg-*` vars |
| Text | `text-primary`, `text-secondary`, `text-muted` | `--text-*` vars |
| Accent | `accent` (DEFAULT + hover + foreground) | `--accent-*` vars |
| State | `success`, `warning`, `error` | `--state-*` vars |
| Border | `border` | `--border-default` |

**Dark mode** is the default. Light mode tokens are defined under `.light`
but are not yet exposed via a theme toggle.

### Adding a new shadcn/ui component

1. Create `web/src/components/ui/<name>.tsx` using Radix + CVA pattern
2. Import from `@/components/ui/<name>` in consuming components
3. Follow existing component patterns (forwardRef, cn() for class merging)

### Adding a new page

1. Create `web/src/pages/<name>/index.tsx`
2. Add a `<Route>` in `app.tsx` inside the `<AppLayout>` route
3. Add a nav item in `layouts/app-layout.tsx` `NAV_ITEMS` array

---

## API Integration

The React app calls the same backend endpoints as the old HTML editor:

| Hook / Method | Endpoint | Purpose |
|---------------|----------|---------|
| `editorApi.resolveDoc()` | `GET /api/editor/resolve-doc/:docId` | Resolve doc ID → run ID |
| `editorApi.pageInfo()` | `GET /api/editor/page-info/:runId` | Page count + filename |
| `editorApi.sourcePdfUrl()` | `GET /api/editor/source-pdf/:runId` | PDF binary for viewer |
| `editorApi.markdown()` | `GET /api/editor/markdown/:runId` | Full markdown content |
| `editorApi.pageMarkdown()` | `GET /api/editor/page-markdown/:runId/:page` | Per-page markdown |
| `useVisionRefine()` | `POST /api/editor/vision-refine` | VLM page correction |
| `useSaveMarkdown()` | `POST /api/editor/save-markdown` | Persist edits |
| `useLlmRefine()` | `POST /api/editor/llm-refine` | LLM full-doc refine |
| `useReJudge()` | `POST /api/editor/re-judge` | Quality re-scoring |

All mutations use React Query with Zustand-synced status updates and Sonner
toast notifications.

---

## Container Integration

### Dockerfile (multi-stage)

The main `Dockerfile` uses a two-stage build:

1. **Stage 1 (Node.js):** `npm ci` + `npm run build` → produces `static/editor/`
2. **Stage 2 (Python):** `COPY --from=ui-build` overlays the React output

### Dev bind-mounts

In `docker-compose.dev.yml`, `./static:/app/static` is bind-mounted so you can
run `npm run build` locally and have the container serve the latest build
without rebuilding the image.

For live development, run `npm run dev` outside Docker and use the Vite dev
server directly (port 5173).

---

## Relationship to Streamlit UI (`ui/`)

The Streamlit operator console (`ui/app.py`) and the React editor are
**complementary surfaces** during the migration period:

| Surface | Technology | Scope | Access |
|---------|-----------|-------|--------|
| Operator Console | Streamlit | Upload, Search, Library, Review, Admin | `http://localhost:18501` |
| Document Editor | React SPA | PDF viewer + markdown editor + VLM tools | `http://localhost:18080/editor` |

The React app will eventually absorb all operator console functionality.
The Streamlit UI will be retired once feature parity is reached.
