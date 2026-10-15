# Changelog

## 0.1.0 — 2026-10-15

First public release of `spatial-ui`, a Python 3.14+ spatial UI engine under the
MIT license, with no third-party runtime dependencies.

- Square logical grids, relative placement, explicit layout errors, and
  container-width breakpoints for responsive layouts.
- Typed t-string templates with escaped interpolation, a conservative HTML
  vocabulary, and generated CSS Grid layout.
- Stateful components, per-browser sessions, explicit click handlers, and
  rejection of stale event bindings.
- A loopback development server with bounded in-memory sessions, origin and
  request-token checks, and bundled JavaScript and CSS assets.
- Keyed DOM updates, preserved form values and focus, and spatial transitions
  that respect reduced-motion preferences.
- An interactive dashboard available through `spatial-ui demo` or
  `python -m spatial_ui demo`.

This is an early API. Minor releases before 1.0 may contain breaking changes;
patch releases are intended for compatible fixes. The bundled server is for
local development. Production hosting, typed form events, advanced timelines,
and virtual-world support remain on the roadmap.
