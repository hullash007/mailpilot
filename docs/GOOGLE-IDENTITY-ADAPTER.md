# Concrete explicit Google adapter: offline source checkpoint

18 November 2026. Based on accepted protected-auth206ee51. New adapter module and finite offline tests only; accepted app/session/storage architecture unchanged. Public sample remains independently live and usable. No live private app release, provider traffic, real credentials, new cipher run, consent/config/account change, migration or spend.

## What now joins up

GoogleIdentityAdapter implements the mandatory exchange interface accepted by protected_auth_candidate. It builds a fresh explicit official OAuth Flow, sets the stored PKCE verifier, exchanges the code, runs official google.oauth2.id_token.verify_oauth2_token with exact audience and zero clock skew, enforces issuer/expiry/email verification/subject/nonce and readonly-scope subset, obtains the authenticated Gmail profile, compares its address to the verified identity, then returns credential/claim dictionaries in the existing interface. It does not consume state, select accounts, persist tokens or create sessions. The accepted composition does those after the adapter returns.

The constructor requires explicit signer/flow factory/session factory/verifier. google_identity_adapter(state_signer=...) selects the concrete installed official dependencies without network calls. Test seams are trusted code, not input/config toggles for public visitors. No ambient settings, legacy OAuth builder or GmailClient fallback. Real provider adapter still requires actual qualification before use in the live app.

Each operation gets fresh sessions; no mutable account state is cached. Only exact token POST, certificate GET and Gmail-profile GET URLs are accepted at transport send. Redirects are disabled, TLS verification on, environment proxies/netrc disabled, no retries added, session closed after exchange. The token response hook checks actual raw granted scope before OAuthlib can substitute requested scope. Missing scope, missing identity token, wrong client/token endpoint, profile mismatch or invalid signature fail closed. Response bodies capped at256KiB decoded. Fixed connect3.05s/read-inactivity10s timeouts and a15s elapsed check between streamed chunks are NOT a hard wall-clock/DNS or whole-exchange deadline. Do not describe them as one. Adapter errors are generic with suppressed cause; this does not prove every external library debug-logging policy.

Installed dependencies inspected: google-auth2.62.2, google-auth-oauthlib1.5.0, requests2.34.2, requests-oauthlib2.0.0, oauthlib3.2.0. Official documentation confirms verifier audience/clock-skew interface and certificate fetch behavior. Requests documentation explicitly warns connect/read timeouts are not wall-clock bounds. Neither a code listing nor an offline fixture proves Google's present production keys or transport behavior.

## Evidence

24 new offline tests use the actual installed official Flow and ID-token verifier, ephemeral locally generated RSA signing keys, fictional JWT claims, and a requests BaseAdapter that responds from memory only. No acquired package/runtime or external API request. Success verifies local signature via fake certificate response, actual OAuthlib token parsing and bound profile request. Negative cases cover issuer/audience/expiry/nonce/subject/email/signature/cert/token/profile,302/500 at each of3endpoints, byte limits, missing actual granted scope with no requested-scope fallback, unexpected URL blocked before send, required dependency validation, repeated operation fresh sessions.

One coherent TestClient journey runs actual official Flow authorization with S256 PKCE, persisted random state/verifier, signed fictional token verification through the concrete adapter, callback identity/persistence/session, explicit Fernet ciphertext readback and private workspace200. Legacy settings and ORM crypto property poisoned. The verifier from login is passed unchanged to exchange. This is offline code-path proof, NOT real Google browser/consent/signature-server proof.

Full builder422passed/2skipped, same Python3.12.16 with official3.12.3 SQLite extension/SQLite3.45.1 environment. Independent review pending. No visual UI delta; accepted composition/public sample markup unchanged.

## Remaining gates

Account-scoped disconnect/erase controls, exact token revocation/local erase failure state, runtime storage/key delivery/ARM compatibility, offline conversion/recovery/write reconciliation, real same-browser pilot flow under recovered mailbox authority, truthful approved public policies/Google scope verification and applicable assessment costs remain separate. Do not remove Google Testing/account guards or claim full public Gmail service.

Sources:
https://googleapis.dev/python/google-auth/latest/reference/google.oauth2.id_token.html
https://requests.readthedocs.io/en/latest/user/advanced/?highlight=timeout
https://developers.google.com/workspace/gmail/api/auth/scopes
