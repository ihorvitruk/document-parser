# Technical spec

Concrete names and contracts for the implementation. Read together with
`decisions.md` (what and why) and `plan.md` (order of work). Where this file names
an SDK call, check the SDK's current documentation before relying on it.

## Layout

```
backend/
  Dockerfile              python:3.13-slim, one image for API and worker
  pyproject.toml          dependencies managed with uv
  app/
    config.py             settings from environment (pydantic-settings)
    models.py             ParserName, DocumentStatus, Page, Document
    errors.py             TransientError, PermanentError
    storage.py            document repository on Redis
    queue.py              stream enqueue, consume, reclaim, ack
    api/main.py           FastAPI app, lifespan (Redis client, consumer group)
    api/routes.py         endpoints
    parsers/base.py       Parser protocol
    parsers/registry.py   name -> parser, availability check
    parsers/pypdf_parser.py
    parsers/gemini_parser.py
    parsers/openai_parser.py
    llm/retry.py          bounded retry with backoff
    llm/summarizer.py     Gemini summary
    worker/main.py        entry point: loop, signals
    worker/processor.py   process one document
  tests/
frontend/
  Dockerfile              node build stage, then nginx
  nginx.conf              serves the build, proxies /api to api:8000
  src/  api.ts, types.ts, App.tsx, components/UploadForm.tsx, components/DocumentCard.tsx
samples/
docker-compose.yml
.env.example
```

## Environment variables

| Name | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | empty | required for summaries and the Gemini parser |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | used for parsing and summary |
| `OPENAI_API_KEY` | empty | optional; enables the GPT-5 Mini parser |
| `OPENAI_MODEL` | `gpt-5-mini` | |
| `REDIS_URL` | `redis://redis:6379/0` | |
| `UPLOAD_DIR` | `/data/uploads` | volume shared by API and worker |
| `MAX_UPLOAD_MB` | `20` | per file |
| `DOCUMENT_TTL_HOURS` | `24` | |
| `LLM_MAX_ATTEMPTS` | `3` | per LLM call |
| `PAGES_PER_CHUNK` | `5` | pages per LLM parsing request |
| `CLAIM_IDLE_SECONDS` | `120` | idle time before a pending entry is reclaimed |
| `MAX_DELIVERIES` | `3` | deliveries before a document is marked failed |

The app must start without `GEMINI_API_KEY`: PyPDF still parses, the Gemini parser
is reported unavailable and documents finish with `summary_error`.

## Data model

`ParserName`: `pypdf`, `gemini`, `gpt5mini`.
`DocumentStatus`: `queued`, `processing`, `done`, `failed`.

Document fields: `id` (uuid4 hex), `filename`, `parser`, `status`, `error`,
`summary`, `summary_error`, `page_count`, `pages` (`[{number, content}]`, numbers
start at 1), `created_at`, `updated_at` (ISO 8601 UTC).

## Redis

| Key | Type | Content |
|---|---|---|
| `doc:{id}` | hash | one field per document field; `pages` is a JSON string. TTL = `DOCUMENT_TTL_HOURS` |
| `docs:index` | sorted set | member = id, score = created_at as epoch seconds. Entries older than the TTL are trimmed on each list call |
| `docs:stream` | stream | entries with a single field `doc_id` |

Consumer group `docs:workers`, created with `XGROUP CREATE ... MKSTREAM` by both the
API and the worker at startup (ignore `BUSYGROUP`). Consumer name = container
hostname.

Redis runs with `--appendonly yes` and a named volume.

## API

All routes are under `/api`. Handlers are `async` and use the async Redis client.

| Method and path | Behaviour |
|---|---|
| `POST /api/documents` | multipart: `files` (one or more), `parser`. Returns `202` with `{"documents": [{id, filename, parser, status}]}` |
| `GET /api/documents` | newest first, without `pages` |
| `GET /api/documents/{id}` | full document; `404` when unknown or expired |
| `GET /api/parsers` | `[{name, label, available, reason}]` |
| `GET /api/health` | pings Redis |

Upload rules:

- Validation is all-or-nothing for the request; nothing is queued if any file fails.
- `400` when no files, when a file does not start with `%PDF-`, or when the chosen
  parser is unavailable (missing key). `413` when a file exceeds the limit.
  `422` for an unknown parser value. Error bodies name the offending file.
- Files are streamed to `{UPLOAD_DIR}/{id}.pdf` in chunks; the size limit is enforced
  while streaming, not after reading the whole body.
- Order of writes: save file, write `doc:{id}` as `queued`, add to `docs:index`,
  `XADD docs:stream`.

## Worker

One job at a time per worker process; Compose runs 2 replicas.

Loop:

1. `XAUTOCLAIM` entries idle longer than `CLAIM_IDLE_SECONDS`. For each, read its
   delivery count from `XPENDING`; above `MAX_DELIVERIES` mark the document `failed`
   ("processing crashed repeatedly"), then `XACK`.
2. Otherwise `XREADGROUP ... COUNT 1 BLOCK 5000 >`.
3. Process the document, then `XACK` and `XDEL` the entry.

Processing one document:

1. Load `doc:{id}`. Missing, `done` or `failed` -> acknowledge and skip (idempotent).
2. Set `processing`.
3. Parse with the chosen parser into pages. `PermanentError`, or `TransientError`
   after retries are exhausted -> `failed` with a readable `error`.
4. Store `pages` and `page_count`.
5. Summarize with Gemini. Any failure after retries -> keep pages, set
   `summary_error`; status is still `done`. No extractable text at all -> skip the
   call and set `summary_error`.
6. Set `done`. Delete the uploaded file in every terminal state.

Unexpected exceptions are caught per job, logged and recorded as `failed`; the loop
never dies because of one document. `SIGTERM` lets the current job finish.

## Parsers

```python
class Parser(Protocol):
    name: ParserName
    async def parse(self, path: Path) -> list[Page]: ...
```

Availability (API key present or not) is decided in `parsers/registry.py` from the
settings, not by the parser classes. `get_parser(name)` raises `PermanentError` for a
parser that is not built yet.

- **PyPDF:** runs in `asyncio.to_thread`. Encrypted or unreadable files raise
  `PermanentError`. Empty pages are kept with empty content.
- **Gemini:** `google-genai` SDK, async client. The PDF is split with pypdf into
  chunks of `PAGES_PER_CHUNK` pages; each chunk is sent as a PDF part with a prompt
  asking for faithful markdown per page, with a JSON response schema
  `[{page_number, markdown}]`. Page numbers are re-based to the whole document.
- **GPT-5 Mini:** `openai` SDK, same chunking and output contract, PDF passed as a
  file input.

## LLM retry

`LLM_MAX_ATTEMPTS` attempts with backoff of roughly 2 s then 8 s plus jitter, and a
per-call timeout, so the total stays near 30 seconds.

- Transient: HTTP 429, 5xx, timeouts, connection errors.
- Permanent: 400, 401, 403, invalid or unparseable response after retries.

The summarizer and LLM parsers take the client as a constructor argument so tests
can pass a fake.

## Summary

One request to `GEMINI_MODEL` with the pages joined in order. Input is truncated to
a fixed character budget with a note in the prompt when truncation happened. The
prompt asks for a concise summary of 3 to 6 sentences in the document's language.

## Compose

| Service | Image / build | Notes |
|---|---|---|
| `redis` | `redis:7-alpine` | appendonly, volume, healthcheck |
| `api` | `./backend` | uvicorn on 8000, published as `8000:8000`, depends on healthy Redis |
| `worker` | `./backend` | `python -m app.worker.main`, `deploy.replicas: 2` |
| `frontend` | `./frontend` | nginx, published as `3000:80`, `client_max_body_size` above the upload limit |

`api` and `worker` share the `uploads` volume and read the same `.env`.

## Frontend

- On load and while any document is `queued` or `processing`, poll
  `GET /api/documents` every 1.5 s; fetch the full document when a card is expanded
  or becomes `done`.
- Parser options come from `GET /api/parsers`; unavailable ones are disabled and
  show their `reason`.
- Page content is rendered as markdown (`react-markdown` with GFM); PyPDF text is
  shown preformatted.
- Upload errors from the API are shown next to the form.

## Tests

- `pytest`, `pytest-asyncio`, `httpx` test client, `pytest-cov`.
- Unit tests use `fakeredis` where it supports the needed stream commands; small
  PDF fixtures are generated in `conftest.py`.
- Integration test is marked `integration` and runs against the Redis from Compose
  with a fake LLM client.

## Conventions

- One commit per plan step, imperative subject line.
- Structured log lines include the document id.
- No secrets in the repository; `.env` is git-ignored and excluded from the zip.
