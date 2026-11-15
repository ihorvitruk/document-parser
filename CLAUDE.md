# Document parser (test task)

Async PDF processing app: FastAPI API, Redis Streams queue, Python worker, React
frontend, all run through Docker Compose.

Before writing code, read these three files:

- `docs/decisions.md`: what was agreed and why
- `docs/plan.md`: the steps, in order; do them one at a time
- `docs/tech-spec.md`: exact names, Redis keys, API contract, module layout

## How to work in this repo

- Keep process light: implement directly and verify by actually running the stack.
  No over-engineering. Time budget is 2 hours.
- Follow the task's priority order: backend, PyPDF processing, Redis Streams queue,
  Gemini summary, GPT-5 Mini, frontend.
- Docs that go into the submission are written in English and contain only what a
  reviewer needs: clear decisions and how to run.
- Commit locally after each plan step. Never push.
- Never commit API keys; they live in `.env` (git-ignored).
- Keep `docs/REPORT.md` (time and token statistics) up to date at the end of the work.
