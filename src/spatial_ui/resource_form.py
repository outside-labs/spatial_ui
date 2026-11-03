"""A session-owned typed editor over the optional checked REST binding."""

from spatial_ui import Component, Field, FieldError, FormBinding, FormResult, FormSchema, html
from spatial_ui.forms import NAME
from spatial_ui.live import LiveBinding, LiveConfig
from spatial_ui.rest import BindingError, KEY, ResourceBinding, ResourceClient

ACTIONS = (("save", "Save"), ("load", "Load / refresh"), ("discard", "Discard edits"),
           ("delete", "Delete"), ("new", "Start new"))


class ResourceForm(Component):
    """Create one fresh component per browser; clients may share stateless transport."""

    def __init__(self, client: ResourceClient, *, title="Resource editor", labels=None, choices=None, key="resource-editor", live=None):
        if not isinstance(client, ResourceClient) or not isinstance(title, str) or not isinstance(key, str) or not NAME.fullmatch(key):
            raise BindingError("Select a resource client, title and stable form key.")
        if len(client.spec.schema.fields) >= 64 or any(field.name == "operation" for field in client.spec.schema.fields):
            raise BindingError("Reserve one form field named operation for editor actions.")
        self.binding = ResourceBinding(client)
        if live is not None and not isinstance(live, LiveConfig):
            raise BindingError("Select an explicit LiveConfig for this editor.")
        self.live = live
        self.title, self.key = title, key
        self.labels, self.choices = dict(labels or {}), {name: tuple(values) for name, values in (choices or {}).items()}
        names = {field.name for field in client.spec.schema.fields}
        if self.labels.keys() - names or self.choices.keys() - names or any(type(value) is not str for value in self.labels.values()):
            raise BindingError("Configure labels and choices only for declared fields.")
        for field in client.spec.schema.fields:
            if field.kind == "select":
                values = self.choices.get(field.name, ())
                if not 1 <= len(values) <= 64 or len(set(values)) != len(values):
                    raise BindingError("Select controls require 1–64 unique text choices.")
                for value in values:
                    field.check(value)
        self.schema = FormSchema((*client.spec.schema.fields, Field("operation", "select", default="save")))
        self.operation = "save"
        self.last_result = FormResult()

    def handle(self, event):
        values = {name: value for name, value in event.fields.items() if name != "operation"}
        operation = event.fields.get("operation", self.operation)
        if operation not in dict(ACTIONS):
            return self._result(FormResult("invalid", (FieldError("operation"),)))
        self.operation = operation
        identity = self.binding.client.spec.key_field
        if self.binding.committed is not None and identity in values and values[identity] != self.binding.committed[identity]:
            return self._result(FormResult("invalid", (FieldError(identity, "immutable"),)))
        changed = {name: value for name, value in values.items() if value != self.binding.draft[name]}
        self.binding.edit(changed)
        if event.kind != "submit":
            return self._result(self.binding.result() if self.binding.status in {"conflict", "uncertain"} else FormResult())
        if operation in {"save", "load"} and not KEY.fullmatch(self.binding.draft[identity]):
            return self._result(FormResult("invalid", (FieldError(identity),)))
        if operation == "save":
            missing = tuple(FieldError(field.name, "required") for field in self.binding.client.spec.schema.fields
                            if not field.disabled and field.required and field.kind != "checkbox" and not self.binding.draft[field.name].strip())
            if missing:
                return self._result(FormResult("invalid", missing))
        try:
            if operation in {"discard", "new"}:
                self.binding.discard(new=operation == "new")
                result = FormResult("loaded")
            else:
                selected = ("create" if self.binding.committed is None else "update") if operation == "save" else operation
                self.binding.perform(selected)
                result = self.binding.result()
                if result.status == "deleted":
                    self.binding.discard(new=True)
        except BindingError:
            result = self.binding.result() if self.binding.status in {"conflict", "uncertain"} else FormResult("error")
        if result.status in {"saved", "loaded", "deleted"}:
            self.operation = "save"
        return self._result(result)

    def _result(self, result):
        self.last_result = result
        return result

    def render(self):
        controls = []
        errors = {error.name: error for error in self.last_result.errors}
        for field in self.binding.client.spec.schema.fields:
            name = field.name
            identifier = f"{self.key}-{name}"
            value = self.binding.draft.get(name, self.binding.committed.get(name) if self.binding.committed else field.default)
            value = value if value is not None else False if field.kind == "checkbox" else ""
            label = self.labels.get(name, name.replace("_", " ").capitalize())
            error = errors.get(name)
            description = f"{identifier}-error" if error else None
            readonly = name == self.binding.client.spec.key_field and self.binding.committed is not None
            if field.kind == "textarea":
                control = html(t'<textarea key={name} id={identifier} name={name} disabled={field.disabled} aria-invalid={bool(error)} aria-describedby={description}>{value}</textarea>')
            elif field.kind == "select":
                options = [html(t'<option value={choice} selected={choice == value}>{choice}</option>') for choice in self.choices[name]]
                control = html(t'<select key={name} id={identifier} name={name} disabled={field.disabled} aria-invalid={bool(error)} aria-describedby={description}>{options}</select>')
            elif field.kind == "checkbox":
                control = html(t'<input key={name} id={identifier} name={name} type="checkbox" checked={value} disabled={field.disabled} aria-invalid={bool(error)} aria-describedby={description}>')
            else:
                control = html(t'<input key={name} id={identifier} name={name} type="text" value={value} readonly={readonly} disabled={field.disabled} aria-invalid={bool(error)} aria-describedby={description}>')
            message = html(t'<p id={description}>{error.as_dict()["message"]}</p>') if error else None
            controls.append(html(t'<div key={f"row-{name}"}><label for={identifier}>{label}</label>{control}{message}</div>'))
        actions = [html(t'<option value={value} selected={self.operation == value}>{label}</option>') for value, label in ACTIONS]
        binding = FormBinding(self.schema, self.handle)
        action_id = f"{self.key}-operation"
        message = self.last_result.as_dict()["message"]
        revision = self.binding.revision or "unsaved"
        committed = None
        remote = None
        if self.binding.committed is not None:
            rows = [html(t'<dt>{name}</dt><dd>{str(value)}</dd>') for name, value in self.binding.committed.items()]
            committed = html(t'<details key="committed"><summary>Loaded server value</summary><dl>{rows}</dl></details>')
            if self.live is not None and self.binding.status != "closed":
                live_binding = LiveBinding(self.live, self.binding.client.spec, dict(self.binding.committed))
                remote = html(t'''<aside key="live" live={live_binding}>
                  <h3>Live server value</h3><p data-live-status role="status" aria-live="polite">Connecting to live updates.</p>
                  <dl data-live-value></dl></aside>''')
        return html(t'''<section key={f"section-{self.key}"}><h2>{self.title}</h2>
          <form key={self.key} on_form={binding} novalidate>{controls}
            <label for={action_id}>Action</label><select key="operation" id={action_id} name="operation">{actions}</select>
            <button type="submit">Apply</button>
          </form><p role="status" aria-live="polite">{message}</p><p>Revision: {revision}</p>{committed}{remote}
        </section>''')
