# Atlas React UI Style Guide

This guide standardizes page layout, components, and interaction patterns for the React SPA in `web/`.

---

## 1) Shared Components & Layout Primitives

All new pages should compose from these shared building blocks instead of writing bespoke layout code.

### Layout Primitives (`@/components/layout/`)

| Component | Purpose |
|---|---|
| `PageShell` | Standard page wrapper — title, subtitle, action slot, scrollable content area |
| `PanelLayout` | Resizable multi-panel split (left/center/right) via `react-resizable-panels` |
| `PreviewSurface` | Canvas/PDF/image preview container with built-in fit-mode toolbar + zoom |
| `CardGrid` | Responsive `auto-fill` grid for card-based listings |

### Common Components (`@/components/`)

| Component | Purpose |
|---|---|
| `ConfirmDialog` | Reusable confirmation dialog (delete, discard, reset) |
| `LoadingState` | Centered or inline loading spinner with optional label |
| `EmptyState` | Consistent no-data placeholder with icon, heading, and CTA slot |
| `ThemeProvider` + `useThemeContext` | Dark/light mode with localStorage persistence |

### UI Primitives (`@/components/ui/`)

shadcn/ui components adapted to Atlas design tokens. Available:

`alert-dialog`, `badge`, `button`, `card`, `checkbox`, `dialog`, `dropdown-menu`,
`input`, `label`, `popover`, `progress`, `resizable`, `scroll-area`, `select`,
`separator`, `sheet`, `skeleton`, `slider`, `switch`, `table`, `tabs`,
`textarea`, `tooltip`

### Hooks (`@/hooks/`)

| Hook | Purpose |
|---|---|
| `useTheme` | Dark/light/system theme toggle with localStorage |
| `usePreviewFit` | ResizeObserver + fit-mode zoom logic for preview surfaces |
| `useMobile` | Responsive breakpoint detection (returns `boolean`) |

---

## 2) Page Composition Pattern

Every new page should follow this template:

```tsx
import { PageShell } from '@/components/layout';
import { Button } from '@/components/ui/button';

export function MyPage() {
  return (
    <PageShell
      title="Page Title"
      subtitle="Brief description"
      actions={<Button>Primary Action</Button>}
    >
      {/* Page content */}
    </PageShell>
  );
}
```

For tool pages with panels:

```tsx
import { PageShell, PanelLayout, PreviewSurface } from '@/components/layout';

export function ToolPage() {
  return (
    <PageShell title="Tool" scrollable={false}>
      <PanelLayout
        left={<ControlsSidebar />}
        leftSize={{ default: 25, min: 15, max: 40 }}
      >
        <PreviewSurface>
          <DocumentPreview />
        </PreviewSurface>
      </PanelLayout>
    </PageShell>
  );
}
```

---

## 3) Page Layout Standard

Rules:
- Prefer full-width pages with `px-4` or `px-6` outer padding.
- Avoid hard global max widths for tool/workspace pages unless content is form-only.
- Control panels should be fixed/narrow; workspaces should be fluid/fill.
- Use `PageShell` for consistent header + scrollable content.
- Use `PanelLayout` for resizable split layouts instead of manual grid templates.

---

## 4) Preview/Canvas Surfaces (PDF, image, render)

### Fit modes (required)
Use `PreviewSurface` or `usePreviewFit` — every preview surface automatically gets:
- **Fit Page** (default)
- **Fit Width**
- **Actual Size**
- Manual zoom (`+/-`) switching mode to `manual`

### Auto-fit behavior
- Fit modes auto-recompute on resize (ResizeObserver).
- Fit Page uses a safety factor to avoid edge clipping.
- No hard min-height on inner render containers that can force clipping.

### Overlay guidance
- Overlay lines must be high-contrast and visible over varied backgrounds.
- For crop guides, use red lines and keep thickness consistent.

---

## 5) Controls and Spacing

- Keep top-right utility controls compact (`size="sm"`, grouped).
- Keep navigation row independent from large scrollable content.
- Use consistent spacing scale:
  - page sections: `gap-6`
  - cards/panels: `gap-3`/`gap-4`
  - tiny control groups: `gap-1`/`gap-2`

---

## 6) Theme & Design Tokens

- Dark mode is the default. Light mode is supported via theme toggle.
- All colors use CSS custom properties defined in `globals.css`.
- Surface hierarchy: `bg-base` → `bg-surface` → `bg-card`.
- Text hierarchy: `text-primary` → `text-secondary` → `text-muted`.
- State: `success`, `warning`, `error`.
- Accent: `accent` / `accent-hover` / `accent-foreground`.
- Never use raw hex colors — always reference semantic tokens.

---

## 7) State + Data Wiring

- **Zustand** stores for page/app state; **React Query** for server state.
- Avoid effect dependencies on whole store objects when mutating store state.
  - Use stable selectors for actions (`const setX = useStore(s => s.setX)`).
- On backend session loss (404/session-not-found), show explicit recovery UI.

---

## 8) Error/Recovery UX

- Never fail silent or blank a workspace.
- Use `EmptyState` for no-data and `LoadingState` for loading.
- Show:
  - human-readable reason
  - one-click recovery action
  - retained context when possible

---

## 9) Accessibility + Readability

- Keep control labels explicit (`Fit Page`, `Fit Width`, `Actual Size`).
- Show current mode/state near controls.
- Preserve keyboard-friendly button semantics and visible focus styles.
- Use `sr-only` spans for icon-only buttons.

---

## 10) Implementation Checklist for New Pages

Before merging a new page:
- [ ] Uses `PageShell` (or documented reason not to)
- [ ] Header + subtitle present
- [ ] Primary layout uses `PanelLayout` or `CardGrid` (not bespoke grid)
- [ ] Preview/workspace uses `PreviewSurface` with fit modes (if applicable)
- [ ] Resize auto-fit verified
- [ ] No forced inner min-height causing clip
- [ ] Loading state present (`LoadingState`)
- [ ] Empty state present (`EmptyState`)
- [ ] Error + recovery state present
- [ ] Only semantic tokens used (no raw hex)
- [ ] Dark + light mode verified
- [ ] Typecheck/build pass (`npm run build`)
