# Phase 13 Roadmap

Security, delivery reliability, maintainability, long-running storage, and daily-use improvements are implemented and committed in nine independent phases.

## Progress

- [x] Phase 1: Single-user web authentication and CSRF
- [x] Phase 2: Legacy monitor isolation
- [x] Phase 3: Reliable notification outbox
- [x] Phase 4: Complete web module split
- [x] Phase 5: Build identity and cache-safe PWA delivery
- [x] Phase 6: Automatic backups and maintenance worker
- [x] Phase 7: Bounded Douyin raw payload storage
- [x] Phase 8: Avatar, cover, and continue-watching UX
- [x] Phase 9: Optional adaptive scheduler

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

- Status: complete
- Main design: persistent idempotent notification outbox with background retry, stale-claim recovery, and bounded backoff.
- Schema changes: v19 adds durable notification delivery jobs keyed by Episode, event type, and channel.
- Tests: idempotent enqueue, successful delivery, retry recovery, stale processing recovery, dead-letter behavior, notification history compatibility, repository regression, and full suite (235 tests passed).
- Commit: recorded after implementation commit.
- Production verification still needed: Telegram/Feishu outage recovery and retry timing on the VPS; external endpoints provide at-least-once rather than transactional exactly-once delivery.

### Phase 4

- Status: complete
- Main design: extracted API contracts and result serialization from route assembly; split the browser application into shared core, Shows, library/review, system, and bootstrap modules without changing URLs or global event handlers.
- Schema changes: none; schema remains v19.
- Tests: static module routing, unchanged Dashboard/API behavior, authentication compatibility, compile checks, and full suite.
- Commit: recorded after implementation commit.
- Production verification still needed: browser interaction smoke test across every route after deployment.

### Phase 5

- Status: complete
- Main design: deterministic content-derived build identity, public non-sensitive `/version`, versioned immutable static URLs, and a build-scoped service-worker cache that removes prior shells on activation.
- Schema changes: none; schema remains v19.
- Tests: deterministic build format, versioned template URLs, immutable/version-mismatch cache headers, service-worker cache identity, Web/auth regression, and full suite.
- Commit: recorded after implementation commit.
- Production verification still needed: update an installed PWA across two production deployments and confirm the prior cache is removed.

### Phase 6

- Status: complete
- Main design: low-frequency worker reuses SQLite online backup, prunes only bounded scan-run history, applies passive WAL checkpoints, retains named backups, and exposes redacted health state.
- Schema changes: none; schema remains v19.
- Tests: online backup creation, due-time suppression, passive checkpoint, disabled lifecycle, existing maintenance CLI, settings, diagnostics, compile checks, and full suite.
- Commit: recorded after implementation commit.
- Production verification still needed: backup filesystem capacity/permissions and WAL behavior under the VPS workload.

### Phase 7

- Status: complete
- Main design: compact new/refreshed Douyin payloads to a versioned audit projection and let maintenance rewrite bounded batches of legacy rows while preserving all parser-relevant fields.
- Schema changes: none expected; schema remains v19.
- Tests: compact projection, storage-size bound, refresh semantics, reparse preservation, bounded legacy batches, and maintenance reporting; full suite passed with 241 tests.
- Commit: `perf: bound stored douyin raw payloads`.
- Production verification still needed: pending.

### Phase 8

- Status: complete
- Main design: persist provider avatars on short-drama accounts, derive resilient show covers from valid episode sources, and expose the next recorded regular episode after watch progress as a direct continue-watching action.
- Schema changes: SQLite v20 adds nullable `accounts.avatar_url`; show cover and continue-watching data remain derived to avoid stale duplicate state.
- Tests: avatar persistence and best-effort refresh, v19-to-v20 migration, derived cover selection, continue-watching across missing episodes, media URL validation and Web/API response regressions; full suite passed with 244 tests.
- Commit: recorded after implementation commit.
- Production verification still needed: verify Douyin avatar/cover URL longevity and broken-image fallback behavior on the deployed dashboard.

### Phase 9

- Status: complete
- Main design: optional success-only adaptive scheduling keeps each account's manual interval as its fastest baseline, expands polling after 6/24/72 hours of inactivity, limits expansion to roughly twelve checks per observed update cadence, clamps the result to configured bounds, and leaves failure backoff authoritative.
- Schema changes: none; schema remains v20.
- Tests: disabled compatibility, deterministic inactivity tiers, cadence ceiling, manual baseline preservation, current-update reset, configuration validation, failure backoff priority, scheduler integration, compile checks, and full suite (248 tests passed).
- Commit: recorded after implementation commit.
- Production verification still needed: observe effective `next_check_at` values for active and inactive accounts for several days before enabling broadly on the VPS.
