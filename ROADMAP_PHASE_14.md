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
- [x] Phase 8: Bounded avatar and cover media cache
- [x] Phase 9: Global search and show pagination

## Phase Records

### Phase 1

- Status: complete
- Main design: create a new Episode, its first EpisodeSource, update event, and channel-snapshotted notification delivery intents in one SQLite transaction; notifier HTTP remains exclusively in the background delivery worker.
- Schema changes: none expected; the existing durable delivery table is sufficient.
- Tests: transaction success/rollback, duplicate source idempotency, initial/history suppression, restart recovery, non-blocking slow notifier, per-channel isolation, focused suites, and full suite (255 tests passed).
- Commit: `4bc2190 fix: make notification outbox transactional`.
- Production verification still needed: observe worker wake latency and retry delivery against real Telegram/Feishu endpoints after deployment.

### Phase 2

- Status: complete
- Main design: diagnostics GET uses bounded aggregate queries and a `SELECT 1` latency probe; full doctor remains explicit, while doctor and maintenance timestamps survive process restarts through `app_meta`. Scheduler, history, notification, and maintenance workers expose a common health shape.
- Schema changes: none; `app_meta` stores service state.
- Tests: GET doctor isolation, explicit POST doctor execution, persisted doctor/backup/checkpoint/maintenance state, worker and queue payloads, focused worker/Web suites, and full suite.
- Commit: `afb4178 fix: make diagnostics lightweight`.
- Production verification still needed: compare diagnostics latency on the production-sized database and confirm persisted times across a container restart.

### Phase 3

- Status: complete
- Main design: resolve adaptive scheduling per account through `inherit`, `fixed`, or `adaptive`, with optional account-level bounds and persisted effective intervals.
- Schema changes: v21 adds account scheduling mode, adaptive min/max overrides, and last effective interval.
- New settings: none; account overrides fall back to existing global adaptive scheduler settings.
- Tests: global/account mode matrix, account bounds, persisted effective interval, manual-run configuration preservation, API null clearing, UI controls, focused suites, and full suite (260 tests passed).
- Commit: `bebcf4e feat: add per-account adaptive scheduling`.
- Production verification still needed: confirm migrated accounts remain `inherit` and compare effective intervals with expected author cadence after several successful scans.

### Phase 4

- Status: complete
- Main design: pair new backups with SHA256 manifests, verify old or new backups offline, and require a validated source, stopped application, pre-restore backup, fsynced temporary copy, atomic replacement, and post-restore verification for disaster recovery.
- Schema changes: none.
- New settings: none; restore reuses the configured database path and backup retention count.
- Tests: manifest/hash verification, legacy manifestless backups, corruption, older/future schema, dry-run, confirmation, active writer refusal, successful restore, rollback after validation failure, CLI coverage, and maintenance worker regression.
- Commit: `c000e7b feat: add verified database restore`.
- Production verification still needed: perform a restore drill on a disposable VPS copy with Docker stopped and verify restart/migration behavior; do not use restore during a normal upgrade.

### Phase 5

- Status: completed
- Main design: persist a manually versioned parser identity, stable input hash, and build SHA for every parser outcome; expose outdated processing without automatically rewriting matched Episode records.
- Schema changes: v22 adds nullable parser version, parser input hash, and processed build SHA to videos; null remains the legacy marker.
- New settings: none; parser rule changes require an explicit `PARSER_VERSION` bump.
- Tests: parser identity for matched/review/ignored results, stable input hash refresh behavior, outdated API and reparse filtering, v21 migration, quality reporting, focused repository/pipeline/web suites, and the complete 268-test suite.
- Commit: `c1c4846 feat: version parser processing results`.
- Production verification still needed: confirm the quality center's legacy count and run a bounded outdated reparse on a backup-verified production database.

### Phase 6

- Status: completed
- Main design: committed exact-match parser corpus evaluated through a network-free `EpisodeParser` with human and JSON CLI reports.
- Schema changes: none.
- New settings: none.
- Tests: 24 committed golden cases at 100% exact match, failure-report CLI coverage, focused parser suites, and the complete 270-test suite.
- Commit: `1b428a9 test: add parser golden regression suite`.
- Production verification still needed: run `parser-eval` from the deployed image and require future parser changes to update the corpus intentionally.

### Phase 7

- Status: complete
- Main design: independent LLM/OCR guards reserve durable UTC-day usage before each external call, enforce separate process-wide concurrency, open a per-provider cooldown circuit after consecutive failures, and degrade budget/circuit denials into review reasons without failing account scans.
- Schema changes: v23 adds `ai_usage_daily` with one durable aggregate row per UTC date/provider.
- New settings: `LLM_MAX_CONCURRENT_REQUESTS`, `OCR_MAX_CONCURRENT_REQUESTS`, `LLM_DAILY_CALL_LIMIT`, `OCR_DAILY_CALL_LIMIT`, `AI_FAILURE_THRESHOLD`, and `AI_COOLDOWN_MINUTES`.
- Tests: unlimited and exhausted budgets, UTC reset, independent concurrency, LLM/OCR circuits, scan degradation, diagnostics redaction, quality categorization, migration, focused suites, and the complete 278-test suite.
- Commit: `2e199da feat: add ai request budgets and guards`.
- Production verification still needed: verify configured provider limits, observe daily counters/cooldown state, and confirm no real credentials or raw AI payloads appear in diagnostics.

### Phase 8

- Status: complete
- Main design: browser image URLs point to authenticated entity-ID routes; the service resolves the stored database URL, validates every HTTP(S) redirect and all resolved addresses, enforces image-only bounded downloads, serves stale cache on refresh errors, and falls back to a local SVG.
- Schema changes: v24 adds `media_cache_entries` with URL hash, relative file path, content type, fetch/access timestamps, and bounded size metadata.
- New settings: `MEDIA_CACHE_ENABLED`, `MEDIA_CACHE_DIR`, `MEDIA_CACHE_MAX_MB`, `MEDIA_CACHE_TTL_HOURS`, `MEDIA_CACHE_TIMEOUT_SECONDS`, and `MEDIA_CACHE_MAX_FILE_MB`.
- Tests: cache hit, TTL refresh, stale fallback, private/redirect SSRF rejection, type and size rejection, entity-only routes, auth protection, LRU eviction, migration, and proof that video bytes are never cached.
- Commit: `ab54bc2 feat: add bounded media cache`.
- Production verification still needed: confirm Douyin CDN host resolution and response types, observe cache growth/eviction, and verify stale covers after a controlled upstream failure.

### Phase 9

- Status: complete
- Main design: Shows expose bounded `page/page_size` metadata while retaining the legacy `limit`; a single authenticated search endpoint groups explicit safe projections for shows, accounts, and videos. SQLite FTS5 virtual tables are maintained by triggers, with runtime capability detection and automatic LIKE fallback.
- Schema changes: v25 adds optional `search_shows`, `search_accounts`, and `search_videos` FTS5 indexes plus synchronization triggers when the SQLite runtime supports FTS5. Core relational data is unchanged when it does not.
- New settings: none. `python -m douyin_user_monitor search-rebuild` rebuilds available FTS indexes and reports fallback mode otherwise.
- Tests: show/following pagination, title/alias/nickname/video/aweme search, FTS and forced LIKE paths, auth protection, safe response projection, rebuild/doctor checks, and a synthetic 100-account/1,000-show/50,000-video database without timing thresholds.
- Commit: `0130e90 feat: add global search and show pagination`.
- Production verification still needed: confirm the deployed SQLite reports FTS5 or LIKE mode, run `search-rebuild`, exercise Ctrl/Cmd+K and mobile search, and inspect page navigation with the production row count.

## Final Verification

- Schema migration path: v20 -> v21 account scheduling -> v22 parser identity -> v23 AI daily usage -> v24 media cache metadata -> v25 optional FTS5 search indexes. Migrations preserve the existing relational tables and data.
- Full suite: `python -m unittest discover -s tests -v` ran 289 tests in 69.013 seconds; all passed.
- Parser corpus: `python -m douyin_user_monitor parser-eval` ran 24 cases; 24 passed and 0 failed. Status, show title, season number, episode number, and content type were all 100% accurate.
- Container: `docker build .` completed successfully with the Python 3.12 slim image.
- UI QA: a disposable v25 database with 31 shows was checked at 1440x900 and 390x844. Desktop and mobile search, Ctrl+K, grouped results, 24-item pagination, the seven-item final page, and bottom-navigation spacing passed with no browser console warnings or errors.
- Secret review: only the explicitly listed Phase 9 files were staged; no credential-shaped values, runtime database, WAL/SHM file, backup, cookie, token, webhook, or `.env` file was included.
