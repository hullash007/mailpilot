"""Explicit synthetic-only bootstrap. Refuses every existing path. Never migrates."""
from pathlib import Path
from datetime import datetime
import os,sqlite3,json
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from .database import Base
from .models import GmailAccount,Thread,Email
from .action_models import ActionBase
from . import sync_models
from .google_main import GooglePrincipal

VERSION=1001
MARKER='mailpilot-synthetic-pilot-v1'

def manifest():
    tables={t.name:tuple(c.name for c in t.columns) for t in Base.metadata.sorted_tables}
    tables.update({t.name:tuple(c.name for c in t.columns) for t in ActionBase.metadata.sorted_tables})
    tables['synthetic_fixture']=('marker',)
    return tables


def bootstrap(destination):
    path=Path(destination)
    if not path.is_absolute() or any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('Choose a new absolute synthetic fixture path.')
    # Atomic exclusive create. Never overwrite, copy or convert an existing DB.
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    engine=None
    try:
        engine=create_engine('sqlite:///'+str(path))
        Base.metadata.create_all(engine);ActionBase.metadata.create_all(engine)
        with engine.begin() as c:
            c.exec_driver_sql('PRAGMA user_version='+str(VERSION))
            c.exec_driver_sql('CREATE TABLE synthetic_fixture(marker TEXT NOT NULL)')
            c.exec_driver_sql('INSERT INTO synthetic_fixture VALUES (?)',(MARKER,))
        with Session(engine) as db:
            for i,label in ((1,'Cedar'),(2,'Harbor')):
                a=GmailAccount(id=i,email_address=label.lower()+'@example.test',google_user_id='fixture-'+str(i));db.add(a);db.flush()
                db.add(GooglePrincipal(subject='fixture-'+str(i),account_id=i))
                t=Thread(account_id=i,gmail_thread_id='fixture-thread-'+str(i),subject='SAMPLE: '+label+' launch checklist');db.add(t);db.flush()
                body='SYNTHETIC FIXTURE ONLY. Please review the '+label+' launch checklist. Could you send the revised proposal?'
                db.add(Email(account_id=i,thread_id=t.id,gmail_message_id='fixture-mail-'+str(i),subject=t.subject,sender='Alex <alex@example.test>',recipients=a.email_address,body_text=body,snippet=body,internal_date=datetime(2026,11,18,9),label_ids=['INBOX'],is_sample=True,category='personal',priority='normal'))
            db.commit()
    except Exception:
        if engine:engine.dispose()
        path.unlink(missing_ok=True)
        raise
    finally:
        if engine:engine.dispose()
    return path


class FakeProvider:
    """In-memory, bounded provider-shaped responses. No network client exists."""
    def __init__(self,account_id):self.account_id=account_id
    def get_profile(self):return {'historyId':'100'}
    def list_message_page(self,page_token=None,page_size=25):
        return {'messages':[{'id':'fakeprovider'+str(self.account_id),'threadId':'fakethread'+str(self.account_id)}]}
    def list_history_page(self,start_history_id,page_token=None):return {'history':[],'historyId':'120'}
    def get_thread_snapshot(self,tid):
        body='SYNTHETIC FAKE-PROVIDER. Please review this provider-origin item. It must not become an Action candidate.'
        return {'history_id':'110','messages':[dict(gmail_message_id='fakeprovider'+str(self.account_id),gmail_thread_id='fakethread'+str(self.account_id),subject='FAKE PROVIDER: '+('Cedar' if self.account_id==1 else 'Harbor')+' update',sender='fixture@example.test',recipients='fixture@example.test',snippet=body,body_text=body,headers={},label_ids=['INBOX'],internal_date=datetime(2026,11,18,10))]}


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description='Create a NEW synthetic-only fixture. Never opens an existing DB.')
    p.add_argument('destination');args=p.parse_args();bootstrap(args.destination)
    print('Created synthetic-only fixture. No real mail or encryption. Schema version '+str(VERSION)+'.')
