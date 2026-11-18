# Explicit OAuth/token primitives, source-only checkpoint

18 November 2026. Based on accepted lazy-storage c2c8c0a. This is the first preparatory sub-slice, not full protected authentication composition.

## Changed boundary

- OAuthFlowConfig is frozen and requires explicit nonempty client ID, client secret, redirect URI and tuple of scopes. Its repr omits the secret. build_explicit_flow creates a fresh library flow and fresh scope list without consulting settings. It preserves Google endpoint constants and caller-selected state. It is not a hosted-origin policy validator, identity verifier or token exchange implementation.
- TokenCipher takes an explicit Fernet key and preserves existing Fernet JSON serialization. Invalid key errors are generic and do not include parser details. encrypt_tokens/decrypt_tokens remain legacy settings adapters; _fernet is preserved. No key generation, storage or delivery mechanism added.
- StateSigner takes explicit bytes of at least 32 bytes and preserves existing HMAC-SHA256 state signature format and constant-time match. Legacy sign_state/cookie_matches retain their configured or process-random fallback semantics. Explicit signer never falls back.

These services carry no mutable per-account state and select no accounts. They do not establish account authority. No OAuth scopes, identity rules, token format, database schema, account binding or route behavior changed. The legacy flow adapter now also rejects empty/invalid scopes or redirect configuration through the explicit config validator; normal existing settings are unchanged.

## Still open

Protected/integrated composition is NOT wired to these primitives yet. google_main sessions, identity validation, callback helpers and account credential ORM properties still use legacy defaults. No claim of protected runtime independence or missing-dependency startup rejection. Explicit token persistence needs an account-scoped path without changing the global ORM credentials property or accidentally using default encryption. Full composition should be a separately reviewed follow-on, with exact callback, session and token-read/write dependencies covered together. This slice does not prove cross-account callback/refresh persistence isolation beyond existing regression tests.

## Evidence

10 new fake-only tests: poisoned settings on explicit flow/cipher/signer, independent flow/config scope lists, frozen config/secret repr, six invalid OAuth config cases, bidirectional standard Fernet compatibility, different-key/no-plaintext rejection, different signer/state rejection, invalid key rejection, and legacy wrappers including process-key fallback. No network/real credentials/provider calls. Existing suite: 381 passed, 2 skipped, same Python3.12.16 builder with official3.12.3 SQLite extension/SQLite3.45.1 used previously. Not independent review, actual Google browser auth, cipher-app runtime, ARM/service key delivery or live proof.

The immutable delivery ZIP at44d4105 and public/deployed0391767 remain untouched. No acquired-code runs, host/config/account change, deploy or spending. Rollback is discarding the branch; no data/service rollback.
