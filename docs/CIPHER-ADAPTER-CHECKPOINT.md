# Concrete cipher adapter source checkpoint
18November2026,10:02IST. New source-only branch from accepted storage-factorya310313ad5a579f8a508c31c473669b8aee26f76. No SQLCipher import or acquired-code runtime, installation, live access, migration, publication or owner policy choice.

## Finite implementation

app/cipher_adapter.py supplies a concrete adapter to prepare_storage. Driver module and raw32byte key provider remain explicit trusted inputs. No driver discovery or packaged wheel loading. Absolute path, no symlink path/parent, existing regular file required on each physical open; URI mode=rw independently refuses file creation when missing. URI construction percent-encodes question/hash characters. It does not eliminate hostile filesystem substitution/TOCTOU by a writer in the database directory; operational filesystem ownership and isolation remain required.

Key application precedes runtime/schema reads. Raw bytes become a strictly hex-encoded documented SQLCipher PRAGMA key value. This necessarily creates transient Python/SQL strings and is not memory zeroization, trace-proof secret handling or an actual key custody solution. The new seam does not enable SQL echo, add URLs containing key material, or install a trace callback. A trusted driver/debugger that captures SQL can still see key material. Runtime errors are handled by the previously accepted generic safe failure/connection-close seam.

Per physical connection, exact reported SQLCipher4.20.0community,SQLite3.54.1 and openssl provider must match; unavailable/empty/older/other editions refuse. These are selfreported consistency checks, not publisher authenticity. Set temp_storeMEMORY and require readback2. Verify explicit integer user_version from caller, then existing schema tables/columns through the factory. No automatic schema version inference or DDL. The caller must provide the accepted application version; this slice invents no production value.

Disable DBAPI extension loading via enable_load_extension(False), then install an authorizer rejecting SQL load_extension while allowing existing normal queries. It cannot stop a trusted internal caller holding the raw connection from replacing the authorizer/re-enabling the DBAPI capability. No externally supplied SQL endpoint or connection handle is exposed here. No global cipher pragmas, memory-security performance policy, retention/custody selection or SQLCipher defaults altered globally.

Return PreparedStorage to the accepted integrated_candidate.create_app(storage=...) injection point. No modification of the accepted factory/entrypoints/legacy application. Full actual cipher application wiring and runtime validation remain separately reviewable; fake metadata is not encryption.

## Tests and evidence

Five source-only tests with ordinarySQLite-backed fake cipher metadata cover per-new-connection key/runtime/temp/version order, extension disable+authorizer and literal URI escaping; missing/symlink/directory refusal without driver open; empty/wronglength key, missing/old/otheredition cipher,SQLite/provider mismatch/schema version/schema column failures; tempstore/extension-control failure closes connection; invalid explicit parameters and import-without-DBIO/no sqlcipher3 import. SQL load_extension is actually rejected by the ordinarySQLite author's policy in the fake fixture; not an acquired SQLCipher run. Wrong32byte key cryptographic behavior is not tested by mocks and remains only in prior isolated proof.

Builder full363passed/2skipped on Python3.12.16 with official CPython3.12.3 source extension/SQLite3.45.1, same builder as accepted source suites. No clean independent363claim, no host/ARM64 or actual cipher adapter runtime claim. No UI change, no new visual artifact needed.

Actual originalChatGPT accepted-result report sent10:00:40; its bounded advice agrees with explicit existing-file/key-first/runtime/schema/fakeDBAPI scope. Advice is technical review, not execution authority. Parent scoped instruction09:59:58 owns this step. No broad executor/cipher test campaign reopened.

Sources for API decisions: https://www.zetetic.net/sqlcipher/sqlcipher-api/index.html and https://docs.python.org/3/library/sqlite3.html . Imported driver interface support remains to test in a separately reviewed runtime slice. Accepted wheel/source provenance limitations remain unchanged.
