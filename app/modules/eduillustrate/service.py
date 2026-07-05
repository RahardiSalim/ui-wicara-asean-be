from __future__ import annotations

import asyncio
import builtins
from contextlib import contextmanager
from dataclasses import dataclass
import io
import os
from pathlib import Path
import re
import sys
import time
import traceback
from typing import Iterator

from dotenv import load_dotenv

from app.core.config import get_settings, resolve_project_path


class EduIllustrateGenerationError(RuntimeError):
    """Raised when EduIllustrate cannot generate an explanation."""


@dataclass(frozen=True)
class EduIllustrateGenerationResult:
    success: bool
    output_dir: str
    explanation_path: str | None
    doc_path: str | None
    markdown: str
    time_seconds: float
    model: str


_GENERATION_LOCK = asyncio.Lock()


async def generate_problem_explanation(
    *,
    problem: str,
    output_dir: str | Path,
    model: str | None = None,
    max_retries: int | None = None,
    max_scene_concurrency: int | None = None,
    translate_to_chinese: bool | None = None,
) -> EduIllustrateGenerationResult:
    """
    Generate a full EduIllustrate explanation for one problem.

    The generation steps intentionally mirror EduIllustrate/mcp_server.py:
    LiteLLMWrapper -> ExplanationGenerator -> generate_markdown_diagrams ->
    collect solution.md and mp4 paths.
    """

    settings = get_settings()
    problem_text = str(problem or "").strip()
    if not problem_text:
        raise EduIllustrateGenerationError("Problem text must not be empty.")

    repo_dir = resolve_project_path(settings.eduillustrate_repo_dir)
    if not repo_dir.exists():
        raise EduIllustrateGenerationError(
            f"EduIllustrate repo was not found at {repo_dir}."
        )

    resolved_output_dir = _resolve_output_dir(output_dir)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    resolved_model = (model or settings.eduillustrate_model).strip()
    if not resolved_model:
        raise EduIllustrateGenerationError("EduIllustrate model must not be empty.")

    resolved_max_retries = max_retries or settings.eduillustrate_max_retries
    resolved_concurrency = (
        max_scene_concurrency or settings.eduillustrate_max_scene_concurrency
    )
    resolved_translate = (
        settings.eduillustrate_translate_to_chinese
        if translate_to_chinese is None
        else translate_to_chinese
    )

    async with _GENERATION_LOCK:
        return await _run_eduillustrate_generation(
            repo_dir=repo_dir,
            output_dir=resolved_output_dir,
            problem=problem_text,
            model=resolved_model,
            max_retries=resolved_max_retries,
            max_scene_concurrency=resolved_concurrency,
            translate_to_chinese=resolved_translate,
        )


async def _run_eduillustrate_generation(
    *,
    repo_dir: Path,
    output_dir: Path,
    problem: str,
    model: str,
    max_retries: int,
    max_scene_concurrency: int,
    translate_to_chinese: bool,
) -> EduIllustrateGenerationResult:
    _prepare_import_path(repo_dir)
    _load_eduillustrate_env(repo_dir)

    try:
        from generate_explanation import ExplanationGenerator
        from mllm_tools.litellm import LiteLLMWrapper
    except ModuleNotFoundError as exc:
        raise EduIllustrateGenerationError(
            "EduIllustrate dependency is missing. Install EduIllustrate requirements "
            f"for the backend environment. Missing module: {exc.name}."
        ) from exc
    except Exception as exc:
        raise EduIllustrateGenerationError(
            f"EduIllustrate could not be imported: {exc}"
        ) from exc

    try:
        # Copied from EduIllustrate/mcp_server.py with backend path plumbing.
        planner_model = LiteLLMWrapper(
            model_name=model,
            temperature=0.7,
            print_cost=True,
            verbose=False,
            use_langfuse=False,
        )
        scene_model = LiteLLMWrapper(
            model_name=model,
            temperature=0.7,
            print_cost=True,
            verbose=False,
            use_langfuse=False,
        )
        helper_model = LiteLLMWrapper(
            model_name=model,
            temperature=0.7,
            print_cost=True,
            verbose=False,
            use_langfuse=False,
        )

        explanation_generator = ExplanationGenerator(
            planner_model=planner_model,
            scene_model=scene_model,
            helper_model=helper_model,
            output_dir=str(output_dir),
            verbose=False,
            use_rag=False,
            use_context_learning=False,
            use_visual_fix_code=False,
            use_langfuse=False,
            max_scene_concurrency=max_scene_concurrency,
            translate_to_chinese=translate_to_chinese,
        )

        topic = "problem_0_math"
        start_time = time.perf_counter()
        with _utf8_default_text_open():
            await explanation_generator.generate_markdown_diagrams(
                topic,
                problem,
                max_retries=max_retries,
                only_plan=False,
                problem_image=None,
            )
        elapsed = time.perf_counter() - start_time

        topic_dir = output_dir / _topic_file_prefix(topic)
        explanation_path = _find_first_file(topic_dir, "*.mp4")
        doc_path = _find_solution_markdown(topic_dir)
        markdown = doc_path.read_text(encoding="utf-8") if doc_path else ""
        _validate_generated_markdown(
            markdown=markdown,
            topic=topic,
            topic_dir=topic_dir,
        )

        return EduIllustrateGenerationResult(
            success=True,
            output_dir=str(topic_dir),
            explanation_path=str(explanation_path) if explanation_path else None,
            doc_path=str(doc_path) if doc_path else None,
            markdown=markdown,
            time_seconds=elapsed,
            model=model,
        )
    except Exception as exc:
        raise EduIllustrateGenerationError(
            "EduIllustrate generation failed: "
            f"{exc}\n{traceback.format_exc()}"
        ) from exc


def _prepare_import_path(repo_dir: Path) -> None:
    repo_path = str(repo_dir)
    if repo_path not in sys.path:
        sys.path.insert(0, repo_path)


def _load_eduillustrate_env(repo_dir: Path) -> None:
    env_path = repo_dir / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


@contextmanager
def _utf8_default_text_open() -> Iterator[None]:
    original_builtin_open = builtins.open
    original_io_open = io.open
    original_stdout_encoding = getattr(sys.stdout, "encoding", None)
    original_stdout_errors = getattr(sys.stdout, "errors", None)
    original_stderr_encoding = getattr(sys.stderr, "encoding", None)
    original_stderr_errors = getattr(sys.stderr, "errors", None)

    def open_with_utf8_default(file, mode="r", buffering=-1, encoding=None, *args, **kwargs):
        resolved_encoding = encoding
        if "b" not in str(mode) and resolved_encoding is None:
            resolved_encoding = "utf-8"
        return original_builtin_open(
            file,
            mode,
            buffering,
            resolved_encoding,
            *args,
            **kwargs,
        )

    builtins.open = open_with_utf8_default
    io.open = open_with_utf8_default
    _reconfigure_stream(sys.stdout, encoding="utf-8", errors="replace")
    _reconfigure_stream(sys.stderr, encoding="utf-8", errors="replace")
    try:
        yield
    finally:
        builtins.open = original_builtin_open
        io.open = original_io_open
        _reconfigure_stream(
            sys.stdout,
            encoding=original_stdout_encoding,
            errors=original_stdout_errors,
        )
        _reconfigure_stream(
            sys.stderr,
            encoding=original_stderr_encoding,
            errors=original_stderr_errors,
        )


def _reconfigure_stream(
    stream: object,
    *,
    encoding: str | None,
    errors: str | None,
) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure) or encoding is None:
        return
    kwargs = {"encoding": encoding}
    if errors is not None:
        kwargs["errors"] = errors
    try:
        reconfigure(**kwargs)
    except Exception:
        pass


def _resolve_output_dir(output_dir: str | Path) -> Path:
    path = Path(output_dir)
    if path.is_absolute():
        return path.resolve()
    return resolve_project_path(path)


def _topic_file_prefix(topic: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", topic.lower())


def _validate_generated_markdown(*, markdown: str, topic: str, topic_dir: Path) -> None:
    stripped = str(markdown or "").strip()
    header_only = {f"# {topic}", f"# {_topic_file_prefix(topic)}"}
    if stripped and stripped not in header_only:
        return

    outline_path = topic_dir / f"{_topic_file_prefix(topic)}_scene_outline.txt"
    outline_text = ""
    if outline_path.exists():
        outline_text = outline_path.read_text(encoding="utf-8", errors="replace").strip()

    failure_hint = _eduillustrate_failure_hint(outline_text)
    if failure_hint:
        raise EduIllustrateGenerationError(
            "EduIllustrate did not produce a usable explanation. "
            f"{failure_hint}"
        )
    raise EduIllustrateGenerationError(
        "EduIllustrate did not produce a usable explanation document. "
        "The generated Markdown only contains the placeholder title."
    )


def _eduillustrate_failure_hint(text: str) -> str | None:
    if not text:
        return None
    first_line = text.splitlines()[0].strip()
    if "DefaultCredentialsError" in text or "default credentials were not found" in text:
        return (
            "The model call used Google Cloud Vertex credentials, but ADC is not configured. "
            "Use a Gemini API-key model such as gemini/gemini-2.5-flash or configure Google ADC."
        )
    if "APIConnectionError" in text or "Traceback" in text:
        return first_line or "The EduIllustrate planner failed before writing solution content."
    return None


def _find_first_file(directory: Path, pattern: str) -> Path | None:
    if not directory.exists():
        return None
    matches = sorted(directory.glob(pattern), key=os.fspath)
    return matches[0] if matches else None


def _find_solution_markdown(topic_dir: Path) -> Path | None:
    doc_dir = topic_dir / "doc"
    for filename in ("solution.md", "solution_no_diagram.md"):
        candidate = doc_dir / filename
        if candidate.exists():
            return candidate
    return None
