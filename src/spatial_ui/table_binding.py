"""Session-owned dataset queries with one bounded worker and late-result refusal."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
import math
import re
import threading

from .components import Component
from .forms import Field, FieldError, FormBinding, FormResult, FormSchema
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

    def __init__(self, transport, path, schema, *, key_column=None):
        if type(path) is not str or not PATH.fullmatch(path):
            raise TableError("Select a static resource path.")
        schema = deepcopy(schema)
        try:
            if type(schema) is not dict or schema["contract"] != "tabular/1" or type(schema["columns"]) is not list or not 1 <= len(schema["columns"]) <= 64:
                raise ValueError
            self.table_id, self.schema_version = schema["table_id"], schema["schema_version"]
            self.columns = tuple(TableColumn(c["id"], c["label"], c["sortable"], c["searchable"], c["type"], c["nullable"], c["writable"]) for c in schema["columns"])
            if any(c["type"] not in {"text", "boolean", "integer", "number"} or c["visible"] is not True or type(c["nullable"]) is not bool for c in schema["columns"]):
                raise ValueError
            TablePage(self.table_id, self.schema_version, self.columns, ())
        except (ValueError, TypeError, KeyError):
            raise TableError("Invalid tabular schema.") from None
        self._spec = {c["id"]: c for c in schema["columns"]}
        if key_column is not None and (type(key_column) is not str or key_column not in self._spec
                or self._spec[key_column]["type"] != "text" or self._spec[key_column]["nullable"] or self._spec[key_column]["writable"]):
            raise TableError("Select the declared immutable text key column.")
        self.key_column = key_column
        self.can_edit = key_column is not None
        self.transport, self.path = transport, path
        self.mode = "Remote dataset query"

    @classmethod
    def open(cls, transport, path, *, key_column=None):
        if type(path) is not str or not PATH.fullmatch(path):
            raise TableError("Select a static resource path.")
        response = transport.request("GET", path + "/_table/schema")
        if response.status != 200:
            raise TableError("Table schema could not be loaded.")
        return cls(transport, path, response.data, key_column=key_column)

    def query(self, query, cancel):
        _check_cancel(cancel)
        response = self.transport.request("POST", self.path + "/_table/query", data=query)
        _check_cancel(cancel)
        if response.status != 200:
            raise TableError("Table query was refused or unavailable.")
        return self._page(response.data, query)

    def _page(self, data, query):
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
                rows.append(TableRow(row["key"], tuple(display(row["values"][name]) for name in data["columns"]), row["revision"],
                                     tuple(row["values"][name] for name in data["columns"])))
            return TablePage(self.table_id, self.schema_version, columns, tuple(rows), data["generation"], data["next_cursor"])
        except (ValueError, TypeError, KeyError, StopIteration):
            raise TableError("Invalid detached table response.") from None

    def close(self):
        pass

    def edit(self, draft, value, cancel):
        _check_cancel(cancel)
        response = self.transport.request("POST", self.path + "/_table/cell", data=dict(
            table_id=self.table_id, key=draft.key, column_id=draft.column.id, expected_revision=draft.revision, value=value))
        # A canceled dispatched request is uncertain even if storage committed.
        _check_cancel(cancel)
        if response.status != 200:
            code = response.data.get("error", {}).get("code") if type(response.data) is dict else None
            return EditResult({"revision_conflict": "conflict", "validation": "invalid", "unsupported_type": "invalid",
                               "read_only": "read_only", "forbidden": "denied", "not_found": "denied", "unauthenticated": "denied"}.get(code, "uncertain"))
        try:
            data = response.data
            if (type(data) is not dict or set(data) != {"contract", "table_id", "schema_version", "row"}
                    or (data["contract"], data["table_id"], data["schema_version"]) != ("tabular/1", self.table_id, self.schema_version)
                    or data["row"]["key"] != draft.key):
                raise ValueError
            page = self._page(dict(contract="tabular/1", table_id=self.table_id, schema_version=self.schema_version,
                generation=0, columns=[c.id for c in self.columns], rows=[data["row"]], next_cursor=None),
                dict(generation=0, columns=[c.id for c in self.columns], limit=1))
            if int(page.rows[0].revision) != int(draft.revision) + 1:
                raise ValueError
            return EditResult("saved", page.rows[0])
        except (ValueError, TypeError, KeyError, IndexError):
            return EditResult("uncertain")

    def reload(self, draft, cancel):
        if self.key_column is None:
            raise TableError("Authoritative reload requires the declared key column.")
        page = self.query(dict(generation=0, columns=[c.id for c in self.columns], order=[], search=None, cursor=None,
            limit=1, filters=[dict(column_id=self.key_column, operator="eq", value=draft.key)]), cancel)
        if not page.rows:
            return EditResult("denied")
        if page.rows[0].key != draft.key:
            raise TableError("Reload did not return the requested durable row.")
        return EditResult("review", page.rows[0])


@dataclass
class CellDraft:
    key: str
    column: TableColumn
    revision: str
    text: str
    is_null: bool
    epoch: int
    current: TableRow | None = None


@dataclass(frozen=True)
class EditResult:
    status: str
    row: TableRow | None = None


class TableBinding:
    """At most one active operation and one latest desired descriptor per session."""

    def __init__(self, client, *, page_size=50, owns_client=False, editable=False):
        if type(page_size) is not int or not 1 <= page_size <= 1000 or type(owns_client) is not bool or type(editable) is not bool:
            raise TableError("Select a bounded page size and explicit client ownership.")
        if editable and (not getattr(client, "can_edit", False) or any(not callable(getattr(client, name, None)) for name in ("edit", "reload"))):
            raise TableError("Editing requires checked writes and authoritative keyed reloads.")
        self.client, self.page_size, self.owns_client = client, page_size, owns_client
        self.columns = [c.id for c in client.columns]
        if page_size * len(self.columns) > 16000:
            raise TableError("Page exceeds the cell budget.")
        self.order, self.term, self.cursors = [], "", [None]
        self.generation, self.status, self.closed = 0, "loading", False
        self.page = TablePage(client.table_id, client.schema_version, client.columns, ())
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="table-query")
        self._active = self._desired = None
        self.editable, self.draft, self.edit_status = editable, None, "idle"
        self._epoch, self._write = 0, None
        self.refresh()

    def refresh(self, *, reset=True):
        if self.closed:
            raise TableError("Table binding is closed.")
        if reset:
            self.cursors = [None]
        self.generation += 1
        if self._write:
            self._write[4].set()
            self._write[0].cancel()
            self.edit_status = "uncertain"
            if self.draft:
                self.draft.current = None
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
        if self._write and self._write[0].done():
            future, draft, epoch, generation, cancel, operation = self._write
            self._write = None
            if draft is self.draft:
                if epoch != self._epoch or generation != self.generation or cancel.is_set():
                    self.edit_status, draft.current = "uncertain", None
                else:
                    try:
                        result = future.result()
                        if not isinstance(result, EditResult) or result.status not in {"saved", "review", "conflict", "invalid", "read_only", "denied", "uncertain"}:
                            raise TableError("Invalid edit completion.")
                        if result.status in {"saved", "review"} and (not isinstance(result.row, TableRow) or result.row.key != draft.key or result.row.revision is None):
                            raise TableError("Invalid committed row.")
                        self.edit_status = result.status
                        draft.current = result.row if result.status == "review" else None
                        if result.status == "saved":
                            self.draft = None
                            self.refresh()
                    except Exception:
                        self.edit_status, draft.current = "uncertain", None
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
        if self._active is None and self._desired is not None and self._write is None:
            query = self._desired
            cancel = threading.Event()
            self._active = self._worker.submit(self.client.query, deepcopy(query), cancel), self.generation, cancel

    def begin_edit(self, key, column_id):
        if self.closed or not self.editable or self.draft is not None:
            return
        column = next((c for c in self.page.columns if c.id == column_id), None)
        row = next((r for r in self.page.rows if r.key == key), None)
        if column is None or not column.writable or row is None or row.revision is None or row.raw_values is None:
            self.edit_status = "read_only"
            return
        value = row.raw_values[[c.id for c in self.page.columns].index(column_id)]
        text = "" if value is None else str(value).lower() if type(value) is bool else str(value)
        if len(text.encode("utf-8")) > 4096:
            self.edit_status = "read_only"
            return
        self.draft = CellDraft(key, column, row.revision, text, value is None, self._epoch)
        self.edit_status = "draft"

    def stage(self, text=None, is_null=None):
        if self.draft is None or self._write or self.closed:
            return
        if text is not None:
            self.draft.text = text
        if is_null is not None:
            self.draft.is_null = is_null
        if self.edit_status in {"draft", "invalid"}:
            self.edit_status = "draft"

    def _value(self):
        draft = self.draft
        if draft.is_null:
            if not draft.column.nullable:
                raise ValueError
            return None
        text = draft.text
        if draft.column.type == "text":
            return text
        if draft.column.type == "boolean":
            if text not in {"true", "false"}:
                raise ValueError
            return text == "true"
        if draft.column.type == "integer":
            if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)", text, re.ASCII) or abs(value := int(text)) > 9007199254740991:
                raise ValueError
            return value
        if draft.column.type == "number":
            if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", text, re.ASCII) or not math.isfinite(value := float(text)):
                raise ValueError
            return value
        raise ValueError

    def _operation(self, operation, value=None):
        draft = self.draft
        if draft is None or self.closed or self._write or draft.epoch != self._epoch:
            return
        cancel = threading.Event()
        client, captured = self.client, deepcopy(draft)
        callback = (lambda: client.edit(captured, value, cancel)) if operation == "edit" else (lambda: client.reload(captured, cancel))
        self._write = self._worker.submit(callback), draft, self._epoch, self.generation, cancel, operation
        self.edit_status = "pending" if operation == "edit" else "reloading"

    def save(self):
        if self.draft is None or self.edit_status not in {"draft", "invalid"}:
            return
        try:
            value = self._value()
        except (ValueError, OverflowError):
            self.edit_status = "invalid"
            return
        self._operation("edit", value)

    def reload_draft(self):
        self._operation("reload")

    def reapply(self):
        if self.draft is not None and self.draft.current is not None and self.edit_status == "review" and self.draft.epoch == self._epoch:
            self.draft.revision, self.draft.current, self.edit_status = self.draft.current.revision, None, "draft"

    def discard(self):
        if self._write:
            self._write[4].set()
            self._write[0].cancel()
            self.edit_status = "uncertain"
        else:
            self.draft, self.edit_status = None, "idle"

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
        self._epoch += 1
        if self.draft:
            self.draft.current, self.edit_status = None, "uncertain"
        self.client, self.columns, self.order, self.term = client, [c.id for c in client.columns], [], ""
        self.page = TablePage(client.table_id, client.schema_version, client.columns, ())
        self.refresh()
        if self.owns_client and old is not client:
            # The single worker closes old sources after every accepted operation.
            self._worker.submit(old.close)

    def close(self):
        if self.closed:
            return
        self.closed, self.status, self._desired = True, "closed", None
        if self._active:
            self._active[2].set()
            self._active[0].cancel()
        if self._write:
            self._write[4].set()
            self._write[0].cancel()
        self._worker.shutdown(wait=True, cancel_futures=False)
        self._active = None
        self._write, self.draft = None, None
        if self.owns_client:
            self.client.close()


class TableBrowser(Component):
    """Dataset controls and a TableView; construct one fresh instance per session."""

    def __init__(self, client, *, title="Table", key="table-browser", page_size=50, owns_client=False, editable=False):
        self.view = TableView(TablePage(client.table_id, client.schema_version, client.columns, ()), title=title, key=key + "-view")
        self.binding = TableBinding(client, page_size=page_size, owns_client=owns_client, editable=editable)
        self.view.on_sort = self.binding.sort
        self.view.editable = editable
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

    def _edit_event(self, event):
        self.binding.stage(event.fields.get("value"), event.fields.get("null"))
        if event.kind == "submit":
            self.binding.save()
        return FormResult("invalid", (FieldError("value"),)) if self.binding.edit_status == "invalid" else FormResult()

    def _editor(self):
        binding = self.binding
        if not binding.editable:
            return None
        messages = {"idle": "Select a cell, then choose Edit selected cell.", "draft": "Unsaved cell draft.",
            "pending": "Saving cell.", "saved": "Cell saved.", "invalid": "Invalid value. Your draft is preserved.",
            "read_only": "This cell is read-only or exceeds the editor limit.", "denied": "Access denied or row unavailable. Your draft is preserved.",
            "conflict": "Row changed. Your draft is preserved. Reload current row before reapplying.",
            "uncertain": "Save outcome is uncertain. Reload current row before retrying.", "reloading": "Loading current row.",
            "review": "Review the current value, then explicitly reapply or discard your draft."}
        controls = []
        draft = binding.draft
        if draft is None:
            def begin():
                if self.view.selected:
                    binding.begin_edit(*self.view.selected)
            controls.append(html(t'<button type="button" on_click={begin} disabled={self.view.selected is None}>Edit selected cell</button>'))
        else:
            busy, detached = binding._write is not None, draft.epoch != binding._epoch
            editor_id = _key(self.key, draft.key, draft.column.id, str(draft.epoch))
            schema = FormSchema((Field("value", "select" if draft.column.type == "boolean" else "text", required=False, default="", disabled=busy),
                                 Field("null", "checkbox", required=False, default=False, disabled=busy)))
            form = FormBinding(schema, self._edit_event)
            if draft.column.type == "boolean":
                control = html(t'<select key="value" id={editor_id} name="value" disabled={busy}><option value="false" selected={draft.text == "false"}>false</option><option value="true" selected={draft.text == "true"}>true</option></select>')
            else:
                control = html(t'<input key="value" id={editor_id} name="value" type="text" data-table-edit-input="true" value={draft.text} maxlength="4096" aria-invalid={binding.edit_status == "invalid"} disabled={busy}>')
            current = ""
            if draft.current is not None:
                index = [c.id for c in binding.client.columns].index(draft.column.id)
                current = f"Current value: {draft.current.values[index]}. Current revision: {draft.current.revision}."
            controls.append(html(t'''<form key={editor_id} data-table-editor="true" on_form={form}>
              <p>Editing row {draft.key}, column {draft.column.label}, revision {draft.revision}.</p>
              <label for={editor_id}>Cell value</label>{control}
              <label><input key="null" name="null" type="checkbox" checked={draft.is_null} disabled={busy}>Use null</label>
              <button type="submit" disabled={busy or detached or binding.edit_status not in {"draft", "invalid"}}>Save cell</button>
              <button type="button" data-table-discard="true" on_click={binding.discard}>Discard cell draft</button>
              <button type="button" on_click={binding.reload_draft} disabled={busy or detached}>Reload current row</button>
              <p>{current}</p><button type="button" on_click={binding.reapply} disabled={busy or detached or binding.edit_status != "review"}>Reapply draft to current revision</button>
              <p>{"This draft belongs to the previous source. Discard it before editing the new source." if detached else "Enter saves; Escape discards. View changes preserve this target and draft."}</p>
            </form>'''))
        return html(t'<section key={self.key + "-editor"}><p role="status" data-table-edit-state={binding.edit_status}>{messages[binding.edit_status]}</p>{controls}</section>')

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
            <input key="search" id={search_id} name="search" type="text" maxlength="256" value={binding.term}><button type="submit">Search</button></form>
          <fieldset><legend>Displayed columns</legend>{visibility}{movement}</fieldset>
          <button type="button" on_click={binding.previous} disabled={binding.closed or len(binding.cursors) == 1}>Previous page</button>
          <button type="button" on_click={binding.next} disabled={binding.status != "ready" or not binding.page.next_cursor}>Next page</button>
          <button type="button" on_click={binding.refresh} disabled={binding.closed}>Refresh table</button>
          <button type="button" hidden data-table-poll={"true" if binding.status == "loading" or binding._write is not None else None} on_click={binding.poll}>Check query</button>
          {self._editor()}{self.view.render()}</section>''')
