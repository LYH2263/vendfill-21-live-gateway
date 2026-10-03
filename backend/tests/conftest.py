import os

# 必须在导入 app.* 之前：database.py 在导入期按 DATABASE_URL 建引擎，
# 测试一律用内存 sqlite，绝不连 postgres。
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app  # noqa: F401  确保路由与模型全部注册


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(engine)
    yield TestingSession
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(session_factory):
    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # 不使用 with 触发 lifespan（lifespan 会连真实 postgres）；路由无需 startup。
    c = TestClient(app)
    try:
        yield c
    finally:
        app.dependency_overrides.clear()
