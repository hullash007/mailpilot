# Lazy default storage: protected import boundary
18November2026,10:37IST. Reversible source/local change from accepted synthetic pilot44d4105. Delivery ZIP unchanged. No acquired SQLCipher import/run, host/live/mail/account change, deployment or spending.

## Scope

Protected composition imported google_main and models, which imported database.engine and eagerly called make_engine/get_settings, even when explicit storage overrode get_db and the protected lifespan avoided DDL. This could make an unrelated legacy .env/driver failure prevent explicit protected composition.

- database.default_engine/default_sessions now resolve the legacy settings/engine/sessionmaker on first actual default use, not import of Base/get_db. Explicit compatibility aliases database.engine/SessionLocal resolve through module __getattr__.
- google_main/main import default_engine and call it inside their existing legacy lifespan only. The old legacy entrypoints retain settings validation/DDL behavior; protected and synthetic entrypoints retain noDDL startup and explicit dependency injection.
- No cache reset/dispose lifecycle is added. Defaults remain process-scoped as before. A lock publishes one atomic engine/sessionmaker pair on first use. A32thread simultaneous-first-use probe asserts exactly one construction, shared alias/factory identity and every session bind identical. This fixes the reviewed initial lru_cache race; it is not a live incident claim. Explicit protected factories are separate and do not touch this cache.

This removes an import/startup dependency, not every global settings use. Browser/auth/session/token helpers still read legacy settings when actually used. No claim of complete self-contained protected production authentication. Existing explicit get_settings.cache_clear behavior does not replace an already-resolved default engine, matching previous process-bound selection.

## Evidence

Four new tests run fresh processes: poisoned settings lookup plus tracked create_engine verifies protected model/google/integrated imports construct no engine/read no settings; explicit protected composition constructs only its supplied engine and no extra work at startup; legacy default first-use aliases/session SELECT resolve once; main legacy settings path and DDL lifespan remain functional. Ordinary SQLite only.

Full builder371passed/2skipped, local Python3.12.16 with official CPython3.12.3 source SQLite extension linked SQLite3.45.1, same previous builder setup. Not independent fullsuite, actual ARM64, acquired cipher app runtime or live behavior. Existing synthetic app journey tests pass; no UI changes or new pixels needed.

## Review target

Four changed/new code/test files plus this note. Focus on preserved legacy import/use behavior and no accidental default resolution from protected imports. Changes are isolated in a separate branch; rollback is discarding that branch, no DB/service rollback. Existing product ZIP/source freeze and10:48delivery remain unchanged.
