# Optional checked REST binding

Import `HTTPTransport`, `ResourceSpec`, `ResourceClient` and `ResourceBinding`
explicitly from `spatial_ui.rest`. The binding uses only the standard library;
installing an API server into a standalone UI environment is unnecessary.

The selected wire profile is the synthetic checked REST service in
`compose-application` source `dc49317ca006230e69b5784c9f3697c9e51a3e9e`, with
`expose-api` source `01d7c486ae9041532c8bfe01d8a2a00496523e68` and its pinned
Backpack Store/Enforce Type dependencies. These are qualified unreleased source
revisions, not a claim about published 0.1.0 features. The service owns validation,
access checks and committed revisions. The UI owns detached drafts and visibility.

Declare one exact origin, resource path, form schema, visible field names and
text identity field. This finite profile supports UTF-8 text and booleans; domain
keys match `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`. Other model shapes require explicit
conversion in an application-specific adapter. Hidden response fields and hidden
validation paths are excluded from UI values. Local visibility projection is not
server authorization.

`GET`, `POST`, `PATCH` and `DELETE` use the existing resource endpoints. Successful
get/create/update responses require one strong, canonical positive integer ETag;
checked mutations send that revision in `If-Match`. Domain revisions never use
render versions or draft sequences. List pagination is bounded to 1–1000 rows
and offsets 0–10000. A list cannot establish a checked editing revision; load the
individual record before update/delete.

`HTTPTransport` opens and closes one direct connection per request. HTTPS uses
the standard verified TLS context; plain HTTP accepts explicit loopback IPs.
Credentials are configured by the trusted host through explicit headers. The
transport does not follow redirects, use ambient proxies, forward cookies or
select an identity provider. JSON request/response bodies are at most 65536 bytes,
unique-key, finite and UTF-8; socket timeout defaults to five seconds. This is a
socket-operation bound, not a DNS or total request deadline.

Each `ResourceBinding` belongs to one editor. `edit` validates and detaches raw
form values. `begin` admits at most one request; `complete` accepts only its
current ticket. Later edits survive earlier completions. Conflict/validation
preserves drafts and the prior committed value. An explicit load refreshes the
revision and committed projection while retaining dirty fields for a deliberate
merge. Unknown write outcomes block further mutations until a successful reload.
Cancel invalidates a ticket without claiming rollback; close rejects all late
completions. Neither operation retries a write.

`perform` is synchronous and suitable for the current loopback callback profile.
Applications with their own asynchronous transport can own dispatch and call
`begin`/`complete`/`cancel` explicitly. The session lock and the single-threaded
preview server serialize callbacks; this profile makes no asynchronous or
production hosting claim. Native form/browser stop aborts the browser fetch;
an already dispatched service write may still commit.

## Reusable resource forms

`spatial_ui.resource_form.ResourceForm(client)` creates a fresh binding for each
component instance. Supply a fresh form from the `App` factory for each browser
session. A stateless client/transport can be shared. Labels and bounded select
choices are configured explicitly; raw text is escaped at the template boundary.

The Action selector makes each operation explicit: Save, Load / refresh, Discard
edits, Delete and Start new. Load retains dirty fields alongside a newly loaded
committed value/revision, enabling a deliberate merge followed by Save. Discard
edits replaces the draft with the loaded value; Start new clears the editor.
Loaded identities are read-only. Invalid fields retain drafts and receive focus;
backend errors without visible field paths use a fixed live status message.
Unknown writes must be reconciled before discarding or starting another record.

Run `uv run --group reference-tests python -m unittest discover -s tests -p
test_reference_form.py` for actual checked REST validation, two-editor conflicts,
merge/discard, deletion and SQLite restart checks. This optional development
profile does not add dependencies to installed UI distributions.
