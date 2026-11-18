"""Fresh-process dependency-boundary checks, ordinary SQLite only."""
import os,subprocess,sys

def run(code):
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_protected_import_and_composition_never_resolve_default_settings():
    run('''import app.config
app.config.get_settings=lambda:(_ for _ in ()).throw(AssertionError('legacy settings resolved'))
import sqlalchemy
original=sqlalchemy.create_engine
calls=[]
def tracked(*a,**k):calls.append(a);return original(*a,**k)
sqlalchemy.create_engine=tracked
import app.models,app.google_main,app.integrated_candidate,app.protected_candidate
assert calls==[]
import sqlite3
app=app.protected_candidate.create_app(driver_module=sqlite3,open_existing=lambda:(_ for _ in ()).throw(AssertionError('unexpected open')),key_provider=lambda:b'fixture',apply_key=lambda c,k:None,validate_runtime=lambda c:True,required_schema={'fixture':('id',)})
assert calls==[('sqlite://',)]
import asyncio
async def startup():
 async with app.router.lifespan_context(app):pass
asyncio.run(startup())
assert calls==[('sqlite://',)]
''')


def test_legacy_default_resolves_once_on_first_use_and_aliases_match():
    run('''from types import SimpleNamespace
import app.database as d
calls=[]
def settings():calls.append(True);return SimpleNamespace(database_url='sqlite://')
d.get_settings=settings
assert calls==[]
e=d.default_engine();assert calls==[True]
assert d.engine is e and d.default_engine() is e
factory=d.default_sessions();assert d.SessionLocal is factory
with factory() as db:
 assert db.execute(__import__('sqlalchemy').text('SELECT 1')).scalar()==1
assert calls==[True]
e.dispose()
''')


def test_legacy_create_settings_and_startup_schema_are_preserved():
    # Main remains the old explicit scaffold, not the protected entrypoint.
    run('''from types import SimpleNamespace
import app.config
original=app.config.get_settings
app.config.get_settings=lambda:SimpleNamespace(public_origin='')
import app.main as main
import app.database as d
assert d._default_pair is None
engine=__import__('sqlalchemy').create_engine('sqlite://')
main.default_engine=lambda:engine
import asyncio
async def start():
 async with main.lifespan(main.app):
  assert __import__('sqlalchemy').inspect(engine).has_table('emails')
asyncio.run(start());engine.dispose()
''')


def test_concurrent_first_use_publishes_one_pair():
    run('''from concurrent.futures import ThreadPoolExecutor
from threading import Barrier,Lock
import time
import app.database as d
from sqlalchemy import create_engine
barrier=Barrier(32);guard=Lock();count=[0]
def make():
 with guard:count[0]+=1
 time.sleep(.02)
 return create_engine('sqlite://')
d.make_engine=make
assert d._default_pair is None
def worker(i):
 barrier.wait()
 if i%2:factory=d.SessionLocal;engine=d.engine
 else:engine=d.default_engine();factory=d.default_sessions()
 return engine,factory,factory.kw['bind']
with ThreadPoolExecutor(max_workers=32) as pool:results=list(pool.map(worker,range(32)))
assert count==[1]
assert len({id(r[0]) for r in results})==1
assert len({id(r[1]) for r in results})==1
assert all(r[0] is r[2] for r in results)
results[0][0].dispose()
''')
