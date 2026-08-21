# Phase 14 Roadmap

Transactional reliability, lightweight operations, parser governance, bounded AI/media resources, and scalable library navigation are delivered in nine independent phases.

## Progress

- [x] Phase 1: Transactional notification outbox
- [x] Phase 2: Lightweight diagnostics and persistent service state
- [x] Phase 3: Per-account adaptive scheduling
- [x] Phase 4: Verified database restore
- [x] Phase 5: Parser processing versioning
- [x] Phase 6: Parser golden regression suite
- [x] Phase 7: AI request budgets and guards
- [ ] Phase 8: Bounded avatar and cover media cache
- [ ] Phase 9: Global search and show pagination

## Phase Records

### Phase 1

- Status: complete
- Main design: create a new Episode, its first EpisodeSource, update event, and channel-snapshotted notification delivery intents in one SQLite transaction; notifier HTTP remains exclusively in the background delivery worker.
- Schema changes: none expected; the existing durable delivery table is sufficient.
- Tests: transaction success/rollback, duplicate source idempotency, initial/history suppression, restart recovery, non-blocking slow notifier, per-channel isolation, focused suites, and full suite (255 tests passed).
- Commit: `fix: make notification outbox transactional`.
- Production verification still needed: observe worker wake latency and retry delivery against real Telegram/Feishu endpoints after deployment.

### Phase 2

- Status: complete
- Main design: diagnostics GET uses bounded aggregate queries and a `SELECT 1` latency probe; full doctor remains explicit, while doctor and maintenance timestamps survive process restarts through `app_meta`. Scheduler, history, notification, and maintenance workers expose a common health shape.
- Schema changes: none; `app_meta` stores service state.
- Tests: GET doctor isolation, explicit POST doctor execution, persisted doctor/backup/checkpoint/maintenance state, worker and queue payloads, focused worker/Web suites, and full suite.
- Commit: `fix: make diagnostics lightweight`.
- Production verification still needed: compare diagnostics latency on the production-sized database and confirm persisted times across a container restart.

### Phase 3

- Status: complete
- Main design: resolve adaptive scheduling per account through `inherit`, `fixed`, or `adaptive`, with optional account-level bounds and persisted effective intervals.
- Schema changes: v21 adds account scheduling mode, adaptive min/max overrides, and last effective interval.
- New settings: none; account overrides fall back to existing global adaptive scheduler settings.
- Tests: global/account mode matrix, account bounds, persisted effective interval, manual-run configuration preservation, API null clearing, UI controls, focused suites, and full suite (260 tests passed).
- Commit: `feat: add per-account adaptive scheduling`.
- Production verification still needed: confirm migrated accounts remain `inherit` and compare effective intervals with expected author cadence after several successful scans.

### Phase 4

- Status: complete
- Main design: pair new backups with SHA256 manifests, verify old or new backups offline, and require a validated source, stopped application, pre-restore backup, fsynced temporary copy, atomic replacement, and post-restore verification for disaster recovery.
- Schema changes: none.
- New settings: none; restore reuses the configured database path and backup retention count.
- Tests: manifest/hash verification, legacy manifestless backups, corruption, older/future schema, dry-run, confirmation, active writer refusal, successful restore, rollback after validation failure, CLI coverage, and maintenance worker regression.
- Commit: `feat: add verified database restore`.
- Production verification still needed: perform a restore drill on a disposable VPS copy with Docker stopped and verify restart/migration behavior; do not use restore during a normal upgrade.

### Phase 5

- Status: completed
- Main design: persist a manually versioned parser identity, stable input hash, and build SHA for every parser outcome; expose outdated processing without automatically rewriting matched Episode records.
- Schema changes: v22 adds nullable parser version, parser input hash, and processed build SHA to videos; null remains the legacy marker.
- New settings: none; parser rule changes require an explicit `PARSER_VERSION` bump.
- Tests: parser identity for matched/review/ignored results, stable input hash refresh behavior, outdated API and reparse filtering, v21 migration, quality reporting, focused repository/pipeline/web suites, and the complete 268-test suite.
- Production verification still needed: confirm the quality center's legacy count and run a bounded outdated reparse on a backup-verified production database.

### Phase 6

- Status: completed
- Main design: committed exact-match parser corpus evaluated through a network-free `EpisodeParser` with human and JSON CLI reports.
- Schema changes: none.
- New settings: none.
- Tests: 24 committed golden cases at 100% exact match, failure-report CLI coverage, focused parser suites, and the complete 270-test suite.
- Production verification still needed: run `parser-eval` from the deployed image and require future parser changes to update the corpus intentionally.

### Phase 7

- Status: complete
- Main design: independent LLM/OCR guards reserve durable UTC-day usage before each external call, enforce separate process-wide concurrency, open a per-provider cooldown circuit after consecutive failures, and degrade budget/circuit denials into review reasons without failing account scans.
- Schema changes: v23 adds `ai_usage_daily` with one durable aggregate row per UTC date/provider.
- New settings: `LLM_MAX_CONCURRENT_REQUESTS`, `OCR_MAX_CONCURRENT_REQUESTS`, `LLM_DAILY_CALL_LIMIT`, `OCR_DAILY_CALL_LIMIT`, `AI_FAILURE_THRESHOLD`, and `AI_COOLDOWN_MINUTES`.
- Tests: unlimited and exhausted budgets, UTC reset, independent concurrency, LLM/OCR circuits, scan degradation, diagnostics redaction, quality categorization, migration, focused suites, and the complete 278-test suite.
- Commit: `feat: add ai request budgets and guards`.
- Production verification still needed: verify configured provider limits, observe daily counters/cooldown state, and confirm no real credentials or raw AI payloads appear in diagnostics.

### Phase 8

- Status: pending

### Phase 9

- Status: pending
