# MailPilot coherent synthetic pilot
18November2026. Candidate only. Ordinary SQLite behind an explicitly fake cipher DBAPI. NOT encrypted storage. No Google identity, provider, model or real-mail calls. No source publication/deployment.

## What can be tried

One local entrypoint joins accepted stored search/detail, generation-aware sync, sample-only Action decisions/history and the explicit storage/cipher-adapter seams. Every page labels the fixture identities, fake provider, AI-off state and lack of encryption. The fake driver reports synthetic cipher metadata so the accepted concrete adapter can be exercised through the app; it is not evidence of actual protected-app cipher runtime.

1. Choose Cedar fixture. Search checklist and open the stored conversation.
2. Review the sample request, confirm a local action and inspect decision history.
3. From Fake sync status, run exactly one fake tick. It imports one provider-shaped synthetic row, which remains ineligible for Action, and leaves initial catch-up incomplete.
4. Stop and restart the process using the same fixture path. Local decisions and saved sync progress remain. A second explicit tick completes fake history replay. No background worker runs.
5. Switch to Harbor fixture. Cedar mail/decisions cannot be read through Harbor routes. This demonstrates query/account scoping, not real Google authentication; fictional identity switches are intentionally available to anyone using the loopback preview.
6. Invalid stored pagination fails safely; returning to the workspace works. Missing/partial/wrong-version fixture gives a generic503 without creating, upgrading or repairing it.

## Reproduce with existing dependencies only

Use an already provisioned Python environment with backend/requirements.txt dependencies. This runbook does not install anything. Source code still imports existing legacy model/config modules and constructs their lazy engine as before, but the preview removes real auth/import routes, overrides every app DB dependency and replaces the hosted browser boundary. Startup never opens/initializes the legacy database. Run from a new empty working directory with no .env and no service credentials.

From the backend directory, set an absolute source path and a new absolute fixture path. Example shell steps:

```sh
BACKEND="$PWD"
RUN_DIR=$(mktemp -d)
PYTHON=$(command -v python)
cd "$RUN_DIR"
env -i PATH="$PATH" HOME="$RUN_DIR" PYTHONPATH="$BACKEND" "$PYTHON" -m app.synthetic_fixture "$RUN_DIR/synthetic.db"
env -i PATH="$PATH" HOME="$RUN_DIR" PYTHONPATH="$BACKEND" "$PYTHON" -m app.synthetic_pilot "$RUN_DIR/synthetic.db"
```

Open http://127.0.0.1:8091/ on the same machine. Server binds127.0.0.1 only and independently checks loopback client, exact Host and exact Origin on writes. It cannot be used through a remote tunnel without code changes/review. CLI expects an existing reviewed fixture; startup is not bootstrap. For restart, rerun only the second command with the same path. Never point this command at an existing personal/service DB. The fixture marker/version prevents unrelated schemas from being used, but it is not an authorization system or defense against a malicious local actor fabricating a fixture.

## Schema and bootstrap safety

SYNTHETIC-PILOT-SCHEMA.json records fixture-only user_version1001, marker and11required tables/column sets. app.synthetic_fixture.bootstrap creates a new absolute path with atomic O_EXCL and0600, refuses any existing path/symlink and creates only synthetic tables/rows. It never converts an existing DB. No credentials or real tokens seeded. Failure removes only the newly exclusive-created partial fixture, not an existing file. Runtime checks expected version, marker and columns on every physical connection through the accepted factory. No automatic schema repair. A new explicit fixture path is the only reset.

The old legacy factory and real entrypoint are unchanged. One shared origin check now honors an explicit app-state origin when provided; absent it, prior .env behavior remains. This lets inherited local-decision routes obey the preview's exact origin without mutating global settings.

## Evidence

- Full builder367passed/2skipped, same local Python3.12.16 with source-built official CPython3.12.3 SQLite extension/SQLite3.45.1 as previous builder suites. Not an independent full suite, actual ARM64 host or acquired cipher application run.
- App-level tests cover journey/persistence/account isolation/provider-origin Action exclusion, missing fixture/partial schema/no automatic writes, no real routes, exact host/origin, exclusive bootstrap and safe recovery. Socket create_connection replaced with a failing function in the TestClient journey, no real external clients constructed by preview routes. This is source/test evidence, not OS packet tracing.
- Actual local Chromium320/390/1280 search/detail/sampledecision/history/fake-sync journey, real server process stop/start retention, account switch and422recovery passed. Pixels inspected after correcting duplicate workspace navigation and misleading baseline manual-import copy. Screenshots show readable wrapping and no horizontal overflow.
- Existing isolated SQLCipher runtime and actual-adapter compatibility proofs remain accepted separately. This synthetic app uses a fake driver, so complete protected UI+SQLCipher runtime remains unverified.

## Rollback and cleanup

Stop the local preview process. Accepted branches e5496a5/a310313/2a944c9 and live0391767 were not changed. Remove only the explicitly created disposable RUN_DIR after checking it is the preview directory; no automatic cleanup command targets arbitrary paths. To undo this source slice, discard its separate branch/worktree, not the accepted source. No live service rollback needed because nothing was deployed.

## Deferred release gates

No fresh host audit or live release claim. Technical next work can prepare a protected app entrypoint without global settings/lazy-engine dependency, schema-version/offline migration/rollback and actual ARM64 artifact/service-key wiring; actual Google login and real-provider bounded sync remain separate qualification gates. Keep AI off until the separate Hermes inference/client/model-quality gates pass. Before real-mail live pilot, resolve only uncovered owner choices on mail scope/retention and recovery/key custody/backup destination/expiry plus release approval. This file chooses none of those policies. No subscription upgrade/spend authorized.
