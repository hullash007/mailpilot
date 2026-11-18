# Disposable Action decision migration proof

17 November2026,23:00IST. Local synthetic files only. Not deployed, imported into an entrypoint, published or run against the actual VM. No live schema, PRAGMA, provider, model or stored mail change.

## Result

255 passed,2 optional skips in the complete candidate suite on local Python3.12.16 with the CPython3.12.3 Modules/_sqlite driver built from official source and linked to SQLite3.45.1. This matches the actual host SQLite and driver source versions, not the host interpreter patch version(3.12.3), architecture(ARM64) or binary/build flags. The actual-host connection itself was not tested. Initial33-proof tests also run on the local3.53.1 library, but use the exact3.45 complete-suite log for review.

Sources used for the isolated test runtime:
https://www.python.org/ftp/python/3.12.3/Python-3.12.3.tgz
https://www.sqlite.org/2024/sqlite-amalgamation-3450100.zip

## New bounded code

- app/action_migration_proof.py: no CLI or automatic import. Explicit disposable installation owns BEGIN IMMEDIATE, creates one table plus index and protection triggers, rolls back on failure, refuses name conflicts. Uses no PRAGMA mutation. Empty rollback drops only candidate objects. Populated rollback unconditionally refuses, even after export; a later reviewed preservation/removal flow is not implemented.
- tests/test_action_migration_proof.py:33synthetic tests using actual separate file connections, actual SQLAlchemy application service and actual source-model schema. No production data, mailbox IDs, tokens or credentials. Source credentials are a literal synthetic placeholder.
- services/action_review.py: only new behavior is safe503 and rollback only for SQLite OperationalError primary error codes5/6(BUSY/LOCKED). Other database errors remain errors, not silently swallowed. Self-review replaced string matching with sqlite_errorcode &0xff to avoid misclassifying a missing table whose name contains busy/locked. A real missing-table error propagates and rolls back in an explicit33rd proof.
- history_page is an internal, decision-first, owned-source LEFT JOIN query, limited50with before-id cursor. It is proved against deliberately malformed legacy orphan/foreign-source fixtures, not wired into an HTTP view. Browser caller authentication must still come from own_account before a future route uses it. No source content is copied into history.

## Proven cases

FKoff identity ownership and sample insertion guards; explicit account/principal/source identity change and deletion rejection while history exists; protected direct insert; source content edits; exact-content reversion tradeoff; account-filtered history when source missing or foreign; schema collision refusal; additive original account/email/principal/thread/oauth-state placeholders byte-for-byte logical equality; explicit authorization-denied migration failure rolls back table/index; empty rollback preserves all originals; populated rollback refuses intact.

Two actual connections barrier immediately before writes: first decision insert has one success and one409; same-revision update has one success and one409. Another connection changes source after validation before INSERT and UPDATE, both reject409 without stale write. A second connection holds BEGIN IMMEDIATE, writer times out to safe503, session rollback verified, retry after lock release succeeds.

Found and closed an extra SQLite hole during proof: INSERT OR REPLACE can remove conflicting rows without DELETE triggers when recursive_triggers=0. Added BEFORE INSERT protection for source/account/principal collisions on both IDs and applicable unique natural keys. Six direct REPLACE probes preserve originals. No global recursive_triggers change.

## Limits

This is a migration proof utility, not an operational migrator. No deployment path, schema ledger, UI history route, real erasure, export/delete override or existing-real-data activation is implemented. Direct deletion/replacement of decision rows and timestamp manipulation remain outside the accidental-writer model, as independently specified. Hard deletion restrictions are a candidate design boundary, not an indefinite retention grant. No live release, live Action table or real-mail detection permission is implied.

Visual: r2 candidate UI remains the previously inspected320/390/1280 prototype. This delta adds no visual route or layout. Live UI remains existing-data Stored Conversations only.
