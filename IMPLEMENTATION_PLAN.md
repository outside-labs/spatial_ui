# Implementation plan

`spacial_ui` is a Python spatial UI engine using Python 3.14 t-string templates.
The first milestone is an interactive, responsive dashboard with real semantic
HTML, square logical units, relative placement, and Python callbacks.

## Design

- Keep the distribution name `spacial-ui` and import name `spacial_ui`.
- Use Python 3.14 and the existing `uv` package scaffold. Keep the runtime free
  of third-party dependencies for the first milestone; use `unittest` for tests.
- Separate geometry and constraint resolution from HTML and transport. Expose
  integer, zero-based grid coordinates; translate to one-based CSS Grid lines
  only in the renderer. One unit has the same physical size on both axes.
- Resolve named relative placements with a dependency graph. Report missing
  targets, cycles, out-of-bounds rectangles, and unwanted collisions explicitly.
- Start with `fit` and `reflow`. `fit` contains a fixed scene within both
  container dimensions. `reflow` selects authored arrangements at container
  width breakpoints; it does not shrink an entire desktop page onto a phone.
- Compile t-strings into a typed, immutable tree. Treat interpolated strings as
  text, never markup. Validate HTML attributes and URL schemes, reject executable
  markup, and accept layout objects and callbacks only in their typed directives.
  Keep reading and keyboard order in source order.
- Components own state. Each render reevaluates the t-string. Explicitly retain
  child component instances; creating a new instance creates new state.
- Execute Python callbacks on the server. Start with synchronous, argument-free
  click handlers and opaque per-render event bindings, rather than accepting
  Python expressions or arbitrary method names from the browser.
- Provide a small browser module for queued events, DOM updates that preserve
  keyed nodes and active form controls, and spatial transitions honoring reduced
  motion. Python does not calculate browser layout on every animation frame.
- The bundled HTTP server is for local development, bound to loopback. Isolate
  browser sessions, validate request origin and event tokens, limit request size,
  and serve only explicit application routes and bundled assets.

## First milestone: five focused pull requests

Issues and the GitHub Project are the canonical queue. Each task gets its own
branch, tests, Conventional Commit, and pull request. Dependent work starts from
the preceding branch while CI runs. Retarget stacked pull requests as dependencies
are squash-merged and verify the final `main` branch.

| Order | Task | Scope and acceptance criteria | Depends on |
| --- | --- | --- | --- |
| 1 | INIT-01 | Package metadata, this plan, contributor commands, version CLI, and Linux/macOS/Windows Python 3.14 CI. Build and install the wheel in a clean environment. | — |
| 2 | SPACE-01 | `Point`, `Size`, `Rect`, `Grid`, `Breakpoint`, `at`, `right_of`, `left_of`, `above`, `below`, and `center_in`. Resolve forward references deterministically; test cycles, bounds, collisions, invalid input, square scaling, and responsive arrangements. | INIT-01 |
| 3 | HTML-01 | Typed t-string fragments, nested templates, safe text/attribute escaping and URLs, and grid CSS. Test hostile inputs, unsupported contexts, malformed structure, fit/reflow, and source order. | SPACE-01 |
| 4 | STATE-01 | Per-instance `State`, component composition, render snapshots, opaque event registration, stale-event rejection, and state-driven rerendering. Test independent sessions and callback failures. | HTML-01 |
| 5 | WEB-01 | Local development server, browser event/DOM runtime, accessible Outside Labs dashboard, fit example, container reflow, and reduced-motion-aware spatial transitions. Test real HTTP session isolation, invalid requests, and installed assets; inspect the interactive demo in a browser at narrow and wide widths. | STATE-01 |

## Intended authoring shape

The first milestone will make this style executable. Public examples are updated
alongside the implementation so they reflect the actual API.

```python
from spacial_ui import Component, Grid, State, at, html


class Counter(Component):
    count = State(0)

    def increment(self):
        self.count += 1

    def render(self):
        return html(t"""
            <main layout={Grid(4, 3, mode="reflow")}>
                <section key="counter" place={at(0, 0, 4, 3)}>
                    <h1>Actions: {self.count}</h1>
                    <button type="button" on_click={self.increment}>Add one</button>
                </section>
            </main>
        """)
```

## Subsequent milestones

These are roadmap proposals, not first-milestone acceptance criteria. Promote
them from triage with concrete scope after reviewing the running dashboard.

| Task | Proposed work | Decision or evidence needed |
| --- | --- | --- |
| MOTION-01 | Public movement/keyframe/timeline API on the same logical coordinates. | Validate initial spatial transitions; select how interrupted animations and state changes compose. |
| INPUT-01 | Typed input/change/submit payloads and form validation. | Define the supported event payload schema and editing/conflict behavior. |
| WORLD-01 | Virtual world, camera, tile movement, and dialogue example. | Select browser-local execution for frame-sensitive input; keep server callbacks out of the frame loop. |
| LAYOUT-02 | Content-sized tracks, responsive reparenting, richer constraints, and overflow policies. | Review real dashboard content and stable identity across parent changes. |
| HOST-01 | Production transport adapter, concurrency policy, deployment documentation. | Choose hosting/ASGI integration and any external dependencies explicitly. |
| REL-01 | License, compatibility policy, first package release and documentation hosting. | Choose license and release scope; package publication and live deployment require separate authorization. |

## Verification

Run focused `unittest` modules during implementation, then all tests for each
ready branch. CI runs the full suite on Python 3.14 on Linux, macOS, and Windows,
builds source and wheel distributions, and tests installation outside the source
tree. Browser verification covers callback updates, independent sessions,
keyboard focus, form values, container breakpoints, square cells, and reduced
motion. Do not claim browser behavior based only on generated HTML assertions.

## References

- [Python template strings](https://docs.python.org/3.14/library/string.templatelib.html)
- [PEP 750](https://peps.python.org/pep-0750/)
- [HTMLParser](https://docs.python.org/3.14/library/html.parser.html): a tokenizer,
  not an HTML validator or sanitizer; the compiler must impose its own rules.
- [Container size queries](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Containment/Container_size_and_style_queries)
- [Web Animations API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Animations_API)
