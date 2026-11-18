"""Explicit local synthetic composition. No startup DDL or provider sync route.

Use a separately prepared disposable fixture DB. This is not a live entrypoint.
"""
from contextlib import asynccontextmanager
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import http_exception_handler,request_validation_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi import HTTPException
from sqlalchemy import inspect
from .action_candidate import create_app as action_app,shell
from .workspace_candidate import install_routes,_shell
from .google_main import esc
from .services import workspace as sync_views,stored_conversations as stored_views

REQUIRED_SYNC={'mailbox_sync','message_remote_state','thread_snapshots','sync_runs'}

class LocalViews:
    """Never equate absent schema or account sync state with completed sync."""
    def enabled(self,db):
        present=set(inspect(db.connection()).get_table_names())
        existing=present & REQUIRED_SYNC
        if existing and existing!=REQUIRED_SYNC:raise HTTPException(503,'Candidate sync schema is incomplete. Stored data was not changed.')
        return existing==REQUIRED_SYNC
    def sync_status(self,db,account):
        if not self.enabled(db):
            value=stored_views.storage_status(db,account)
            return dict(label=value['label'],detail=value['detail']+' Sync metadata is not installed.',last_success=None)
        return sync_views.sync_status(db,account)
    def search(self,db,account,*args):
        return (sync_views if self.enabled(db) else stored_views).search(db,account,*args)
    def conversation(self,db,account,thread):
        if self.enabled(db):return sync_views.conversation(db,account,thread)
        return dict(stored_views.conversation(db,account,thread),snapshot=None,unobserved=False)

@asynccontextmanager
async def local_lifespan(app):
    # Fixture setup belongs to a separate explicit test/maintenance operation.
    yield


def create_app(*, storage=None):
    app=action_app()
    if storage is not None:
        from .database import get_db
        from .storage_factory import PreparedStorage, StorageUnavailable
        if not isinstance(storage, PreparedStorage):
            raise TypeError('Use an explicitly prepared storage factory.')
        def explicit_db():
            db = storage.session()
            try:
                yield db
            finally:
                db.close()
        app.dependency_overrides[get_db] = explicit_db
        async def storage_error(request, exc):
            # Do not disclose driver errors, paths or key-provider internals.
            response = _shell('Stored workspace unavailable', '<p class="notice">Storage is unavailable. Stored mail was not changed.</p>')
            response.status_code = 503
            return response
        app.add_exception_handler(StorageUnavailable, storage_error)
    app.router.lifespan_context=local_lifespan
    # Remove baseline workspace and the real Gmail import endpoint, not auth.
    app.router.routes[:]=[r for r in app.routes if not getattr(r,'path','').startswith('/workspace') and getattr(r,'path','')!='/app/sync']
    install_routes(app,views_override=LocalViews())
    async def http_error(request:Request,exc):
        if request.url.path.startswith('/actions'):
            response=shell('<h1>Review could not continue</h1><p class="notice">'+esc(exc.detail)+'</p><p><a href="/actions">Reload potential actions</a></p>')
        elif request.url.path.startswith('/workspace'):
            action = ('<a class="button" href="/login">Sign in with Google</a>' if exc.status_code == 401 else
                      '<a href="/workspace">Return to stored conversations</a>')
            response=_shell('Stored workspace unavailable','<p class="notice">'+esc(exc.detail)+'</p><p>Stored mail was not changed.</p><p>'+action+'</p>')
        else:return await http_exception_handler(request,exc)
        response.status_code=exc.status_code;return response
    async def validation_error(request:Request,exc):
        if request.url.path.startswith(('/actions','/workspace')):
            return await http_error(request,HTTPException(422,'Choose a valid stored page, search or local decision.'))
        return await request_validation_exception_handler(request,exc)
    app.add_exception_handler(HTTPException,http_error)
    app.add_exception_handler(StarletteHTTPException,http_error)
    app.add_exception_handler(RequestValidationError,validation_error)
    return app
