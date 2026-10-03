import os

# 必须在导入 app 之前设好：测试全程用内存 SQLite，且不让 lifespan 往库里灌种子
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SEED_ON_EMPTY"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.services.seed import seed_if_empty

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    session = TestingSessionLocal()
    seed_if_empty(session)  # 用真实种子：含超占道 C2（容量24 库存24 在途2）
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    with TestClient(app) as c:
        yield c
