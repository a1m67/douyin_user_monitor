# Phase 12 Roadmap

This roadmap tracks the nine reliability, data-model, concurrency, metrics,
daily-experience, and modularization phases. Each phase is implemented,
tested, and committed independently without pushing during the phase.

## Progress

- [x] Phase 1: ShowSeason metadata
- [x] Phase 2: Reliable Douyin empty-page handling
- [x] Phase 3: Central Douyin request protection
- [x] Phase 4: Normalized Show aliases
- [x] Phase 5: Batch parser context snapshots
- [x] Phase 6: SQLite and scheduler concurrency
- [x] Phase 7: Parser execution metrics
- [x] Phase 8: Episode watch progress
- [x] Phase 9: Modular short-drama web application

## Phase Records

### Phase 1

- Status: complete
- Main design: persisted season-level status, expected count, lifecycle timestamps, episode coverage, API editing, detail display, and latest-season card progress while preserving Show compatibility fields.
- Schema changes: v15 adds `show_seasons` with a unique `(show_id, season_number)` key; migration creates records for existing Episode seasons and copies legacy expected count/status only to Season 1.
- Tests: focused repository/migration/Web tests plus full suite (205 tests) passed.
- Commit: `f1e269e` (`feat: add show season metadata`)
- Production verification still needed: run migration against a copied production v14 database and verify multi-season display/editing.

### Phase 2

- Status: complete
- Main design: provider pages expose explicit success/end/transient states; malformed payloads and transient empty pages raise classified errors, and history advances only after a valid page.
- Schema changes: none.
- Tests: provider payload matrix, transient retry/success, three-attempt failure with unchanged cursor, focused pipeline/worker tests, and full suite (209 tests) passed.
- Commit: `8552ea3` (`fix: distinguish douyin empty pages from completion`)
- Production verification still needed: observe real risk-control and expired-cookie payloads to confirm crawler error messages retain the intended classification.

### Phase 3

- Status: complete
- Main design: `DouyinRequestGuard` wraps provider profile/page/detail requests with the shared circuit, global semaphore, and minimum interval. Scheduler/history/manual and Cookie probe share the same runtime guard; force only bypasses circuit and resets it after a successful probe.
- Schema changes: none; new settings `DOUYIN_MAX_CONCURRENT_REQUESTS` and `DOUYIN_MIN_REQUEST_INTERVAL_SECONDS` default to 3 and 0.5.
- Tests: shared circuit, force reset, concurrency, rate interval, scheduler/history compatibility, and full suite.
- Commit: `dadc3a7` (`refactor: centralize douyin request protection`)
- Production verification still needed: validate real Cookie probe behavior and tune interval/concurrency against VPS traffic limits.

### Phase 4

- Status: complete
- Main design: aliases are indexed by normalized value and queried in SQL; canonical titles remain on `shows`, parser candidates load aliases from the normalized table, and the legacy JSON field is maintained as an API-compatible mirror.
- Schema changes: v16 adds `show_aliases` with cascading Show ownership and a unique normalized alias. Migration expands JSON aliases in Show-id order, retains the earliest alias owner, skips canonical-title conflicts, logs warnings, and repairs the compatibility mirror.
- Tests: JSON migration conflict handling, SQL lookup, ignored-Show alias behavior, removal, canonical-title conflicts, merge/update compatibility, focused repository/parser/pipeline suite, and full suite.
- Commit: `727a20a` (`refactor: normalize show aliases`)
- Production verification still needed: inspect migration warnings against a copied production database and confirm all expected legacy aliases remain assigned to the intended Show.

### Phase 5

- Status: complete
- Main design: `ParsingContextSnapshot` is built once per incremental sync, once per history page, and once per reparse batch. It carries known Shows, recent account videos/matches, and account candidates; successful matches update the in-memory windows so later videos in the same batch can use newly created context.
- Schema changes: none.
- Tests: repository context query-count assertions, same-batch bare-episode context resolution, history/pipeline regression, reparse snapshot reuse, and full suite.
- Commit: `5365145` (`perf: reuse parser context per video batch`)
- Production verification still needed: compare SQL statement counts and parser latency on a copied production-sized database before/after deployment.

### Phase 6

- Status: complete
- Main design: lazy write-lock acquisition for Repository connections lets pure reads proceed without the process write lock while preserving short write transactions and SQLite WAL safeguards. Scheduler uses per-account asyncio locks with bounded cleanup; the due loop retains its global concurrency cap.
- Schema changes: none.
- Tests: read/write lock behavior, same-account manual serialization, cross-account manual/scheduler independence, scheduler backoff/circuit regression, SQLite performance regression, and full suite.
- Commit: `04162cb` (`refactor: improve sqlite and scheduler concurrency`)
- Production verification still needed: observe WAL write contention and scheduler overlap on a copied VPS workload, especially while history and latest scans target the same account.

### Phase 7

- Status: complete
- Main design: `parse_with_trace()` preserves the existing parser API while recording actual regex, context, and LLM execution. Pipeline OCR attempts/successes and both external-stage latencies are aggregated into `SyncResult`, persisted by scheduler scans, and exposed in 24-hour diagnostics.
- Schema changes: v17 adds regex/context/OCR counts, OCR successes, and LLM/OCR latency totals to `scan_runs`; the existing `llm_calls` column now receives real execution counts.
- Tests: direct regex/LLM trace behavior, LLM-called-then-review semantics, OCR attempt/success aggregation, scan-run persistence, diagnostics exposure, and full suite.
- Commit: `56cfea7` (`feat: add parser execution metrics`)
- Production verification still needed: confirm provider-specific LLM latency and OCR success rates on the VPS without exposing prompts, credentials, or model responses.

### Phase 8

- Status: complete
- Main design: per-show/per-season watch progress with explicit rollback support; following and detail views show watched and recorded-unwatched counts. `read_at` remains independent. Update API adds presentation-only burst groups without changing stored events.
- Schema changes: v18 adds `watch_progress` with unique `(show_id, season_number)`, non-negative progress, and cascading Show ownership.
- Tests: progress counting with missing episodes and Episode 0 exclusion, rollback, API read/write independence from update reads, burst grouping, doctor orphan check, and full suite.
- Commit: `0123250` (`feat: add episode watch progress`)
- Production verification still needed: validate progress semantics with real user workflows and confirm grouped update rendering on mobile browsers.

### Phase 9

- Status: complete
- Main design: extracted the dashboard CSS and application JavaScript into stable `/static/app.css`, `/static/api.js`, and `/static/app.js` resources; kept the server-rendered shell and all existing routes; moved update burst grouping into a presentation helper; replaced startup/shutdown event handlers with FastAPI lifespan; and deferred default runtime/crawler construction until lifespan startup while preserving explicit runtime injection and legacy lifecycle helpers.
- Schema changes: none; schema remains v18.
- Tests: static asset and PWA tests, page/API compatibility tests, lifecycle tests including deferred runtime construction, dashboard UI tests, and full suite (226 tests passed).
- Commit: `9eaf0d0` (`refactor: modularize short drama web app`)
- Production verification still needed: exercise the static-resource cache behavior and lifespan start/stop on the VPS, then verify all dashboard routes and service-worker updates after deployment.
