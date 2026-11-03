# Typed form event contract v1

This contract selects the first typed input boundary for INPUT-01. The loopback
development transport implements keyed `on_form={FormBinding(schema, callback)}`
forms with synchronous callbacks returning `FormResult`. The optional
`browser-tests` development group verifies actual Chromium interaction; production
and asynchronous transports retain their separate owning contracts.

## Events and admission

Retain the session-owned opaque `event` token and integer render `version`.
Add exactly one `payload` object with `kind` (`input`, `change`, or `submit`)
and `fields`. No arbitrary callback name, component lookup or model import
comes from the browser. `input` and `change` contain exactly one field; `submit`
contains the complete declared writable field set, with at most 64 fields.
The full UTF-8 request is at most 16384 bytes and uses unique JSON keys.
Reject unknown top-level/payload fields, unknown field names, non-finite values,
invalid Unicode and stale render versions before invoking any callback.

Fields map declared names to strings (text, select, textarea) or booleans
(checkbox). Text is at most 4096 UTF-8 bytes. Missing, empty string and false
are distinct; an unchecked checkbox submits false. Disabled fields are absent
and cannot satisfy a required field. Omitted optional fields use explicit
form-model defaults. Numeric/date parsing is application validation over the
original string; transport does not coerce it. Multi-select arrays, files,
binary values, dynamic repeated field groups and credential forms need their
own selected profiles. Payloads never alter event routing or authorization.

## Callback and validation ownership

The form declaration owns field names, kinds, required/default rules and event
bindings. Callbacks receive an immutable detached event value. The initial
callback profile is synchronous, returns a typed result, and runs under the
existing session lock; asynchronous save ownership belongs to its later
transport contract. A validation result contains public field paths and stable
codes plus fixed messages, without echoing submitted values. Failed validation
preserves draft strings/checkboxes and does not modify committed domain state.
Focus targets a declared first-invalid control; non-field errors use a live
status region. DOM markup is always rendered/escaped by the existing template
boundary; server-supplied error text is text, not executable markup.

## Editing, generation and conflict rules

The browser owns a draft map with a monotonically increasing edit sequence per
form. The session owns render-binding versions; the domain store owns opaque
resource revisions. A render version or edit sequence cannot replace a domain
revision. A submit captures its draft sequence and expected domain revision.
Duplicate submit admission is blocked while that form's save is pending.

Input and change update drafts only. Submit validates the complete declared
payload before a write. A stale domain revision preserves both the draft and
the previous committed value and displays an explicit refresh/merge choice.
Reloading remote data never silently replaces dirty fields. Only a completion
for the current session, form generation and captured edit sequence may clear
dirty status or replace its draft. Later typing survives older completions.
Canceled/disconnected saves remain visibly uncertain after dispatch until a
receipt or reload reconciles them. Failed callbacks invalidate bindings using
the existing session failure behavior; no automatic retry of a write follows.

Keyed controls retain DOM identity, focus, caret/selection and composition
state. Defer a patch to a dirty/composing control rather than overwrite the
user's edit. Input method composition emits one final input after composition
ends. Stop/expiry invalidates bindings, removes listeners and clears queued
events; late completions cannot mutate a new session. Browser evidence must
cover actual typing, checkboxes, keyboard submit, validation, focus/caret,
composition, stale completion and independent sessions before acceptance.

`Field`, `FormSchema`, `FormEvent`, `FormBinding`, `FieldError` and `FormResult`
are public values. Typed forms declare a key and one control per schema field;
inputs support text and checkbox, alongside textarea and single select. Disabled
fields must be optional. Numeric parsing stays in application callbacks.
The browser bounds its serialized event queue to 64 entries. A `sui:stop` event
on `#sui-app` or page teardown aborts the current fetch, removes application
listeners, stops animations/observation and invalidates queued work. Cancellation
after dispatch has an uncertain write outcome and requires explicit reload.

The [finite fixtures](form-events-v1.json) illustrate admission and refusal.
Production session routing, credentials/origin policy and asynchronous transport
remain owned by HOST-01 and API-AUTH-01. The existing loopback click wire format
and its tests remain supported.
