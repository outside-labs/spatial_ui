# spatial_ui

A Python spatial UI engine with square logical grid units, relative placement,
and Python 3.14 t-string templates. Version 0.1.0 is an early release under the
[MIT license](LICENSE), with no third-party runtime dependencies.

The first milestone includes a responsive dashboard with semantic HTML and Python
event handlers. See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the
architecture, acceptance criteria, and later milestones. Issues and the GitHub
Project track current work; pull requests record implementation and verification.

## Try the dashboard

Requires Python 3.14 or later.

```sh
python -m pip install "spatial-ui==0.1.0"
spatial-ui demo
```

You can also start it with `python -m spatial_ui demo`.

Open `http://127.0.0.1:8000/`. Add actions, switch between compact and wide canvases,
type in the scratchpad, and move the tile. The scene uses the same coordinate
model as the navigation and cards. Use `--port 8001` for another local preview.

## Spatial layout

```python
from spatial_ui import Grid, at, below, right_of

layout = Grid(12, 8).resolve({
    "nav": at(0, 0, 12, 2),
    "sidebar": below("nav", width=3, height=6),
    "card": right_of("sidebar", width=6, height=3, gap=1),
})
assert layout.rects["card"].x == 4
```

Coordinates and sizes use whole logical cells. Adjacent edges can touch;
overlapping rectangles require `allow_overlap=True`. Centering rounds an odd
remainder toward the top/left. `fit` scales one square unit against both container
dimensions; `reflow` uses the container width and optional `Breakpoint` rules.
Each breakpoint overrides the base placements independently, rather than
inheriting overrides from smaller breakpoints.

## T-string templates

```python
from spatial_ui import Grid, at, html, render

name = "Outside Labs"
page = html(t"""
    <main layout={Grid(4, 3, mode="reflow")}>
        <section key="welcome" place={at(0, 0, 4, 3)}>
            <h1>Hello, {name}</h1>
        </section>
    </main>
""")
result = render(page)  # result.markup and result.css
```

Strings interpolate as text. Nested `html()` fragments, t-strings, lists, and
tuples compose as children. `None` omits a child or optional attribute. Format
values before interpolation. `layout` and `place` require typed values; every
direct grid child needs a unique sibling `key` and a `place`.

The compiler accepts a conservative HTML fragment vocabulary. It rejects scripts,
inline styles, native event attributes, dynamic tag/attribute names, unsafe URL
schemes, duplicate attributes, and unsupported nesting. URLs use explicit
HTTP(S), relative paths, or (for links) `mailto:` and `tel:`; URL whitespace must
be percent-encoded. There is no raw-markup escape hatch. A `fit` grid needs a
parent with a definite width and height. Visual order follows placement while
reading order follows template order.

## Components and state

```python
from spatial_ui import Component, Session, State, html

class Counter(Component):
    count = State(0)

    def increment(self):
        self.count += 1

    def render(self):
        return html(t"""
            <section>
                <p>Actions: {self.count}</p>
                <button type="button" on_click={self.increment}>Add one</button>
            </section>
        """)

session = Session(Counter())
first = session.render()
second = session.dispatch(first.event_ids[0], first.version)
assert "Actions: 1" in second.markup
```

State defaults are copied per instance. Retain child component instances on the
parent to preserve their state, then interpolate them as children. Rendering
reevaluates the t-string; it does not make a previously compiled template reactive.
Click handlers run synchronously without arguments and return `None`. They bind
only to semantic buttons; disabled buttons register no event. Every render
replaces the event allowlist, and session dispatch serializes callbacks and
rejects stale versions. Callback exceptions invalidate bindings and require a
page refresh. Ordinary Python side effects are not automatically rolled back.

## Run your component

```python
from spatial_ui import App

App(Counter, title="My workspace").run()  # Counter from the example above
```

Pass a factory that creates a fresh root component for each browser session.
The bundled server is for loopback development, with synchronous callbacks and
in-memory state. Sessions expire after 30 minutes of inactivity; a server restart
clears their state. Tabs in the same browser share a cookie and component tree;
an update in one tab makes another tab's event bindings stale, requiring refresh.
No filesystem directory is served. Custom CSS can be supplied as `stylesheet=`.

Apply `motion={Transition(duration=260, easing="ease-out")}` to a container to
animate changes in its descendant grid rectangles. Browser layout supplies the
positions, and the runtime uses Web Animations to move between them. A zero
duration or the system's reduced-motion preference disables transitions. This
initial transition API covers placement and container reflow, with advanced
movement/timeline composition left to the roadmap.

The browser preserves keyed nodes, focus, and live input/textarea/select values
through updates. Supply stable sibling keys (or unique HTML IDs) for controls
whose identity matters. Cross-parent moves and server-controlled form editing
are future work. Scratchpad text stays in the DOM and is not persisted on reload
or sent to Python by the click-only transport.

## Compatibility

The public API is early. Before 1.0, minor releases may include breaking changes;
patch releases are intended for compatible fixes. Pin the package version when
reproducibility matters. See [CHANGELOG.md](CHANGELOG.md) for release notes.

## Development

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --locked
uv run spatial-ui --version
uv run spatial-ui demo
uv run python -m unittest discover -s tests -v
uv build
```

Tests use the Python standard library. CI verifies Linux, macOS, and Windows
and installs both built distributions outside the checkout.
