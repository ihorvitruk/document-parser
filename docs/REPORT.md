# Report: time and token usage

How long building this project took and how many tokens it used, split by phase and
by model. The project was built with AI coding assistants, as the task suggests:
the first phase with a stronger model for thinking, the second with a faster one for
writing code. Both ran at medium reasoning effort.

Total working time: **2 h 12 min** (1 h 00 min planning, 59 min implementation, 13 min finishing).

## Summary by phase

| Phase | Model | Working time | API calls | Output tokens | Input tokens, new (fresh + cache writes) | Input tokens read from cache |
|---|---|---|---|---|---|---|
| 1. Discussion, decisions, plan, specs | Fable 5.2, medium | 1 h 00 min | 40 | 45,301 | 92,227 | 4,571,943 |
| 2. Implementation, steps 1 to 8 | Sonnet 5.5, medium | 59 min | 85 | 161,736 | 291,989 | 21,321,633 |
| 3. Finishing (tests, samples, README, docs, packaging) | Sonnet 5.5, medium | 13 min | 47 | 83,777 | 110,734 | 19,072,763 |
| **Total** | | **2 h 12 min** | **172** | **290,814** | **494,950** | **44,966,339** |

- **Planning** was a conversation: reading the task, resolving its
  contradictions, deciding scope, architecture, error handling and tests, then writing
  `decisions.md`, `plan.md` and `tech-spec.md`. No product code was written.
- **Implementation** followed the plan one step at a time, each ending with a real run of the
  stack and a local commit.
- **Finishing** covers integration tests and coverage, sample PDFs, the README, the
  "how it works" document, packaging, and checking the archive from a clean folder.

## Implementation, step by step

Time is measured between consecutive commits, so it also contains pauses while
waiting for replies; token counts are only the model calls in that window.

| Step | What | Time between commits | API calls | Output tokens |
|---|---|---|---|---|
| Step 1 | Project skeleton with Redis, FastAPI health endpoint and Compose | 2 min | 4 | 6,441 |
| Step 2 | Document upload, get and list API with validation | 3 min | 5 | 16,635 |
| Step 3 | Parser interface, registry and PyPDF parser | 4 min | 4 | 9,579 |
| Step 4 | Redis Streams queue and worker with crash recovery | 6 min | 10 | 27,752 |
| Step 5 | Gemini summaries with bounded retries and partial results | 14 min | 14 | 24,954 |
| Step 6 | Gemini parser producing markdown per page | 6 min | 8 | 22,482 |
| Extra | Document every environment setting in .env.example | 2 min | 3 | 3,573 |
| Step 7 | GPT-5 Mini parser on the OpenAI Responses API | 6 min | 11 | 21,934 |
| Step 8 | React frontend with upload, parser choice and live document cards | 15 min | 26 | 28,386 |

## Plan usage (Claude Pro, from the app)

| Moment | 5-hour window used | Weekly, all models | Weekly, Fable |
|---|---|---|---|
| At the end of phase 1 (just before switching to Sonnet) | 22 % | n/a | n/a |
| At the time of writing this report | 58 % | 27 % | 8 % |

The 5-hour window covers the whole session, so the planning phase on the stronger model
used roughly 22 % of it and implementation plus finishing the other
36 percentage points.

## How the numbers were obtained

- Token counts are the usage records of every model call in this session's transcript:
  *output* tokens are what the model wrote (including its reasoning); *new input* is
  everything sent to the model for the first time (fresh tokens plus tokens written to the
  prompt cache); *read from cache* is context the model had already seen, billed at a
  fraction of the normal input price. Each call re-reads the whole conversation, which is
  why cache reads are much larger than everything else.
- The per-step times come from the git history and include the time spent waiting for
  tool runs (Docker builds, real Gemini calls) and for replies.
- Only the main session is counted; no sub-agents were used.
- The numbers were taken shortly before the final commit; the last few calls (writing this
  report and packaging) are not included.

## Observations

- Planning produced 16 % of the output tokens but took about as much clock
  time as all of implementation: the effort went into decisions, not into typing.
- Implementation moved quickly once the decisions were written down: eight steps, each ending
  in a running, tested system.
- The longest steps were the ones that touched real services or heavy builds: step 5 (real
  Gemini calls) and step 8 (frontend build and browser checks).
- The implementation session inherited the full planning conversation as context, so every
  call re-read it. Starting implementation in a fresh session that only loads the three
  documents would have cut the cache-read volume substantially.
