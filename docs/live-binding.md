# Same-origin live resource profile v1

This optional browser consumer adopts Expose API source
`22e7afb7638440b52c08affa4c4c3c1da1bccfaf` and its
[single-record event profile](https://github.com/wispsy/expose_api/blob/22e7afb7638440b52c08affa4c4c3c1da1bccfaf/docs/events.md).
Import `LiveConfig` explicitly from `spatial_ui.live`. Base installation needs
no extra runtime package. Browser and installed acceptance are separate checks.

```python
from spatial_ui.live import LiveConfig
from spatial_ui.resource_form import ResourceForm

# client is the application's existing checked ResourceClient.
editor = ResourceForm(client, live=LiveConfig("/api/work-items"))
```

Supply a fresh editor from the App factory for each UI session. Creating an
editor or rendering an unloaded record opens no socket. Once loaded, the browser
runtime shows the remote value beside the checked REST baseline. An explicit
Load / refresh keeps dirty fields for merging; Discard edits uses the loaded
REST value. Deleting or starting a new record removes the subscription. Existing
forms without `live=` retain their current behavior.

The application routes a static API collection path on the UI's exact origin.
Select `LiveConfig("/api/work-items")` to subscribe to the loaded record under
that path. The host supplies TLS and WebSocket upgrades and preserves the
configured Host. Register that exact external Host and Origin with the API.
Cross-origin subscriptions, query parameters, collection watches and client
writes are outside this profile. The bundled HTTP preview runner cannot upgrade
WebSockets; use the application's selected ASGI host for a live composition.

The application owns a bounded cookie verifier shared by its REST access and
subscription routes. Its identity cookie should be HttpOnly, Secure on HTTPS,
SameSite=Strict and scoped to the application. The API verifies current opaque
principals, operation grants and record policies on every poll. The UI's random
session cookie establishes component isolation only. Credentials never enter
markup, URLs, subprotocols or browser messages. No login or identity provider is
selected here; the host provisions and revokes the application's credentials.

Each loaded editor owns one socket offering `expose-api.events.v1`, one current
snapshot and at most one timer. There are at most 16 live editors per page.
Incoming text is limited to 65536 UTF-8 bytes by default, with JSON depth 32,
unique object keys and the existing finite text/boolean visibility projection.
The API should select an equal or smaller message bound. Opaque cursors permit
duplicate suppression and reconnect; they are unordered and are never write
revisions. A resync clears the remote baseline until a fresh snapshot arrives.
Snapshots coalesce directly into a single remote view; they do not enqueue
component callbacks or change the checked REST baseline.

The editor shows current remote values separately and announces that an explicit
Load / refresh is needed before merging. Remote events never replace drafts,
focus, selections or composition and never advance an If-Match revision.
Render/event versions, draft sequences, domain revisions, cursors and connection
generations have separate owners. An already authorized in-flight snapshot is
permitted by the API; fresh REST access and checked writes remain authoritative.

Transient disconnects reconnect at a bounded exponential delay (at most 10
seconds) up to the selected retry limit. A connection must receive its initial
snapshot within the handshake timeout. Retry count resets only after a stable
connection has delivered a snapshot for 30 seconds; rapid open/close loops
exhaust the budget. Policy/protocol failures are terminal and clear the remote
view. The page retains local drafts and offers reload for recovery. Record
switch/removal and stop retire old handlers, sockets and timers; generation
checks refuse late callbacks. Browser stop requires a page reload to start a
fresh runtime, matching the existing native UI lifecycle. Server restart expires
process-local UI sessions. Do not retry an uncertain REST write automatically.

The native UI still owns one synchronous worker and private process-local
sessions. Callback timeouts, reverse proxy admission, TLS, send bounds and a
production ASGI host remain application responsibilities. No deployment,
package publication, new external service or Track B browser grant is implied.

## Browser acceptance

The `browser-tests` and `reference-tests` development groups qualify actual
Chromium WebSockets against the accepted API revision. The bounded test-only
wire host carries native HTTP and WebSocket scopes; it is not a production
hosting selection. Three cases cover two independent contexts at 390/1280px,
dirty remote updates, validation/conflict/merge, focus/selection/composition,
16-editor admission, missing/denied records, journal-expiry resync, stale
callbacks, revocation and stop/reload. The receipt asserts zero API sockets,
UI sessions/requests and a closed UI worker at shutdown.

```sh
uv sync --locked --group browser-tests --group reference-tests
uv run --frozen python -W error::ResourceWarning -m unittest discover -s tests -p browser_live.py -v
```

CI also builds and installs independent wheel/source artifacts outside the
checkout on Linux, macOS and Windows. Each base-only installation runs the
16 configuration/form/native checks and nine deterministic protocol/lifecycle
checks against its installed browser assets without skips. Retained receipts
record artifact/module hashes, Python/platform and check counts. These are
unreleased source qualifications; installed UI artifacts require no API, store
or browser automation package.
