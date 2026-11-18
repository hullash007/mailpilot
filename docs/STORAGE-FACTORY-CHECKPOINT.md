# Explicit storage factory candidate
18November2026,09:57IST. Source-only local candidate on accepted synthetic integration2a944c97e9d639253f545f83dafdf4fd159f4f1e. No publication, live changes, installation, real-mail access or additional cipher execution.

## Changed seam

- New app/storage_factory.py provides a lazy explicit driver/open-existing/key-provider/key-application/runtime-validator/session seam. Every new physical connection receives a fresh key before runtime/schema reads; positive injected runtime validation and immutable required existing tables/columns must pass before yielding. NullPool avoids a pooled old connection bypassing a changed key. No key-bearing URL/settings, import-time connection, schema creation or fallback exists in this seam. Failed connections close and errors are replaced with a safe StorageUnavailable message, suppressing callback exception text in normal traceback display. This is not memory zeroization or protection against debug-local-variable collection.
- app/protected_candidate.py requires those dependencies and attaches the prepared factory to the existing local composition. No automatic SQLCipher import or module-level protected app. Startup does no schema work, shutdown disposes its engine. Existing legacy database.py and .env-driven globals remain unchanged; importing the integrated app still constructs the legacy engine lazily as before, although protected routes use the explicit get_db override instead. It does not remove all legacy globals.
- integrated_candidate.create_app(storage=...) supports the explicit prepared factory. No-argument behavior remains unchanged. Storage failure returns a generic503 without driver/key/path details. Current exception headers are still the previously accepted next-touch limitation, not changed here.

## Trusted adapter contract and limits

open_existing MUST refuse a missing DB; the seam does not open file paths itself and cannot prevent a malicious or incorrect injected opener from creating a file before validation. apply_key and validate_runtime are trusted code, not validated by their names. No built-in runtime cipher implementation is supplied in this slice. A True callback in the fake tests is deliberately not proof of encryption. Required schema is an explicit caller specification, not an operational migrator or a complete MailPilot schema version ledger. Real app integration needs an actual reviewed adapter/key delivery/schema manifest before any protected deployment.

The earlier accepted isolated cipher proof remains separate: pinned PyPI distribution+digest only, no signed-source-to-wheel attestation; selfreported runtime consistency, crossprocess concurrency deferred, no production encryption conclusion. This source slice did not load that wheel.

## Evidence

Seven new tests cover lazy/key ordering on each physical connection, missing-key/runtime rejection, sanitized failure/close, partial-schema and missing-file no-create refusal, frozen schema spec/name/explicit-driver validation, injected app/default/no-startup-DDL behavior with generic503, and import-without-DBIO. Ordinary SQLite and fake key/runtime callbacks only. Existing integrated seam regressions also pass.

Full builder suite358passed/2skipped. Python3.12.16 with source-built official CPython3.12.3 SQLite extension linked SQLite3.45.1, same prior builder setup. Not actual ARM64 host, SQLCipher application integration, or independently repeated full suite. Source driver is distinct from wheel runtime4.20.0/SQLite3.54.1 proof.

Actual new503HTML rendered in local Chromium390x844, pixels inspected: readable wrapping/no horizontal overflow, no private path/key/driver text. No other visual layout changed.

## Remaining gates

Focused independent review of this source/test delta. Actual adapter implementation and driver connection hardening, extension-loading/temp-store design, explicit schema versioning/offline migration and synthetic app integration are separate next technical choices. Production key delivery/recovery, operational backups/erasure/retention policy, ARM64 host/runtime, live conversion/import and release remain separate. No owner policy is selected here.
