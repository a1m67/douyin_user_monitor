# Phase 14 Roadmap

Transactional reliability, lightweight operations, parser governance, bounded AI/media resources, and scalable library navigation are delivered in nine independent phases.

## Progress

- [x] Phase 1: Transactional notification outbox
- [x] Phase 2: Lightweight diagnostics and persistent service state
- [x] Phase 3: Per-account adaptive scheduling
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
