# Reliable Mailbox Sync and Conversation Workspace: implementation boundary

Starting public commit705c588. This work follows the November17 original ChatGPT follow-up and owner instruction to execute the next useful product steps. It is a feature-branch candidate, not deployed or a claim of live mailbox completeness.

Inspected: google_main own-subject session/Google callback/manual-five import, GmailClient list/get/thread, account/thread/email models, additive SQLite ensure_schema and current190-test suite. Existing authentication/OAuth remains unchanged. No frameworks, cloud bill, model exposure or AI activation.

## First slice

Add explicitly imported candidate-only sync models/service and paginated read-only Gmail methods. No existing startup import/route/worker invokes them. Per-account state and per-run receipts preserve scan token, initial history anchor, safe committed history cursor, status and error code. Initial scope is all non-Spam/non-Trash messages, including Inbox/Sent/archive. Listing excludes Spam/Trash; complete thread API responses are label-filtered before persisting new content. Retained previously stored excluded messages are separately marked, not silently purged. Thread snapshots distinguish stored eligible and excluded counts. Scope is explicit, checked before every tick and immutable for a run and not inferred from an opaque query. No entire-mailbox import will run before storage-policy review.

Each tick fetches one bounded provider page and full relevant threads without a write transaction open. Only a fully validated page writes emails/thread metadata plus progress atomically. Compare-and-set revision makes overlapping stale workers fail rather than advance someone else's cursor. Replays upsert account-scoped provider IDs. Existing rule classification stays authoritative unless previously user_confirmed. No AI queue population. OAuth credentials and opaque provider tokens are never logged or copied into status.

Capture profile history before initial enumeration; completion of enumeration moves to catching-up, not synced. History traversal keeps the original start anchor across page tokens. Cursor advances to response historyId only after final history page persisted. New/label changes fetch actual threads. History deletion creates a separate remote-deletion marker, not message-content purging. Missing from a paginated enumeration does not prove deletion.404 on history resets scan with stale/reconcile-required state and no content removal.404 fetching a concurrently removed message is distinct from403/429/5xx.

Candidate has no automatic retention expiry, purge, retries or scheduled worker yet. A safe error code persists, last committed progress stays and manual retry can replay. Backoff, leases and production request/total worker budgets must be reviewed before unattended execution. Per-tick service resource ceilings refuse whole pages/threads, never silently truncate and mark complete. SQLite snapshot/restore/integrity and file permissions need actual deployment qualification before expanded import.

## Product slice after core

Own-subject chronological conversation workspace and explicit partial/stale/failed status, followed by useful body-aware stored search. Then source-backed user-confirmable Action Inbox. No new dashboard experiment and no premise that model JSON validity gives semantic quality.

## Live gates

Retention after Gmail deletion and application content-encryption/storage/backup policy remain unresolved before broadening real-mail storage. Neither ChatGPT advice nor this implementation silently sets owner policy. No live DB/schema/config/deploy/import or source publication occurs in this first slice. Synthetic API/failure/migration tests are implementation checks, not genuine Google sync proof.

Official contracts consulted:
-https://developers.google.com/workspace/gmail/api/guides/sync
-https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list
-https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list
-https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.threads/get

History IDs increase but are not contiguous; expired404 requires full sync. No nextPageToken means all returned history pages consumed. Provider pages are not frozen snapshots. Preserve anchor and replay rather than infer completeness from a counter.

## Completed contract review slice, November17

ChatGPT's next-step review corrected initial allmail default: candidate now excludes Spam/Trash both at message listing and inside fullthread results. This is a proposed conservative default, not owner consent to a large live import. New content in excluded messages is not persisted. Previously retained content is untouched and the remote exclusion marker changes.

26 focused cases now cover new message arriving between initial pages (captured from pre-scan anchor), initial/incremental persistence crash with atomic rollback, stale competing revision, history pagination original anchor, expired-history404 versus thread404, label/body update, user classification preservation, per-account identity, content-preserving deletion, excluded content, malformed provider page/cursor, restore/integrity and idempotent additive schema simulation. Existing full suite is rerun after each material change. This is synthetic implementation evidence only. No deployment/app startup import/OAuth scope change occurs.

Remaining unknowns/limits: actual Google pagination churn/history replay qualification; invalid page token recovery; race where a fetched fullthread becomes unavailable; same-page label/delete ordering when Gmail fullthread snapshot is newer; snapshot staleness following deletion-only histories; whole-tick request/time limits and controlled retry schedule; missing plaintext for HTML-only/attachment-backed bodies; live SQLite backup/restore/perms/free space. No content purging/backfill activation before storage policy and operational qualification. Candidate not production-ready or mailbox-complete.

## Thread reconciliation and token recovery slices

Deletion-only events now refresh affected threads (local account-bound lookup for omitted thread IDs). Snapshot stores current eligible IDs, excluded count, provider thread historyId and available/unavailable. Lower provider version aborts transaction; no commit-order guess.404 records unavailability, not emptiness/deletion of all retained mail. Current fullthread explicit presence supersedes old deletion event. Local retained content is not current provider membership.

Invalid-token recovery accepts only listing/history400 with structured errors all locating pageToken as a parameter and a nonempty current continuation. Google's docs do not guarantee this shape; no guessed reason string or generic400 interpretation. Restart page sequence preserves original anchor and upserts; two durable restarts per generation, third blocks all further ticks until explicit operator resolution. Terminal ready resets count. History404 follows separate new-generation full scan. Thread400/404 never reset enumeration. Auth/transient errors preserve state. No automatic network retries yet.

Provider fixture provenance: error-location fixtures are constructed defensive examples, NOT captured actual Gmail token failures. Official handle-errors documentation shows generic400 shape but no guaranteed pageToken-specific example. No deliberate live malformed request was made just to obtain one. This remains a qualification caveat; ambiguous400 fails safely.

39 focused synthetic tests after token recovery; full-suite result is recorded separately. No live import/deploy/schema changes. Next review concerns actual bounded qualification prerequisites, tick budgets/backoff, missing plaintext and UI current/retained/stale status. Prior remaining-gap notes are historical, superseded for implemented deletion/token behavior only.

## Candidate workspace slice

Explicit app.workspace_candidate:create_app opt-in local entrypoint exposes /workspace and own-subject /workspace/threads/{id}, while deployed app.google_main imports neither candidate service nor tables. Existing /app remains unchanged. Scope-aware metadata/body LIKE search,50matching-record page grouped by thread on that page, chronological up-to500message detail, per-message source links/current-snapshot/retained/unavailable distinctions and statecopy. Exact temporal ordering unknown timestamps last, known timestamps UTC. UI does not load email HTML/links/attachments. Search case matching depends on DB collation. Same conversation may occur across pages; count explicitly page-local. No live provider sync route added.

19synthetic journey tests,7Gmail discovery contract checks and full255/2. Actual local Chromium bodysearch->conversation->back and320/390/1280; actual pixels inbox/thread/empty/error/unavailable/loading inspected. Visual fixtures fake, local auth overridden only by external testharness, not candidate product. GET SQL trace no writes. Screenshot grouping defect fixed and rechecked. No real five-message database copied into local tests; no claim actual owner Gmail candidate UI qualified. Full private deployment untouched.

## Aggregate tick-budget slice

Candidate default60seconds/25Gmail requests, independently configured TickBudget. Every profile/list/history/thread read accounted. Before each request, refuse after aggregate limit; actual HTTP timeout tightened to min(20seconds, remaining deadline) including cached connections. Socket inactivity timeout is not a hard process kill or total-response-duration guarantee: DNS/library/slow streaming and local DB persistence can overrun. No absolute wall-clock termination claim. This remains an operational qualification limit before unattended live activation.

Avoid page-level starvation: page metadata/pending affected threadIDs/deletionIDs/nexttoken/anchor/finalcursor stored, eligible fullthread batches commit underCAS without advancing providerpage or historycursor. Future tick continues pending IDs without repeating listing. Only final pendingthread batch applies deletionmarks and advances page/cursor. Bodies not staged in separate unencrypted payloads. No snapshots beyond already permitted candidateDB. Metadata is private too, absent diagnostics. Crash rolls back data+pendingqueue together. Failure/expiredtoken behavior preserves or resets relevant sequence as before.

Tests cover two-request tick progresses5threadpage across restarted sessions, fakeclock prevents any starts after deadline, dynamictimeoutremaining cap, stagedcommit crash atomicreplay and budgetpaused UI. No retries/backoff/worker added. Same-version discrepancy, actualVMoperational/storage/retention gates remain.

## Retry slice

No sleeping or inline retryloop. Durable retry_attempts/retry_at/retry_exhausted,429/500/502/503/504 only. Candidate max5attempts,30sbase,1hcap, exponential half-to-full jitter with injectedrandom,Retry-After positive seconds/HTTPdate respected. Longer-than-cap Retry-After requires review, never retries earlier than provider directed. Malformed/ambiguous header uses boundedjitter.401/403/400/history404 follow existing nonretry paths. Pendingpage/content/anchors unchanged on failure. Beforeeligible returns without APIread. Exhaustion blocks later network until explicit reviewed operatoraction. Success resets attempts; budgetpause preserves prior retrycount unless validatedpartialprogress commits. This code only schedulesstate; no activeworker wakes orbackgroundloop.

Retrytests deterministicclock/random,429with/withoutheaders/date,all4transient5xx, restartbeforeeligibility,cap/limit,pendingthreadretry,budgetbeforeeligiblework. Mobilecopy condensed and scope/orderdetail moved into native disclosure, no semantics hidden orremoved. Rechecked actual local320/390/1280journey aftercopychange. PendingactualVMbackup/permissions/storagepolicies unchanged.

## Actual one-time recovery qualification,22:14IST

Existing running service account and SQLite location revalidated live.106496bytes,DELETEjournal,noWAL/SHM,0700privateroot/0600DB. Free74,237,734,912of102,888,095,744bytes before. Same-host serviceowner Connection.backup snapshot plus isolatedrestore, integrity/schema/aggregatecount/local-onlylogicalfingerprints equal, livebeforeafterlogicalsame.5messages/5threads/1account/1principal/1OAuthstate. Both temporarycopies/directory removed, absenceverified, no rowvalues/token/content/hashes leftVM. Freeafter74,237,517,824bytes, unexplainedotherfilesystemactivity notassignedcause. No restored-appboot or recurring/offhost/encryption/disasterrecovery qualification. Parent reviewedscript/proof, didnot independentlyrepeat hostoperation. Live app/schema/maildata unchanged.

## Same provider version conflict

Snapshot fingerprint canonicalizes message ordering and labelordering, excludes userconfirmeddecisions, includes providerparsed snapshot content/membership. Equivalent sameversionresponse idempotent; differing membership/content refusesoverwrite and records same_version_conflict without page/cursoradvance, content/pendingpage unchanged. Later explicitcontrolled tick with newerversion canresolve. No activeworker/operatorretryroute.57focused sync tests includingbothmembership/contentconflicts,sessionrestart,newerversionresolution,orderidempotence,pendingpageconflict. Requires actualproviderqualification beforeliveactivation. Initial missingfingerprint legacycandidate snapshot allowsfirstversionedbaseline; this is notlive migration yet.

Additional identified gap to review: expired-history fullreconciliation does not yet prove absence for old storedsnapshotmembers absent from initialenumeration. Neverpurge based merely on paginatedabsence; a reconciliationgeneration/verificationpass is needed before renewedcompletenessclaim. Status should not call absence verified untilimplemented. This remains sourcecandidate, no claim wholemailboxready.

## Reconciliation generations

Each account has durable `generation` and `completed_generation` counters. Each thread snapshot records `observed_generation`. An expired history cursor starts a new generation but leaves the previous completed generation intact. Snapshot observations commit atomically with content and pending progress. Only the terminal listing plus history commit advances the completed generation. Interrupted, failed or conflicting scans do not establish absence.

UI and search compare each snapshot's observed generation with the latest completed generation. Older unseen threads are withheld from current search results. Their retained detail says stale or unverified, not proof of deletion. Retained content and user decisions are preserved. Explicit unavailable state stays unavailable. History replay can update a thread observed on an earlier listing page. There is no bulk purge or absence-based deletion. Account binding and compare-and-set transactions preserve isolation. There is no explicit operator full-rescan route yet; expiry is the current rescan trigger.

The earlier expired-history gap note is historical and is addressed by this candidate slice. Full regression: 279 passed, 2 optional tests skipped. Independent reviewer reproduced the suite and accepted the same-version and generation changes, plus five additional probes. Actual local synthetic stale-detail pixels were inspected at 390px; 390px and 1280px checks found no horizontal overflow. None of this qualifies a live schema change, worker, expanded import or whole-mailbox completeness.

## Next finite live compatibility check

Use the existing five stored message IDs and five thread IDs only. Verify the existing immutable subject binding, then provider profile mailbox identity before any message requests. Request `format=minimal` for both message and thread lookups, with field masks excluding snippets, headers, bodies and payloads. Eleven planned Gmail requests, twelve maximum, no retries or pagination, 20-second per-request timeout and a cooperative 60-second total budget. Count an OAuth refresh separately and allow it only in memory without credential persistence. Abort on identity ambiguity, auth errors, throttling, server errors or timeouts. A fixed-ID 404 is unavailable, not deletion proof.

Compare membership, labels and provider/local counts without requiring equality with an older local snapshot. Export aggregate results only, never raw IDs, addresses, history IDs, tokens, content or fingerprints. Verify local logical database content, row counts and credential ciphertext are unchanged. Inspect the actual client for automatic retries, logging and token persistence before execution. This is compatibility evidence for the existing five records, not full-sync qualification. The candidate UI may be considered separately against those existing records; broader import remains gated on storage and operations decisions.
