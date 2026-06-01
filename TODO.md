# TODO

## Goal
Improve responsive UX for narrow/mobile viewports. Desktop remains primary; mobile gets usable chat and companion views.

## Tasks

### 1. HTML — Wrap utility panels in collapsible `<details>`
- [x] Wrap status-grid, prompt-panel, tts-panel, audio-panel in `<details open class="panel-details">`
- [x] Add `<summary>` labels for each

### 2. HTML — Sidebar toggle button for chat tab
- [x] Add toggle button in `.workspace` between conversation and side

### 3. HTML — Companion sub-tabs for mobile
- [x] Add "Chat" / "Memory & Settings" sub-tab buttons in view-companion

### 4. CSS — Shell height fix (100dvh)
- [x] Use `100dvh` with `100vh` fallback for shell
- [x] Keep overflow hidden on mobile (revert `height: auto` at 850px)

### 5. CSS — Panel-details styling
- [x] Style `<details>` as seamless panels on desktop
- [x] On mobile, show summary bars, allow collapse

### 6. CSS — Sidebar toggle & responsive sidebar
- [x] Sidebar hidden by default on mobile, toggle reveals it
- [x] Toggle button visible only on mobile

### 7. CSS — Companion responsive layout
- [x] On mobile, sub-tabs switch between chat and memory/sampler
- [x] Companion config row collapses

### 8. CSS — Tablet breakpoint (768px)
- [x] Narrower sidebar, tighter spacing, 2-col status grid

### 9. CSS — Touch targets
- [x] 44px min-height on mobile for all interactive elements

### 10. JS — Toggle handlers
- [x] Sidebar toggle handler
- [x] Companion sub-tab handler
- [x] Auto-close details panels on mobile init

## Notes
- Desktop layout unchanged. All mobile additions are additive.
- `<details>`/`<summary>` provides CSS-only baseline; JS enhances.
- `100dvh` handles mobile browser chrome (address bar).
