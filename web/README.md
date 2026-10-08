# Vectra knowledge workspace

The React interface for the existing PostgreSQL + pgvector project. Category
hubs and document nodes form a draggable graph. Dragging a hub moves its
documents with eased motion; hovering a document opens a foreground preview.
Faint node-to-node links are computed from actual vector distances. The right
detail panel shows a selected document, exploration path, and its four nearest
neighbors measured across the full corpus. Search uses the project's embedding
model and logs queries in `query_log`. The health cards read real
`health_snapshots` and `maintenance_events` rows.

This is an initial, local read interface. It does not yet provide login,
permissions, document CRUD, or a production deployment. Keep both development
servers bound to `127.0.0.1`.

## Run on Kush's Windows setup

From the repository root, start the database, API, and UI with one command:

```powershell
.\web\start-local.ps1
```

The launcher verifies each service and opens <http://127.0.0.1:5173/> in the
default browser. If the automatic browser opening fails, paste that address
directly into the address bar on the same Windows computer.

To run each service manually, start PostgreSQL from the repository root:

```powershell
.\demo\db.ps1 up
```

Terminal 1, start the Django API:

```powershell
$env:MAMBA_ROOT_PREFIX = 'C:\tools\mmroot'
Set-Location .\web\backend
& 'C:\tools\micromamba.exe' run -n pgv python -m pip install -r requirements.txt
& 'C:\tools\micromamba.exe' run -n pgv python manage.py runserver 127.0.0.1:8000
```

Terminal 2, start React:

```powershell
Set-Location .\web\frontend
npm install
npm run dev
```

Open <http://127.0.0.1:5173/>. Vite proxies `/api` to Django on port 8000.

## What to try

1. Drag a category hub and watch its document dots follow; drag empty space to pan and scroll to zoom.
2. Choose a category in the left rail; the graph rearranges around that hub.
3. Hover a document dot for a readable preview. Click it for details, then follow a nearest neighbor in the right panel.
4. Search for `smartphone battery update`. Select a result to read it.
5. Scroll to the live recall chart and repair timeline.

## API

| Route | Data |
|---|---|
| `GET /api/graph/?limit=90&category=technology` | Balanced document sample, category counts, nearest document links |
| `GET /api/search/?q=smartphone+battery+update&limit=20` | Logged pgvector cosine search, optionally filtered by `category` |
| `GET /api/documents/161/neighbors/` | Four nearest documents from the full corpus using pgvector cosine distance |
| `GET /api/health/` | Latest 12 health snapshots and 10 repair events |

The graph is a visual sample (90 of 480 documents by default). Category counts
are totals; selecting one category shows up to 90 of its own documents. The API
uses unmanaged Django ORM mappings for existing tables so no project data is
reset or migrated by starting the UI. Vector search is parameterized SQL because
it uses the pgvector cosine operator.

For another machine, install `web/backend/requirements.txt`, configure
`SELFHEAL_DSN` in the root `.env`, and build the frontend with `npm run build`.
