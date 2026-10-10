"""Database for tests: SQLite in a temp folder, or a real server when TEST_DATABASE_URL is set.

    TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/journalizer_test pytest

The schema is dropped and recreated for every test, so never point this at a database with real data.
"""
import os

from sqlalchemy.engine import Engine

from app.db import make_engine, metadata


def fresh_engine(tmp_dir: str) -> Engine:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        return make_engine(f"sqlite:///{tmp_dir}/test.db")
    engine = make_engine(url)
    metadata.drop_all(engine)
    metadata.create_all(engine)
    return engine
