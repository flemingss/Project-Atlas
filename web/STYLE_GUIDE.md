# Atlas React UI Style Guide

This guide standardizes page layout and interaction patterns for the React SPA in `web/`.

## 1) Page Layout Standard

Use this structure for all full pages:

1. **Header block**: title + subtitle/context
2. **Primary content area**: `grid` or `flex` with explicit role split (controls vs workspace)
3. **Bottom nav/action row**: persistent primary flow controls

Rules:
- Prefer full-width pages with `px-4` outer padding.
- Avoid hard global max widths for tool/workspace pages unless content is form-only.
- Control panels should be fixed/narrow; workspaces should be fluid/fill.

Recommended pattern:
- Left controls: `xl:grid-cols-[360px,minmax(0,1fr)]`
- Right workspace: `minmax(0,1fr)` to prevent overflow bugs.

## 2) Preview/Canvas Surfaces (PDF, image, render)

### Fit modes (required)
Every preview surface should support:
- **Fit Page** (default)
- **Fit Width**
- **Actual Size**
- Manual zoom (`+/-`) switching mode to `manual`

### Auto-fit behavior
- Fit modes must auto-recompute on resize (`ResizeObserver` + window resize fallback).
- Fit Page should use a small safety factor (e.g., 0.98) to avoid edge clipping.
- No hard min-height on inner render containers that can force clipping.

### Overlay guidance
- Overlay lines must be high-contrast and visible over varied backgrounds.
- For crop guides, use red lines and keep thickness consistent.

## 3) Controls and Spacing

- Keep top-right utility controls compact (`size="sm"`, grouped).
- Keep navigation row independent from large scrollable content.
- Use consistent spacing scale:
  - page sections: `gap-6`
  - cards/panels: `gap-3`/`gap-4`
  - tiny control groups: `gap-1`/`gap-2`

## 4) State + Data Wiring

- Zustand stores for page/app state; React Query for server state.
- Avoid effect dependencies on whole store objects when mutating store state.
  - Use stable selectors for actions (`const setX = useStore(s => s.setX)`).
- On backend session loss (404/session-not-found), show explicit recovery UI.

## 5) Error/Recovery UX

- Never fail silent or blank a workspace.
- Show:
  - human-readable reason
  - one-click recovery action
  - retained context when possible

## 6) Accessibility + Readability

- Keep control labels explicit (`Fit Page`, `Fit Width`, `Actual Size`).
- Show current mode/state near controls.
- Preserve keyboard-friendly button semantics and visible focus styles.

## 7) Implementation Checklist for New Pages

Before merging a new page:
- [ ] Header + subtitle present
- [ ] Primary layout split is explicit and responsive
- [ ] Preview/workspace fit modes implemented (if applicable)
- [ ] Resize auto-fit verified
- [ ] No forced inner min-height causing clip
- [ ] Error + recovery state present
- [ ] Typecheck/build pass (`npm run build`)
