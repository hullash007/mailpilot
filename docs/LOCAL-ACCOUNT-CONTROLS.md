# Local account controls candidate

Private source/fake qualification only. Not deployed. No permission to operate on real mailboxes, migrate a real database or publish a retention policy.

## Composition

`app.account_controls_candidate.create_app` joins the accepted explicit protected-auth dependency contract with the stored workspace/actions and new local account settings. It is a separate composition so existing accepted source remains unchanged. No import, refresh, revocation or automatic worker route is exposed. The accepted official Google adapter can be injected; its finite local-signature/transport journey now continues through disconnect and erase.

This candidate does not deploy itself and has no production entrypoint or automatic schema migration. Importing models or starting the app performs no DDL. Disposable test fixtures explicitly create `ControlBase` tables, existing `Base` tables, `ActionBase` tables and the one clock row `(id=1,generation=0)`. Missing schema/clock blocks operations. Existing databases require a separately reviewed explicit migration and storage/runtime qualification, not `create_all` at startup.

## Durable fence

OAuth initiation cannot know a new person's verified Google subject. It snapshots a durable global issuance clock into a separate pending-state row with the exact PKCE verifier and expiry. This is not an email-hint binding. After cryptographic identity verification, callback takes the SQLite writer reservation with a no-op UPDATE on that clock before reading the immutable subject fence or mailbox binding. The same lock is first in disconnect/erase, and is held through local commit. A callback state older than that subject's fence is refused before credentials or a session are created. Unrelated subjects' older states are not invalidated by another subject's operation.

Disconnect/erase advances the clock and subject fence atomically. Explicit new `/login` after the fence can reconnect/recreate only the verified same subject through the trusted identity adapter. No transparent retry upgrades an old state. A new offline grant without a refresh token cannot reconnect an existing disconnected empty-token account. Invalid lifecycle status/generation fails closed. SQLite is the scoped storage implementation; this lock mechanism is not qualified for another database dialect.

Protected session cookies have a distinct name/signing purpose and include subject generation. All workspace/actions authentication checks that durable generation and immutable binding. Local action writes also take the clock writer reservation before their authorization read, so an in-flight decision cannot reinsert after erase. Read requests already in flight may return an earlier snapshot. Already displayed/saved device copies cannot be clawed back.

Only the browser successfully disconnecting is issued a replacement session at the new fence, permitting read access to kept mail and a subsequent erase. All earlier sessions lose ordinary access. Erase invalidates ordinary access from all prior sessions and removes the principal/account. Signed prior cookies survive solely to authenticate a replay of their exact already-recorded operation, not a new action. Session expiry still limits that reconciliation to 12 hours. Ordinary logout deletes this browser cookie only.

## Local outcomes

Disconnect clears encrypted credential bytes, keeps stored imported mail and local decisions, and records a committed operation receipt. Erase transaction deletes this account's action decisions, sync runs, snapshots, remote-state rows, sync state, emails, threads, principal and account in FK order. A full expected account-table inventory is required before erase; unknown tables, missing expected tables or unexpected account-table columns block it. Browser-supplied account selectors have no authority.

The durable subject fence and operation receipts remain after erase. They contain subject, generation, local operation ID/kind/result and remote `not_attempted`, not address, mail, credentials or provider responses. These are still personal metadata. Pending login state/verifiers are not subject-bound before verified callback, remain until consumed or expiry cleanup during a later login, and are refused after their 10 minute expiry. No automatic physical state cleanup or metadata retention expiry is promised. Production retention and recovery policy are not selected here.

Google grant revocation is not attempted. There is no retry queue storing revocation tokens, no provider deletion and no false remote success state. This source stage offers local disconnect and active-database erase only. Remote-revocation semantics and recovery/backup-fence application remain separate, unimplemented stages. This avoids implying that an encrypted queued token has already been removed.

Every exception around a control transaction or durable receipt read produces a sanitized **unconfirmed** outcome, never a claim of unchanged data or successful erase. Replay of the same operation ID by its matching signed subject/session generation checks the durable receipt first, even after successful erase. A receipt describes a past committed operation, not the current account state after a later explicit reconnect. No automatic blind retry. Failure before commit rolls back; lost acknowledgement after commit can be reconciled with the same receipt. No claim about backup/snapshot/log deletion, physical page erasure, Google grant state or production recovery follows.

## Finite evidence

- 24 focused tests: two-account complete-table isolation, pending/verified callback fencing, both transaction orderings including true overlapping writers, exact receipt replay, old-session rejection, restart persistence, missing clock/schema inventory, FK enforcement, CSRF/confirmation, absent provider routes, expiry and distinct cookie purposes.
- Injected failure at each of 9 delete statements preserves all records/fence before retry. Commit exceptions before/after commit and post-commit receipt-read failure all report unconfirmed and reconcile with exact retry.
- Coherent actual official Flow / verifier using an ephemeral fictional RSA JWT and in-memory token/cert/profile transport, exact pending PKCE verifier, encrypted token persistence, session/workspace, local disconnect then erase. Controls add no provider request. Not live Google proof.
- Builder Python 3.12.16 full suite: 446 passed / 2 skipped, same qualified local test setup as prior slice. Actual host/ARM/encrypted runtime remains unproved for this source.
- Local Chromium actual form journey through TestClient transport: account controls -> checkbox disconnect -> renewed read-only local session -> checkbox erase. No external provider traffic or page errors. Account controls rendered at 320, 390 and 1280 CSS pixels, no horizontal overflow; decisive 320/1280 settings and 390 disconnect/erase screenshots visually inspected. No public website changes.

## Remaining release gates

Independent source review; explicit production schema migration; qualified encrypted runtime and key custody; real browser Google flow under the existing tester/mailbox grant; separate remote revocation and recovery semantics; owner-approved actual data/metadata retention/contact/backups; Google production verification and any assessment budget. None is supplied by passing these local tests.
