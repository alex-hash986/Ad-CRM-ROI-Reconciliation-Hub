from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import DATABASE_URL, FRONTEND, SAMPLE_DATA
from app.database import build_engine, lock_demo, sessions
from app.models import Base
from app.routers.reconciliations import router
from app.services.demo_seed import seed_demo


def create_app(database_url: str = DATABASE_URL) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        engine = build_engine(database_url)
        application.state.sessions = sessions(engine)
        try:
            with application.state.sessions.begin() as session:
                lock_demo(session)
                Base.metadata.create_all(session.connection())
                seed_demo(session)
            yield
        finally:
            engine.dispose()

    application = FastAPI(title="Ad & CRM ROI Reconciliation Hub", lifespan=lifespan)
    application.include_router(router)
    application.mount("/sample_data", StaticFiles(directory=SAMPLE_DATA), name="sample_data")
    if FRONTEND.is_dir():
        application.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
    return application


app = create_app()
