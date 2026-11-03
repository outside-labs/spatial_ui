# Read-only tables

`TableView(TablePage(...))` renders a bounded detached page with semantic
caption, column/row headers, escaped scalar display text, explicit ready/empty,
loading/error states and visible keyboard focus. Import TableColumn, TableRow,
TablePage and TableView from spatial_ui. Each session owns a fresh component
and its `(row_key, column_id)` selection. Duplicate labels/values do not define
identity. Replacing a table/schema clears selection; revisions and request
generations remain separate. TableView itself is a display component; editing
is explicitly selected on TableBrowser.

`TablePage.from_source(snapshot)` consumes the public Headless Spreadsheets
TableSnapshot shape without importing that package. It captures display text
from native cell values, never executes formulas or interprets Markdown/HTML.
The underlying detached headless snapshot retains rich metadata; the UI page
is explicitly a display projection. The qualified source boundary is Headless
Spreadsheets `b0902fbf1c8b51ff0de167de697aee65bc83ca80`.

Page limits are 64 columns, 1,000 rows, 16,000 cells and 1,000,000 total display
code points. Cells above 16,384 code points are explicitly shortened by the
source adapter; manually supplied TableRow values must already meet the bound.
There is no implicit row fetch, write, HTML renderer, storage or API dependency.

Tab reaches the active cell. Arrow keys move and select by stable row/column
identity; Home/End move within the row and Ctrl+Home/End within the page.
Enter/Space select the focused cell using a semantic button. A scroll region
contains wide tables on phone/desktop; focus scrolls the selected cell into
view. Generated component CSS includes focus and reduced-motion rules.
Sort state uses aria-sort when supplied to the component; dataset sort/search
controls belong to the separate source binding.

## Dataset controls and source bindings

Create `TableBrowser(client, page_size=50)` once per session. Header buttons
cycle ascending/descending/unsorted order. Literal text search, visible-column
buttons, explicit column reordering and previous/next/refresh controls change
session view state only. Search uses up to eight declared searchable columns,
at most 256 code points and no normalization. Empty search clears the term.
Every relevant view/source change resets continuation. The component preserves
search focus/composition through the existing typed form runtime.

`HeadlessTableClient(source)` queries the complete bounded public TableSource;
`HTTPTableClient.open(transport, "/items")` fetches the explicit API schema and
queries the remote dataset, rather than filtering/sorting a loaded page. Mode
is visible in the component. HTTPTransport accepts an explicit origin, timeout,
application-owned headers, request bound and optional max_response_body through
4,194,304 bytes. Existing REST callers retain their original default bound.
The qualified query profile is expose_api source
`248ea04ff9ade8973e74cc2122e66f8933c4fb97` and its pinned Backpack source.
HTTP pages validate table/schema/request identities, requested projection,
portable scalar types, row keys/revisions and response bounds before display.

TableBinding owns one worker, at most one active query and one latest desired
descriptor. Superseded operations receive cancellation and queued futures are
canceled; dispatched blocking I/O may finish under its socket timeout. Only a
matching source/schema/generation can apply a result. A failed request leaves
the previous page labeled as unavailable until explicit refresh. Worker results
never mutate component state directly. The browser polls pending results every
150ms through current session events; it stops on teardown and during composition.

`close()` cancels admission, drains the worker and optionally closes an explicitly
owned client/source. Caller-owned clients/sources remain the caller's responsibility.
Session expiry, preview server close, native ASGI shutdown and authenticated
same-origin `POST /_spatial/close` run component cleanup. Table browser stop/pagehide
requests session closure with the existing private cookie and request token.
Composite application components must close their owned child components.
Read-only mode sends no writes. Persisted preferences and collection
subscriptions remain separate scope.

## Explicit scalar cell editing

Use `HTTPTableClient.open(transport, "/items", key_column="key")` with
`TableBrowser(client, editable=True)`. The application declares an immutable,
non-null public text key column for authoritative reloads using a bounded exact
key predicate. The key is the registered domain key; selecting that mapping is
the caller's responsibility. All columns default to read-only. API source
`cdff4682db5decc3d6bdb55f7ba5a6a2105f154a` implements the checked wire route.
This adds no required package dependency.

Select a cell, then choose **Edit selected cell**. One active cell draft per
session captures its durable row key, column ID and expected whole-row revision.
The editor remains available when sorting, searching, paging or changing
displayed columns removes that cell from the page. Save or Enter commits;
Escape or Discard cancels. Selecting another cell does not replace an existing
draft. The editor supports exact text, Boolean selection, portable integers,
finite floats and explicit nullable null. Invalid drafts remain editable.
Text editing is bounded to 4,096 UTF-8 bytes; larger values stay read-only in
this editor. No formula evaluation, rich-value conversion or bulk edit occurs.

Pending, saved, invalid, read-only, denied, conflict and uncertain-save states
are explicit. Successful completions validate the committed row and revision
before refreshing. Different cells in one row share a revision and can conflict.
**Reload current row** performs a fresh keyed read independent of the current
sort/search/page. Review its current value/revision, then explicitly choose
**Reapply draft to current revision** and Save, or discard. No write is
automatically retried or merged. A denied/missing row does not expose values.

Canceling a dispatched save, changing its query or losing its response may
follow commit. Preserve the draft and reload before retrying; a second Save is
disabled until explicit review/reapply. Source replacement refuses old
completions and leaves the old draft detached for discard. Closing a session
cancels admission and drains accepted work before releasing owned clients.
Browser tests exercise independent sessions, composition/focus, sort/search
with hidden edit targets, conflicts, invalid input, scalar controls, canceled
committed saves, lost responses and teardown in actual Chromium.
