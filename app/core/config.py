from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
_PROJECT_ROOT = _ENV_PATH.parent


class Settings(BaseSettings):
    project_name: str = "WICARA Backend"
    api_v1_prefix: str = "/api/v1"
    database_url: str = Field(
        default="postgresql+psycopg://wicara:wicara@localhost:5432/wicara",
        validation_alias=AliasChoices("WICARA_DATABASE_URL", "DATABASE_URL"),
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias=AliasChoices("WICARA_REDIS_URL", "REDIS_URL"),
    )
    media_job_queue_backend: str = Field(
        default="redis",
        validation_alias=AliasChoices("WICARA_MEDIA_JOB_QUEUE_BACKEND", "MEDIA_JOB_QUEUE_BACKEND"),
    )
    media_jobs_queue_key: str = Field(
        default="wicara:media:jobs",
        validation_alias=AliasChoices("WICARA_MEDIA_JOBS_QUEUE_KEY", "MEDIA_JOBS_QUEUE_KEY"),
    )
    media_job_dequeue_timeout_seconds: int = Field(
        default=5,
        ge=1,
        le=60,
        validation_alias=AliasChoices(
            "WICARA_MEDIA_JOB_DEQUEUE_TIMEOUT_SECONDS",
            "MEDIA_JOB_DEQUEUE_TIMEOUT_SECONDS",
        ),
    )
    media_render_output_dir: str = Field(
        default="tmp/media_renders",
        validation_alias=AliasChoices(
            "WICARA_MEDIA_RENDER_OUTPUT_DIR",
            "MEDIA_RENDER_OUTPUT_DIR",
        ),
    )
    media_render_timeout_seconds: int = Field(
        default=240,
        ge=30,
        le=3600,
        validation_alias=AliasChoices(
            "WICARA_MEDIA_RENDER_TIMEOUT_SECONDS",
            "MEDIA_RENDER_TIMEOUT_SECONDS",
        ),
    )
    media_render_max_attempts: int = Field(
        default=2,
        ge=1,
        le=5,
        validation_alias=AliasChoices(
            "WICARA_MEDIA_RENDER_MAX_ATTEMPTS",
            "MEDIA_RENDER_MAX_ATTEMPTS",
        ),
    )
    media_remotion_project_dir: str = Field(
        default="wicara_remotion_templates",
        validation_alias=AliasChoices(
            "WICARA_MEDIA_REMOTION_PROJECT_DIR",
            "MEDIA_REMOTION_PROJECT_DIR",
        ),
    )
    media_remotion_entry: str = Field(
        default="src/index.ts",
        validation_alias=AliasChoices(
            "WICARA_MEDIA_REMOTION_ENTRY",
            "MEDIA_REMOTION_ENTRY",
        ),
    )
    media_remotion_timeout_seconds: int = Field(
        default=600,
        ge=60,
        le=3600,
        validation_alias=AliasChoices(
            "WICARA_MEDIA_REMOTION_TIMEOUT_SECONDS",
            "MEDIA_REMOTION_TIMEOUT_SECONDS",
        ),
    )
    media_npx_binary: str = Field(
        default="npx",
        validation_alias=AliasChoices(
            "WICARA_MEDIA_NPX_BINARY",
            "MEDIA_NPX_BINARY",
        ),
    )
    media_node_binary: str = Field(
        default="node",
        validation_alias=AliasChoices(
            "WICARA_MEDIA_NODE_BINARY",
            "MEDIA_NODE_BINARY",
        ),
    )
    media_remotion_concurrency: int = Field(
        default=2,
        ge=1,
        le=16,
        validation_alias=AliasChoices(
            "WICARA_MEDIA_REMOTION_CONCURRENCY",
            "MEDIA_REMOTION_CONCURRENCY",
        ),
    )
    media_tts_provider: str = Field(
        default="gtts_voiceover",
        validation_alias=AliasChoices(
            "WICARA_MEDIA_TTS_PROVIDER",
            "MEDIA_TTS_PROVIDER",
        ),
    )
    media_tts_required: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "WICARA_MEDIA_TTS_REQUIRED",
            "MEDIA_TTS_REQUIRED",
        ),
    )
    media_ffmpeg_binary: str = Field(
        default="ffmpeg",
        validation_alias=AliasChoices(
            "WICARA_MEDIA_FFMPEG_BINARY",
            "MEDIA_FFMPEG_BINARY",
        ),
    )
    media_ffprobe_binary: str = Field(
        default="ffprobe",
        validation_alias=AliasChoices(
            "WICARA_MEDIA_FFPROBE_BINARY",
            "MEDIA_FFPROBE_BINARY",
        ),
    )
    media_postprocess_timeout_seconds: int = Field(
        default=180,
        ge=30,
        le=3600,
        validation_alias=AliasChoices(
            "WICARA_MEDIA_POSTPROCESS_TIMEOUT_SECONDS",
            "MEDIA_POSTPROCESS_TIMEOUT_SECONDS",
        ),
    )
    media_postprocess_max_attempts: int = Field(
        default=2,
        ge=1,
        le=5,
        validation_alias=AliasChoices(
            "WICARA_MEDIA_POSTPROCESS_MAX_ATTEMPTS",
            "MEDIA_POSTPROCESS_MAX_ATTEMPTS",
        ),
    )
    media_storage_backend: str = Field(
        default="local",
        validation_alias=AliasChoices(
            "WICARA_MEDIA_STORAGE_BACKEND",
            "MEDIA_STORAGE_BACKEND",
        ),
    )
    media_storage_local_dir: str = Field(
        default="tmp/media_storage",
        validation_alias=AliasChoices(
            "WICARA_MEDIA_STORAGE_LOCAL_DIR",
            "MEDIA_STORAGE_LOCAL_DIR",
        ),
    )
    media_storage_public_base_url: str = Field(
        default="/media-storage",
        validation_alias=AliasChoices(
            "WICARA_MEDIA_STORAGE_PUBLIC_BASE_URL",
            "MEDIA_STORAGE_PUBLIC_BASE_URL",
        ),
    )
    media_storage_upload_timeout_seconds: int = Field(
        default=120,
        ge=10,
        le=1800,
        validation_alias=AliasChoices(
            "WICARA_MEDIA_STORAGE_UPLOAD_TIMEOUT_SECONDS",
            "MEDIA_STORAGE_UPLOAD_TIMEOUT_SECONDS",
        ),
    )
    media_upload_max_attempts: int = Field(
        default=3,
        ge=1,
        le=5,
        validation_alias=AliasChoices(
            "WICARA_MEDIA_UPLOAD_MAX_ATTEMPTS",
            "MEDIA_UPLOAD_MAX_ATTEMPTS",
        ),
    )
    media_storage_supabase_bucket: str = Field(
        default="media-artifacts",
        validation_alias=AliasChoices(
            "WICARA_MEDIA_STORAGE_SUPABASE_BUCKET",
            "MEDIA_STORAGE_SUPABASE_BUCKET",
        ),
    )
    media_duration_policy_mode: str = Field(
        default="soft_fail",
        validation_alias=AliasChoices(
            "WICARA_MEDIA_DURATION_POLICY_MODE",
            "MEDIA_DURATION_POLICY_MODE",
        ),
    )
    media_duration_min_seconds_sd: int = Field(
        default=60,
        ge=0,
        le=3600,
        validation_alias=AliasChoices(
            "WICARA_MEDIA_DURATION_MIN_SECONDS_SD",
            "MEDIA_DURATION_MIN_SECONDS_SD",
        ),
    )
    media_duration_min_seconds_smp: int = Field(
        default=90,
        ge=0,
        le=3600,
        validation_alias=AliasChoices(
            "WICARA_MEDIA_DURATION_MIN_SECONDS_SMP",
            "MEDIA_DURATION_MIN_SECONDS_SMP",
        ),
    )
    media_duration_min_seconds_sma: int = Field(
        default=120,
        ge=0,
        le=3600,
        validation_alias=AliasChoices(
            "WICARA_MEDIA_DURATION_MIN_SECONDS_SMA",
            "MEDIA_DURATION_MIN_SECONDS_SMA",
        ),
    )
    media_duration_min_seconds_default: int = Field(
        default=90,
        ge=0,
        le=3600,
        validation_alias=AliasChoices(
            "WICARA_MEDIA_DURATION_MIN_SECONDS_DEFAULT",
            "MEDIA_DURATION_MIN_SECONDS_DEFAULT",
        ),
    )
    supabase_project_url: str = Field(
        default="https://gwbqhirtkgkghnpahtgt.supabase.co",
        validation_alias=AliasChoices("WICARA_SUPABASE_PROJECT_URL", "SUPABASE_PROJECT_URL"),
    )
    supabase_jwks_url: str = Field(
        default="https://gwbqhirtkgkghnpahtgt.supabase.co/auth/v1/.well-known/jwks.json",
        validation_alias=AliasChoices("WICARA_SUPABASE_JWKS_URL", "SUPABASE_JWKS_URL"),
    )
    supabase_issuer: str = Field(
        default="https://gwbqhirtkgkghnpahtgt.supabase.co/auth/v1",
        validation_alias=AliasChoices("WICARA_SUPABASE_ISSUER", "SUPABASE_ISSUER"),
    )
    supabase_jwt_audience: str = Field(
        default="authenticated",
        validation_alias=AliasChoices("WICARA_SUPABASE_JWT_AUDIENCE", "SUPABASE_JWT_AUDIENCE"),
    )
    supabase_anon_key: str = Field(
        default="",
        validation_alias=AliasChoices("WICARA_SUPABASE_ANON_KEY", "SUPABASE_ANON_KEY"),
    )
    supabase_service_role_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "WICARA_SUPABASE_SERVICE_ROLE_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
        ),
    )
    supabase_jwt_secret: str = Field(
        default="",
        validation_alias=AliasChoices("WICARA_SUPABASE_JWT_SECRET", "SUPABASE_JWT_SECRET"),
    )
    review_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("WICARA_REVIEW_ENABLED", "REVIEW_ENABLED"),
    )
    review_confidence_threshold: float = Field(
        default=0.55,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices(
            "WICARA_REVIEW_CONFIDENCE_THRESHOLD",
            "REVIEW_CONFIDENCE_THRESHOLD",
        ),
    )
    review_sample_pct: int = Field(
        default=10,
        ge=0,
        le=100,
        validation_alias=AliasChoices("WICARA_REVIEW_SAMPLE_PCT", "REVIEW_SAMPLE_PCT"),
    )
    teacher_emails: str = Field(
        default="",
        validation_alias=AliasChoices("WICARA_TEACHER_EMAILS", "TEACHER_EMAILS"),
    )
    workspace_demo_script_mode: bool = Field(
        default=False,
        validation_alias=AliasChoices("WICARA_DEMO_SCRIPT_MODE", "DEMO_SCRIPT_MODE"),
    )

    cors_allow_origins: list[str] = [
        "http://localhost",
        "http://localhost:*",
        "http://127.0.0.1",
        "http://127.0.0.1:*",
    ]

    @property
    def teacher_email_set(self) -> set[str]:
        return {
            email.strip().lower()
            for email in self.teacher_emails.split(",")
            if email.strip()
        }

    model_config = SettingsConfigDict(
        env_prefix="WICARA_",
        env_file=_ENV_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        text = str(value).strip().strip('"').strip("'")
        if text.startswith("DATABASE_URL="):
            text = text.removeprefix("DATABASE_URL=").strip().strip('"').strip("'")
        text = _quote_database_credentials(text)
        if text.startswith("postgresql://"):
            text = text.replace("postgresql://", "postgresql+psycopg://", 1)
        return text

    @field_validator("media_job_queue_backend", mode="before")
    @classmethod
    def normalize_media_job_queue_backend(cls, value: str) -> str:
        text = str(value).strip().lower()
        if text not in {"redis", "noop"}:
            raise ValueError("MEDIA_JOB_QUEUE_BACKEND must be either 'redis' or 'noop'.")
        return text

    @field_validator("media_tts_provider", mode="before")
    @classmethod
    def normalize_media_tts_provider(cls, value: str) -> str:
        text = str(value).strip().lower()
        if text == "edge_tts":
            text = "gtts_voiceover"
        aliases = {
            "gtts": "gtts_voiceover",
            "openai": "gtts_voiceover",
            "openai_tts": "gtts_voiceover",
            "whisper": "gtts_voiceover",
            "openai_whisper": "gtts_voiceover",
            "openai_voiceover": "gtts_voiceover",
        }
        text = aliases.get(text, text)
        if text not in {"gtts_voiceover", "none"}:
            raise ValueError(
                "MEDIA_TTS_PROVIDER must be one of: 'gtts_voiceover' or 'none'."
            )
        return text

    @field_validator("media_duration_policy_mode", mode="before")
    @classmethod
    def normalize_media_duration_policy_mode(cls, value: str) -> str:
        text = str(value).strip().lower()
        if text not in {"off", "soft_fail", "hard_fail"}:
            raise ValueError(
                "MEDIA_DURATION_POLICY_MODE must be one of: off, soft_fail, hard_fail."
            )
        return text

    @field_validator("media_storage_backend", mode="before")
    @classmethod
    def normalize_media_storage_backend(cls, value: str) -> str:
        text = str(value).strip().lower()
        if text not in {"local", "supabase"}:
            raise ValueError("MEDIA_STORAGE_BACKEND must be either 'local' or 'supabase'.")
        return text


def _quote_database_credentials(url: str) -> str:
    scheme_separator = "://"
    if scheme_separator not in url:
        return url
    scheme, remainder = url.split(scheme_separator, 1)
    if "@" not in remainder:
        return url

    credentials, host_and_path = remainder.rsplit("@", 1)
    if ":" not in credentials:
        return url

    username, password = credentials.split(":", 1)
    safe_credentials = f"{quote(username, safe='')}:{quote(password, safe='')}"
    return f"{scheme}{scheme_separator}{safe_credentials}@{host_and_path}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_project_root() -> Path:
    return _PROJECT_ROOT


def resolve_project_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path.resolve()
    return (_PROJECT_ROOT / path).resolve()
