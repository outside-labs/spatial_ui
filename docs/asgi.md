# Native ASGI UI profile v1

The selected transport is a dependency-free ASGI 3 HTTP/lifespan adapter over
an explicit `App` factory. It preserves the existing version 1 form/event and
browser runtime protocols. HOST-03 implements this contract; HOST-04 owns
browser and installed-artifact acceptance. The bundled loopback HTTP server
retains its current development scope.

## Ownership and concurrency

`ASGIApp(app, origin=..., pending_limit=16, body_timeout=3,
max_response_size=1048576)` constructs configuration without opening a listener,
starting a thread or creating a component. The ASGI host owns sockets, TLS,
request/header admission, send deadlines and lifecycle delivery. The adapter
owns one synchronous worker and a separate session registry in one process and
event loop. Lifespan startup or an explicit async context opens the adapter;
requests before startup or during/after shutdown return 503.

All component construction, session lookup/expiry, rendering and callbacks run
serially on that worker. Callback code stays synchronous, bounded and responsible
for its own I/O timeouts. One slow callback delays every session; there is no
parallel component execution or hard interruption of Python callbacks.
`pending_limit` bounds accepted requests including body readers, queued work and
canceled callbacks that are still running. Overload returns 503 immediately.

Cancellation before worker dispatch cancels queued work when possible. After
dispatch, the callback can still commit effects; no response is applied to a
disconnected browser. Reload/reconcile before retrying. The browser's existing
generation/stop behavior rejects late completions. Shutdown stops admission,
drains accepted requests and callbacks, clears all sessions and joins the owned
worker. Cleanup completes before caller cancellation propagates. Restart uses
fresh session identifiers, event tokens and component state. Persistent domain
storage remains owned by the application.

Sessions expire after the App's configured idle TTL and respect its maximum
session count. A single process owns all sessions. Multiple workers, sticky
routing, distributed sessions, live updates and WebSockets are not qualified by
this profile. One adapter must not be used concurrently with its own previous
lifecycle or across event loops/processes.

## Origin, proxy and HTTP boundaries

Select exactly one canonical external origin, including an explicit port when
nondefault. HTTPS is required except for explicit HTTP loopback. Paths, user
information, query strings and fragments are refused in configuration. The
adapter verifies the request Host against that origin and requires an exact
Origin on event POSTs. Forwarded headers never select origin or identity. A
trusted TLS proxy must preserve the configured Host and route only this
application's exact root paths; mounting below a path prefix is unsupported.

The adapter serves only `/`, `/_spatial/runtime.js`, `/_spatial/theme.css` and
`/_spatial/event`. GET/HEAD serve explicit pages/assets; POST accepts only JSON
events. Unknown paths, query strings, duplicate sensitive headers and unsupported
methods are refused. ASGI request chunks are accumulated only up to the existing
form body bound, with a finite read deadline and optional validated
Content-Length. Transfer decoding belongs to the ASGI host. Responses are
bounded separately, with no partial markup on overflow.

Each adapter uses a random cookie name, opaque session identifiers, HttpOnly,
SameSite=Strict, Path=/ and the configured idle Max-Age. HTTPS origins add Secure.
Cookies establish isolated UI state, not a login identity. Event POSTs require
the session's opaque request token as well as exact origin and current event
bindings. Pages use nonce-based CSP, no-store, nosniff and no-referrer. Application
and callback errors return fixed messages without exception text.

## Hosting and recovery

Run one ASGI worker with lifespan enabled and bounded proxy/server headers,
connections and send timeouts. The hosting owner supplies and pins its ASGI host;
this library does not select or install a third-party server. Prepare a stopped
service checkpoint of compatible code, configuration and domain data before
changes. Drain the adapter before closing application storage. A restart expires
UI sessions and loses drafts held only on the server; browser refresh is required.
Restore compatible code/data to roll back rather than reusing old session tokens.

HOST-01 retains selection of a hosting destination, TLS/proxy operations,
reviewable deployment configuration and actual public activation. This profile
does not deploy the dashboard or select a new external service. The reference
UI remains public synthetic data; sensitive application identity belongs to an
explicit application/API access profile.

References: [ASGI HTTP](https://asgi.readthedocs.io/en/latest/specs/www.html),
[ASGI lifespan](https://asgi.readthedocs.io/en/latest/specs/lifespan.html).
