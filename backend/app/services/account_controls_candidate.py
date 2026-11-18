"""SQLite source/fake lifecycle service. No provider calls or schema mutation.

All exposed credential writers and local control mutations take the clock write
lock FIRST. Holding it until commit serializes callback persistence with controls,
including the absent-principal case. Network exchange must happen before locking.
The clock snapshot permits unknown-subject login without trusting an email hint.
"""
from datetime import datetime, timedelta
from sqlalchemy import update, delete, select, inspect
from ..account_control_models import ControlClock, SubjectFence, PendingLogin, ControlOperation
from ..models import GmailAccount, Email, Thread
from ..google_main import GooglePrincipal
from ..sync_models import MailboxSync, SyncRun, MessageRemoteState, ThreadSnapshot
from ..action_models import ActionDecision
from ..protected_auth_candidate import _link


class ControlsUnavailable(RuntimeError):
    pass


def lock_clock(db):
    # SQLite acquires its durable writer reservation before any lifecycle reads.
    row = db.execute(update(ControlClock).where(ControlClock.id == 1)
        .values(generation=ControlClock.generation).returning(ControlClock.generation)).first()
    if row is None or type(row.generation) is not int or row.generation < 0:
        raise ControlsUnavailable('Account controls are unavailable.')
    return row.generation


def fence_generation(db, subject):
    fence = db.get(SubjectFence, subject)
    if fence and (fence.status not in {'active', 'disconnected', 'erased'}
            or type(fence.generation) is not int or fence.generation < 1):
        raise ControlsUnavailable('Account lifecycle is not qualified.')
    return fence.generation if fence else 0


def issue_login(db, state, verifier):
    generation = lock_clock(db)
    db.execute(delete(PendingLogin).where(PendingLogin.expires_at <= datetime.utcnow()))
    db.add(PendingLogin(state=state, code_verifier=verifier, generation=generation,
        expires_at=datetime.utcnow()+timedelta(seconds=600)))
    db.commit()


def consume_login(db, state):
    row = db.execute(delete(PendingLogin).where(PendingLogin.state == state)
        .returning(PendingLogin.code_verifier, PendingLogin.generation, PendingLogin.expires_at)).first()
    db.commit()
    if row is None or row.expires_at <= datetime.utcnow():
        raise ControlsUnavailable('Restart sign-in.')
    return row.code_verifier, row.generation


def link_identity(db, claims, credentials, cipher, issued_generation):
    current = lock_clock(db)
    subject = claims['sub']
    durable_generation = fence_generation(db, subject)
    if type(issued_generation) is not int or not durable_generation <= issued_generation <= current:
        raise ControlsUnavailable('Restart sign-in.')
    fence = db.get(SubjectFence, subject)
    if fence and issued_generation < fence.generation:
        raise ControlsUnavailable('Restart sign-in.')
    # Only a newly initiated explicit login may reconnect an erased/disconnected
    # subject. This transaction atomically changes status and credentials.
    if fence:
        fence.status = 'active'
        db.flush()
    generation = fence.generation if fence else 0
    # Omitted refresh token after disconnect must not try to decrypt empty bytes.
    binding = db.get(GooglePrincipal, subject)
    account = db.get(GmailAccount, binding.account_id) if binding else None
    if account is not None and not account.credentials_blob and not credentials.get('refresh_token'):
        # _link's legacy refresh-preservation path is inapplicable here. A new
        # offline grant without a refresh token cannot reconnect this composition.
        raise ControlsUnavailable('Restart sign-in and grant offline access.')
    _link(db, claims, credentials, cipher)  # commits credentials + fence under lock
    return generation


def authenticated_account(db, subject, generation, *, write=False):
    if write:
        lock_clock(db)
    if fence_generation(db, subject) != generation:
        raise ControlsUnavailable('Sign in with Google.')
    binding = db.get(GooglePrincipal, subject)
    account = db.get(GmailAccount, binding.account_id) if binding else None
    fence = db.get(SubjectFence, subject)
    if (account is None or account.google_user_id != subject
            or (fence and (fence.status == 'erased'
                or (fence.status == 'disconnected' and account.credentials_blob)))):
        raise ControlsUnavailable('Sign in with Google.')
    return account


# Every account-bearing table in this qualified source composition is enumerated.
# Unknown tables, missing expected tables or columns block ERASE before any delete.
ERASE_MODELS = (ActionDecision, SyncRun, ThreadSnapshot, MessageRemoteState, MailboxSync, Email, Thread)
KNOWN_TABLES = {m.__tablename__ for m in (*ERASE_MODELS, GmailAccount, GooglePrincipal,
    ControlClock, SubjectFence, PendingLogin, ControlOperation)} | {'oauth_states', 'alembic_version'}


def validate_erase_schema(db):
    inspector = inspect(db.connection())
    present = set(inspector.get_table_names())
    if present - KNOWN_TABLES or not {m.__tablename__ for m in ERASE_MODELS} <= present:
        raise ControlsUnavailable('Account data inventory is not qualified.')
    for model in ERASE_MODELS:
        expected = set(model.__table__.columns.keys())
        actual = {col['name'] for col in inspector.get_columns(model.__tablename__)}
        if actual != expected:
            raise ControlsUnavailable('Account data inventory is not qualified.')


def operation_for_actor(db, operation_id, subject, generation, kind):
    op = db.get(ControlOperation, operation_id)
    if op and (op.subject != subject or generation not in {op.session_generation, op.generation} or op.kind != kind):
        raise ControlsUnavailable('Operation does not match this session.')
    return op


def apply_control(db, *, subject, session_generation, kind, operation_id):
    """Commit once. Caller MUST treat every exception as unconfirmed, not unchanged.

    Same actor/session/id/kind replay reads the durable result even after erasure
    invalidates that actor's ordinary session. It cannot authorize a new mutation.
    Retained operation metadata is personal data and is disclosed in the UI/docs.
    """
    if kind not in {'disconnect', 'erase'}:
        raise ControlsUnavailable('Choose a supported account operation.')
    current = lock_clock(db)
    previous = operation_for_actor(db, operation_id, subject, session_generation, kind)
    if previous:
        return previous
    account = authenticated_account(db, subject, session_generation)
    if kind == 'erase':
        validate_erase_schema(db)
    if current < fence_generation(db, subject):
        raise ControlsUnavailable('Account lifecycle is not qualified.')
    next_generation = current + 1
    db.execute(update(ControlClock).where(ControlClock.id == 1).values(generation=next_generation))
    fence = db.get(SubjectFence, subject)
    if fence is None:
        fence = SubjectFence(subject=subject, generation=next_generation, status='disconnected')
        db.add(fence)
    fence.generation = next_generation
    fence.status = 'erased' if kind == 'erase' else 'disconnected'
    if kind == 'disconnect':
        account.credentials_blob = ''
    else:
        for model in ERASE_MODELS:
            db.execute(delete(model).where(model.account_id == account.id))
        db.execute(delete(GooglePrincipal).where(GooglePrincipal.subject == subject,
            GooglePrincipal.account_id == account.id))
        db.execute(delete(GmailAccount).where(GmailAccount.id == account.id,
            GmailAccount.google_user_id == subject))
    op = ControlOperation(id=operation_id, subject=subject, session_generation=session_generation,
        generation=next_generation, kind=kind, local_result='committed', remote_result='not_attempted')
    db.add(op)
    db.commit()
    # No success depends on ORM's remembered value: read the durable receipt.
    db.expire_all()
    confirmed = operation_for_actor(db, operation_id, subject, session_generation, kind)
    if confirmed is None or confirmed.local_result != 'committed':
        raise ControlsUnavailable('Account operation outcome is unconfirmed.')
    return confirmed
