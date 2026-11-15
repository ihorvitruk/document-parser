# Decisions log

Working notes from the pre-implementation discussion. Will later be split into
project docs (architecture, tech stack, README).

## Decided

- **Delivery:** zip with source code (no `.venv`, no `node_modules`). No cloud hosting.
- **Run:** everything on localhost via `docker compose up --build`. The reviewer adds
  a Gemini key to `.env` and runs one command.
- **Containers (4):** Redis, API (FastAPI), worker (stream consumer), frontend.
- **Network:** at runtime the only external service is the Gemini API.
- **Gemini API key:** provided by Ihor (free tier, Google AI Studio). Never committed.
- **Python in containers:** 3.13 (task requires 3.12+; local Python version is irrelevant).
- **API style:** REST.
- **Frontend:** we aim for a real website, kept simple. Backend comes first.
- **Priorities** (from the task, top to bottom) drive the order of subtasks:
  1. Backend able to serve concurrent API requests
  2. Upload and processing with PyPDF
  3. Async Redis queue with Streams
  4. Summarization via Gemini
  5. Document processing via GPT-5 Mini (OCR)
  6. Frontend
- **README:** one run command plus ready-to-copy curl examples.
- **Samples:** a couple of public PDFs in `samples/`.
- **Process:** minimal ceremony, no over-engineering; time budget is 2 hours.
  Rules are in `CLAUDE.md`.
- **Layout:** `backend/`, `frontend/`, `docker-compose.yml`, `.env.example`,
  `README.md`, `samples/`, `docs/`. API and worker share the `backend/` code and one
  image, started with different commands.
- **Submission docs:** English, only what a reviewer needs.
- **Result shape per document:** `id`, `filename`, `parser`, `status`
  (queued / processing / done / failed), `error`, `summary`, and
  `pages: [{number, content}]`. The API returns the same shape, so curl shows what
  the website shows.
- **API:** upload files (returns ids immediately, no parsing in the request), get one
  document, list documents.
- **UI:** one page with an upload area (multiple PDFs), a parser selector
  (PyPDF / Gemini) and a list of document cards. Each card shows status, then the
  summary on top and the page contents below it, one collapsible block per page.
- **Error handling:**
  1. Upload validation: non-PDF or oversized files are rejected by the API
     immediately and never queued.
  2. Transient LLM errors (rate limit, 5xx, network): up to 3 attempts with short
     backoff, about 30 seconds in total, then status `failed` with a readable reason.
  3. Permanent errors (corrupt or encrypted PDF, invalid key): `failed` at once,
     no retries.
  4. Worker crash mid-job: the unacknowledged stream entry is reclaimed by another
     worker after an idle timeout; after 3 deliveries the document is marked `failed`.
  5. No deferred processing hours or days later.
- **Partial result:** if parsing succeeded but the summary failed after all retries,
  the document is still `done`: pages are shown, `summary` is empty and
  `summary_error` explains why.
- **Result updates on the frontend:** API polling every 1-2 seconds until done.

- **Parsers:** three values in one enum: `pypdf`, `gemini`, `gpt5mini`. Parsers are
  pluggable modules behind one interface. PyPDF and Gemini are implemented first.
  GPT-5 Mini is built at its place in the priority order (after summarization,
  before the frontend) and is a visible but disabled option in the UI, with the hint
  "OpenAI key required", whenever `OPENAI_API_KEY` is not set. The app works fully
  with a Gemini key alone.
- **Summary:** always `gemini-3.1-flash-lite`, as the task states. No summarizer
  field in the API and no selector in the UI.

## Technical defaults (Claude's calls, open to change)

- **File hand-off:** the API saves the upload to a Docker volume shared with the
  worker; the stream entry carries only the document id. The file is deleted after
  processing.
- **Upload limits:** 20 MB per file by default, configurable through an environment
  variable; PDF is verified by its `%PDF-` signature, not
  just the file extension.
- **Gemini parsing:** the PDF is sent in chunks of pages and Gemini returns markdown
  per page as structured JSON, so long documents do not hit the output limit and
  requests stay few on the free tier.
- **Summary input:** the parsed text of the whole document, in one request.
- **Workers:** asyncio consumer in a Redis consumer group; Compose starts 2 worker
  replicas to show concurrent processing and crash recovery.
- **Redis:** append-only persistence on; documents expire after 24 hours,
  configurable through an environment variable.
- **Frontend:** Vite + React + TypeScript, served by nginx, which also proxies
  `/api` to the backend so there is no CORS setup.
- **Tests (agreed):** pytest unit tests for everything that can sensibly be tested
  in isolation: upload validation, parsers, the worker's retry and failure paths.
  They are written together with each step. One integration test (upload -> stream
  -> worker -> result against a real Redis with a fake LLM) comes later, at lower
  priority. A coverage report is produced; the aim is high coverage of the core
  logic, not a 100% figure. No automated browser tests.

## Optional, if time remains

- "Auto" parser option: try PyPDF first, fall back to Gemini when the extracted text
  is empty or near-empty (scanned PDF).

- Short screen recording of the demo.

## Environment (checked 2026-11-15)

- Docker 29.9.3 via Colima 0.10.3 (2 CPU, 2 GB RAM; raise memory if builds fail)
- Docker Compose 5.7.1
- Git 2.55.0, repo initialised on `main`
