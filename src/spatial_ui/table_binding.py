"""Session-owned dataset queries with one bounded worker and late-result refusal."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import math
import threading

from .components import Component
from .forms import Field, FormBinding, FormResult, FormSchema
from .rest import PATH
from .table import TableColumn, TableError, TablePage, TableRow, TableView, _key, display
from .templates import html


class QueryCancelled(Exception):
    pass


def _check_cancel(cancel):
    if cancel.is_set():
        raise QueryCancelled


class HeadlessTableClient:
    """Consume a caller-owned public TableSource; no package import is needed."""

    def __init__(self, source, *, owns_source=False):
        if type(owns_source) is not bool:
            raise TableError("Source ownership must be explicit.")
        self.source, self.owns_source = source, owns_source
        self.table_id, self.schema_version = source.table_id, source.schema_version
        self.columns = tuple(TableColumn(c.id, c.label, c.sortable, c.searchable) for c in source.columns if c.visible)
        self.mode = "Complete bounded local table"
        TablePage(self.table_id, self.schema_version, self.columns, ())

    def query(self, query, cancel):
        _check_cancel(cancel)
        snapshot = self.source.query(columns=query["columns"], order=[(item["column_id"], item["descending"]) for item in query["order"]],
            search=query["search"], limit=query["limit"], cursor=query["cursor"], generation=query["generation"])
        _check_cancel(cancel)
        return TablePage.from_source(snapshot)

    def close(self):
        if self.owns_source:
            self.source.close()


class HTTPTableClient:
    """Stateless GET-schema/POST-query binding over an explicit HTTP transport."""

    def __init__(self, transport, path, schema):
        if type(path) is not str or not PATH.fullmatch(path):
            raise TableError("Select a static resource path.")
        schema = deepcopy(schema)
        try:
            if type(schema) is not dict or schema["contract"] != "tabular/1" or type(schema["columns"]) is not list or not 1 <= len(schema["columns"]) <= 64:
                raise ValueError
            self.table_id, self.schema_version = schema["table_id"], schema["schema_version"]
            self.columns = tuple(TableColumn(c["id"], c["label"], c["sortable"], c["searchable"]) for c in schema["columns"])
            if any(c["type"] not in {"text", "boolean", "integer", "number"} or c["visible"] is not True or type(c["nullable"]) is not bool for c in schema["columns"]):
                raise ValueError
            TablePage(self.table_id, self.schema_version, self.columns, ())
        except (ValueError, TypeError, KeyError):
            raise TableError("Invalid tabular schema.") from None
        self._spec = {c["id"]: c for c in schema["columns"]}
        self.transport, self.path = transport, path
        self.mode = "Remote dataset query"

    @classmethod
    def open(cls, transport, path):
        if type(path) is not str or not PATH.fullmatch(path):
            raise TableError("Select a static resource path.")
        response = transport.request("GET", path + "/_table/schema")
        if response.status != 200:
            raise TableError("Table schema could not be loaded.")
        return cls(transport, path, response.data)

    def query(self, query, cancel):
        _check_cancel(cancel)
        response = self.transport.request("POST", self.path + "/_table/query", data=query)
        _check_cancel(cancel)
        if response.status != 200:
            raise TableError("Table query was refused or unavailable.")
        data = response.data
        try:
            if (type(data) is not dict or set(data) != {"contract", "table_id", "schema_version", "generation", "columns", "rows", "next_cursor"}
                    or data["contract"] != "tabular/1" or data["table_id"] != self.table_id or data["schema_version"] != self.schema_version
                    or type(data["generation"]) is not int or data["generation"] != query["generation"]
                    or data["columns"] != query["columns"] or type(data["rows"]) is not list or len(data["rows"]) > query["limit"]):
                raise ValueError
            columns = tuple(next(c for c in self.columns if c.id == name) for name in data["columns"])
            rows = []
            for row in data["rows"]:
                if type(row) is not dict or set(row) != {"key", "revision", "values"} or type(row["values"]) is not dict or set(row["values"]) != set(data["columns"]) or row["revision"] is None:
                    raise ValueError
                for name, value in row["values"].items():
                    spec = self._spec[name]
                    expected = {"text": str, "boolean": bool, "integer": int, "number": float}[spec["type"]]
                    if value is None:
                        if not spec["nullable"]:
                            raise ValueError
                    elif type(value) is not expected or (type(value) is int and abs(value) > 9007199254740991) or (type(value) is float and not math.isfinite(value)):
                        raise ValueError
                rows.append(TableRow(row["key"], tuple(display(row["values"][name]) for name in data["columns"]), row["revision"]))
            return TablePage(self.table_id, self.schema_version, columns, tuple(rows), data["generation"], data["next_cursor"])
        except (ValueError, TypeError, KeyError, StopIteration):
            raise TableError("Invalid detached table response.") from None

    def close(self):
        pass


class TableBinding:
    """At most one active operation and one latest desired descriptor per session."""

    def __init__(self, client, *, page_size=50, owns_client=False):
        if type(page_size) is not int or not 1 <= page_size <= 1000 or type(owns_client) is not bool:
            raise TableError("Select a bounded page size and explicit client ownership.")
        self.client, self.page_size, self.owns_client = client, page_size, owns_client
        self.columns = [c.id for c in client.columns]
        if page_size * len(self.columns) > 16000:
            raise TableError("Page exceeds the cell budget.")
        self.order, self.term, self.cursors = [], "", [None]
        self.generation, self.status, self.closed = 0, "loading", False
        self.page = TablePage(client.table_id, client.schema_version, client.columns, ())
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="table-query")
        self._active = self._desired = None
        self.refresh()

    def refresh(self, *, reset=True):
        if self.closed:
            raise TableError("Table binding is closed.")
        if reset:
            self.cursors = [None]
        self.generation += 1
        if self._active:
            self._active[2].set()
            self._active[0].cancel()
        fields = [c.id for c in self.client.columns if c.searchable][:8]
        self._desired = dict(generation=self.generation, columns=list(self.columns), order=deepcopy(self.order),
            search={"fields": fields, "term": self.term} if self.term and fields else None,
            limit=self.page_size, cursor=self.cursors[-1])
        self.status = "loading"
        self.poll()

    def poll(self):
        if self.closed:
            return
        if self._active and self._active[0].done():
            future, generation, cancel = self._active
            self._active = None
            if generation == self.generation and not cancel.is_set():
                try:
                    page = future.result()
                    if not isinstance(page, TablePage) or page.generation != generation or (page.table_id, page.schema_version) != (self.client.table_id, self.client.schema_version):
                        raise TableError("Stale table response.")
                    self.page, self.status = page, "ready"
                except Exception:
                    self.status = "error"
                self._desired = None
        if self._active is None and self._desired is not None:
            query = self._desired
            cancel = threading.Event()
            self._active = self._worker.submit(self.client.query, deepcopy(query), cancel), self.generation, cancel

    def sort(self, name):
        if name not in {c.id for c in self.client.columns if c.sortable}:
            raise TableError("Column is not sortable.")
        old = next((item["descending"] for item in self.order if item["column_id"] == name), None)
        self.order = [] if old is True else [{"column_id": name, "descending": old is False}]
        self.refresh()

    def search(self, term):
        if type(term) is not str or len(term) > 256:
            raise TableError("Search exceeds its code-point bound.")
        self.term = term
        self.refresh()

    def project(self, columns):
        if type(columns) is not list or len(set(columns)) != len(columns) or any(name not in {c.id for c in self.client.columns} for name in columns):
            raise TableError("Select distinct declared columns.")
        self.columns = list(columns)
        self.refresh()

    def next(self):
        if self.status == "ready" and self.page.next_cursor:
            self.cursors.append(self.page.next_cursor)
            self.refresh(reset=False)

    def previous(self):
        if len(self.cursors) > 1:
            self.cursors.pop()
            self.refresh(reset=False)

    def replace(self, client):
        if self.closed:
            raise TableError("Table binding is closed.")
        old = self.client
        self.client, self.columns, self.order, self.term = client, [c.id for c in client.columns], [], ""
        self.page = TablePage(client.table_id, client.schema_version, client.columns, ())
        self.refresh()
        if self.owns_client and old is not client:
            # Active callers retain old sources until their bounded operation drains.
            if self._active:
                self._active[0].add_done_callback(lambda future: old.close())
            else:
                old.close()

    def close(self):
        if self.closed:
            return
        self.closed, self.status, self._desired = True, "closed", None
        if self._active:
            self._active[2].set()
            self._active[0].cancel()
        self._worker.shutdown(wait=True, cancel_futures=True)
        self._active = None
        if self.owns_client:
            self.client.close()


class TableBrowser(Component):
    """Dataset controls and a TableView; construct one fresh instance per session."""

    def __init__(self, client, *, title="Table", key="table-browser", page_size=50, owns_client=False):
        self.view = TableView(TablePage(client.table_id, client.schema_version, client.columns, ()), title=title, key=key + "-view")
        self.binding = TableBinding(client, page_size=page_size, owns_client=owns_client)
        self.view.on_sort = self.binding.sort
        self.key = key

    def close(self):
        self.binding.close()

    def handle(self, event):
        if "search" in event.fields:
            try:
                self.binding.search(event.fields["search"])
            except TableError:
                return FormResult("invalid")
        return FormResult()

    def render(self):
        binding = self.binding
        binding.poll()
        self.view.replace(binding.page)
        self.view.status = binding.status if binding.status in {"ready", "loading", "error"} else "error"
        self.view.order = tuple((item["column_id"], item["descending"]) for item in binding.order)
        search_id = self.key + "-search"
        schema = FormSchema((Field("search", required=False, default=""),))
        form = FormBinding(schema, self.handle)
        visibility, movement = [], []
        for column in binding.client.columns:
            name = column.id
            shown = name in binding.columns
            toggle = lambda n=name: binding.project([c for c in binding.columns if c != n] if n in binding.columns else [*binding.columns, n])
            visibility.append(html(t'<button key={_key("show", name)} type="button" aria-pressed={shown} on_click={toggle} disabled={binding.closed}>Show {column.label}</button>'))
        for index, name in enumerate(binding.columns):
            if index:
                def earlier(i=index):
                    columns = list(binding.columns)
                    columns[i - 1], columns[i] = columns[i], columns[i - 1]
                    binding.project(columns)
                movement.append(html(t'<button key={_key("move", name)} type="button" on_click={earlier} disabled={binding.closed}>Move {name} earlier</button>'))
        return html(t'''<section key={self.key} data-table-controls="true"><p>{binding.client.mode}</p>
          <form key={self.key + "-form"} on_form={form}><label for={search_id}>Search table</label>
            <input key="search" id={search_id} name="search" type="text" maxlength="256" value={binding.term}/><button type="submit">Search</button></form>
          <fieldset><legend>Displayed columns</legend>{visibility}{movement}</fieldset>
          <button type="button" on_click={binding.previous} disabled={binding.closed or len(binding.cursors) == 1}>Previous page</button>
          <button type="button" on_click={binding.next} disabled={binding.status != "ready" or not binding.page.next_cursor}>Next page</button>
          <button type="button" on_click={binding.refresh} disabled={binding.closed}>Refresh table</button>
          <button type="button" hidden data-table-poll={"true" if binding.status == "loading" else None} on_click={binding.poll}>Check query</button>
          {self.view.render()}</section>''')
