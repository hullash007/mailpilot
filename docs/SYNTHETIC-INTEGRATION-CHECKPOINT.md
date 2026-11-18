# Synthetic integration review checkpoint

18 November2026,09:35IST. Local-only integration branch, not deployed or published. No SQLCipher installation/qualification, live database/schema read/change, provider/model call, worker or backup operation.

## Frozen inputs

- Public Stored Conversations baseline039176769369a8f548911dbd531cc63906fa51c7.
- Private accepted Action/history candidate e3bb273.
- Exact nine-file accepted sync bundle SHA897cbcfe7a79ed5757ab76d9c073e2eb495a308ea7788b61ed35460f387c7236, all files byte-compared to the separate sync worktree before changes, then preserved as local commit240fa58c3f244abfe359a1c4af1514fe759cc496. No push.
- See SYNC-ACCEPTED-MANIFEST.json for per-file input hashes. Integration changes are distinct from that frozen input.

## Composition changes

One explicit app.integrated_candidate:create_app composes the local candidate from baseline auth and Action routes. It replaces baseline /workspace registration once with the sync-aware candidate router; no duplicate route/method remains. Its final HTTPException, StarletteHTTPException and RequestValidationError handlers dispatch by route, covering stored workspace, Action/history and standard fallback. The local lifespan performs no schema creation or mutation. It requires a separately prepared disposable fixture and removes /app/sync, the inherited real Gmail manual-import POST. Auth routes remain for existing verified-subject dependency semantics, not an invitation to sign into live Google in this proof.

LocalViews adapter uses stored-only service when sync tables are absent, refuses partial sync schema503, and uses the accepted sync service when tables exist. No account sync state says Limited stored sample, never completed empty sync. Generation-aware stale membership/search behavior remains in the sync service. Candidate workspace can now install onto an explicit app without creating a second app; its standalone factory removes the newer baseline workspace before registration. Public google_main/stored modules are untouched.

The accepted provider persistence had a provenance seam: refreshing an existing synthetic-marked row retained is_sample=True. Integration explicitly clears is_sample for every persisted provider-origin message. New and refreshed provider-origin rows therefore cannot enter Action detection. The prior local decision remains historical/unavailable in history rather than being discarded. No new detector/status/state or real-mail Action activation.

## Finite evidence

Builder complete suite351passed/2optional skips on local Python3.12.16 with official CPython3.12.3 Modules/_sqlite extension linkedSQLite3.45.1. This is not SQLCipher and not target-host ARM64/interpreter/build qualification. No independent whole-suite replication claimed.

Five changed-seam cases: unique route/method registration and all route-aware errors; missing sync schema fallback/partial-schema refusal; one injected fake-provider tick with preserved confirmed decision, provider-origin Action exclusion, generation stale withholding and expired ORM state; collision refresh clears prior sample eligibility while retaining history; local startup and GET trace no DDL/DML with socket create forbidden. Existing regressions cover sync CAS/pending/budget/retry/account isolation and Action/history boundaries.

One inherited sync test expected /workspace404 because its old705c588baseline lacked the now-deployed route. Updated that assertion to baseline401 and fresh-process metadata/route absence for sync/Action. This is an integration baseline correction, not suppressing a regression. First full run had348pass/1thisfailure/2skip; fixed run351/2 includes two subsequent provenance/startup probes.

Actual local Chromium320/390/1280: partial-sync workspace -> literal fake-provider search -> detail -> paginated local history -> invalid-query route error. No horizontal overflow. Actual pixels inspected320/390/1280workspace and390error, readable partial-state warnings/source/plain-text/no-live labels. Harness temporarily bypasses auth for synthetic pixels only; integration tests use actual own_account fixture login. Harness runtimePython3.10 differs from full regression runtime. Initial visual assertion missed confirmed record on second history page; harness navigated correct older page and reran successfully. No product change for that harness error.

## Boundaries

This slice proves composition with fake injected data, not a reliable production sync, encrypted runtime, migration, erasure, recovery or live Gmail operation. Storage/recovery architecture is accepted design only. SQLCipher/runtime/migration/backup/lifecycle proofs are separate scoped work after review. Owner retention/import/backup destination/key custody/release choices remain unapproved but do not block technical preparation.
