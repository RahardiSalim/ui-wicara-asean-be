from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.config import Settings, get_settings, resolve_project_path
from app.modules.learning.remotion_render_engine import (
    RemotionRenderError,
    render_template_scene_remotion,
)
from app.modules.learning.template_registry import TemplateRegistryError, resolve_template_entry


@dataclass(frozen=True)
class RenderOutput:
    video_path: str
    relative_video_path: str
    stdout: str
    stderr: str


class RenderEngineError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


def render_template_scene(
    *,
    job_id: UUID,
    template_path: str,
    scene_class: str,
    spec_json: dict[str, Any],
    language: str | None = None,
    quality_profile: str,
    timeout_seconds: int | None = None,
    settings: Settings | None = None,
    template_id: str | None = None,
    render_engine: str | None = None,
    runtime: dict[str, Any] | None = None,
) -> RenderOutput:
    resolved_settings = settings or get_settings()
    resolved_template_id = _normalize_token(template_id) or _normalize_token(
        spec_json.get("template_id")
    )
    selected_engine = _normalize_token(render_engine)
    runtime_payload = dict(runtime or {})
    resolved_template_path = template_path

    if resolved_template_id:
        try:
            resolved_entry = resolve_template_entry(resolved_template_id)
        except TemplateRegistryError:
            resolved_entry = None
        if resolved_entry is not None:
            if not selected_engine:
                selected_engine = _normalize_token(resolved_entry.entry.render_engine) or ""
            if not runtime_payload:
                runtime_payload = dict(resolved_entry.entry.runtime or {})
            if not resolved_template_path:
                resolved_template_path = resolved_entry.entry.template_path

    if not selected_engine:
        selected_engine = "remotion" if (resolved_template_id or "").startswith("remotion.") else "manim"

    if selected_engine == "remotion":
        try:
            remotion_output = render_template_scene_remotion(
                job_id=job_id,
                template_id=resolved_template_id
                or _normalize_token(spec_json.get("template_id"))
                or "remotion.unknown.v1",
                template_path=resolved_template_path,
                spec_json=spec_json,
                language=language,
                quality_profile=quality_profile,
                runtime=runtime_payload,
                timeout_seconds=timeout_seconds,
                settings=resolved_settings,
            )
        except RemotionRenderError as exc:
            raise RenderEngineError(
                code=exc.code,
                message=exc.message,
                details=exc.details,
            ) from exc

        return RenderOutput(
            video_path=remotion_output.video_path,
            relative_video_path=remotion_output.relative_video_path,
            stdout=remotion_output.stdout,
            stderr=remotion_output.stderr,
        )

    return _render_template_scene_manim(
        job_id=job_id,
        template_path=resolved_template_path,
        scene_class=scene_class,
        spec_json=spec_json,
        language=language,
        quality_profile=quality_profile,
        timeout_seconds=timeout_seconds,
        settings=resolved_settings,
    )


def _render_template_scene_manim(
    *,
    job_id: UUID,
    template_path: str,
    scene_class: str,
    spec_json: dict[str, Any],
    language: str | None = None,
    quality_profile: str,
    timeout_seconds: int | None = None,
    settings: Settings,
) -> RenderOutput:
    output_root = resolve_project_path(settings.media_render_output_dir)
    workspace_dir = output_root / str(job_id)
    render_workdir = workspace_dir / "work"
    media_dir = workspace_dir / "media"
    render_workdir.mkdir(parents=True, exist_ok=True)
    media_dir.mkdir(parents=True, exist_ok=True)

    project_root = Path(__file__).resolve().parent.parent.parent.parent
    template_file = (project_root / template_path).resolve()
    if not template_file.exists():
        raise RenderEngineError(
            code="render_error",
            message=f"Template file does not exist: {template_file}",
            details={"template_path": str(template_file)},
        )
    if not template_file.is_file():
        raise RenderEngineError(
            code="render_error",
            message=f"Template path is not a file: {template_file}",
            details={"template_path": str(template_file)},
        )

    templates_dir = template_file.parent
    core_templates = templates_dir / "core_templates.py"
    base_scene = templates_dir / "base_scene.py"
    for required in (core_templates, base_scene):
        if not required.exists():
            raise RenderEngineError(
                code="render_error",
                message=f"Missing required template runtime file: {required.name}",
                details={"missing_file": str(required)},
            )

    for stale_file in (
        render_workdir / "core_templates.py",
        render_workdir / "base_scene.py",
        render_workdir / "generated_template.py",
        render_workdir / "render_scene.py",
    ):
        stale_file.unlink(missing_ok=True)
    shutil.rmtree(render_workdir / "__pycache__", ignore_errors=True)

    shutil.copyfile(core_templates, render_workdir / "core_templates.py")
    shutil.copyfile(base_scene, render_workdir / "base_scene.py")
    shutil.copyfile(template_file, render_workdir / "generated_template.py")

    # core_templates imports the brand theme, and the theme registers Poppins
    # from assets/fonts beside itself. Both have to land in the workdir or the
    # scene either fails to import or silently renders in Pango's default face.
    theme_module = templates_dir / "wicara_theme.py"
    if theme_module.exists():
        shutil.copyfile(theme_module, render_workdir / "wicara_theme.py")
    theme_assets = templates_dir / "assets"
    if theme_assets.exists():
        shutil.copytree(
            theme_assets,
            render_workdir / "assets",
            dirs_exist_ok=True,
        )

    normalized_language = _normalize_token(language)
    spec_payload = dict(spec_json)
    if normalized_language and not spec_payload.get("language"):
        spec_payload["language"] = normalized_language
        spec_payload.setdefault("locale", normalized_language)
    spec_payload = _inject_runtime_tts_spec(
        spec_payload=spec_payload,
        settings=settings,
    )

    render_scene_path = render_workdir / "render_scene.py"
    spec_repr = repr(spec_payload)
    render_scene_path.write_text(
        (
            "from generated_template import GeneratedTemplate\n\n"
            "class RenderScene(GeneratedTemplate):\n"
            f"    SPEC = {spec_repr}\n"
        ),
        encoding="utf-8",
    )

    timeout = timeout_seconds or settings.media_render_timeout_seconds
    manim_quality_flag = _quality_profile_to_manim_flag(quality_profile)
    cmd = [
        sys.executable,
        "-m",
        "manim",
        manim_quality_flag,
        str(render_scene_path),
        "RenderScene",
        "--media_dir",
        str(media_dir),
        "--disable_caching",
    ]
    render_env = _build_manim_render_env(settings)
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(render_workdir),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=render_env,
        )
    except subprocess.TimeoutExpired as exc:
        raise RenderEngineError(
            code="render_timeout",
            message=f"Manim render timed out after {timeout} seconds.",
            details={
                "timeout_seconds": timeout,
                "template_path": str(template_file),
                "scene_class": scene_class,
            },
        ) from exc

    if completed.returncode != 0:
        raise RenderEngineError(
            code="render_error",
            message="Manim render command failed.",
            details={
                "return_code": completed.returncode,
                "stdout": _tail_text(completed.stdout),
                "stderr": _tail_text(completed.stderr),
                "template_path": str(template_file),
                "scene_class": scene_class,
            },
        )

    video_path = _find_rendered_video(media_dir=media_dir)
    if video_path is None:
        raise RenderEngineError(
            code="render_error",
            message="Manim render finished but output MP4 was not found.",
            details={"media_dir": str(media_dir)},
        )

    relative_video_path = str(video_path.relative_to(output_root))
    return RenderOutput(
        video_path=str(video_path),
        relative_video_path=relative_video_path.replace("\\", "/"),
        stdout=_tail_text(completed.stdout),
        stderr=_tail_text(completed.stderr),
    )


def _inject_runtime_tts_spec(*, spec_payload: dict[str, Any], settings: Settings) -> dict[str, Any]:
    payload = dict(spec_payload)
    explicit_provider = str(payload.get("tts_provider") or payload.get("voiceover_provider") or "").strip()
    provider = (explicit_provider or str(settings.media_tts_provider or "")).strip().lower()
    if provider in {"openai", "openai_tts", "openai_voiceover", "whisper", "openai_whisper"}:
        provider = "gtts_voiceover"
    if not provider:
        provider = "gtts_voiceover"
    if provider not in {"gtts_voiceover", "none"}:
        provider = "gtts_voiceover"
    payload["tts_provider"] = provider
    return payload


def _build_manim_render_env(settings: Settings) -> dict[str, str]:
    env = dict(os.environ)
    env["MEDIA_TTS_PROVIDER"] = settings.media_tts_provider
    return env


def _quality_profile_to_manim_flag(profile: str) -> str:
    normalized = _normalize_token(profile)
    mapping = {
        "low": "-ql",
        "standard": "-qm",
        "medium": "-qm",
        "high": "-qh",
        "ultra": "-qk",
        "l": "-ql",
        "m": "-qm",
        "h": "-qh",
        "k": "-qk",
    }
    if normalized in mapping:
        return mapping[normalized]
    if normalized.startswith("-q") and len(normalized) == 3:
        return normalized
    return "-qm"


def _find_rendered_video(*, media_dir: Path) -> Path | None:
    candidates = sorted(
        media_dir.rglob("RenderScene.mp4"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    any_mp4 = sorted(
        media_dir.rglob("*.mp4"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return any_mp4[0] if any_mp4 else None


def _normalize_token(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _tail_text(value: str, max_chars: int = 2000) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]
