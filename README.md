# Document Parser

Upload one or more PDFs, choose a parser, and get the content of every page plus one
summary per file. Processing is asynchronous: the API accepts the upload immediately
and a pool of workers does the parsing through a Redis Streams queue.

- **Parsers:** PyPDF (plain text), Google Gemini (markdown, reads scans) and OpenAI
  GPT-5 Mini (markdown, reads scans; needs an OpenAI key).
- **Summary:** always produced by `gemini-3.1-flash-lite`.
- **Stack:** Python 3.13 and FastAPI, Redis 7 (Streams), React with TypeScript, Docker Compose.

For a visual walkthrough of how the parts fit together (diagrams, request flow, modules),
open [docs/how-it-works.html](docs/how-it-works.html) in a browser.

## Quick start

Requirements: Docker with Compose v2, and a free Gemini API key from
[Google AI Studio](https://aistudio.google.com/apikey).

```bash
cp .env.example .env        # then put your key in GEMINI_API_KEY=
docker compose up --build
```

| What | Where |
|---|---|
| Website | http://localhost:3000 |
| API (and interactive docs) | http://localhost:8000 (docs at `/docs`) |

Without a Gemini key the app still starts: PyPDF parsing works, the Gemini parser is
disabled, and documents finish with "summary unavailable: GEMINI_API_KEY is not set".
Four containers run: `redis`, `api`, `worker` (two replicas) and `frontend`.

## Try it

The [samples](samples) folder has three PDFs made for this project:

| File | What it shows |
|---|---|
| `quarterly-report.pdf` | Headings, tables and lists. Gemini returns them as markdown, PyPDF as plain text. |
| `redis-streams-article.pdf` | Plain prose over two pages; works with any parser. |
| `scanned-memo.pdf` | Pages that are pictures with no text layer. PyPDF finds no text (and says so); Gemini reads it. |

### In the browser

Open http://localhost:3000, drop one or more PDFs, pick a parser, press Upload. Each
file becomes a card that goes Queued, Processing, Done. A finished card shows the
summary on top and one collapsible block per page.

### With curl

```bash
# Upload (one or more files, one parser for the request): returns ids immediately
curl -s -F parser=pypdf \
     -F "files=@samples/quarterly-report.pdf" \
     -F "files=@samples/redis-streams-article.pdf" \
     localhost:8000/api/documents

# Which parsers are available, and why not
curl -s localhost:8000/api/parsers

# All documents, newest first (no page contents)
curl -s localhost:8000/api/documents

# One document with its pages and summary
curl -s localhost:8000/api/documents/<id>
```

`parser` is `pypdf`, `gemini` or `gpt5mini`. A document has a `status` of `queued`,
`processing`, `done` or `failed`; poll `GET /api/documents/<id>` until it is `done` or
`failed`.

```json
{
  "id": "264787179ec94f328437fe9c26b913c5",
  "filename": "redis-streams-article.pdf",
  "parser": "gemini",
  "status": "done",
  "error": null,
  "summary": "A Redis Stream is an append-only log ...",
  "summary_error": null,
  "page_count": 2,
  "pages": [{"number": 1, "content": "# Redis Streams in Short ..."}]
}
```

## How it works

```
 browser ──► nginx ──► api (FastAPI) ──► Redis ◄── worker ×2 ──► Gemini / OpenAI
 :3000        /api       validates,      stream      parse + summarize
                         stores, queues  + hashes
```

1. The API checks each upload (it must really be a PDF, within the size limit), saves it
   to a shared volume, stores the document in Redis as `queued` and adds its id to the
   stream, all in one transaction. It answers `202` at once.
2. A worker in the consumer group takes the next id, parses the file with the chosen
   parser and stores the pages, then asks Gemini for the summary and marks the document
   `done`. Each entry goes to exactly one worker.
3. The website polls the API and shows progress.

More detail, including the Redis keys, module map and error handling, is in
[docs/how-it-works.html](docs/how-it-works.html) and [docs/tech-spec.md](docs/tech-spec.md).

## Reliability

- **Worker crash:** an entry that was delivered but never acknowledged is taken over by
  another worker after `CLAIM_IDLE_SECONDS`. A running job keeps telling Redis it is
  alive, so a long job is not taken over by mistake.
- **Poison documents:** after `MAX_DELIVERIES` deliveries the document is marked failed
  with a readable reason instead of being retried forever.
- **LLM errors:** temporary ones (rate limit, 5xx, network, timeout) are retried up to
  `LLM_MAX_ATTEMPTS` times with short pauses; permanent ones (bad key, rejected
  request, no credit) fail at once, without retries.
- **Partial result:** if the pages were parsed but the summary failed, the document is
  still `done`: pages are shown and `summary_error` explains why.
- **Long PDFs with an LLM parser** are sent in chunks of `PAGES_PER_CHUNK` pages, so
  responses stay within model limits and the free tier's request quota is not burnt.
- **Cleanup:** uploaded files are deleted once a document finishes; documents expire
  from Redis after `DOCUMENT_TTL_HOURS`.

## Configuration

Everything is set through environment variables in `.env`; every variable, its default
and its meaning are listed in [.env.example](.env.example). After changing `.env`,
apply it with `docker compose up -d`.

## Tests

```bash
docker compose --profile test run --rm tests
```

Runs unit tests and integration tests (real Redis, two workers, a fake LLM) and prints
a coverage report: 124 tests, about 95% coverage. The integration tests cover upload to
result, work shared between workers, takeover of a crashed worker's job, giving up on a
poison document, and the partial result.

## Project layout

```
backend/app/   api/ (routes), parsers/, llm/ (Gemini and OpenAI clients, retries),
               worker/ (loop and processing), queue.py, storage.py, models.py
backend/tests/ unit and integration tests
frontend/      React app, nginx config
samples/       example PDFs
docs/          how-it-works, decisions, plan, tech spec, report
```

## Limitations and honest notes

- **GPT-5 Mini** was written against OpenAI's documentation and covered by tests with a
  fake client, but not run against the real service: it needs a paid API key. Gemini
  and PyPDF were tested for real.
- **Free-tier Gemini** is slow and rate limited; a long document can take a minute or
  more, and heavy use can hit the quota (the app retries, then reports it).
- **Upload size limit** is checked while the file is copied to storage; the web
  framework has already buffered the request by then, so it protects storage, not
  memory. A real deployment would also limit the request size at the proxy.
- **No authentication and no database.** Results live in Redis only and expire; this
  keeps the project focused on the queue. A production system would keep results in a
  database and use Redis as the queue only.
- **Scans with PyPDF:** PyPDF cannot read text from images; such documents finish with
  empty pages and the summary says there was no text. Use Gemini or GPT-5 Mini.

## Troubleshooting

- **`npm ci` fails during the build with a network or DNS error:** Docker cannot resolve
  `registry.npmjs.org`. Give Docker working DNS servers (for example `1.1.1.1` and
  `8.8.8.8` in its daemon settings) and build again.
- **Port 3000 or 8000 is already in use:** stop the other program or change the ports
  in `docker-compose.yml`.
- **`docker compose` is not a command:** install Docker Compose v2.
- **The Gemini option is greyed out:** `GEMINI_API_KEY` is empty in `.env`; set it and
  run `docker compose up -d`.
