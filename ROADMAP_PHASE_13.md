# Phase 13 Roadmap

Security, delivery reliability, maintainability, long-running storage, and daily-use improvements are implemented and committed in nine independent phases.

## Progress

- [x] Phase 1: Single-user web authentication and CSRF
- [x] Phase 2: Legacy monitor isolation
- [ ] Phase 3: Reliable notification outbox
- [ ] Phase 4: Complete web module split
- [ ] Phase 5: Build identity and cache-safe PWA delivery
- [ ] Phase 6: Automatic backups and maintenance worker
- [ ] Phase 7: Bounded Douyin raw payload storage
- [ ] Phase 8: Avatar, cover, and continue-watching UX
- [ ] Phase 9: Optional adaptive scheduler

## Phase Records

### Phase 1

- Status: complete
- Main design: signed single-user sessions, browser login/logout, session-or-Bearer API authentication, session-bound double-submit CSRF, Origin validation, and baseline security headers.
- Schema changes: none.
- Tests: signed-session tamper/expiry, login, page/API protection, session CSRF and Origin checks, Bearer bypass, settings validation, compatibility regressions, and full suite (230 tests passed).
- Commit: recorded after implementation commit.
- Production verification still needed: HTTPS reverse-proxy cookie behavior and session expiry.

### Phase 2

- Status: complete
- Main design: `/api/monitor` is mounted as an on-demand compatibility sub-application, so importing or starting the default app does not import/build the legacy crawler, JSON storage, notifier, or service. The legacy polling loop remains gated by `LEGACY_MONITOR_ENABLED`; compatibility routes load the old stack only when actually requested.
- Schema changes: none expected.
- Tests: default app construction proves no legacy import, lifecycle gating covers disabled/enabled modes, compatibility dashboard/avatar/statistics/history routes remain functional, and full suite.
- Commit: recorded after implementation commit.
- Production verification still needed: pending.

### Phase 3

- Status: pending
- Main design: persistent idempotent notification outbox with background retry and bounded backoff.
- Schema changes: pending.
- Tests: pending.
- Commit: pending.
- Production verification still needed: pending.

### Phase 4

- Status: pending
- Main design: split backend APIs and frontend route modules without changing framework or URLs.
- Schema changes: none expected.
- Tests: pending.
- Commit: pending.
- Production verification still needed: pending.

### Phase 5

- Status: pending
- Main design: explicit build/version metadata, versioned static URLs, and deterministic service-worker cache upgrades.
- Schema changes: none expected.
- Tests: pending.
- Commit: pending.
- Production verification still needed: pending.

### Phase 6

- Status: pending
- Main design: lightweight maintenance worker for scheduled online backups and bounded cleanup/checkpoint tasks.
- Schema changes: none expected.
- Tests: pending.
- Commit: pending.
- Production verification still needed: pending.

### Phase 7

- Status: pending
- Main design: compact and prune stored Douyin raw payloads while preserving fields required for reparsing and audit.
- Schema changes: pending.
- Tests: pending.
- Commit: pending.
- Production verification still needed: pending.

### Phase 8

- Status: pending
- Main design: reliable account avatars, show covers, and direct continue-watching actions.
- Schema changes: pending.
- Tests: pending.
- Commit: pending.
- Production verification still needed: pending.

### Phase 9

- Status: pending
- Main design: optional adaptive account scheduling based on recent update cadence and failures, bounded by operator configuration.
- Schema changes: pending.
- Tests: pending.
- Commit: pending.
- Production verification still needed: pending.
