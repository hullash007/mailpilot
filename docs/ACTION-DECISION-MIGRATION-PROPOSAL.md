# Candidate decision migration proposal

Design only, 17 November 2026, 22:51 IST. No migration code or live DDL is included. The current Action Inbox remains a disposable synthetic-only prototype. The deployed app still exposes only the existing-data stored-message UI, not Action Inbox.

## Grounded starting point

A one-time read-only target-host inspection at 22:50 used Python3.12.3 / SQLite3.45.1, the actual existing schema and a new SQLAlchemy connection constructed like the app's engine but with a read-only SQLite URL. It read no mail rows, credentials or environment values. Journal mode was DELETE. The new raw connection and matching new SQLAlchemy connection both reported foreign_keys=0. This does not inspect an already-open application's connection, but source app/database.py has no foreign-key connection hook or override. The raw driver used legacy transaction control: isolation_level empty, autocommit=-1, no physical transaction after SELECT. A SQLAlchemy Session transaction alone must therefore not be described as a SQLite snapshot across earlier SELECTs.

Existing emails has primary id and unique(account_id,gmail_message_id), not unique(account_id,id). Existing email/account and thread references are declared NO ACTION. google_principals has unique account_id and subject primary key. No existing message table rebuild or new parent unique index is proposed for this feature.

## Proposed additive record

A new candidate decision table only: id; account_id; source_email_id; detector_version; evidence_fingerprint; status; integer revision; created_at; updated_at. Unique(account_id,source_email_id,detector_version,evidence_fingerprint). Index(account_id,source_email_id). Add CHECK status IN(reviewed,confirmed,dismissed,completed), revision>=1, fixed fingerprint shape and bounded version. Do not persist needs_review: it is a derived presentation state when current evidence has no exact decision but historical decisions exist. Do not copy subject, body, headers, excerpt, address or attachment into this table.

Do not use a lifetime source foreign key: preserving a human decision after local message deletion conflicts with a required source row, while CASCADE would silently erase it. A composite ownership FK also lacks the required parent composite key today. Instead, every read and write enforces source/account ownership in the application and the mutation SQL. Keep source_email_id as immutable historical identity. Never attach orphaned history to a newly created email that happens to reuse a numeric ID: the evidence fingerprint must still match, and an eventual production source identifier must include a non-reusable identity. SQLite rowid reuse and account recreation are explicit migration blockers until a non-reusable source identity or tombstone strategy is selected. Do not silently solve this by storing Gmail IDs in decisions without privacy/deletion design.

An account FK with ON DELETE RESTRICT is desirable but cannot be treated as enforced while writers may have foreign_keys=0. Do not flip PRAGMA globally as a side effect of this feature. Before a real migration, choose and test either a connection-wide FK-enforcement change with compatibility review or explicit database triggers plus application enforcement. Account deletion must be blocked pending a reviewed decision-data deletion flow, not assumed to cascade. This design does not authorize retaining a person's decisions after an account-erasure request.

## Atomic mutation and concurrency

Read the current source only under account ownership and sample-only constraints. The detector derives a source-revision fingerprint from bounded evidence plus complete body and all lowercased header names and values; changing the source or version never inherits confirmation. Existing prototype uses a full body/header equality predicate in the same SQL mutation, not merely an earlier SELECT.

Insert is INSERT SELECT conditional on owned current source/body/header/sample flag and expected revision=0. Uniqueness conflict returns409. Update matches row ID, account, exact version/evidence and expected revision, plus EXISTS current owned source/body/header/sample flag, and increments revision in the same UPDATE. Require exactly one affected row, otherwise409. This closes the validation-to-write race without assuming a physical BEGIN happened at the earlier SELECT. No BEGIN IMMEDIATE change is proposed without actual connection/locking tests. SQLite busy/locked must become a safe retryable conflict or503, never partial success. Concurrent source mutation, concurrent first decision and stale decision updates must be demonstrated on two real disposable connections before acceptance.

GET detection/rendering never creates decisions or updates timestamps. No status is read from message text or URLs. State transitions are explicit human POST actions. Reviewed is not confirmed. Confirmed is a local label, never authority for Gmail, tools, sending, calendar or payment.

## Source lifecycle and history

- Body or any lowercased-header representation changes: old evidence decision stays historical; a new observation needs review.
- Detector version changes: old decisions remain historical; no automatic transfer.
- No current candidate: historical decision remains, with no current evidence claim.
- Missing source: show source unavailable without an excerpt; forbid mutation based on missing source.
- Local source deletion: no automatic decision deletion, no email backup in decision record. Orphan history must still be reachable through an account-filtered history query, not solely through the current message list. This is a gap in the current prototype, which iterates existing messages only.
- Account deletion: no policy assumed. A later explicit reviewed deletion workflow must cover decisions, source references, exports, backups and keys.

History means last status per exact evidence identity plus older evidence decisions, not a complete audit of every intermediate click. A separate append-only audit log is not part of this proposal.

## Browser boundary

Reuse current own_account immutable-subject binding and require_same_origin for POST. Keep Secure/HttpOnly/SameSite session cookies, account predicates, strict status/revision/fingerprint inputs, no GET mutations and escaped evidence. Source links stay local and sample-only. A future real-data feature requires an explicit separate release decision; this proposal retains sample-only rejection. No model, Gmail client, OAuth refresh, import, worker or external action is needed.

## Disposable migration proof plan, not executed

1. Reproduce the inspected schema with synthetic rows in an isolated file. Never copy real mail for this test.
2. Add only the decision table and any separately selected enforcement mechanism in one explicit transaction. Do not import ActionBase into deployed startup.
3. Verify parent schema/rows/credential placeholders and original viewer queries are unchanged; check unique/status/revision constraints and ownership checks.
4. Demonstrate two-connection concurrent insert/update/source-change outcomes and safe lock handling under the actual Python3.12 driver conventions.
5. Verify empty-table rollback leaves the original schema intact. With decisions present, rollback must preserve a private consistent full database backup or reviewed decision-only export before dropping anything. An export is private decision data, not a convenient public artifact. No rollback deletion is allowed merely to make tests pass.
6. Verify history remains reachable when a synthetic source is deleted and no reused source/account ID can inherit decisions. Resolve the non-reusable identity and account deletion gates above before calling this migration ready.

## Current decision

Do not implement or run a live migration. Next candidate-only work, after independent review, is to close the historical-source identity and orphan-history design gaps, then write the finite disposable migration proof. No new permission question is needed for this design. Owner storage/encryption/deletion choices remain gated on a grounded full storage design before expanded email import.
