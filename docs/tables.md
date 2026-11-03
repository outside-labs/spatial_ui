# Read-only tables

`TableView(TablePage(...))` renders a bounded detached page with semantic
caption, column/row headers, escaped scalar display text, explicit ready/empty,
loading/error states and visible keyboard focus. Import TableColumn, TableRow,
TablePage and TableView from spatial_ui. Each session owns a fresh component
and its `(row_key, column_id)` selection. Duplicate labels/values do not define
identity. Replacing a table/schema clears selection; revisions and request
generations remain separate. No editor or write callback exists.

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
