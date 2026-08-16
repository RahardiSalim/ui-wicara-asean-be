from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import make_url
from sqlalchemy import create_engine, pool

from app.core.config import get_settings
from app.db.base import Base
from app.modules.accounts import models as account_models  # noqa: F401
from app.modules.curriculum import models as curriculum_models  # noqa: F401
from app.modules.curriculum import translation_models as curriculum_translation_models  # noqa: F401
from app.modules.evidence import models as evidence_models  # noqa: F401
from app.modules.inputs import models as input_models  # noqa: F401
from app.modules.learning import models as learning_models  # noqa: F401
from app.modules.learning_goal_resolution import models as goal_resolution_models  # noqa: F401
from app.modules.question_bank import models as question_bank_models  # noqa: F401
from app.modules.review import models as review_models  # noqa: F401
from app.modules.teacher_students import models as teacher_student_models  # noqa: F401
from app.modules.workspaces import models as workspace_models  # noqa: F401

config = context.config
settings = get_settings()

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _engine_connect_args(database_url: str) -> dict[str, object]:
    try:
        url = make_url(database_url)
    except Exception:
        return {}

    backend = url.get_backend_name()
    driver = url.get_driver_name()
    host = (url.host or "").lower()
    if backend == "postgresql" and driver == "psycopg" and "pooler.supabase.com" in host:
        return {"prepare_threshold": None}
    return {}


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        settings.database_url,
        poolclass=pool.NullPool,
        connect_args=_engine_connect_args(settings.database_url),
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
