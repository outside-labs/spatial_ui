# spacial_ui

A Python spatial UI engine with square logical grid units, relative placement,
and Python 3.14 t-string templates. Early development; no release is published.

The initial milestone is a responsive dashboard with semantic HTML and Python
event handlers. See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the
architecture, acceptance criteria, and later milestones. Issues and the GitHub
Project track current work; pull requests record implementation and verification.

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
