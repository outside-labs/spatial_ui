"""Bounded semantic read-only tables over detached display pages."""

from dataclasses import dataclass
import hashlib
import re

from spatial_ui.components import Component
from spatial_ui.templates import html

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z", re.ASCII)
TABLE_CSS = """
[data-table-view]{min-inline-size:0;max-inline-size:100%}
.sui-table-scroll{max-inline-size:100%;overflow:auto}
[data-table-view] table{border-collapse:collapse;inline-size:max-content;min-inline-size:100%}
[data-table-view] th,[data-table-view] td{padding:.45rem;border:1px solid #888;text-align:start;max-inline-size:28ch;overflow-wrap:anywhere}
[data-table-cell]{background:transparent;color:inherit;border:0;padding:.25rem;max-inline-size:28ch;text-align:start;white-space:normal;overflow-wrap:anywhere}
[data-table-cell]:focus-visible{outline:3px solid currentColor;outline-offset:2px}
[data-table-view] td[aria-selected=true]{background:color-mix(in srgb,currentColor 12%,transparent)}
@media(prefers-reduced-motion:reduce){[data-table-view] *{scroll-behavior:auto;transition:none}}
"""


class TableError(ValueError):
    """A detached display page exceeds or violates the table profile."""


@dataclass(frozen=True, slots=True)
class TableColumn:
    id: str
    label: str
    sortable: bool = False
    searchable: bool = False

    def __post_init__(self):
        if (type(self.id) is not str or not ID.fullmatch(self.id) or type(self.label) is not str or len(self.label) > 256
                or type(self.sortable) is not bool or type(self.searchable) is not bool):
            raise TableError("Invalid display column.")


@dataclass(frozen=True, slots=True)
class TableRow:
    key: str
    values: tuple[str, ...]
    revision: str | None = None

    def __post_init__(self):
        if type(self.values) not in (tuple, list) or len(self.values) > 64 or any(type(value) is not str or len(value) > 16384 for value in self.values):
            raise TableError("Display cells require bounded text.")
        object.__setattr__(self, "values", tuple(self.values))
        if type(self.key) is not str or not ID.fullmatch(self.key):
            raise TableError("Invalid row identity.")
        if self.revision is not None and (type(self.revision) is not str or not re.fullmatch(r"[1-9][0-9]{0,18}", self.revision, re.ASCII)
                                         or int(self.revision) > 9223372036854775807):
            raise TableError("Invalid row revision.")


@dataclass(frozen=True, slots=True)
class TablePage:
    table_id: str
    schema_version: str
    columns: tuple[TableColumn, ...]
    rows: tuple[TableRow, ...]
    generation: int = 0
    next_cursor: str | None = None

    def __post_init__(self):
        if (type(self.columns) not in (tuple, list) or type(self.rows) not in (tuple, list)
                or len(self.columns) > 64 or len(self.rows) > 1000 or len(self.columns) * len(self.rows) > 16000
                or any(not isinstance(column, TableColumn) for column in self.columns)
                or any(not isinstance(row, TableRow) or len(row.values) != len(self.columns) for row in self.rows)
                or len({c.id for c in self.columns}) != len(self.columns) or len({r.key for r in self.rows}) != len(self.rows)
                or sum(len(value) for row in self.rows for value in row.values) > 1000000):
            raise TableError("Invalid or unbounded table page.")
        if (type(self.table_id) is not str or not ID.fullmatch(self.table_id)
                or type(self.schema_version) is not str or not 1 <= len(self.schema_version) <= 128
                or type(self.generation) is not int or not 0 <= self.generation <= 9007199254740991
                or self.next_cursor is not None and (type(self.next_cursor) is not str or not 1 <= len(self.next_cursor) <= 16384)):
            raise TableError("Invalid page identity or continuation.")
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "rows", tuple(self.rows))

    @classmethod
    def from_source(cls, snapshot):
        """Consume public headless snapshot attributes without importing it."""
        columns = tuple(TableColumn(c.id, c.label, c.sortable, c.searchable) for c in snapshot.columns)
        rows = tuple(TableRow(row.key, tuple(display(cell.value) for cell in row.cells), row.revision) for row in snapshot.rows)
        return cls(snapshot.table_id, snapshot.schema_version, columns, rows, snapshot.generation, snapshot.next_cursor)


def display(value):
    text = "—" if value is None else str(value)
    return text if len(text) <= 16384 else text[:16383] + "…"


def _key(*parts):
    return "cell-" + hashlib.sha256("\0".join(parts).encode()).hexdigest()


class TableView(Component):
    """One session's selection over an immutable page; no write callback exists."""

    def __init__(self, page: TablePage, *, title="Table", key="table", status="ready", order=()):
        if not isinstance(page, TablePage) or type(title) is not str or len(title) > 256 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", key):
            raise TableError("Select a detached page, bounded title and stable table key.")
        if status not in {"ready", "loading", "error"}:
            raise TableError("Unknown table status.")
        self.page, self.title, self.key, self.status = page, title, key, status
        self.selected = None
        self.order = tuple(order)

    def replace(self, page):
        if not isinstance(page, TablePage):
            raise TableError("Select a detached page.")
        if (page.table_id, page.schema_version) != (self.page.table_id, self.page.schema_version):
            self.selected = None
        self.page = page

    def select(self, row_key, column_id):
        if row_key not in {row.key for row in self.page.rows} or column_id not in {column.id for column in self.page.columns}:
            raise TableError("Selection is outside this displayed page.")
        self.selected = row_key, column_id

    def render(self):
        headers = []
        ordering = dict(self.order)
        for column in self.page.columns:
            sort = "descending" if ordering.get(column.id) is True else "ascending" if column.id in ordering else "none"
            headers.append(html(t'<th key={_key(column.id)} scope="col" aria-sort={sort}>{column.label}</th>'))
        rows = []
        for row_index, row in enumerate(self.page.rows):
            cells = []
            for column_index, (column, value) in enumerate(zip(self.page.columns, row.values, strict=True)):
                selected = self.selected == (row.key, column.id)
                tabindex = 0 if selected or self.selected is None and row_index == column_index == 0 else -1
                label = f"Row {row.key}, {column.label}: {value}"
                callback = lambda r=row.key, c=column.id: self.select(r, c)
                cells.append(html(t'''<td key={_key(row.key, column.id)} aria-selected={str(selected).lower()}>
                    <button key={_key(row.key, column.id, "button")} type="button" data-table-cell="true"
                    data-row-key={row.key} data-column-id={column.id} aria-label={label} tabindex={tabindex} on_click={callback}>{value}</button></td>'''))
            rows.append(html(t'<tr key={_key(row.key)}><th scope="row">{row.key}</th>{cells}</tr>'))
        message = "Loading table." if self.status == "loading" else "Table could not be loaded. Refresh to retry." if self.status == "error" else "No rows match this view." if not rows else f"{len(rows)} rows displayed."
        selection = f"Selected row {self.selected[0]}, column {self.selected[1]}." if self.selected else "Select a cell. Use arrow keys, Home and End to navigate."
        return html(t'''<section key={self.key} data-table-view="true" aria-busy={self.status == "loading"}>
          <h2>{self.title}</h2><p role="status" aria-live="polite">{message}</p><p>{selection}</p>
          <div class="sui-table-scroll" role="region" aria-label={self.title} tabindex="0">
            <table><caption>{self.title}</caption><thead><tr><th scope="col">Row key</th>{headers}</tr></thead><tbody>{rows}</tbody></table>
          </div><p>Read-only view. Long cells are shortened for display.</p></section>''')
