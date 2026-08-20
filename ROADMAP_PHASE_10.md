# Phase 10 Roadmap

This roadmap tracks the nine production-hardening phases requested for the
short-drama episode monitor. Phases are implemented and committed in order.

## Progress

- [x] Phase 1: Season / multi-season episode support
- [x] Phase 2: VPS and web security defaults
- [x] Phase 3: Global Douyin crawler circuit breaker
- [x] Phase 4: Disable the legacy monitor by default
- [x] Phase 5: Scan-run history and observability
- [x] Phase 6: SQLite backup, doctor, and GitHub CI
- [x] Phase 7: Video pagination and filters
- [x] Phase 8: Learn show aliases from manual review
- [x] Phase 9: Cover OCR fallback framework

## Phase Records

Each completed phase records its main design, schema changes, test results,
commit SHA, and anything not verified in a real deployment.

### Phase 1

- Status: complete
- Main design: Season-aware parser and pipeline, first-season compatibility defaults, season-grouped show detail, latest-season summaries, and season-local missing-episode calculation.
- Schema changes: schema v9; episodes rebuilt with `season_number` and `UNIQUE(show_id, season_number, episode_number)` while preserving ids and child rows; existing episodes migrate to season 1; shows cache `latest_season`; videos persist parsed/candidate season values.
- Tests: `python -m unittest discover -s tests -v` - 154 passed.
- Commit: `7212190`
- Not yet verified: migration against the production VPS database and live multi-season Douyin posts.

### Phase 2

- Status: complete
- Main design: Compose binds only to localhost; optional constant-time Bearer authentication protects modifying short-drama API requests while reads and health remain public to the local/reverse-proxy boundary.
- Schema changes: none.
- Tests: `python -m unittest discover -s tests -q` - 156 passed; focused settings/web suite - 16 passed.
- Commit: `c695e12`
- Not yet verified: real reverse-proxy, HTTPS, Cloudflare Access, and VPS firewall configuration.

### Phase 3

- Status: complete
- Main design: in-memory closed/open/half-open breaker with centralized error classification, distinct-account thresholds, a single recovery probe, scheduler short-circuiting, manual `force=true`, and structured status output.
- Schema changes: none.
- Tests: focused circuit/scheduler/settings/web suite - 23 passed; full suite - 161 passed.
- Commit: `1e65f89`
- Not yet verified: real Douyin 403/429/login failure signatures and recovery timing on the VPS.

### Phase 4

- Status: complete
- Main design: the SQLite short-drama runtime remains the sole default scheduler; the legacy JSON monitor starts and shuts down only when `LEGACY_MONITOR_ENABLED=true`, while its compatibility routes remain registered.
- Schema changes: none.
- Tests: focused lifecycle/settings suite; full suite - 163 passed.
- Commit: `f9a1fea`
- Not yet verified: intentional dual-runtime migration mode on a production VPS.

### Phase 5

- Status: complete
- Main design: scheduled/manual checks persist non-blocking structured runs; account responses include 20 recent runs, status aggregates 24 hours, and startup retention bounds growth.
- Schema changes: schema v10 adds `scan_runs` and account/time indexes.
- Tests: focused repository/scheduler/settings tests; full suite - 165 passed.
- Commit: `c9549f3`
- Not yet verified: long-term scan volume and production UI readability.

### Phase 6

- Status: complete
- Main design: SQLite online backups with strict filename retention, pre-migration snapshots, read-only doctor plus deterministic repair, and CI for unit, health smoke, and Docker build.
- Schema changes: none beyond the preceding v10 migration.
- Tests: focused maintenance/settings tests; full suite - 167 passed.
- Commit: `75153e5`
- Not yet verified: GitHub-hosted CI and backup retention on the production filesystem.

### Phase 7

- Status: complete
- Main design: dedicated SQLite offset pagination preserves internal list compatibility while the API supports combined account/show/status/parser/content/search/date filters.
- Schema changes: schema remains v10; targeted content/parser ordering indexes added.
- Tests: focused repository/web tests; full suite - 168 passed.
- Commit: `8b387f4`
- Not yet verified: query plans on production-scale data.

### Phase 8

- Status: complete
- Main design: optional manual-review alias learning with normalized global conflict detection; show settings remain the add/remove management surface and known aliases retain parser priority.
- Schema changes: none.
- Tests: focused repository/pipeline/web/parser tests; full suite - 169 passed.
- Commit: `b3d8581`
- Not yet verified: alias suggestions against real typo patterns.

### Phase 9

- Status: complete
- Main design: optional HTTP-compatible OCR abstraction runs only for unresolved review videos with covers, uses conservative confidence, reparses OCR text through the existing parser, and caches success/failure.
- Schema changes: schema v11 adds `ocr_text`, `ocr_confidence`, and `ocr_processed_at` to videos.
- Tests: focused OCR/settings/pipeline/repository tests - 9 passed; full suite - 173 passed.
- Commit: `f026fdb`
- Not yet verified: a real OCR provider, real cover URL access, and production OCR accuracy/cost.
