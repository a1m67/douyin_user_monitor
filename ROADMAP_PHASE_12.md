# Phase 12 Roadmap

This roadmap tracks the nine reliability, data-model, concurrency, metrics,
daily-experience, and modularization phases. Each phase is implemented,
tested, and committed independently without pushing during the phase.

## Progress

- [x] Phase 1: ShowSeason metadata
- [ ] Phase 2: Reliable Douyin empty-page handling
- [ ] Phase 3: Central Douyin request protection
- [ ] Phase 4: Normalized Show aliases
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
- Commit: `feat: add show season metadata` (SHA recorded after commit)
- Production verification still needed: run migration against a copied production v14 database and verify multi-season display/editing.

### Phase 2

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Production verification still needed: pending

### Phase 3

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Production verification still needed: pending

### Phase 4

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Production verification still needed: pending

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
