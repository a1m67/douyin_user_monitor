# Phase 11 Roadmap

This roadmap tracks the nine product-experience phases for the personal AI
short-drama tracker. Phases are implemented, tested, and committed in order.

## Progress

- [x] Phase 1: Smart Douyin account and share-link input
- [x] Phase 2: Personal show following
- [x] Phase 3: Episode update feed and unread state
- [x] Phase 4: Advanced episode corrections
- [x] Phase 5: Web cookie management
- [x] Phase 6: System diagnostics dashboard
- [x] Phase 7: Data quality center
- [x] Phase 8: Mobile and PWA experience
- [x] Phase 9: Long-running SQLite performance

## Phase Records

Each phase records its status, main design, schema changes, tests, commit SHA,
and production behavior that has not yet been verified.

### Phase 1

- Status: complete
- Main design: Provider-scoped `AccountInputResolver` locally parses canonical profile URLs and advanced bare ids, extracts the first official URL from share text, manually validates every official redirect hop, and resolves video/note authors through the existing one-video crawler method. All persisted homepages are canonical profile URLs and pipeline deduplication remains based on `sec_uid`.
- Schema changes: none.
- Tests: focused resolver/provider/web suite - 25 passed; full suite - 183 passed.
- Commit: `bc4d336`
- Not yet verified: real Douyin short-link redirect variants, live video-detail author payloads, and production network timeout behavior.

### Phase 2

- Status: complete
- Main design: Following is an independent Show preference exposed through idempotent follow/unfollow endpoints, boolean filtering, a dedicated `/following` page, stars on library cards, and detail actions. It does not alter ignored-Show behavior or notification dispatch.
- Schema changes: schema v12 adds `shows.is_following` defaulting to false and nullable `followed_at`; existing rows remain unfollowed.
- Tests: focused repository/web suite - 42 passed; full suite - 184 passed.
- Commit: `bf9c6e2`
- Not yet verified: mobile interaction and production migration against the VPS database.

### Phase 3

- Status: complete
- Main design: New Episodes discovered by normal incremental scans create one transactional `new_episode` event. Initial sync, history backfill, manual corrections, and additional sources do not create events. The `/updates` feed supports following and unread filters, per-event/read-all actions, an unread navigation count, and marks a Show's events read when its detail page is opened.
- Schema changes: schema v13 adds `update_events` with a unique `(episode_id, event_type)` key and indexes for time, unread, and Show lookups. Existing Episodes are not backfilled, so migration creates no false unread state.
- Tests: focused pipeline/repository/web suite - 62 passed; full suite - 186 passed.
- Commit: `913c5b2`
- Not yet verified: production incremental scan event creation and the update-feed layout with real long-running data.

### Phase 4

- Status: complete
- Main design: Transactional Episode and EpisodeSource moves support cross-Show/season/number corrections and deterministic conflict merging. Notifications are only relinked, update events are reconciled without duplication, Show latest/missing state is recalculated, Videos and Episodes have batch actions, and every operation remains notification-free.
- Schema changes: schema v14 adds the append-only `manual_corrections` JSON audit table and a newest-first index.
- Tests: focused repository/web/pipeline suite - 65 passed; full suite - 189 passed.
- Commit: `7b2ed64`
- Not yet verified: high-volume correction UX and audit review against production data.

### Phase 5

- Status: complete
- Main design: `/settings/crawler` accepts a Cookie header or browser-export JSON, writes a private runtime file with temp/fsync/replace, reloads the in-process crawler immediately, exposes only masked status/timestamps, and probes one enabled account on demand. Existing admin write authentication protects save and test actions.
- Schema changes: none.
- Tests: focused cookie/settings/web suite - 21 passed; full suite - 191 passed.
- Commit: `10febcc`
- Not yet verified: a live VPS browser export, real Douyin login/risk-control classification, and filesystem ownership in the production container.

### Phase 6

- Status: complete
- Main design: Redacted diagnostics report database/schema/size/backups, scheduler, crawler circuit, Cookie, and LLM/OCR configuration state. Protected actions run read-only doctor and create a SQLite backup; `/health` remains lightweight.
- Schema changes: none.
- Tests: focused web diagnostic test - 1 passed; full suite - 192 passed.
- Commit: `6a7e106`
- Not yet verified: production backup permissions and live external-service probes.

### Phase 7

- Status: complete
- Main design: `/quality` reports eight bounded, read-only issue categories with counts and links into existing review, video, Show settings, and correction workflows: review, gaps, suspicious jumps, expected-count conflicts, source-less Episodes, confidence below 0.90, OCR-only matches, and stale updating Shows.
- Schema changes: none.
- Tests: focused repository/web quality tests - 2 passed; full suite - 194 passed.
- Commit: `3ea4bd3`
- Not yet verified: threshold tuning against the production library's age and episode distribution.

### Phase 8

- Status: complete
- Main design: Mobile-first daily-use routes now expose a fixed four-item bottom navigation while retaining the desktop sidebar. The application ships an installable manifest, self-owned SVG icon, and service worker that caches only the app shell/static assets; API and health requests remain network-only so stale operational data is never presented as current.
- Schema changes: none.
- Tests: focused PWA metadata/route test - 1 passed; full suite - 195 passed. Browser QA at 390x844 confirmed no horizontal overflow on Following or Updates, safe bottom content padding, and a toast offset above the mobile navigation.
- Commit: `9db3595`
- Not yet verified: installation and offline shell behavior on a physical iOS/Android device behind the production reverse proxy.

### Phase 9

- Status: complete
- Main design: SQLite connections now consistently enable WAL, a 30-second busy timeout, and foreign keys. Query-plan-driven composite/expression indexes cover video pagination, update ordering, scan-run ordering, source ordering, and stale-Show quality checks. Show detail batches all EpisodeSource rows in one connection, quality reports count all issues while bounding materialized detail rows, and video pagination avoids an unnecessary GROUP BY sort. `python -m douyin_user_monitor db-stats [--checkpoint]` reports size, WAL, pages, row counts, and indexes without exposing row content; checkpoint remains explicitly operator-triggered and UpdateEvent retention is unchanged.
- Schema changes: no version bump; schema remains v14. Existing databases receive additive `CREATE INDEX IF NOT EXISTS` changes during normal initialization after required column migrations.
- Tests: focused maintenance/performance suite - 9 passed; repository/web/maintenance/performance regression - 59 passed; full suite - 202 passed. The synthetic database contains 100 accounts, 20,000 videos, 1,000 Shows, 10,000 Episodes, and 10,000 UpdateEvents and asserts plans/pagination rather than brittle wall-clock timing.
- Commit: `8879bbf`
- Not yet verified: WAL growth/checkpoint cadence and write contention under the production VPS filesystem and real scheduler concurrency.
