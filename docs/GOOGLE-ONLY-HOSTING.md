# Google-only hosting qualification

The hosted pilot uses `app.google_main:app`. The earlier `app.main:app` is the local development/legacy owner-workspace entrypoint and is not the hosted sign-in surface. Do not expose the legacy shared account selector to multiple Google users.

## Access and ownership

The landing page offers Sign in with Google. A user never enters a separate app access key. The server still needs private persistent signing and encryption secrets: `OAUTH_STATE_SECRET`, `TOKEN_ENCRYPTION_KEY` and the Google OAuth client secret. These are server configuration, not user login credentials.

Requests ask for OpenID identity, verified email and `gmail.readonly`. Google's signed identity token is verified against the configured client, issuer, expiry and browser-flow nonce. State is single-use, browser-bound and PKCE-protected. The Gmail profile must match the verified identity's email. Immutable Google `sub`, not an email string or client-supplied account ID, selects a unique stored mailbox binding.

Only subject-derived own inbox, own message detail and bounded manual sync routes are installed in this entrypoint. Legacy shared `/accounts`, `/emails`, `/inbox` and `/dashboard` routes are absent. Old owner keys and old signed cookies cannot unlock it. Secure HttpOnly SameSite=Lax subject sessions expire after 12 hours. Logout clears this browser's cookie; there is no per-session server revocation list. A copied session can remain replayable until expiry; rotating the private signing secret invalidates all sessions.

The current deployment remains restricted to the configured owner, with Google testing mode unchanged. Synthetic two-subject regression tests do not establish two-real-user hosted acceptance. Do not remove the owner allowlist and call this general-release ready. Google verification and restricted Gmail-data requirements need separate review before broader use.

## Stored data and limits

The actual single-owner pilot database is SQLite in a dedicated nonlogin user's private state directory. It is not PostgreSQL. Application code is root-owned; configuration is root 0600 under a 0700 directory; state is 0700 and the database 0600. OAuth tokens use persistent Fernet encryption. Imported mail text, metadata and search fields are not application-encrypted. Provider-managed disk encryption does not prevent authorized root/cloud administrators from reading the mounted files.

No automatic expiry, message deletion UI, continuous sync or backup/restore plan is provided. Logout does not delete stored mail or revoke Google access. The qualification retained five imported real messages. A temporary private consistent database rollback copy used during the auth replacement was removed after acceptance, not promoted into an ongoing backup policy. No claim is made about erasure from provider infrastructure history.

Manual import is capped at five recent messages per request; it can accumulate additional stored messages across separate imports. The own inbox shows at most 50 stored records per page, with a default of 25 and links to older imported records; it is not a complete mailbox. Message text preview is capped at 32000 characters. AI is off for real mail; no send, delete or label-change permission is requested. Synthetic advisory experiments belong to the separate local entrypoint.

## Host boundary

The application binds to loopback behind a browser-trusted HTTPS proxy. The proxy preserves Host, strips untrusted forwarding headers, suppresses access logs, limits body/header/connection sizes and timeouts, and rejects mismatched Host/SNI. App startup refuses incomplete HTTPS/cookie/signing/encryption configuration and AI-on. The exact hosted maintenance preflight additionally pins the intended owner/client/database/origin.

Normal responses use Referrer-Policy:same-origin so browser form POSTs retain Origin. Error responses use no-referrer. Replacing normal-page policy with no-referrer caused real Chrome navigation form POSTs to omit Origin and fail the exact-Origin guard; the guard was not weakened. Transport-level malformed request/SNI rejections happen before configurable route headers and may not carry the route header set.

Certificate renewal is configured through automatic HTTPS TLS-ALPN on 443; a future renewal event has not yet been observed. A single free VM and dynamic DNS have no availability guarantee. Existing private inference listeners/workloads were preserved.

## Actual qualification, November 17 2026

- 170 passing tests, 2 optional integration tests skipped on ARM64 Python 3.12.3. PostgreSQL qualification was not rerun in this deployment.
- Actual key-free Google button, expected-owner Google chooser/identity/consent, verified OIDC callback and read-only Gmail linkage.
- Existing owner data was not claimed merely by matching email. A real dry run used fresh verified identity credentials to fetch all five existing Gmail message IDs and compare thread, internal date, sender, subject, recipients, snippet and body. Account/mail/thread/original ciphertext hashes stayed unchanged; no subject session or binding was issued. OAuth state issuance/consumption still wrote its operational table.
- Controlled exact-owner binding then changed only the intended account's subject and inserted one unique principal. Mail/thread hashes and original ciphertext were byte-identical through this binding. One-shot flags were removed immediately. A later normal verified login refreshed that subject's encrypted tokens as expected.
- Flags-off normal Google login, service-restart session persistence, escaped own message detail, oversized message ID 422, real logout and fresh relogin were checked. Old keys/cookies failed, shared legacy routes 404, unauthenticated own-mail routes 401.
- The replacement service is boot-enabled. The obsolete key-login service was stopped and disabled, not deleted. No extra real-mail import occurred during the Google-only replacement.

This is scoped functional and operational evidence, not a security audit, public multi-user release, model-quality result, restore exercise or availability promise.

## Maintenance migration helper

Legacy unbound records normally refuse 409; an address match never claims them. The exact-owner helper is disabled by default. It requires fresh verified identity and possession of the existing mailbox's exact stored messages, an explicit account/count and an expected configured owner. Its dry-run reports hashes/count only. Atomic compare-and-set plus unique bindings and rollback protect the final change; original ciphertext is preserved during binding. Remove all `MAILPILOT_LEGACY_BIND_*` flags after a reviewed migration. Do not leave them enabled as an account-claim policy.

## Sources

- https://developers.google.com/identity/openid-connect/openid-connect
- https://developers.google.com/identity/protocols/oauth2/policies
- https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification

## Qualified owner inbox browsing revision

The reviewed revision is deployed on the same configured-owner pilot. It replaces the earlier latest-50-only display. `/app` adds literal subject/sender/snippet search (`q`, max200 characters), allowed category/priority filters, `limit`1..50 (default25) and a positive int64 `before` cursor. Only the signed-in subject's bound account is used; account IDs, subject strings or Google IDs in query parameters cannot select another mailbox. No legacy shared account list or API route is added.

Search is not full-mailbox search: only imported stored subject/sender/full snippet are matched, with SQL LIKE metacharacters escaped literally. Bodies and attachments are excluded; case behavior follows database collation. The visible snippet cap remains1000characters and is flagged when reached. HTML entities are decoded once then escaped; no mail HTML or remote images are loaded. Stored record ID order is not sent/received date order. Filters reset the cursor; older-page and first-page links preserve validated filters/limit. Pagination is not a frozen snapshot, and new imports require returning to the first page. Empty results do not establish absent mail in Gmail.

This revision changes source presentation/query handling only. Authentication, OAuth, immutable subject bindings, import/logout behavior, database schema, migration flags and inference configuration are unchanged. Synthetic tests cover two-account isolation, more than50 records, disjoint page traversal, literal wildcards, search-field exclusions, combined filters, int64 validation, escaping/truncation, unauthenticated rejection and no GET-ingestion side effect. The actual ARM64 Python 3.12.3 host passed190 tests with2 optional integration skips, separately reproduced on Python3.10; PostgreSQL was not run. A genuine configured-owner Google login was used before the source swap, with ordinary OAuth state writes and token refresh accounted. That legitimate browser session survived restart. Read-only served search/filter/pagination/detail and desktop/mobile pixels passed without another import. All five mail/thread rows and principal mapping stayed unchanged throughout; complete account/token/state hashes stayed unchanged from after normal login through rollout and all reads. Environment, proxy and service unit hashes stayed identical. Synthetic previews contain fabricated mail; actual inbox screenshots remain private and are not included in this repository.
