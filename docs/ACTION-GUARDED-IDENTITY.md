# Guarded source identity and reachable history

Candidate-only design follow-up, 17 November2026,22:55IST. This is a proposed synthetic migration contract, not live policy or implemented enforcement. It supersedes the unresolved source-identity alternatives in the first migration proposal only after review.

## Pick: restrict hard deletion while decision history exists

Use existing local(account_id,email.id) as source identity only while additive candidate protection triggers prevent deleting or changing the referenced identity. Do not add a UUID mapping table, provider-ID copy, mail-table rebuild or key dependency. Body/header changes remain allowed and create different evidence. Provider unavailability is not local deletion.

The candidate table persists exact human decision identity and state. Add CHECK constraints and unique identity as proposed. Source/account existence and ownership must also be guarded on decision INSERT and identity-changing UPDATE, not only through the normal application service. Triggers do not depend on PRAGMA foreign_keys. The existing application atomic INSERT SELECT / UPDATE EXISTS remains the primary user-facing source/sample/revision boundary. Database triggers add invariants for accidental direct SQL writers in the disposable proof, not approval to scan real mail.

## Required trigger invariants

- Reject decision INSERT if account absent, source absent, source belongs to another account or source is not a synthetic fixture.
- Reject changing a decision's account/source/version/fingerprint identity. A new observation gets a new row; an owner decision update only changes status/revision/time under CAS.
- Reject DELETE of an email referenced by any decision.
- Reject UPDATE of that email's id or account_id while referenced.
- Reject DELETE of an account with any decision.
- Reject UPDATE of that account's id or google_user_id while referenced. Its binding must not be moved to another principal through google_principals either: reject deletion/rebinding of an applicable principal/account binding while decisions exist, unless a later reviewed account-erasure workflow explicitly removes history first.
- No trigger silently deletes or rewrites a decision, source, account or credentials.

No global PRAGMA change. No trigger installation on the actual host. Trigger DDL belongs only to a separately reviewed additive migration and disposable synthetic file. Trigger names must be reserved and preflight must refuse conflicting existing schema instead of replacing it.

## Deliberate product tradeoff

Retain decisions across content changes, detector changes and provider/source unavailability. Hard local source/account deletion is rejected while history exists. This is a technical candidate restriction, not a policy granting indefinite retention. A user's deletion request must lead to a separate deletion design that explicitly covers decisions, source, backups and keys; it must not be silently blocked forever by these triggers. Before real activation, explain this tradeoff and establish the authorized deletion route.

Exact old evidence can match again if source content and headers return to the same fingerprint. This prototype uses content identity, not a monotonic revision ledger. It does not claim every intermediate version stays distinct. Rowid reuse cannot happen for a referenced source while the protection is installed. After history is removed by a future approved process, there is no prior decision left to inherit.

## Reachable historical view

Add a candidate-only account-filtered history query starting from decisions, with a LEFT JOIN to owned sources, not starting from emails. Every decision remains visible to its owner even if a deliberately malformed legacy fixture lacks its source. Missing source shows Source unavailable and never reconstructs evidence from another account's row. Do not show the old excerpt because none is stored. Mutation is disabled without an exact owned sample source and current observation. Inactive candidates and older evidence remain historical. History is last status per exact evidence, not an append-only log of every click.

Pagination and labels: bound history to50decisions with cursor; explicitly state page scope. Never accept account_id from a browser as authority. Ownership derives from own_account. A missing/deleted principal must be unauthenticated, not a way to expose retained orphan decisions.

## Finite disposable proof to implement after review

Use only synthetic schema and rows under actual Python3.12/SQLite3.45 conventions, foreign_keys=0. Add table/triggers transactionally. Verify protected deletes and identity updates abort, allowed body edits change evidence, repeated detection does not mutate decisions, source/account/principal reuse cannot adopt history, and LEFT JOIN history is account-scoped when source is unavailable. Verify direct decision INSERT cannot bypass ownership/sample rules.

Use two actual connections for first-insert uniqueness, stale revision conflict, concurrent source mutation versus decision mutation and lock timeout handling. Assert one winner or a safe409/503, never both stale writes. Capture original account/email/credential placeholders before/after. Test explicit migration transaction rollback and empty-table rollback. If decisions exist, rollback refuses until a private verified backup/export is preserved; verify that protection in the disposable test. Never drop decision data just to pass a rollback test.

No production migration, FK PRAGMA, model call, Gmail access, source import or live activation is part of this proposal.
