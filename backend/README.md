# AI-Integration-Practice Backend

## Data Source (OneDrive-first)

This backend reads incident files from `DATA_PATH`.

- Default `DATA_PATH`: `~/OneDrive/FraudIncidents`
- Override with env var in `backend/.env`:
  - `DATA_PATH=C:/path/to/your/folder`

`file_dump/` in the project root is not used by backend ingestion/retrieval unless you explicitly point `DATA_PATH` there.

## Ingest Modes

Use `POST /api/ingest` with one of these modes:

- `mode=local` (default): rebuild index from local `DATA_PATH` only.
  - Use this when OneDrive desktop sync already mirrored files to your laptop.
- `mode=onedrive`: pull files from Microsoft Graph first, then rebuild index.
  - Use this when you need API-based pull from OneDrive cloud.

Examples:

- `POST /api/ingest?mode=local`
- `POST /api/ingest?mode=onedrive`

## Automatic OneDrive Sync

You can enable automatic Graph pull + reindex in the backend process.

Environment variables in `backend/.env`:

- `ONEDRIVE_AUTO_SYNC=true`
- `ONEDRIVE_AUTO_SYNC_INTERVAL_SECONDS=120`

Behavior:

- Backend starts a background worker at startup.
- Every interval, it pulls OneDrive files from Graph and rebuilds the index.
- Auto-sync uses non-interactive token refresh from local cache.

Important:

- Run one manual `POST /api/ingest?mode=onedrive` first to complete device login and seed token cache.
- After that, background cycles can run automatically without clicking cloud files locally.
- Check status at `GET /api/ingest/status` under `auto_sync`.
