# Legacy owner-workspace access preparation

The current hosted pilot instead uses the Google-only entrypoint with no user access key. See [Google-only hosting qualification](GOOGLE-ONLY-HOSTING.md). This document preserves the earlier `app.main:app` owner-key/local preparation; it is not the current hosted sign-in design.

This source includes an owner-login hardening candidate, not a completed public deployment. Do not import a real inbox until the checks below pass. No automatic ingestion or real-mail AI processing is enabled.

## Access boundary

Set `PUBLIC_ORIGIN` to the exact HTTPS origin (no trailing slash), `COOKIE_SECURE=1`, and a strong random `APP_ACCESS_TOKEN` of at least 32 characters. Keep the access key in a private secret store, not source or a URL. The server refuses startup when these hosted settings are inconsistent. Set `GOOGLE_ALLOWED_EMAIL` to the one approved Google identity. This controls connection, not app login.

Bind one application process to loopback, with a separate HTTPS reverse proxy. Preserve the exact Host header. Disable access logs at both layers: OAuth callback URLs contain temporary codes. Do not forward arbitrary proxy headers to the backend. Do not expose Uvicorn or the inference listeners.

The browser session is HMAC-signed, expires after 12 hours, and contains a timestamp and random nonce, not the access key. Cookies are HttpOnly, Secure in hosted mode, SameSite=Lax. Lax is necessary for the top-level Google callback. Writes authenticated by cookies require an exact matching Origin header; missing/null origins fail closed in hosted mode. Explicit bearer credentials remain supported for scripts. Logout clears this browser's cookie; access-key rotation invalidates all sessions. There is no per-session server revocation list. A copied session remains replayable until its expiry even after logout; rotate the access key to invalidate every session if compromise is suspected.

Login attempts are globally limited to ten per minute in one process. This bounds memory but can temporarily lock the owner out if an outsider uses all attempts. A reverse proxy must also bound connections, request bodies and read/idle times. This is an owner pilot, not a multi-user identity platform. Do not run multiple workers and assume the in-memory limit is shared.

Responses use no-store caching, frame denial, no cross-origin referrers, nosniff, HSTS in secure mode, and a CSP. Existing inline dashboard scripts/styles require unsafe-inline, so CSP is defense in depth, not an XSS proof. Hosted API documentation routes are disabled.

## Data and process isolation before real mail

- Create a separate nonlogin OS user and a new owner-only state directory (0700). Database and private environment/key files must be 0600, not group/world-readable.
- Use a boot-enabled system service with restrictive umask, no-new-privileges, private temporary files, read-only code and an explicit writable state path. Do not reuse synthetic databases.
- Keep Google refresh tokens encrypted with a persistent Fernet key. The key must not be in the database/source/logs, and must survive service restarts securely.
- Stored message text, metadata and search fields are not encrypted by this application. Provider-managed boot-volume encryption is different from application encryption; it does not stop root or authorized cloud administrators. Confirm the actual volume and backup/access policy rather than describing directory permissions as encryption.
- No automatic backup/restore or disaster recovery is provided by this candidate. Do not promise an availability SLA on a single free VM. Preserve existing model workloads.

## Network and OAuth readiness

Use an owned or explicitly authorized hostname, a valid browser-trusted certificate and a supported renewal path. A free dynamic-DNS hostname is a candidate, not an accepted route until Google client configuration and DNS/certificate behavior are checked. No shared wildcard private key is acceptable.

Validate both cloud security-list/network ingress and the host firewall, opening only the necessary web ports. Keep inference loopback-only. Verify unauthenticated requests cannot read mail. Do not treat a successful TLS handshake as authentication testing.

Add the exact HTTPS callback to the existing Google OAuth client, preserving the localhost entry unless deliberately retired. Do not recreate the client. Complete the real same-browser owner-login -> Google consent -> callback -> encrypted credentials -> at-most-five-message read-only import -> stored inbox inspection. A mocked exchange is not this check. Keep real-mail model processing disabled.

## Evidence in this candidate

The test suite checks signed-session expiry/tampering/rotation, cookie flags, exact Origin and Host handling, login rate/body bounds, logout, Google identity restriction, hosted misconfiguration, and authenticated callback behavior.

A separate disposable Chromium test exercised HTTPS owner login, both Lax cookies and a real cross-site browser return from a synthetic identity-provider page, with a mocked token exchange and synthetic profile. It confirms browser-cookie mechanics only. It never contacted Google or read an inbox. The temporary server/database/certificate were deleted after the test.
