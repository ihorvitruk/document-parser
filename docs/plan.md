# Implementation plan

Order follows the task's priority list. Every step ends with a real run of the
Compose stack and a commit. Decisions referenced here are in `decisions.md`.

## 1. Skeleton

- Repo layout, `.gitignore`, `.env.example`, `docker-compose.yml` with Redis and API.
- FastAPI app with a health endpoint, settings read from the environment.
- **Check:** `docker compose up --build`, health endpoint answers.

## 2. Concurrent API

- `POST /api/documents` (one or more PDFs plus a parser value), `GET /api/documents`,
  `GET /api/documents/{id}`.
- Upload validation (PDF signature, size limit), file saved to the shared volume,
  document stored in Redis as `queued`.
- Async Redis client throughout, so requests do not block each other.
- Unit tests for validation and the endpoints.
- **Check:** parallel uploads with curl return ids immediately.

## 3. PyPDF parser

- Parser interface and registry (`pypdf`, `gemini`, `gpt5mini`), PyPDF implemented:
  one text block per page.
- Corrupt and encrypted PDFs become permanent errors.
- Unit tests with small sample PDFs.

## 4. Redis Streams queue and worker

- API adds the document id to a stream; worker reads through a consumer group,
  parses, stores pages, acknowledges.
- Reclaim of entries left by a crashed worker, delivery limit, `failed` status.
- Two worker replicas in Compose.
- Unit tests for the success, retry and failure paths.
- **Check:** upload several PDFs, watch both workers process them; kill a worker
  mid-job and see the job finish on the other.

## 5. Gemini summary  (needs the Gemini key)

- Summary with `gemini-3.1-flash-lite`, bounded retries with backoff.
- Partial result when the summary fails: pages kept, `summary_error` set.
- Unit tests with a fake LLM client.
- **Check:** real summary for a sample PDF.

## 6. Gemini parser

- PDF sent in page chunks, markdown returned per page as structured JSON.
- **Check:** a PDF with tables or headings comes back as markdown.

## 7. GPT-5 Mini parser

- Same interface, enabled only when `OPENAI_API_KEY` is set.
- `GET /api/parsers` reports which parsers are available and why not.
- Verified with a real key only if one is available; otherwise covered by unit
  tests with a fake client and stated honestly in the README.

## 8. Frontend

- Vite + React + TypeScript, nginx serving the build and proxying `/api`.
- Upload area, parser selector (GPT-5 Mini disabled without a key), document cards
  with status, summary and collapsible pages, polling until done.
- **Check:** full flow in the browser.

## 9. Finish

- Integration tests against a real Redis (fake LLM): upload -> stream -> two workers
  -> result, crash recovery, partial result. Coverage report.
  Run with `docker compose --profile test run --rm tests`.
- Sample PDFs in `samples/`: a text report with a table, a short article, and a
  scanned (image-only) document that shows the difference between the parsers.
- README: one run command, curl examples, tests, troubleshooting, limitations.
- `docs/how-it-works.html`: a visual explanation of the system (diagrams, request
  flow, modules, Redis keys, error handling) for readers new to the project.
- `docs/REPORT.md`: time and token statistics of building this project, by phase
  (planning, implementation, finishing) and by model. Token and usage numbers are read
  from the session tooling; per-step timings come from the git history.
- `scripts/package.sh`: builds the zip without `.venv`, `node_modules`, `.git` and
  `.env`. Verified by unpacking it into a clean folder and running it.

## Optional

- "Auto" parser option.
- Short screen recording.
