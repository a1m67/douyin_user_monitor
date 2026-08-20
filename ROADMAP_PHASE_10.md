# Phase 10 Roadmap

This roadmap tracks the nine production-hardening phases requested for the
short-drama episode monitor. Phases are implemented and committed in order.

## Progress

- [x] Phase 1: Season / multi-season episode support
- [ ] Phase 2: VPS and web security defaults
- [ ] Phase 3: Global Douyin crawler circuit breaker
- [ ] Phase 4: Disable the legacy monitor by default
- [ ] Phase 5: Scan-run history and observability
- [ ] Phase 6: SQLite backup, doctor, and GitHub CI
- [ ] Phase 7: Video pagination and filters
- [ ] Phase 8: Learn show aliases from manual review
- [ ] Phase 9: Cover OCR fallback framework

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
- Commit: pending
- Not yet verified: real reverse-proxy, HTTPS, Cloudflare Access, and VPS firewall configuration.

### Phase 3

- Status: pending

### Phase 4

- Status: pending

### Phase 5

- Status: pending

### Phase 6

- Status: pending

### Phase 7

- Status: pending

### Phase 8

- Status: pending

### Phase 9

- Status: pending
