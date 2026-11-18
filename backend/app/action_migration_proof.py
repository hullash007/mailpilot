"""Disposable synthetic migration proof only. Never imported by a deployed app.

No CLI, automatic setup, live path or PRAGMA mutation. Callers own a disposable
connection. Direct decision DELETE/timestamp tampering are outside this model.
"""
TABLE = 'action_decisions_candidate'
PREFIX = 'action_candidate_guard_'


def statements():
    yield f'''CREATE TABLE {TABLE} (
        id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL,
        source_email_id INTEGER NOT NULL,
        detector_version VARCHAR(32) NOT NULL CHECK(length(detector_version) BETWEEN 1 AND 32),
        evidence_fingerprint VARCHAR(64) NOT NULL CHECK(length(evidence_fingerprint)=64),
        status VARCHAR(16) NOT NULL CHECK(status IN ('reviewed','confirmed','dismissed','completed')),
        revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>=1),
        created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
        CONSTRAINT uq_action_exact_evidence UNIQUE(account_id,source_email_id,detector_version,evidence_fingerprint))'''
    yield f'CREATE INDEX action_candidate_guard_account ON {TABLE}(account_id,id)'
    yield f'''CREATE TRIGGER {PREFIX}decision_insert BEFORE INSERT ON {TABLE}
      WHEN NOT EXISTS (SELECT 1 FROM emails e JOIN gmail_accounts a ON a.id=e.account_id
        JOIN google_principals p ON p.account_id=a.id AND p.subject=a.google_user_id
        WHERE e.id=NEW.source_email_id AND e.account_id=NEW.account_id AND e.is_sample=1)
      BEGIN SELECT RAISE(ABORT,'Synthetic owned source required'); END'''
    yield f'''CREATE TRIGGER {PREFIX}decision_identity BEFORE UPDATE ON {TABLE}
      WHEN NEW.id IS NOT OLD.id OR NEW.account_id IS NOT OLD.account_id
        OR NEW.source_email_id IS NOT OLD.source_email_id
        OR NEW.detector_version IS NOT OLD.detector_version
        OR NEW.evidence_fingerprint IS NOT OLD.evidence_fingerprint
      BEGIN SELECT RAISE(ABORT,'Decision identity immutable'); END'''
    # SQLite REPLACE can delete conflicts without DELETE triggers when recursive
    # triggers are off. Guard INSERT too, including conflicts on natural keys.
    replacements = [
        ('email_replace','emails',f'''EXISTS(SELECT 1 FROM {TABLE} d JOIN emails e ON e.id=d.source_email_id
            WHERE e.id=NEW.id OR (e.account_id=NEW.account_id AND e.gmail_message_id=NEW.gmail_message_id))'''),
        ('account_replace','gmail_accounts',f'''EXISTS(SELECT 1 FROM {TABLE} d JOIN gmail_accounts a ON a.id=d.account_id
            WHERE a.id=NEW.id OR a.email_address=NEW.email_address)'''),
        ('principal_replace','google_principals',f'''EXISTS(SELECT 1 FROM {TABLE} d WHERE d.account_id=NEW.account_id
            OR d.account_id IN (SELECT account_id FROM google_principals WHERE subject=NEW.subject))'''),
    ]
    for name,table,predicate in replacements:
        yield f'''CREATE TRIGGER {PREFIX}{name} BEFORE INSERT ON {table}
          WHEN {predicate} BEGIN SELECT RAISE(ABORT,'Decision history rejects source replacement'); END'''
    guards = [
        ('email_delete','DELETE','emails','', 'source_email_id=OLD.id'),
        ('email_identity','UPDATE','emails','(NEW.id IS NOT OLD.id OR NEW.account_id IS NOT OLD.account_id) AND ', 'source_email_id=OLD.id'),
        ('account_delete','DELETE','gmail_accounts','', 'account_id=OLD.id'),
        ('account_identity','UPDATE','gmail_accounts','(NEW.id IS NOT OLD.id OR NEW.google_user_id IS NOT OLD.google_user_id) AND ', 'account_id=OLD.id'),
        ('principal_delete','DELETE','google_principals','', 'account_id=OLD.account_id'),
        ('principal_identity','UPDATE','google_principals','(NEW.subject IS NOT OLD.subject OR NEW.account_id IS NOT OLD.account_id) AND ', 'account_id=OLD.account_id OR account_id=NEW.account_id'),
    ]
    for name,op,table,condition,predicate in guards:
        yield f'''CREATE TRIGGER {PREFIX}{name} BEFORE {op} ON {table}
          WHEN {condition}EXISTS(SELECT 1 FROM {TABLE} WHERE {predicate})
          BEGIN SELECT RAISE(ABORT,'Decision history protects source identity'); END'''


def install_disposable(conn):
    """Transactional additive installation, refuses a name collision. No live use."""
    if conn.in_transaction:
        raise ValueError('Migration requires its own transaction')
    conn.execute('BEGIN IMMEDIATE')
    try:
        collisions=conn.execute("SELECT name FROM sqlite_master WHERE name=? OR name GLOB ?",(TABLE,PREFIX+'*')).fetchall()
        if collisions:
            raise ValueError('Candidate schema already exists or conflicts')
        required={'emails':{'id','account_id','is_sample'},'gmail_accounts':{'id','google_user_id'},'google_principals':{'subject','account_id'}}
        for table,columns in required.items():
            actual={r[1] for r in conn.execute('PRAGMA table_info('+table+')')}
            if not columns.issubset(actual):
                raise ValueError('Required disposable source schema absent')
        for sql in statements():
            conn.execute(sql)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def rollback_empty_disposable(conn):
    """Refuses populated history, including after export. No destructive override."""
    if conn.in_transaction:
        raise ValueError('Rollback requires its own transaction')
    conn.execute('BEGIN IMMEDIATE')
    try:
        if conn.execute(f'SELECT count(*) FROM {TABLE}').fetchone()[0]:
            raise ValueError('Preserve decision history; populated rollback refused')
        for sql in list(statements())[2:]:
            name=sql.split()[2]
            conn.execute('DROP TRIGGER '+name)
        conn.execute('DROP TABLE '+TABLE)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def history_page(conn, account_id, before_id=None):
    """Internal account parameter must come from authenticated own_account only.

    Decision-first history includes unavailable sources; no copied excerpt.
    Returned source id is NULL for absent, foreign-owned or non-sample sources.
    """
    return conn.execute(f'''SELECT d.id,d.source_email_id,d.status,d.revision,
        d.detector_version,d.evidence_fingerprint,e.id AS available_source_id
        FROM {TABLE} d LEFT JOIN emails e ON e.id=d.source_email_id
        AND e.account_id=d.account_id AND e.is_sample=1
        WHERE d.account_id=? AND (? IS NULL OR d.id<?)
        ORDER BY d.id DESC LIMIT 50''',(account_id,before_id,before_id)).fetchall()
