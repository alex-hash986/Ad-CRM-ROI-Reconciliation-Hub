from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def build_engine(database_url):
    url = make_url(database_url)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    if url.get_backend_name() != "sqlite":
        return create_engine(url, pool_pre_ping=True)

    options = {"connect_args": {"check_same_thread": False}}
    if url.database in {None, "", ":memory:"}:
        options["poolclass"] = StaticPool
    engine = create_engine(url, **options)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys = ON")

    return engine


def sessions(engine):
    return sessionmaker(engine, expire_on_commit=False)


def lock_demo(session: Session) -> None:
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    elif connection.dialect.name == "postgresql":
        connection.execute(text("SELECT pg_advisory_xact_lock(663014528)"))
