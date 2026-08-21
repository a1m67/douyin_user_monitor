# Phase 12 Roadmap

This roadmap tracks the nine reliability, data-model, concurrency, metrics,
daily-experience, and modularization phases. Each phase is implemented,
tested, and committed independently without pushing during the phase.

## Progress

- [x] Phase 1: ShowSeason metadata
- [x] Phase 2: Reliable Douyin empty-page handling
- [x] Phase 3: Central Douyin request protection
- [x] Phase 4: Normalized Show aliases
- [ ] Phase 5: Batch parser context snapshots
- [ ] Phase 6: SQLite and scheduler concurrency
- [ ] Phase 7: Parser execution metrics
- [ ] Phase 8: Episode watch progress
- [ ] Phase 9: Modular short-drama web application

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
- Commit: `refactor: normalize show aliases` (SHA recorded after commit)
- Production verification still needed: inspect migration warnings against a copied production database and confirm all expected legacy aliases remain assigned to the intended Show.

### Phase 5

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Production verification still needed: pending

### Phase 6

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Production verification still needed: pending

### Phase 7

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Production verification still needed: pending

### Phase 8

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Production verification still needed: pending

### Phase 9

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Production verification still needed: pending
