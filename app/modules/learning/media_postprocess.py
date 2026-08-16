from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

try:
    from gtts import gTTS
except Exception:  # pragma: no cover - optional dependency
    gTTS = None

from app.core.config import Settings, get_settings, resolve_project_path
from app.modules.learning.models import MediaArtifact
from app.modules.learning.render_engine import RenderOutput

LANGUAGE_ALIASES: dict[str, str] = {
    "id": "id",
    "id-id": "id",
    "indonesia": "id",
    "bahasa indonesia": "id",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "english": "en",
    "vi": "vi",
    "vi-vn": "vi",
    "vietnamese": "vi",
    "ms": "ms",
    "ms-my": "ms",
    "malay": "ms",
    "ja": "ja",
    "ja-jp": "ja",
    "japanese": "ja",
}


@dataclass(frozen=True)
class MediaPostprocessOutput:
    video_path: str
    relative_video_path: str
    thumbnail_path: str
    relative_thumbnail_path: str
    duration_seconds: int
    transcript: str
    voiceover_script: str
    audio_path: str | None
    relative_audio_path: str | None
    quality_gate: dict[str, Any]
    tts_meta: dict[str, Any]
    ffmpeg_meta: dict[str, Any]


class MediaPostprocessError(RuntimeError):
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


def postprocess_render_output(
    *,
    job_id: UUID,
    artifact: MediaArtifact,
    render_output: RenderOutput,
    settings: Settings | None = None,
) -> MediaPostprocessOutput:
    resolved_settings = settings or get_settings()
    output_root = resolve_project_path(resolved_settings.media_render_output_dir)
    workspace_dir = output_root / str(job_id)
    final_dir = workspace_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)

    source_video_path = Path(render_output.video_path).resolve()
    if not source_video_path.exists():
        raise MediaPostprocessError(
            code="ffmpeg_error",
            message="Rendered video file is missing before post-process.",
            details={"video_path": str(source_video_path)},
        )

    source_duration_raw, source_ffprobe_stdout = _probe_video_duration_seconds(
        video_path=source_video_path,
        settings=resolved_settings,
    )

    voiceover_script = _build_voiceover_script(artifact.spec_json)
    tts_provider = _resolve_effective_tts_provider(
        spec_json=artifact.spec_json,
        settings=resolved_settings,
    )
    tts_language = _resolve_tts_language(
        spec_json=artifact.spec_json,
        fallback_language=artifact.language,
    )
    tts_meta: dict[str, Any] = {
        "provider": tts_provider,
        "configured_provider": resolved_settings.media_tts_provider,
        "required": bool(resolved_settings.media_tts_required),
        "mode": "postprocess_voiceover_overlay",
        "enabled": False,
        "language": tts_language,
        "audio_stream_present": False,
        "warning": None,
    }
    voiceover_audio_path: Path | None = None
    voiceover_audio_duration_raw = 0.0
    voiceover_audio_ffprobe_stdout = ""

    if voiceover_script and tts_provider != "none":
        tts_meta["enabled"] = True
        try:
            voiceover_audio_path, voiceover_provider_meta = _generate_voiceover_audio(
                script=voiceover_script,
                provider=tts_provider,
                language=tts_language,
                output_dir=final_dir,
            )
            tts_meta.update(voiceover_provider_meta)
            voiceover_audio_duration_raw, voiceover_audio_ffprobe_stdout = _probe_media_duration_seconds(
                media_path=voiceover_audio_path,
                settings=resolved_settings,
                step_name="Voiceover audio duration probe",
            )
            tts_meta["audio_duration_seconds_raw"] = voiceover_audio_duration_raw
        except MediaPostprocessError as exc:
            tts_meta["warning"] = exc.message
            tts_meta["error"] = exc.to_dict()
            if resolved_settings.media_tts_required:
                raise

    if resolved_settings.media_tts_provider == "none":
        tts_meta["warning"] = "TTS provider is disabled; video may contain no narration."
    if resolved_settings.media_tts_required and not voiceover_script:
        raise MediaPostprocessError(
            code="tts_error",
            message="TTS is required but no voiceover_script was found in spec_json.",
            details={"template_id": artifact.template_id},
        )
    if resolved_settings.media_tts_required and tts_provider == "none":
        raise MediaPostprocessError(
            code="tts_error",
            message="TTS is required but MEDIA_TTS_PROVIDER is set to 'none'.",
            details={"provider": resolved_settings.media_tts_provider},
        )

    final_video_path = final_dir / "final_video.mp4"
    if voiceover_audio_path is not None:
        ffmpeg_completed, hold_seconds = _finalize_video_with_voiceover_audio(
            source_video_path=source_video_path,
            voiceover_audio_path=voiceover_audio_path,
            source_video_duration_seconds=source_duration_raw,
            voiceover_audio_duration_seconds=voiceover_audio_duration_raw,
            output_video_path=final_video_path,
            settings=resolved_settings,
        )
    else:
        hold_seconds = 0.0
        ffmpeg_completed = _finalize_video_with_ffmpeg(
            source_video_path=source_video_path,
            output_video_path=final_video_path,
            settings=resolved_settings,
        )

    duration_raw, ffprobe_stdout = _probe_video_duration_seconds(
        video_path=final_video_path,
        settings=resolved_settings,
    )
    duration_seconds = max(0, int(round(duration_raw)))
    thumbnail_seek_seconds = _resolve_thumbnail_seek_seconds(duration_raw)

    thumbnail_path = final_dir / "thumbnail.jpg"
    thumbnail_completed, thumbnail_seek_used = _extract_thumbnail_with_ffmpeg(
        video_path=final_video_path,
        thumbnail_path=thumbnail_path,
        seek_seconds=thumbnail_seek_seconds,
        settings=resolved_settings,
    )

    audio_stream_present, ffprobe_audio_stdout = _probe_video_audio_stream(
        video_path=final_video_path,
        settings=resolved_settings,
    )

    if resolved_settings.media_tts_required and not audio_stream_present:
        raise MediaPostprocessError(
            code="tts_error",
            message="TTS is required but rendered video does not contain an audio stream.",
            details={"video_path": str(final_video_path)},
        )
    if tts_meta["enabled"] and not audio_stream_present:
        if voiceover_audio_path is not None:
            raise MediaPostprocessError(
                code="tts_error",
                message="Voiceover audio was generated but final video has no audio stream.",
                details={"video_path": str(final_video_path)},
            )
        tts_meta["warning"] = "Voiceover script exists but output video has no audio stream."
    if tts_meta["enabled"] and voiceover_audio_path is None and not audio_stream_present:
        underlying_error = tts_meta.get("error")
        underlying_message = (
            underlying_error.get("message") if isinstance(underlying_error, dict) else None
        )
        raise MediaPostprocessError(
            code="tts_error",
            message=(
                "Voiceover is enabled but no synthesized audio was attached and final video has "
                "no audio stream."
                + (f" Underlying cause: {underlying_message}" if underlying_message else "")
            ),
            details={"video_path": str(final_video_path), "underlying_error": underlying_error},
        )
    tts_meta["audio_stream_present"] = audio_stream_present
    tts_meta["hold_last_frame_seconds"] = round(float(hold_seconds), 3)

    quality_gate = _evaluate_duration_policy(
        spec_json=artifact.spec_json,
        duration_seconds=duration_seconds,
        settings=resolved_settings,
    )
    if quality_gate["result"] == "failed":
        raise MediaPostprocessError(
            code="duration_policy_error",
            message=str(quality_gate.get("message") or "Duration policy failed."),
            details=quality_gate,
        )

    relative_video_path = _to_relative_path(final_video_path, output_root)
    relative_thumbnail_path = _to_relative_path(thumbnail_path, output_root)
    relative_audio_path = (
        _to_relative_path(voiceover_audio_path, output_root) if voiceover_audio_path is not None else None
    )

    ffmpeg_meta = {
        "finalize_stdout": _tail_text(ffmpeg_completed.stdout),
        "finalize_stderr": _tail_text(ffmpeg_completed.stderr),
        "thumbnail_stdout": _tail_text(thumbnail_completed.stdout),
        "thumbnail_stderr": _tail_text(thumbnail_completed.stderr),
        "source_ffprobe_stdout": _tail_text(source_ffprobe_stdout),
        "ffprobe_stdout": _tail_text(ffprobe_stdout),
        "ffprobe_audio_stdout": _tail_text(ffprobe_audio_stdout),
        "voiceover_audio_ffprobe_stdout": _tail_text(voiceover_audio_ffprobe_stdout),
        "source_duration_seconds_raw": source_duration_raw,
        "duration_seconds_raw": duration_raw,
        "thumbnail_seek_seconds": thumbnail_seek_used,
        "hold_last_frame_seconds": round(float(hold_seconds), 3),
    }

    return MediaPostprocessOutput(
        video_path=str(final_video_path),
        relative_video_path=relative_video_path,
        thumbnail_path=str(thumbnail_path),
        relative_thumbnail_path=relative_thumbnail_path,
        duration_seconds=duration_seconds,
        transcript=voiceover_script,
        voiceover_script=voiceover_script,
        audio_path=str(voiceover_audio_path) if voiceover_audio_path is not None else None,
        relative_audio_path=relative_audio_path,
        quality_gate=quality_gate,
        tts_meta=tts_meta,
        ffmpeg_meta=ffmpeg_meta,
    )


def _finalize_video_with_ffmpeg(
    *,
    source_video_path: Path,
    output_video_path: Path,
    settings: Settings,
) -> subprocess.CompletedProcess[str]:
    output_video_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        settings.media_ffmpeg_binary,
        "-y",
        "-i",
        str(source_video_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        str(output_video_path),
    ]
    return _run_command(
        cmd=cmd,
        timeout_seconds=settings.media_postprocess_timeout_seconds,
        error_code="ffmpeg_error",
        step_name="Video finalization",
    )


def _finalize_video_with_voiceover_audio(
    *,
    source_video_path: Path,
    voiceover_audio_path: Path,
    source_video_duration_seconds: float,
    voiceover_audio_duration_seconds: float,
    output_video_path: Path,
    settings: Settings,
) -> tuple[subprocess.CompletedProcess[str], float]:
    output_video_path.parent.mkdir(parents=True, exist_ok=True)
    hold_seconds = max(0.0, float(voiceover_audio_duration_seconds) - float(source_video_duration_seconds))
    if hold_seconds > 0.02:
        cmd = [
            settings.media_ffmpeg_binary,
            "-y",
            "-i",
            str(source_video_path),
            "-i",
            str(voiceover_audio_path),
            "-filter_complex",
            f"[0:v]tpad=stop_mode=clone:stop_duration={hold_seconds:.3f}[v]",
            "-map",
            "[v]",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_video_path),
        ]
    else:
        cmd = [
            settings.media_ffmpeg_binary,
            "-y",
            "-i",
            str(source_video_path),
            "-i",
            str(voiceover_audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_video_path),
        ]
    completed = _run_command(
        cmd=cmd,
        timeout_seconds=settings.media_postprocess_timeout_seconds,
        error_code="ffmpeg_error",
        step_name="Video/audio merge finalization",
    )
    return completed, hold_seconds


def _generate_voiceover_audio(
    *,
    script: str,
    provider: str,
    language: str,
    output_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    normalized_provider = _normalize_tts_provider(provider)
    cleaned_script = " ".join(str(script or "").split()).strip()
    if not cleaned_script:
        raise MediaPostprocessError(
            code="tts_error",
            message="Voiceover script is empty after normalization.",
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    if normalized_provider == "gtts_voiceover":
        return _generate_gtts_voiceover_audio(
            script=cleaned_script,
            language=language,
            output_dir=output_dir,
        )
    raise MediaPostprocessError(
        code="tts_error",
        message="Unsupported TTS provider for post-process voiceover generation.",
        details={"provider": provider},
    )


def _generate_gtts_voiceover_audio(
    *,
    script: str,
    language: str,
    output_dir: Path,
) -> tuple[Path, dict[str, Any]]:
    if gTTS is None:
        raise MediaPostprocessError(
            code="tts_error",
            message="gTTS package is missing. Install render extras to enable gTTS voiceover.",
        )
    gtts_lang = _voiceover_lang_for_gtts(language)
    output_path = output_dir / "voiceover_gtts.mp3"
    try:
        tts = gTTS(text=script, lang=gtts_lang)
        tts.save(str(output_path))
    except Exception as exc:
        raise MediaPostprocessError(
            code="tts_error",
            message="gTTS voiceover generation failed.",
            details={"language": gtts_lang, "error": repr(exc)},
        ) from exc

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise MediaPostprocessError(
            code="tts_error",
            message="gTTS voiceover generation finished without audio file output.",
            details={"audio_path": str(output_path)},
        )
    return output_path, {
        "provider_used": "gtts_voiceover",
        "gtts_lang": gtts_lang,
        "response_format": "mp3",
    }


def _resolve_effective_tts_provider(*, spec_json: dict[str, Any], settings: Settings) -> str:
    explicit_provider = (
        spec_json.get("tts_provider")
        or spec_json.get("voiceover_provider")
        or ""
    )
    return _normalize_tts_provider(explicit_provider or settings.media_tts_provider)


def _normalize_tts_provider(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    mapping = {
        "gtts": "gtts_voiceover",
        "gtts_voiceover": "gtts_voiceover",
        "openai": "gtts_voiceover",
        "openai_tts": "gtts_voiceover",
        "openai_voiceover": "gtts_voiceover",
        "whisper": "gtts_voiceover",
        "openai_whisper": "gtts_voiceover",
        "none": "none",
    }
    return mapping.get(normalized, "gtts_voiceover")


def _resolve_tts_language(*, spec_json: dict[str, Any], fallback_language: str | None) -> str:
    raw = (
        spec_json.get("language")
        or spec_json.get("locale")
        or spec_json.get("narration_language")
        or fallback_language
        or "en"
    )
    token = str(raw or "").strip().lower()
    if not token:
        return "en"
    token = token.replace("_", "-")
    mapped = LANGUAGE_ALIASES.get(token)
    if mapped:
        return mapped
    if "-" in token:
        root = token.split("-", 1)[0]
        return LANGUAGE_ALIASES.get(root, root)
    return LANGUAGE_ALIASES.get(token, token)


def _voiceover_lang_for_gtts(language: str) -> str:
    normalized = _resolve_tts_language(spec_json={"language": language}, fallback_language="en")
    if normalized in {"id", "en", "vi", "ms", "ja"}:
        return normalized
    return "en"


def _extract_thumbnail_with_ffmpeg(
    *,
    video_path: Path,
    thumbnail_path: Path,
    seek_seconds: float,
    settings: Settings,
) -> tuple[subprocess.CompletedProcess[str], float]:
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    candidates: list[float] = []
    for raw in (seek_seconds, 0.0):
        value = max(0.0, float(raw))
        if not any(abs(value - existing) < 0.001 for existing in candidates):
            candidates.append(value)

    last_error: MediaPostprocessError | None = None
    for candidate in candidates:
        thumbnail_path.unlink(missing_ok=True)
        cmd = [
            settings.media_ffmpeg_binary,
            "-y",
            "-ss",
            f"{candidate:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(thumbnail_path),
        ]
        try:
            completed = _run_command(
                cmd=cmd,
                timeout_seconds=settings.media_postprocess_timeout_seconds,
                error_code="ffmpeg_error",
                step_name=f"Thumbnail extraction (seek={candidate:.3f}s)",
            )
        except MediaPostprocessError as exc:
            last_error = exc
            continue

        if thumbnail_path.exists() and thumbnail_path.stat().st_size > 0:
            return completed, candidate

    if last_error is not None:
        raise last_error
    raise MediaPostprocessError(
        code="ffmpeg_error",
        message="Thumbnail extraction finished without output file.",
        details={"thumbnail_path": str(thumbnail_path)},
    )


def _resolve_thumbnail_seek_seconds(duration_seconds_raw: float, target_seconds: float = 5.0) -> float:
    duration = max(0.0, float(duration_seconds_raw or 0.0))
    if duration <= 0:
        return 0.0
    # Keep a small buffer from the exact video end.
    safe_end = max(0.0, duration - 0.2)
    return max(0.0, min(float(target_seconds), safe_end))


def _probe_video_duration_seconds(
    *,
    video_path: Path,
    settings: Settings,
) -> tuple[float, str]:
    return _probe_media_duration_seconds(
        media_path=video_path,
        settings=settings,
        step_name="Video duration probe",
    )


def _probe_media_duration_seconds(
    *,
    media_path: Path,
    settings: Settings,
    step_name: str,
) -> tuple[float, str]:
    cmd = [
        settings.media_ffprobe_binary,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(media_path),
    ]
    completed = _run_command(
        cmd=cmd,
        timeout_seconds=settings.media_postprocess_timeout_seconds,
        error_code="ffmpeg_error",
        step_name=step_name,
    )
    duration = _parse_duration_from_ffprobe(completed.stdout)
    if duration <= 0:
        raise MediaPostprocessError(
            code="ffmpeg_error",
            message=f"FFprobe returned invalid duration for {step_name.lower()}.",
            details={"stdout": _tail_text(completed.stdout), "media_path": str(media_path)},
        )
    return duration, completed.stdout


def _probe_video_audio_stream(
    *,
    video_path: Path,
    settings: Settings,
) -> tuple[bool, str]:
    cmd = [
        settings.media_ffprobe_binary,
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "json",
        str(video_path),
    ]
    completed = _run_command(
        cmd=cmd,
        timeout_seconds=settings.media_postprocess_timeout_seconds,
        error_code="ffmpeg_error",
        step_name="Audio stream probe",
    )
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaPostprocessError(
            code="ffmpeg_error",
            message="Failed to parse ffprobe audio stream output.",
            details={"stdout": _tail_text(completed.stdout)},
        ) from exc
    streams = payload.get("streams")
    has_audio = isinstance(streams, list) and len(streams) > 0
    return has_audio, completed.stdout


def _parse_duration_from_ffprobe(output: str) -> float:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise MediaPostprocessError(
            code="ffmpeg_error",
            message="Failed to parse ffprobe JSON output.",
            details={"stdout": _tail_text(output)},
        ) from exc

    duration_value = payload.get("format", {}).get("duration")
    try:
        return float(duration_value)
    except (TypeError, ValueError) as exc:
        raise MediaPostprocessError(
            code="ffmpeg_error",
            message="FFprobe output did not contain valid format.duration.",
            details={"stdout": _tail_text(output)},
        ) from exc


def _evaluate_duration_policy(
    *,
    spec_json: dict[str, Any],
    duration_seconds: int,
    settings: Settings,
) -> dict[str, Any]:
    audience_level = str(spec_json.get("audience_level", "")).strip().lower() or "default"
    minimum_seconds = _minimum_duration_seconds_for_audience(
        settings=settings,
        audience_level=audience_level,
    )
    mode = settings.media_duration_policy_mode

    gate = {
        "mode": mode,
        "audience_level": audience_level,
        "minimum_seconds": minimum_seconds,
        "actual_seconds": duration_seconds,
        "passed": duration_seconds >= minimum_seconds,
        "result": "pass",
        "message": "Duration policy passed.",
    }

    if mode == "off":
        gate.update(
            {
                "result": "skipped",
                "message": "Duration policy is disabled.",
            }
        )
        return gate

    if duration_seconds >= minimum_seconds:
        return gate

    if mode == "soft_fail":
        gate.update(
            {
                "result": "warning",
                "message": (
                    "Duration is below minimum policy but allowed by soft_fail mode."
                ),
            }
        )
        return gate

    gate.update(
        {
            "result": "failed",
            "message": "Duration is below minimum policy and blocked by hard_fail mode.",
        }
    )
    return gate


def _minimum_duration_seconds_for_audience(
    *,
    settings: Settings,
    audience_level: str,
) -> int:
    normalized = audience_level.strip().lower()
    if normalized in {"sd", "elementary"}:
        return settings.media_duration_min_seconds_sd
    if normalized in {"smp", "junior", "middle"}:
        return settings.media_duration_min_seconds_smp
    if normalized in {"sma", "high", "senior"}:
        return settings.media_duration_min_seconds_sma
    return settings.media_duration_min_seconds_default


def _build_voiceover_script(spec_json: dict[str, Any]) -> str:
    explicit = _clean_text(spec_json.get("voiceover_script"))
    sections: list[str] = []
    if explicit:
        sections.append(explicit)
    sections.append(_clean_text(spec_json.get("title")))
    sections.append(_clean_text(spec_json.get("subtitle")))
    sections.append(_clean_text(spec_json.get("intro_narration")))

    raw_steps = spec_json.get("steps")
    if isinstance(raw_steps, list):
        for step in raw_steps:
            if not isinstance(step, dict):
                continue
            narration = _clean_text(step.get("narration") or step.get("voiceover"))
            title = _clean_text(step.get("title"))
            body = _clean_text(step.get("body"))
            combined = " ".join(part for part in [narration, title, body] if part)
            if combined:
                sections.append(combined)

    raw_segments = spec_json.get("segments")
    if isinstance(raw_segments, list):
        for segment in raw_segments:
            if isinstance(segment, str):
                sections.append(_clean_text(segment))
                continue
            if not isinstance(segment, dict):
                continue
            narration = _clean_text(segment.get("narration") or segment.get("voiceover"))
            text = _clean_text(segment.get("text"))
            title = _clean_text(segment.get("title"))
            body = _clean_text(segment.get("body"))
            combined = " ".join(part for part in [narration, text, title, body] if part)
            if combined:
                sections.append(combined)

    sections.append(_clean_text(spec_json.get("summary_narration")))
    sections.append(_clean_text(spec_json.get("summary")))
    sections.append(_clean_text(spec_json.get("outro_narration")))

    deduped_sections: list[str] = []
    seen: set[str] = set()
    for section in sections:
        cleaned = _clean_text(section)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped_sections.append(cleaned)

    script = " ".join(deduped_sections)
    return script[:6000]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return text.strip()


def _to_relative_path(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(resolved)


def _run_command(
    *,
    cmd: list[str],
    timeout_seconds: int,
    error_code: str,
    step_name: str,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaPostprocessError(
            code=error_code,
            message=f"{step_name} timed out after {timeout_seconds} seconds.",
            details={
                "command": cmd,
                "timeout_seconds": timeout_seconds,
            },
        ) from exc
    except FileNotFoundError as exc:
        raise MediaPostprocessError(
            code=error_code,
            message=f"Binary for {step_name} was not found.",
            details={"command": cmd},
        ) from exc

    if completed.returncode != 0:
        raise MediaPostprocessError(
            code=error_code,
            message=f"{step_name} command failed.",
            details={
                "command": cmd,
                "return_code": completed.returncode,
                "stdout": _tail_text(completed.stdout),
                "stderr": _tail_text(completed.stderr),
            },
        )
    return completed


def _tail_text(value: str, max_chars: int = 2000) -> str:
    text = (value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]
