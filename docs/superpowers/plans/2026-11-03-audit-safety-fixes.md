# Audit Safety Fixes Implementation Plan

> **For agentic workers:** Use test-driven implementation and independent final review. Execute the user's authorized fixes continuously; real library deletions require separate authorization.

**Goal:** Resolve F01–F12 and prepare a verified, identity-sensitive duplicate proposal.

**Architecture:** Keep existing Python APIs where practical. Use alias-aware discovery, scoped identity, committed install state, immutable job inputs and race-safe OS locks. Prefer preservation on uncertainty.

**Tech Stack:** Python 3.10+, stdlib unittest, existing requests/dotenv/blake3.

**Spec:** `docs/superpowers/specs/2026-11-03-audit-safety-fixes-design.md`

## Global Constraints

- No real library deletions without explicit user authorization.
- Different versions/authors/variants do not qualify as duplicates.
- No invented source URLs, trigger phrases or publisher identities.
- All model/network tests use isolated temporary fixtures and fake clients.

## Review Focus

- Concurrent duplicate listings or failed downloads preserve the last working copy.
- Missing author/file/version information prevents duplicate deletion.
- Parent, child and symlink writer scopes exclude conflicting operations, including other processes.
- False, zero, empty lists and explicit All override inherited saved filters.
- Wrong, short, incomplete or inconsistent HTTP ranges never commit a destination.

## Task 1 — Install, version and discovery safety (F01–F05)

Files: indexer.py, version_prune.py, download_one.py, heal_library.py, downloader.py; focused regression files.
- [x] Add failing regressions for all seven original safety cases and user duplicate criteria.
- [x] Implement base-scoped update/prune state, no pruning on skips/reservations, verified identity-sensitive collapse, exact heal file resolution and alias/cycle-aware discovery.
- [x] Preserve historical versions by default; test explicit cleanup separately.
- [x] Run focused tests and full suite; report any interface changes.

## Task 2 — UI, transport, cache and filters (F06–F09, F11)

Files: ui/server.py, client.py, listing_cache.py, cli.py, filter_presets.py; focused regression files. Coordinate downloader.py cache-call changes with Task 1.
- [x] Add failing admission-race, permissions, range, cache-dimension and saved-filter regressions.
- [x] Implement immutable admitted manifests, secure settings replacement, validated resume, complete cache identities and explicit-value precedence.
- [x] Run focused tests and full suite.

## Task 3 — Writer locking and structural validation (F12, remaining F05, F10)

Files: run_lock.py, maintenance.py; focused regression files.
- [x] Reproduce empty-file lock takeover, same-process/other-process writers, parent/alias overlap, and invalid dtype/shape lengths.
- [x] Implement kernel-backed locking with safe owner/release behavior and overlapping physical scope coordination; validate shapes/dtypes and scalar/empty tensors.
- [x] Run focused tests and full suite.

## Task 4 — Integration, review, data and deletion proposal

- [x] Verify all twelve findings against regression evidence, run the full suite, and perform independent code review.
- [x] Integrate verified source/tests/docs into the original checkout and re-run tests there.
- [x] Compare duplicate versions, author and file identities with computed payload hashes; retain v1/v2 and unknown identities.
- [x] Recover only supported missing metadata; identify unresolved facts and partial-download options.
- [x] Update the audit artifact with fixed findings and an exact candidate removal list, then request authorization for that list.

The user explicitly approved all six bundles in the exact24-file proposal. Cleanup executed against fresh identity/hash/signature checks, with companion backups; all excluded groups and the partial were preserved.
