# spacial_ui

A Python spatial UI engine with square logical grid units, relative placement,
and Python 3.14 t-string templates. Early development; no release is published.

The initial milestone is a responsive dashboard with semantic HTML and Python
event handlers. See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the
architecture, acceptance criteria, and later milestones. Issues and the GitHub
Project track current work; pull requests record implementation and verification.

## Spatial layout

```python
from spacial_ui import Grid, at, below, right_of

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
from spacial_ui import Grid, at, html, render

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

## Development

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --locked
uv run spacial-ui --version
uv run python -m unittest discover -s tests -v
uv build
```

The package has no third-party runtime dependencies. Tests use the Python
standard library. CI verifies Linux, macOS, and Windows and installs both built
distributions outside the checkout. Package publication, hosted documentation,
and live deployment are separate release decisions.
