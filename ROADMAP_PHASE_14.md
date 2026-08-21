# Phase 14 Roadmap

Transactional reliability, lightweight operations, parser governance, bounded AI/media resources, and scalable library navigation are delivered in nine independent phases.

## Progress

- [x] Phase 1: Transactional notification outbox
- [ ] Phase 2: Lightweight diagnostics and persistent service state (in progress)
- [ ] Phase 3: Per-account adaptive scheduling
- [ ] Phase 4: Verified database restore
- [ ] Phase 5: Parser processing versioning
- [ ] Phase 6: Parser golden regression suite
- [ ] Phase 7: AI request budgets and guards
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

- Status: in progress

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
