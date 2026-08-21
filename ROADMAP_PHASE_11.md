# Phase 11 Roadmap

This roadmap tracks the nine product-experience phases for the personal AI
short-drama tracker. Phases are implemented, tested, and committed in order.

## Progress

- [x] Phase 1: Smart Douyin account and share-link input
- [ ] Phase 2: Personal show following
- [ ] Phase 3: Episode update feed and unread state
- [ ] Phase 4: Advanced episode corrections
- [ ] Phase 5: Web cookie management
- [ ] Phase 6: System diagnostics dashboard
- [ ] Phase 7: Data quality center
- [ ] Phase 8: Mobile and PWA experience
- [ ] Phase 9: Long-running SQLite performance

## Phase Records

Each phase records its status, main design, schema changes, tests, commit SHA,
and production behavior that has not yet been verified.

### Phase 1

- Status: complete
- Main design: Provider-scoped `AccountInputResolver` locally parses canonical profile URLs and advanced bare ids, extracts the first official URL from share text, manually validates every official redirect hop, and resolves video/note authors through the existing one-video crawler method. All persisted homepages are canonical profile URLs and pipeline deduplication remains based on `sec_uid`.
- Schema changes: none.
- Tests: focused resolver/provider/web suite - 25 passed; full suite - 183 passed.
- Commit: this phase commit (SHA recorded during final roadmap pass)
- Not yet verified: real Douyin short-link redirect variants, live video-detail author payloads, and production network timeout behavior.

### Phase 2

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Not yet verified: pending

### Phase 3

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Not yet verified: pending

### Phase 4

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Not yet verified: pending

### Phase 5

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Not yet verified: pending

### Phase 6

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Not yet verified: pending

### Phase 7

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Not yet verified: pending

### Phase 8

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Not yet verified: pending

### Phase 9

- Status: pending
- Main design: pending
- Schema changes: pending
- Tests: pending
- Commit: pending
- Not yet verified: pending
