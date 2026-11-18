"""Local-only explicit storage composition. No automatic driver/key selection."""
from contextlib import asynccontextmanager
from .storage_factory import prepare_storage


def create_app(*, driver_module, open_existing, key_provider, apply_key,
               validate_runtime, required_schema):
    from .integrated_candidate import create_app as integrated_app
    storage = prepare_storage(driver_module=driver_module, open_existing=open_existing,
                              key_provider=key_provider, apply_key=apply_key,
                              validate_runtime=validate_runtime, required_schema=required_schema)
    app = integrated_app(storage=storage)
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            storage.close()
    app.router.lifespan_context = lifespan
    app.state.prepared_storage = storage
    return app
