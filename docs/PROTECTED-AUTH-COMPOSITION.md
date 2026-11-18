# Explicit protected auth composition, source/fake checkpoint

18 November 2026. Based on accepted primitives49cf3a2. This is a separate opt-in composition, not a new default entrypoint or shipped product ZIP.

## Concrete path

protected_auth_candidate.create_app requires PreparedStorage, AuthConfig, TokenCipher, StateSigner, flow_factory and exchange_identity. No dependency defaults or environment-based selection. AuthConfig validates HTTPS origin, exact callback URI, exact existing Google scopes, separate >=32byte session key and optional existing allowed-email policy. The caller must supply distinct security keys; this code does not deliver, generate, rotate or prove custody of service keys.

Reuses integrated workspace/action views through an internal account dependency override. Removes inherited legacy login/callback/logout/access/inbox/import routes and ambient BrowserBoundary middleware. Installs explicit Host/Origin and security headers. No startup DDL/migration or provider sync/import/refresh route. The legacy modules and their entrypoints remain unchanged.

Login creates a fresh configured flow/state/PKCE verifier, persists state with the existing durable OAuthState helper, signs a Secure HttpOnly SameSite=Lax cookie, and keeps the ten-per-minute app-local throttle. Callback verifies browser binding before atomic DELETE RETURNING consumption; expiry/unknown/replay fail before exchange. Existing state storage is retained verbatim, including raw random state and verifier in the prepared database. No new digest format/schema migration introduced.

The mandatory trusted exchange adapter is responsible for cryptographic Google token verification, bounded transport and Gmail-profile correspondence. It receives explicit config/code/state/verifier and returns credential/claim dictionaries. No concrete production adapter is bundled; arbitrary returned dictionaries are not independent identity proof. This composition rechecks existing issuer/audience/expiry/email verification/subject/nonce and exact readonly credential client/token-endpoint/scope constraints. Fake adapters demonstrate composition only.

Verified subjects resolve explicit GooglePrincipal bindings. Existing legacy mailboxes are never adopted by email. New accounts receive credentials_blob from the explicit cipher, never the ambient ORM credentials property. Same-subject reauthorization retains an omitted refresh token only after decrypting that bound account. Credential replacement uses compare-and-set on original ciphertext, immutable subject and still-current principal binding; a stale concurrent update fails closed rather than overwriting newer credentials. Conflicting inserts fail safely and require fresh sign-in. Provider token refresh itself is not implemented or qualified here.

Successful persistence precedes session issuance. Distinct mailpilot_protected_session cookie and origin-derived signing key isolate protected sessions from legacy entrypoints and other origins even if operators accidentally reuse a session base key. Session subject always re-resolves current principal/account consistency. No browser-provided account selector establishes authority. Session payload retains minimal sub/iat/random nonce shape, signature and TTL semantics. Signout removes cookie; server-side revocation is unchanged/not added.

Errors during login/callback/persistence return generic messages and rollback pending work, with no adapter/key/token detail in response. Existing already-authenticated sessions are not revoked by a failed reauthorization. Storage middleware retains accepted safe storage handling. This is not a blanket claim about every library/server logging configuration.

## Finite evidence

17 new source/fake tests use disposable file-backed ordinary SQLite and fake flow/exchange only. Poisoned settings across relevant imported modules plus poisoned ORM token property remain untouched through construction/startup/login/callback/workspace/decisions. Two clients/accounts prove own workspace visibility, selected-account query cannot redirect authority, other-account source404, signed-in logout isolation, encrypted explicit persistence. Tests cover browser mismatch, expired state, replay and two concurrent callbacks with exactly one exchange/success; wrong issuer/audience/nonce/subject/expiry/email/scopes/client rejected without persistence; sanitized adapter error; same-subject refresh retention, different-subject email collision, corrupt ciphertext preservation; CAS refuses an in-flight newer credential update; every missing dependency and invalid config fail; host/origin refusal; origin/legacy-cookie/session isolation; startup emits no SQL. No complete cross-process/concurrency runtime claim.

Builder398passed/2skipped, same Python3.12.16 with official3.12.3 SQLite extension/SQLite3.45.1 setup. Fullsuite is builder evidence, not independent acceptance. Home mobile390px HTML was rendered and pixels inspected: readable, no horizontal overflow. Reused stored journey markup unchanged.

## Limits and next gate

Not actual Google browser/signature proof, real provider token lifecycle, SQLCipher app runtime, service-key delivery, ARM64 or live auth acceptance. Adapter trust is explicit and still needs a concrete separately qualified implementation. No real credentials/provider/mail/config/account/migration/deploy/spend effects. Frozen product44d4105 ZIP and public/deployed0391767 stay unchanged. Review exact new module/test/checkpoint only before freeze; rollback is discarding branch.
