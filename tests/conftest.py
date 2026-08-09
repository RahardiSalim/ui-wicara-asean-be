import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
os.environ["WICARA_REVIEW_ENABLED"] = "false"

from app.db.base import Base  # noqa: E402
from app.db.session import get_session  # noqa: E402
from app.main import create_app  # noqa: E402
from app.modules.accounts import models as account_models  # noqa: F401,E402
from app.modules.curriculum import models as curriculum_models  # noqa: F401,E402
from app.modules.inputs import models as input_models  # noqa: F401,E402
from app.modules.learning import models as learning_models  # noqa: F401,E402
from app.modules.question_bank import models as question_bank_models  # noqa: F401,E402
from app.modules.teacher_students import models as teacher_student_models  # noqa: F401,E402
from app.modules.workspaces import models as workspace_models  # noqa: F401,E402


@pytest.fixture()
def test_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def db_session(test_engine):
    TestingSessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    with TestingSessionLocal() as session:
        yield session


@pytest.fixture()
def client(test_engine):
    TestingSessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def seeded_curriculum(test_engine):
    from app.modules.curriculum.seed import seed_curriculum

    TestingSessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    with TestingSessionLocal() as session:
        seed_curriculum(session)
