from __future__ import annotations

import asyncio
import json
import os
import random
import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.language import normalize_language_code
from app.modules.curriculum.kurikulum_merdeka import translate_curriculum_label_to_english
from app.modules.curriculum.models import KnowledgeConcept
from app.modules.ai.client import ai_client
from app.modules.ai.config import get_ai_settings
from app.modules.learning.models import (
    AssessmentOption,
    AssessmentQuestion,
    AssessmentQuestionPack,
    AssessmentSession,
)
from app.modules.pretests.question_validator import QuestionValidator, VALID_QUESTION_TYPES
from app.modules.review.flagger import enqueue_flag

PACK_PROMPT_VERSION = "adaptive_node_pack_v6_flexible_subject_tasks"
FRESH_QUESTION_PROMPT_VERSION = "fresh_assessment_node_batch_v12_graph_path_blueprint"
DEFAULT_PACK_GENERATION_MAX_ATTEMPTS = 4
DEFAULT_PRETEST_LLM_GENERATION_TIMEOUT_SECONDS = 270.0


class AssessmentQuestionGenerationError(ValueError):
    pass


class AdaptivePretestGenerationService:
    def __init__(self, *, validator: QuestionValidator | None = None) -> None:
        self.validator = validator or QuestionValidator()

    def ensure_pack(
        self,
        session: Session,
        *,
        assessment: AssessmentSession,
        concept: KnowledgeConcept,
        language: str = "en",
    ) -> AssessmentQuestionPack:
        existing = session.scalar(
            select(AssessmentQuestionPack)
            .where(
                AssessmentQuestionPack.session_id == assessment.id,
                AssessmentQuestionPack.concept_id == concept.id,
            )
            .options(
                selectinload(AssessmentQuestionPack.questions).selectinload(
                    AssessmentQuestion.options
                )
            )
        )
        if existing is not None:
            return existing

        generated_pack, generation_metadata = self._generate_pack(
            concept=concept,
            language=language,
        )
        self.validator.validate_pack(concept_code=concept.code, pack=generated_pack)
        pack = AssessmentQuestionPack(
            session_id=assessment.id,
            concept_id=concept.id,
            generation_source=generation_metadata["generation_source"],
            llm_provider=generation_metadata["llm_provider"],
            llm_model=generation_metadata["llm_model"],
            prompt_version=PACK_PROMPT_VERSION,
            status="ready",
        )
        session.add(pack)
        session.flush()

        base_sort_order = int(
            session.scalar(
                select(func.count()).select_from(AssessmentQuestion).where(
                    AssessmentQuestion.session_id == assessment.id
                )
            )
            or 0
        )
        concept_title = _concept_prompt_title(concept, language=language)
        for offset, difficulty in enumerate(("easy", "medium", "hard"), start=1):
            payload = generated_pack[difficulty]
            question = AssessmentQuestion(
                session_id=assessment.id,
                pack_id=pack.id,
                concept_id=concept.id,
                step_label="Adaptive Pretest",
                topic=concept_title,
                prompt=payload["prompt"],
                helper_text=payload.get("helper_text", ""),
                difficulty_label=difficulty,
                sort_order=base_sort_order + offset,
                metadata_json={
                    "source": "adaptive_generated_pack",
                    "concept_code": concept.code,
                    "correct_option_key": _correct_label(payload),
                    "explanation": payload["explanation"],
                },
                generation_source=generation_metadata["generation_source"],
                generation_prompt_version=PACK_PROMPT_VERSION,
                llm_metadata_json=generation_metadata,
                expected_reasoning=payload["expected_reasoning"],
                rubric_json=payload.get("rubric", {}),
            )
            session.add(question)
            session.flush()
            enqueue_flag(
                artifact_type="question",
                artifact_id=question.id,
                signals={"generation_source": generation_metadata["generation_source"]},
                concept_id=concept.id,
                summary=question.prompt[:160],
            )
            for sort_order, option in enumerate(payload["options"], start=1):
                session.add(
                    AssessmentOption(
                        question_id=question.id,
                        option_key=option["label"],
                        label=option["label"],
                        text=option["text"],
                        is_correct=bool(option["is_correct"]),
                        sort_order=sort_order,
                    )
                )
        session.flush()
        return session.scalar(
            select(AssessmentQuestionPack)
            .where(AssessmentQuestionPack.id == pack.id)
            .options(
                selectinload(AssessmentQuestionPack.questions).selectinload(
                    AssessmentQuestion.options
                )
            )
        ) or pack

    @staticmethod
    def question_for_difficulty(
        pack: AssessmentQuestionPack,
        *,
        difficulty: str,
    ) -> AssessmentQuestion:
        for question in pack.questions:
            if question.difficulty_label.lower() == difficulty:
                return question
        raise LookupError(f"Pack is missing {difficulty} question.")

    @staticmethod
    def pack_state(pack: AssessmentQuestionPack) -> dict[str, object]:
        return {
            "pack_id": str(pack.id),
            "questions": {
                question.difficulty_label.lower(): str(question.id)
                for question in pack.questions
            },
        }

    def create_fresh_question(
        self,
        session: Session,
        *,
        assessment: AssessmentSession,
        concept: KnowledgeConcept,
        difficulty: str,
        assessment_type: str,
        language: str | None = None,
        node_role: str = "goal",
        skill_candidates: list[dict[str, Any]] | None = None,
        diagnosis_context: str = "",
        step_label: str | None = None,
        topic: str | None = None,
    ) -> AssessmentQuestion:
        questions = self.create_fresh_questions(
            session,
            assessment=assessment,
            concept=concept,
            difficulties=[difficulty],
            assessment_type=assessment_type,
            language=language,
            node_role=node_role,
            skill_candidates=skill_candidates,
            diagnosis_context=diagnosis_context,
            step_label=step_label,
            topic=topic,
        )
        return questions[0]

    def create_fresh_questions(
        self,
        session: Session,
        *,
        assessment: AssessmentSession,
        concept: KnowledgeConcept,
        difficulties: list[str],
        assessment_type: str,
        language: str | None = None,
        node_role: str = "goal",
        skill_candidates: list[dict[str, Any]] | None = None,
        diagnosis_context: str = "",
        step_label: str | None = None,
        topic: str | None = None,
    ) -> list[AssessmentQuestion]:
        created: list[AssessmentQuestion] = []
        learner_language = _normalize_generation_language(language)
        normalized_difficulties = [difficulty.strip().lower() for difficulty in difficulties]
        previous_questions = _previous_question_summary(
            session,
            assessment=assessment,
            concept=concept,
        )
        payloads, metadata = self._generate_fresh_questions(
            concept=concept,
            difficulties=normalized_difficulties,
            assessment_type=assessment_type,
            language=learner_language,
            node_role=node_role,
            skill_candidates=skill_candidates,
            diagnosis_context=diagnosis_context,
            previous_questions=previous_questions,
        )
        if len(payloads) != len(normalized_difficulties):
            raise AssessmentQuestionGenerationError(
                f"AI generated {len(payloads)} questions, expected {len(normalized_difficulties)}."
            )
        for index, (normalized_difficulty, payload) in enumerate(
            zip(normalized_difficulties, payloads, strict=True),
            start=1,
        ):
            self.validator.validate_question(
                concept_code=concept.code,
                difficulty=normalized_difficulty,
                question=payload,
            )
            question = self._persist_question(
                session,
                assessment=assessment,
                concept=concept,
                payload=payload,
                metadata={
                    **metadata,
                    "learner_language": learner_language,
                    "assessment_type": assessment_type,
                    "node_role": node_role,
                    "non_reusable": True,
                    "batch_index": index,
                    "batch_size": len(normalized_difficulties),
                    "difficulty_sequence": normalized_difficulties,
                    "skill_candidates": skill_candidates or [],
                },
                assessment_type=assessment_type,
                step_label=step_label or (
                    "Adaptive Posttest" if assessment_type == "posttest" else "Adaptive Pretest"
                ),
                topic=topic or _concept_prompt_title(concept, language=learner_language),
            )
            created.append(question)
        return created

    def _persist_question(
        self,
        session: Session,
        *,
        assessment: AssessmentSession,
        concept: KnowledgeConcept,
        payload: dict[str, Any],
        metadata: dict[str, Any],
        assessment_type: str,
        step_label: str,
        topic: str,
    ) -> AssessmentQuestion:
        sort_order = int(
            session.scalar(
                select(func.count()).select_from(AssessmentQuestion).where(
                    AssessmentQuestion.session_id == assessment.id
                )
            )
            or 0
        ) + 1
        question = AssessmentQuestion(
            session_id=assessment.id,
            pack_id=None,
            concept_id=concept.id,
            step_label=step_label,
            topic=topic,
            prompt=str(payload["prompt"]),
            helper_text=str(payload.get("helper_text", "")),
            difficulty_label=str(payload["difficulty"]),
            sort_order=sort_order,
            metadata_json={
                "source": "fresh_ai_assessment_question",
                "assessment_type": assessment_type,
                "concept_code": concept.code,
                "correct_option_key": _correct_label(payload),
                "explanation": payload["explanation"],
                "learner_language": metadata.get("learner_language", "en"),
                "language": metadata.get("learner_language", "en"),
                "node_role": metadata.get("node_role", ""),
                "question_type": payload.get("question_type", ""),
                "skill_trace": payload.get("skill_trace", []),
                "non_reusable": True,
            },
            generation_source=str(metadata.get("generation_source") or "llm_generated"),
            generation_prompt_version=FRESH_QUESTION_PROMPT_VERSION,
            llm_metadata_json=metadata,
            expected_reasoning=str(payload["expected_reasoning"]),
            rubric_json=payload.get("rubric", {}),
        )
        session.add(question)
        session.flush()
        for sort_order, option in enumerate(payload["options"], start=1):
            session.add(
                AssessmentOption(
                    question_id=question.id,
                    option_key=str(option["label"]),
                    label=str(option["label"]),
                    text=str(option["text"]),
                    is_correct=bool(option["is_correct"]),
                    sort_order=sort_order,
                )
            )
        session.flush()
        enqueue_flag(
            artifact_type="question",
            artifact_id=question.id,
            signals={"generation_source": str(metadata.get("generation_source") or "llm_generated")},
            concept_id=concept.id,
            summary=question.prompt[:160],
        )
        return session.scalar(
            select(AssessmentQuestion)
            .where(AssessmentQuestion.id == question.id)
            .options(selectinload(AssessmentQuestion.options))
        ) or question

    def _fallback_pack(
        self,
        *,
        concept: KnowledgeConcept,
        language: str,
    ) -> dict[str, dict[str, Any]]:
        title = _concept_prompt_title(concept, language=language)
        authored_pack = _AUTHORED_FALLBACK_PACKS.get(concept.code)
        if authored_pack is not None:
            return authored_pack
        code = concept.code.lower()
        text = f"{code} {title}".lower()
        if "perkalian" in text or "multiplication" in text or "kali" in text:
            return _math_pack(
                concept_code=concept.code,
                title=title,
                rows=[
                    ("easy", "Ada 3 kantong. Tiap kantong berisi 2 apel. Berapa apel semuanya?", "6", ["5", "6", "7", "8"], "3 kelompok berisi 2 berarti 2 + 2 + 2 = 6."),
                    ("medium", "Rina punya 4 kotak. Setiap kotak berisi 3 pensil. Berapa pensil semuanya?", "12", ["7", "12", "14", "16"], "4 kelompok berisi 3 berarti 3 + 3 + 3 + 3 = 12."),
                    ("hard", "Sebuah kelas punya 6 meja. Tiap meja dipakai 4 siswa. Jika 2 siswa tidak hadir, berapa siswa yang hadir?", "22", ["20", "22", "24", "26"], "6 x 4 = 24 kursi terisi semula, lalu 24 - 2 = 22."),
                ],
            )
        if "penjumlahan" in text or "addition" in text or "tambah" in text:
            return _math_pack(
                concept_code=concept.code,
                title=title,
                rows=[
                    ("easy", "Budi punya 5 kelereng lalu mendapat 3 lagi. Berapa kelereng Budi sekarang?", "8", ["6", "8", "9", "10"], "5 + 3 = 8."),
                    ("medium", "Ani membaca 18 halaman pagi hari dan 14 halaman sore hari. Berapa halaman yang ia baca?", "32", ["22", "30", "32", "34"], "18 + 14 = 32."),
                    ("hard", "Toko menjual 27 buku Senin, 36 buku Selasa, dan 18 buku Rabu. Berapa total buku terjual?", "81", ["71", "79", "81", "91"], "27 + 36 + 18 = 81."),
                ],
            )
        if "pengurangan" in text or "subtraction" in text or "kurang" in text:
            return _math_pack(
                concept_code=concept.code,
                title=title,
                rows=[
                    ("easy", "Ada 9 jeruk. 4 jeruk dimakan. Berapa jeruk tersisa?", "5", ["4", "5", "6", "7"], "9 - 4 = 5."),
                    ("medium", "Dari 35 stiker, 17 diberikan ke teman. Berapa stiker tersisa?", "18", ["16", "18", "20", "22"], "35 - 17 = 18."),
                    ("hard", "Sebuah bus berisi 48 penumpang. Di halte pertama turun 19, di halte kedua naik 7. Berapa penumpang sekarang?", "36", ["29", "34", "36", "40"], "48 - 19 + 7 = 36."),
                ],
            )
        if "derivative" in text or "turunan" in text or "differentiation" in text:
            return _math_pack(
                concept_code=concept.code,
                title=title,
                rows=[
                    ("easy", "Jika $f(x)=x^2$, berapa $f'(x)$?", "$2x$", ["$x$", "$2x$", "$x^3$", "$2$"], "Gunakan aturan pangkat: turunan $x^2$ adalah $2x$."),
                    ("medium", "Diketahui $f(x)=3x^2-4x+5$. Berapa $f'(x)$?", "$6x-4$", ["$3x-4$", "$6x-4$", "$6x+5$", "$x^2-4$"], "Turunkan tiap suku: $3x^2 \\to 6x$, $-4x \\to -4$, dan konstanta $5 \\to 0$."),
                    ("hard", "Kemiringan garis singgung kurva $f(x)=x^3-2x$ di $x=2$ adalah...", "$10$", ["$6$", "$8$", "$10$", "$12$"], "Kemiringan garis singgung adalah $f'(2)$. Karena $f'(x)=3x^2-2$, maka $f'(2)=12-2=10$."),
                ],
            )
        if "limit" in text or "limits" in text:
            return _math_pack(
                concept_code=concept.code,
                title=title,
                rows=[
                    ("easy", "Hitung $\\lim_{x\\to 2}(x+3)$.", "$5$", ["$2$", "$3$", "$5$", "$6$"], "Substitusi langsung: $2+3=5$."),
                    ("medium", "Hitung $\\lim_{x\\to 3}(x^2-4)$.", "$5$", ["$3$", "$5$", "$9$", "$13$"], "Substitusi langsung: $3^2-4=9-4=5$."),
                    ("hard", "Hitung $\\lim_{x\\to 2}\\frac{x^2-4}{x-2}$.", "$4$", ["$0$", "$2$", "$4$", "Tidak ada"], "Faktorkan $x^2-4=(x-2)(x+2)$, lalu limit menjadi $x+2$ di $x=2$, hasilnya $4$."),
                ],
            )
        if "linear" in text or "equation" in text or "persamaan" in text:
            return _math_pack(
                concept_code=concept.code,
                title=title,
                rows=[
                    ("easy", "Selesaikan $x+5=12$.", "$x=7$", ["$x=5$", "$x=7$", "$x=12$", "$x=17$"], "Kurangi kedua sisi dengan 5, jadi $x=7$."),
                    ("medium", "Selesaikan $3x-4=11$.", "$x=5$", ["$x=3$", "$x=4$", "$x=5$", "$x=7$"], "Tambah 4 ke kedua sisi: $3x=15$, maka $x=5$."),
                    ("hard", "Selesaikan $2(x-3)+5=17$.", "$x=9$", ["$x=6$", "$x=7$", "$x=9$", "$x=12$"], "Uraikan: $2x-6+5=17$, jadi $2x-1=17$, $2x=18$, maka $x=9$."),
                ],
            )
        if "fraction" in text or "fractions" in text or "pecahan" in text:
            return _math_pack(
                concept_code=concept.code,
                title=title,
                rows=[
                    ("easy", "Hitung $\\frac{1}{2}+\\frac{1}{4}$.", "$\\frac{3}{4}$", ["$\\frac{1}{6}$", "$\\frac{2}{6}$", "$\\frac{3}{4}$", "$\\frac{2}{4}$"], "Samakan penyebut: $\\frac{1}{2}=\\frac{2}{4}$, jadi $\\frac{2}{4}+\\frac{1}{4}=\\frac{3}{4}$."),
                    ("medium", "Hitung $\\frac{2}{3}\\times\\frac{3}{5}$, lalu sederhanakan.", "$\\frac{2}{5}$", ["$\\frac{5}{8}$", "$\\frac{2}{5}$", "$\\frac{5}{15}$", "$\\frac{3}{10}$"], "Kalikan pembilang dan penyebut: $\\frac{6}{15}$ lalu sederhanakan menjadi $\\frac{2}{5}$."),
                    ("hard", "Sederhanakan $\\frac{3}{4}+\\frac{2}{3}$.", "$\\frac{17}{12}$", ["$\\frac{5}{7}$", "$\\frac{6}{12}$", "$\\frac{13}{12}$", "$\\frac{17}{12}$"], "Penyebut sama 12: $\\frac{9}{12}+\\frac{8}{12}=\\frac{17}{12}$."),
                ],
            )
        if "matrix" in text or "matriks" in text:
            return _math_pack(
                concept_code=concept.code,
                title=title,
                rows=[
                    (
                        "easy",
                        "Diketahui matriks $A=\\begin{bmatrix}2&5\\\\1&3\\end{bmatrix}$. Elemen baris ke-1 kolom ke-2 adalah...",
                        "$5$",
                        ["$1$", "$2$", "$3$", "$5$"],
                        "Baris ke-1 adalah $[2,5]$, dan kolom ke-2 pada baris itu bernilai $5$.",
                    ),
                    (
                        "medium",
                        "Jika $A=\\begin{bmatrix}1&2\\\\3&4\\end{bmatrix}$ dan $B=\\begin{bmatrix}5&1\\\\2&6\\end{bmatrix}$, maka $A+B$ adalah...",
                        "$\\begin{bmatrix}6&3\\\\5&10\\end{bmatrix}$",
                        [
                            "$\\begin{bmatrix}6&3\\\\5&10\\end{bmatrix}$",
                            "$\\begin{bmatrix}5&2\\\\6&24\\end{bmatrix}$",
                            "$\\begin{bmatrix}4&1\\\\1&2\\end{bmatrix}$",
                            "$\\begin{bmatrix}6&1\\\\2&10\\end{bmatrix}$",
                        ],
                        "Penjumlahan matriks dilakukan elemen-sebaris-sekolom: $1+5=6$, $2+1=3$, $3+2=5$, $4+6=10$.",
                    ),
                    (
                        "hard",
                        "Diketahui $A=\\begin{bmatrix}2&1\\\\0&3\\end{bmatrix}$ dan $v=\\begin{bmatrix}4\\\\5\\end{bmatrix}$. Hasil $Av$ adalah...",
                        "$\\begin{bmatrix}13\\\\15\\end{bmatrix}$",
                        [
                            "$\\begin{bmatrix}8\\\\15\\end{bmatrix}$",
                            "$\\begin{bmatrix}13\\\\15\\end{bmatrix}$",
                            "$\\begin{bmatrix}9\\\\8\\end{bmatrix}$",
                            "$\\begin{bmatrix}6\\\\8\\end{bmatrix}$",
                        ],
                        "Kalikan baris dengan kolom: baris pertama $2(4)+1(5)=13$, baris kedua $0(4)+3(5)=15$.",
                    ),
                ],
            )
        if "data" in text or "statistik" in text or "statistics" in text:
            return _math_pack(
                concept_code=concept.code,
                title=title,
                rows=[
                    ("easy", "Data nilai: 6, 8, 10. Rata-ratanya adalah...", "8", ["6", "7", "8", "10"], "Rata-rata $=\\frac{6+8+10}{3}=8$."),
                    ("medium", "Data penjualan selama 4 hari adalah 12, 15, 15, dan 18. Median data tersebut adalah...", "15", ["12", "15", "16.5", "18"], "Urutan data sudah $12,15,15,18$. Median dua tengah $=\\frac{15+15}{2}=15$."),
                    ("hard", "Data 4, 6, 6, 8, 11 memiliki jangkauan...", "7", ["5", "6", "7", "11"], "Jangkauan adalah nilai terbesar dikurangi terkecil: $11-4=7$."),
                ],
            )
        if "function" in text or "fungsi" in text:
            return _math_pack(
                concept_code=concept.code,
                title=title,
                rows=[
                    ("easy", "Jika $f(x)=2x+1$, berapa $f(3)$?", "$7$", ["$5$", "$6$", "$7$", "$8$"], "Substitusi $x=3$: $2(3)+1=7$."),
                    ("medium", "Jika $g(x)=x^2-1$, berapa $g(4)$?", "$15$", ["$7$", "$12$", "$15$", "$17$"], "Substitusi $x=4$: $4^2-1=15$."),
                    ("hard", "Jika $f(x)=3x-2$ dan $g(x)=x+5$, berapa $f(g(2))$?", "$19$", ["$7$", "$13$", "$19$", "$21$"], "Hitung $g(2)=7$, lalu $f(7)=3(7)-2=19$."),
                ],
            )
        return _generic_pack(concept_code=concept.code, title=title, language=language)

    def _generate_pack(
        self,
        *,
        concept: KnowledgeConcept,
        language: str,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        ai_pack, ai_metadata = self._try_generate_pack_with_ai(concept=concept, language=language)
        if ai_pack is not None:
            return ai_pack, ai_metadata
        return self._fallback_pack(concept=concept, language=language), {
            "generation_source": "fallback_generated",
            "llm_provider": "deterministic",
            "llm_model": "template_v1",
            "fallback_reason": "LLM generation is disabled, unavailable, or failed validation after bounded retries.",
            "llm_generation_attempt": ai_metadata,
        }

    def _try_generate_pack_with_ai(
        self,
        *,
        concept: KnowledgeConcept,
        language: str,
    ) -> tuple[dict[str, dict[str, Any]] | None, dict[str, Any]]:
        settings = get_ai_settings()
        if os.getenv("WICARA_PRETEST_LLM_GENERATION", "").strip().lower() not in {
            "1",
            "true",
            "yes",
        }:
            return None, {"llm_attempted": False, "reason": "WICARA_PRETEST_LLM_GENERATION is not enabled."}
        if not settings.openrouter_api_key.strip():
            return None, {"llm_attempted": False, "reason": "OpenRouter API key is not configured."}
        try:
            asyncio.get_running_loop()
            return None, {"llm_attempted": False, "reason": "Generation called inside a running event loop."}
        except RuntimeError:
            pass

        validation_errors: list[str] = []
        max_attempts = _max_generation_attempts()
        for attempt in range(1, max_attempts + 1):
            prompt = _pack_prompt(
                concept=concept,
                language=language,
                previous_errors=validation_errors,
            )
            try:
                response = asyncio.run(
                    ai_client.generate(
                        system_instruction="Return valid JSON only.",
                        user_instruction=prompt,
                        params={"temperature": 0.15, "response_format": {"type": "json_object"}},
                    )
                )
                payload = json.loads(response.text)
                pack = _extract_pack_payload(payload)
                if not isinstance(pack, dict):
                    validation_errors.append(
                        f"attempt {attempt}: response must be an object with easy, medium, and hard questions."
                    )
                    continue
                self.validator.validate_pack(concept_code=concept.code, pack=pack)
                return pack, {
                    "generation_source": "llm_generated",
                    "llm_provider": response.provider,
                    "llm_model": response.model,
                    "attempt_count": attempt,
                    "prompt_version": PACK_PROMPT_VERSION,
                    "previous_validation_errors": validation_errors,
                }
            except Exception as exc:
                validation_errors.append(f"attempt {attempt}: {exc}")
        return None, {
            "llm_attempted": True,
            "attempt_count": max_attempts,
            "prompt_version": PACK_PROMPT_VERSION,
            "validation_errors": validation_errors,
        }

    def _generate_fresh_questions(
        self,
        *,
        concept: KnowledgeConcept,
        difficulties: list[str],
        assessment_type: str,
        language: str,
        node_role: str,
        skill_candidates: list[dict[str, Any]] | None,
        diagnosis_context: str,
        previous_questions: list[str],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        cached_pack = _golden_flow_pretest_cache(concept=concept, assessment_type=assessment_type)
        if cached_pack is not None:
            return _fresh_fallback_questions(
                fallback_pack=cached_pack,
                difficulties=difficulties,
            ), {
                "generation_source": "golden_flow_cached",
                "llm_provider": "curated",
                "llm_model": "golden_flow_cache_v1",
                "prompt_version": FRESH_QUESTION_PROMPT_VERSION,
                "cache_reason": "Curated curve-sketching golden-flow question cache.",
                "batch_size": len(difficulties),
                "difficulty_sequence": difficulties,
            }
        if _allow_dev_fallback_questions():
            fallback_pack = self._fallback_pack(concept=concept, language=language)
            fallback_questions = _fresh_fallback_questions(
                fallback_pack=fallback_pack,
                difficulties=difficulties,
            )
            return fallback_questions, {
                "generation_source": "dev_fallback_generated",
                "llm_provider": "deterministic",
                "llm_model": "dev_template_v1",
                "prompt_version": FRESH_QUESTION_PROMPT_VERSION,
                "fallback_reason": "WICARA_ASSESSMENT_DEV_FALLBACK_QUESTIONS enabled",
                "batch_size": len(difficulties),
                "difficulty_sequence": difficulties,
            }
        ai_questions, ai_metadata = self._try_generate_fresh_questions_with_ai(
            concept=concept,
            difficulties=difficulties,
            assessment_type=assessment_type,
            language=language,
            node_role=node_role,
            skill_candidates=skill_candidates,
            diagnosis_context=diagnosis_context,
            previous_questions=previous_questions,
        )
        if ai_questions is not None:
            return ai_questions, ai_metadata
        if assessment_type == "pretest":
            raise AssessmentQuestionGenerationError(
                _fresh_generation_failure_message(ai_metadata)
            )
        fallback_pack = self._fallback_pack(concept=concept, language=language)
        fallback_questions = _fresh_fallback_questions(
            fallback_pack=fallback_pack,
            difficulties=difficulties,
        )
        return fallback_questions, {
            "generation_source": "fallback_generated",
            "llm_provider": "deterministic",
            "llm_model": "template_v1",
            "prompt_version": FRESH_QUESTION_PROMPT_VERSION,
            "fallback_reason": "LLM generation is unavailable or failed validation after bounded retries.",
            "llm_generation_attempt": ai_metadata,
            "batch_size": len(difficulties),
            "difficulty_sequence": difficulties,
        }

    def _try_generate_fresh_questions_with_ai(
        self,
        *,
        concept: KnowledgeConcept,
        difficulties: list[str],
        assessment_type: str,
        language: str,
        node_role: str,
        skill_candidates: list[dict[str, Any]] | None,
        diagnosis_context: str,
        previous_questions: list[str],
    ) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
        settings = get_ai_settings()
        if not settings.openrouter_api_key.strip():
            return None, {"llm_attempted": False, "reason": "AI question generation requires a configured OpenRouter API key."}
        try:
            asyncio.get_running_loop()
            return None, {"llm_attempted": False, "reason": "AI question generation cannot run inside an active event loop."}
        except RuntimeError:
            pass

        validation_errors: list[str] = []
        max_attempts = _max_generation_attempts(assessment_type=assessment_type)
        for attempt in range(1, max_attempts + 1):
            prompt = _fresh_question_prompt(
                concept=concept,
                difficulties=difficulties,
                assessment_type=assessment_type,
                language=language,
                node_role=node_role,
                skill_candidates=skill_candidates,
                diagnosis_context=diagnosis_context,
                previous_questions=previous_questions,
                previous_errors=validation_errors,
            )
            try:
                response = asyncio.run(
                    asyncio.wait_for(
                        ai_client.generate(
                            system_instruction=_fresh_question_system_instruction(),
                            user_instruction=prompt,
                            params={
                                "temperature": 0.2,
                                "max_tokens": _fresh_generation_max_tokens(
                                    question_count=len(difficulties)
                                ),
                                "provider": {"require_parameters": True},
                                "response_format": _fresh_question_response_format(
                                    question_count=len(difficulties),
                                    question_types=_fresh_question_type_choices(
                                        difficulties=difficulties,
                                        node_role=node_role,
                                    ),
                                ),
                            },
                        ),
                        timeout=_fresh_generation_timeout_seconds(
                            assessment_type=assessment_type,
                            ai_request_timeout_seconds=settings.ai_request_timeout_seconds,
                        ),
                    )
                )
                try:
                    payload = json.loads(response.text)
                except json.JSONDecodeError as exc:
                    output_tokens = (
                        response.usage.output_tokens if response.usage is not None else None
                    )
                    raise ValueError(
                        "Invalid JSON response "
                        f"(finish_reason={response.finish_reason or 'unknown'}, "
                        f"output_tokens={output_tokens or 'unknown'}): {exc}"
                    ) from exc
                questions = _extract_fresh_questions_payload(payload)
                if len(questions) != len(difficulties):
                    validation_errors.append(
                        f"attempt {attempt}: response must contain exactly {len(difficulties)} questions."
                    )
                    continue
                normalized_questions = _normalize_fresh_questions_payload(
                    questions,
                    concept_code=concept.code,
                    difficulties=difficulties,
                )
                _validate_completed_reasoning(normalized_questions)
                _validate_no_leaked_meta_commentary(normalized_questions)
                for difficulty, question in zip(difficulties, normalized_questions, strict=True):
                    self.validator.validate_question(
                        concept_code=concept.code,
                        difficulty=difficulty,
                        question=question,
                    )
                _validate_skill_traces(
                    normalized_questions,
                    concept_code=concept.code,
                    skill_candidates=skill_candidates,
                )
                _validate_diagnostic_path_trace(
                    normalized_questions,
                    difficulties=difficulties,
                    skill_candidates=skill_candidates,
                )
                _validate_unique_question_prompts(normalized_questions)
                return normalized_questions, {
                    "generation_source": "llm_generated",
                    "llm_provider": response.provider,
                    "llm_model": response.model,
                    "attempt_count": attempt,
                    "prompt_version": FRESH_QUESTION_PROMPT_VERSION,
                    "previous_validation_errors": validation_errors,
                    "batch_size": len(difficulties),
                    "difficulty_sequence": difficulties,
                    "skill_candidates": skill_candidates or [],
                }
            except TimeoutError:
                timeout_seconds = _fresh_generation_timeout_seconds(
                    assessment_type=assessment_type,
                    ai_request_timeout_seconds=settings.ai_request_timeout_seconds,
                )
                validation_errors.append(
                    f"attempt {attempt}: generation timed out after {timeout_seconds:g} seconds."
                )
                break
            except Exception as exc:
                validation_errors.append(f"attempt {attempt}: {exc}")
        return None, {
            "llm_attempted": True,
            "attempt_count": max_attempts,
            "prompt_version": FRESH_QUESTION_PROMPT_VERSION,
            "validation_errors": validation_errors,
            "reason": "AI assessment question generation failed validation after bounded retries.",
        }


def _math_pack(
    *,
    concept_code: str,
    title: str,
    rows: list[tuple[str, str, str, list[str], str]],
) -> dict[str, dict[str, Any]]:
    pack: dict[str, dict[str, Any]] = {}
    labels = ["A", "B", "C", "D"]
    for difficulty, prompt, correct, options, explanation in rows:
        question_type = {
            "easy": "concept_application",
            "medium": "word_problem",
            "hard": "multi_step_application",
        }.get(difficulty, "concept_application")
        pack[difficulty] = {
            "concept_code": concept_code,
            "difficulty": difficulty,
            "question_type": question_type,
            "prompt": prompt,
            "helper_text": f"Pilih jawaban yang paling tepat untuk {title}.",
            "options": [
                {"label": label, "text": text, "is_correct": text == correct}
                for label, text in zip(labels, options, strict=True)
            ],
            "explanation": explanation,
            "expected_reasoning": explanation,
            "difficulty_reason": {
                "easy": "Cek konsep dasar dengan satu langkah sederhana.",
                "medium": "Aplikasi konsep dalam konteks singkat, bukan sekadar hitung langsung.",
                "hard": "Membutuhkan lebih dari satu langkah atau penerapan konsep dalam konteks lebih kompleks.",
            }.get(difficulty, "Cocok dengan kompleksitas yang diminta."),
            "distractor_rationales": {
                label: (
                    "Jawaban benar."
                    if text == correct
                    else "Distraktor mewakili kesalahan hitung atau salah memilih operasi."
                )
                for label, text in zip(labels, options, strict=True)
            },
            "rubric": {
                "correct_answer_score": 1.0,
                "reasoning_score_available": True,
                "canvas_score_available": True,
            },
        }
    return pack


def _generic_pack(
    *,
    concept_code: str,
    title: str,
    language: str,
) -> dict[str, dict[str, Any]]:
    return _math_pack(
        concept_code=concept_code,
        title=title,
        rows=[
            (
                "easy",
                f"Dalam latihan {title}, sebuah nilai awal 12 bertambah 5. Berapa nilai akhirnya?",
                "17",
                ["15", "16", "17", "18"],
                "Hitung perubahan langsung: $12+5=17$.",
            ),
            (
                "medium",
                f"Pada konteks {title}, data berurutan adalah 18, 24, dan 30. Selisih tetap antar data adalah...",
                "6",
                ["4", "6", "8", "12"],
                "Selisihnya $24-18=6$ dan $30-24=6$.",
            ),
            (
                "hard",
                f"Sebuah model sederhana untuk {title} memakai aturan $S=3a+2b$. Jika $a=4$ dan $b=5$, maka $S$ adalah...",
                "22",
                ["17", "20", "22", "26"],
                "Substitusi nilai: $S=3(4)+2(5)=12+10=22$.",
            ),
        ],
    )


def _authored_pack(
    *,
    concept_code: str,
    rows: list[tuple[str, str, list[tuple[str, str, bool]], str, str, list[tuple[str, str]]]],
) -> dict[str, dict[str, Any]]:
    """Build a fallback pack from real previously-generated question content.

    Each row carries an authored skill_trace so evidence-directed prerequisite
    routing keeps working for dev-fallback questions, unlike the generic packs
    above whose skill_trace is synthesized as a single self-referencing step.
    """
    pack: dict[str, dict[str, Any]] = {}
    for difficulty, prompt, options, explanation, expected_reasoning, skill_trace in rows:
        pack[difficulty] = {
            "concept_code": concept_code,
            "difficulty": difficulty,
            "question_type": "direct_computation",
            "prompt": prompt,
            "helper_text": "",
            "options": [
                {"label": label, "text": text, "is_correct": is_correct}
                for label, text, is_correct in options
            ],
            "explanation": explanation,
            "expected_reasoning": expected_reasoning,
            "skill_trace": [
                {"concept_code": trace_code, "criterion": criterion}
                for trace_code, criterion in skill_trace
            ],
            "rubric": {
                "correct_answer_score": 1.0,
                "reasoning_score_available": True,
                "canvas_score_available": True,
            },
        }
    return pack


def _curve_sketching_fallback_pack() -> dict[str, dict[str, Any]]:
    concept_code = "km_f_matematika_tingkat_lanjut_sketsa_kurva_menggunakan_turunan"
    polynomial_code = "km_f_matematika_tingkat_lanjut_turunan_fungsi_polinomial"
    chain_rule_code = "km_f_matematika_tingkat_lanjut_aturan_rantai"
    trig_code = "km_f_matematika_tingkat_lanjut_turunan_fungsi_trigonometri"
    return _authored_pack(
        concept_code=concept_code,
        rows=[
            (
                "easy",
                "Given f(x)=x^3-12x, classify the critical point at x=-2.",
                [
                    ("A", "Point of discontinuity", False),
                    ("B", "Neither", False),
                    ("C", "Local maximum", True),
                    ("D", "Local minimum", False),
                ],
                "Since f' changes from positive to negative at x=-2, the function increases then decreases, so x=-2 is a local maximum.",
                "The derivative is f'(x)=3x^2-12=3(x-2)(x+2). At x=-2, f'=0. Test sign: for x<-2 (e.g., x=-3), f' positive; for x between -2 and 2 (e.g., x=0), f' negative. So f' changes from positive to negative, indicating a local maximum at x=-2.",
                [
                    (polynomial_code, "Differentiates polynomial function f(x)=x^3-12x to obtain f'(x)=3x^2-12 and identifies critical points."),
                    (concept_code, "Uses sign analysis of f'(x) to classify the critical point as a local maximum based on the change from positive to negative."),
                ],
            ),
            (
                "medium",
                "Determine the x-coordinate of the inflection point of f(x)=(x-1)(x+2)^2.",
                [
                    ("A", "1", False),
                    ("B", "0", False),
                    ("C", "-1", True),
                    ("D", "-2", False),
                ],
                "An inflection point occurs where f''(x)=0 and concavity changes. f''(-1)=0, and concavity changes from down to up at x=-1, so it is an inflection point.",
                "Expand: f(x)=x^3+3x^2-4. Then f'(x)=3x^2+6x, f''(x)=6x+6. Set f''=0 -> x=-1. Check concavity: for x<-1 (e.g., x=-2), f''=-6<0 (concave down); for x>-1 (e.g., x=0), f''=6>0 (concave up). Since concavity changes, x=-1 is an inflection point.",
                [
                    (polynomial_code, "Expands or differentiates f(x) to find f'(x)=3x^2+6x and f''(x)=6x+6."),
                    (concept_code, "Sets f''(x)=0, solves for x=-1, and verifies concavity change to confirm the inflection point."),
                ],
            ),
            (
                "hard",
                "Classify the critical point at x=0 for f(x)=sin(πx^2) on the interval [-1,1].",
                [
                    ("A", "Local maximum", False),
                    ("B", "It is not a critical point", False),
                    ("C", "Local minimum", True),
                    ("D", "Neither (inflection point)", False),
                ],
                "f'(x) changes from negative to positive at x=0, so the function decreases then increases, indicating a local minimum. The second derivative test confirms with f''(0)=2π>0.",
                "f'(x)=2πx cos(πx^2). At x=0, f'(0)=0. First derivative test: for x<0 near 0, x negative and cos(πx^2) positive, so f'(x) negative; for x>0 near 0, positive, so f'(x) changes from negative to positive -> local minimum. Second derivative test: f''(x)=2π cos(πx^2) - 4π^2 x^2 sin(πx^2); f''(0)=2π>0 -> local minimum.",
                [
                    (chain_rule_code, "Applies the chain rule to differentiate f(x)=sin(πx^2) and obtains f'(x)=2πx cos(πx^2)."),
                    (trig_code, "Differentiates the trigonometric outer function and multiplies by the inner derivative correctly, preserving the correct form."),
                    (concept_code, "Uses the first or second derivative test to classify x=0 as a local minimum based on sign change of f'(x) or positive second derivative."),
                ],
            ),
        ],
    )


def _chain_rule_fallback_pack() -> dict[str, dict[str, Any]]:
    concept_code = "km_f_matematika_tingkat_lanjut_aturan_rantai"
    return _authored_pack(
        concept_code=concept_code,
        rows=[
            (
                "easy",
                "Differentiate $f(x) = (3x^2 + 2)^4$.",
                [
                    ("A", "$f'(x) = 4(3x^2 + 2)^3$", False),
                    ("B", "$f'(x) = 24x(3x^2 + 2)^3$", True),
                    ("C", "$f'(x) = 12x(3x^2 + 2)^3$", False),
                    ("D", "$f'(x) = 24x(3x^2 + 2)^4$", False),
                ],
                "The chain rule requires multiplying the derivative of the outer function (keeping the inner function unchanged) by the derivative of the inner function. The correct derivative is $24x(3x^2+2)^3$.",
                "The outer function is $u^4$ with $u=3x^2+2$. The derivative of the outer function with respect to $u$ is $4u^3 = 4(3x^2+2)^3$. The derivative of the inner function is $6x$. By the chain rule, $f'(x) = 4(3x^2+2)^3 \\cdot 6x = 24x(3x^2+2)^3$.",
                [
                    (concept_code, "Identify outer function $u^4$ and inner function $u=3x^2+2$, differentiate outer to get $4u^3$, multiply by derivative of inner $6x$, and simplify to $24x(3x^2+2)^3$."),
                ],
            ),
            (
                "medium",
                "Differentiate $f(x) = \\sin(4x^3)$.",
                [
                    ("A", "$f'(x) = 4x^3 \\cos(4x^3)$", False),
                    ("B", "$f'(x) = \\cos(4x^3)$", False),
                    ("C", "$f'(x) = 12x^2 \\cos(4x^3)$", True),
                    ("D", "$f'(x) = 12x^2 \\sin(4x^3)$", False),
                ],
                "The chain rule requires multiplying the derivative of the outer function (keeping the inner function unchanged) by the derivative of the inner function. The correct derivative is $12x^2 \\cos(4x^3)$.",
                "The outer function is $\\sin(u)$ with $u=4x^3$. The derivative of the outer function is $\\cos(u) = \\cos(4x^3)$. The derivative of the inner function is $12x^2$. By the chain rule, $f'(x) = \\cos(4x^3) \\cdot 12x^2 = 12x^2 \\cos(4x^3)$.",
                [
                    (concept_code, "Identify outer function $\\sin(u)$ and inner function $u=4x^3$, differentiate outer to get $\\cos(u)$, multiply by derivative of inner $12x^2$, and simplify to $12x^2 \\cos(4x^3)$."),
                ],
            ),
            (
                "hard",
                "Differentiate $f(x) = (1 + \\cos(2x))^3$.",
                [
                    ("A", "$f'(x) = -6 \\sin(2x) (1 + \\cos(2x))^2$", True),
                    ("B", "$f'(x) = -3 \\sin(2x) (1 + \\cos(2x))^2$", False),
                    ("C", "$f'(x) = 6 \\sin(2x) (1 + \\cos(2x))^2$", False),
                    ("D", "$f'(x) = 3(1 + \\cos(2x))^2$", False),
                ],
                "The chain rule must be applied to each layer. The correct derivative is $-6\\sin(2x)(1+\\cos(2x))^2$.",
                "The function is a composition: outer $u^3$ with $u=1+\\cos(2x)$, and $\\cos(2x)$ is itself a composition. First, derivative of outer: $3(1+\\cos(2x))^2$. Then derivative of $1+\\cos(2x)$ is $-\\sin(2x)\\cdot 2 = -2\\sin(2x)$. Multiply: $3(1+\\cos(2x))^2 \\cdot (-2\\sin(2x)) = -6\\sin(2x)(1+\\cos(2x))^2$.",
                [
                    (concept_code, "Apply chain rule twice: differentiate outer $u^3$ with $u=1+\\cos(2x)$ to get $3(1+\\cos(2x))^2$, then differentiate $\\cos(2x)$ to get $-\\sin(2x)\\cdot 2$, multiply all factors to obtain $-6\\sin(2x)(1+\\cos(2x))^2$."),
                ],
            ),
        ],
    )


_AUTHORED_FALLBACK_PACKS: dict[str, dict[str, dict[str, Any]]] = {
    "km_f_matematika_tingkat_lanjut_sketsa_kurva_menggunakan_turunan": _curve_sketching_fallback_pack(),
    "km_f_matematika_tingkat_lanjut_aturan_rantai": _chain_rule_fallback_pack(),
}


def _golden_flow_pretest_cache(
    *,
    concept: KnowledgeConcept,
    assessment_type: str,
) -> dict[str, dict[str, Any]] | None:
    if assessment_type != "pretest":
        return None
    return _AUTHORED_FALLBACK_PACKS.get(concept.code)


def _pack_prompt(
    *,
    concept: KnowledgeConcept,
    language: str,
    previous_errors: list[str] | None = None,
) -> str:
    concept_title = _concept_prompt_title(concept, language=language)
    concept_description = _concept_prompt_description(
        concept,
        language=language,
        title=concept_title,
    )
    retry_instruction = ""
    if previous_errors:
        compact_errors = "\n".join(f"- {error}" for error in previous_errors[-6:])
        retry_instruction = f"""

The previous generated pack failed backend validation.
Regenerate the entire pack, do not patch only one field.
Fix these validation errors:
{compact_errors}
""".rstrip()

    return f"""
Generate one adaptive pretest question pack for this existing curriculum concept.

Concept:
- concept_code: {concept.code}
- title: {concept_title}
- description: {concept_description}
- language: {language}

Return JSON shaped exactly as:
{{
  "easy": {{...}},
  "medium": {{...}},
  "hard": {{...}}
}}

Each question object must contain:
- concept_code matching "{concept.code}"
- difficulty: easy, medium, or hard
- question_type: multiple_choice
- prompt
- helper_text
- options: exactly 4 options with label, text, is_correct
- explanation
- expected_reasoning
- rubric

Exactly one option must be correct per question.
Do not invent a new concept_code.
Make every question a concrete task for the learner, not a definition quiz or vague theory check.
Do not ask about test-taking strategy, "which step is best", or generic concept recognition.
Options must be concrete final answers, not descriptions of strategies.
For math concepts, prefer numeric/algebraic tasks with a definite answer.
For calculus/derivative concepts, ask users to compute derivatives, slopes, tangent values, or apply derivative rules.
For non-math concepts, use a short applied scenario, source snippet, observation, or case; the answer should still be checkable.
Use lightweight Markdown when useful:
- inline math may use $...$
- display math may use $$...$$
- matrices must use LaTeX matrix notation inside math delimiters, for example $A=\\begin{{bmatrix}}1&2\\\\3&4\\end{{bmatrix}}$
- keep prompts short enough for mobile
Options may also contain math notation.
{retry_instruction}
""".strip()


def _fresh_question_system_instruction() -> str:
    return """
You are an expert STEAM assessment item writer.

Generate fresh, high-quality multiple-choice assessment questions for one learner.
The questions must diagnose or verify concept mastery, not test memorized wording.

You must follow the requested learner language exactly.
If the learner language is missing, use English.

Return valid JSON only.
Do not include markdown, commentary, or extra text.
Every field value must contain only learner-facing content. Never write notes, self-critique, or commentary about how or why you designed the question anywhere in the output, including inside the stem.
""".strip()


def _fresh_question_prompt(
    *,
    concept: KnowledgeConcept,
    difficulties: list[str],
    assessment_type: str,
    language: str,
    node_role: str,
    skill_candidates: list[dict[str, Any]] | None,
    diagnosis_context: str,
    previous_questions: list[str],
    previous_errors: list[str] | None = None,
) -> str:
    concept_title = _concept_prompt_title(concept, language=language)
    concept_description = _concept_prompt_description(
        concept,
        language=language,
        title=concept_title,
    )
    pedagogical_context = _concept_prompt_pedagogy(
        concept,
        language=language,
        difficulties=difficulties,
    )
    previous_question_text = "\n".join(f"- {item}" for item in previous_questions[-12:]) or "- none"
    difficulty_sequence = ", ".join(difficulties)
    skill_catalog = _solution_skill_catalog(
        concept=concept,
        skill_candidates=skill_candidates,
    )
    diagnostic_path = _diagnostic_path_candidates(skill_candidates)
    retry_instruction = ""
    if previous_errors:
        compact_errors = "\n".join(f"- {error}" for error in previous_errors[-6:])
        retry_instruction = f"""

The previous generated question failed backend validation.
Regenerate the full question and fix these validation errors:
{compact_errors}
""".rstrip()
    assessment_specific_rules = ""
    if assessment_type == "pretest":
        assessment_specific_rules = """

Pretest-specific rules:
- You are generating one node-level question set for an adaptive pretest, not a fixed-size test.
- For each node, the backend may request easy, medium, and hard together so later adaptive branching can reuse the same node set without another model call.
- The frontend still asks questions sequentially; do not imply all generated questions will be shown.
- Do not assume max_questions means the pretest should contain that many questions.
- max_questions is only a backend safety cap; it is not a target question count.
- Return exactly the number of question objects requested by the difficulty sequence.
- If the difficulty sequence is easy, medium, hard, make the three questions meaningfully different.
- The backend decision engine controls traversal. Do not assume or describe which generated question will be shown next.
""".rstrip()
    prerequisite_probe_rules = ""
    if node_role == "prerequisite" and any(
        difficulty in {"medium", "hard"} for difficulty in difficulties
    ):
        prerequisite_probe_rules = """

Prerequisite-probe rules:
- Ask the learner to solve the task directly (e.g., compute the derivative), not to find or correct someone else's mistake.
- Require the learner to show their working steps; expected_reasoning must describe the exact step-by-step method so a learner's submitted steps can be checked against it.
- One question must test one hypothesis: choose the simplest concrete task whose correct solution uses ONLY the skill in concept_code (this node) plus skills already listed in the allowed solution-skill catalog.
- Never choose a function or task that structurally requires a technique outside the allowed solution-skill catalog. For example, do not combine chain rule with product rule, quotient rule, or any other untested rule unless that rule's concept_code is itself in the catalog.
- If probing chain rule specifically, use a single outer-inner composition (e.g., a power of a polynomial, or a single transcendental function of a polynomial) and nothing else.
""".rstrip()
    diagnostic_path_rules = ""
    if node_role == "goal" and "hard" in difficulties and diagnostic_path:
        path_lines = "\n".join(
            f"  {index}. {item['concept_code']} — {item['title']}: {item['description']}"
            for index, item in enumerate(diagnostic_path, start=1)
        )
        diagnostic_path_rules = f"""

Graph-selected hard diagnostic path:
{path_lines}
- The hard question must assess the target through a task that genuinely requires every skill in this path.
- Include every selected path concept_code exactly once in the hard question's skill_trace, in causal solution order.
- Do not mention graph nodes or prerequisite codes to the learner.
- The path was selected generically from curriculum structure; do not replace it with an easier unrelated skill.
- The concrete function, representation, numbers, and distractors must remain freshly generated.
""".rstrip()
    if assessment_type == "posttest":
        assessment_specific_rules = """

Posttest-specific rules:
- You are generating a fixed-size posttest for the selected learning goal after the learner completed a workspace learning session.
- Use the compact workspace learning summary in Diagnosis context as the primary source.
- Evaluate whether the learner mastered the goal concept that was studied, not pretest remediation nodes.
- Generate exactly 10 questions when 10 difficulties are requested: 3 medium and 7 hard.
- Do not generate easy questions unless explicitly requested.
- Medium questions test application in context, simple story problems, equation representation, or choosing a correct strategy.
- Hard questions test complex reasoning, multi-step story problems, error analysis, misconception detection, strategy comparison, table interpretation, missing value reasoning, or transfer.
- Difficulty must not differ only by larger numbers.
- For math, hard questions should often include story problems, wrong-solution analysis, multi-step reasoning, or realistic misconception traps.
""".rstrip()

    return f"""
Assessment type: {assessment_type}
Learner language: {language}
Concept code: {concept.code}
Concept title: {concept_title}
Concept description: {concept_description}
Node role: {node_role}
Difficulty sequence, in exact output order: {difficulty_sequence}
Question count: {len(difficulties)}

Curriculum assessment contract:
{pedagogical_context}

Allowed solution-skill catalog:
{json.dumps(skill_catalog, ensure_ascii=False)}

Diagnosis context:
{diagnosis_context or 'none'}

Previous questions to avoid:
{previous_question_text}

Generate {len(difficulties)} fresh multiple-choice question(s).

Language requirements:
- Write all learner-facing text in {language}.
- If the language is unknown, write in English.
- Avoid unnecessary code-switching.
- Keep concept terms understandable for this learner.

Freshness requirements:
- Do not reuse old stored questions.
- Do not copy or lightly paraphrase previous session questions.
- Avoid the same scenario, numbers, wording, or solution pattern already listed.
- If a similar idea is unavoidable, change the representation and reasoning path.

Question quality requirements:
- Use concrete applied tasks, not vague theory checks.
- You must make easy, medium, and hard questions meaningfully different by cognitive demand.
- Assess the curriculum evidence and follow the guidance for each requested difficulty.
- When appropriate, build plausible distractors from the listed misconceptions without teaching the misconception as correct.
- Build skill_trace from the actual steps required by the specific generated question.
- Use only exact concept_code values from Allowed solution-skill catalog.
- Include a skill only when the expected solution genuinely uses it; never add a skill merely to force adaptive routing.
- A concept_code may appear at most once within one question's skill_trace.
- If several solution steps use the same concept, merge those steps into one observable criterion for that concept_code.
- Never create separate skill_trace entries for the same concept_code merely because their criterion text differs.
- Order skill_trace by the causal order of the expected solution steps.
- Each criterion must state observable evidence that distinguishes correct use of that skill from a nearby misconception.
- The selected answer alone must not be enough to diagnose a skill gap; expected_reasoning must expose the learner's method.
- Do not mention the curriculum graph, diagnosis, evidence labels, or prerequisite codes to the learner.
- Return one correct_answer and exactly 3 distractors per question. The backend assigns and shuffles A/B/C/D labels; never assign option labels yourself.
- correct_answer and every distractor must contain the complete learner-visible answer text, never a label or reference such as "A", "option B", or "the first statement".
- Incorrect output example: correct_answer="A", distractors=["B", "C", "D"].
- Correct output example: correct_answer="Increasing on $(-\\infty,-1)\\cup(1,\\infty)$", distractors=["Increasing on $(-1,1)$", "Decreasing for all $x$", "Increasing for all $x$"].
- All four options must answer the same specific task and be mutually exclusive.
- Keep the stem and every option in the same answer dimension: if the stem asks for a missing factor, options must be factors only; if options are complete corrected results, the stem must ask for the complete corrected result.
- Every distractor must be mathematically or factually false, not merely less complete than the correct option.
- Distractors must be plausible and reveal misconceptions.
- Solve the problem independently before writing correct_answer, then make sure the explanation agrees with that answer.
- Complete all internal checking before returning JSON. Never return self-correction, drafting notes, or statements that an answer/distractor still needs to be fixed; silently fix every affected field first.
- stem must contain only the learner-facing question. Never append a note, summary, or commentary explaining why the question is well-designed, what the distractors represent, or how skill_trace/expected_reasoning/explanation were written.
- Never mention field names from this schema (stem, skill_trace, expected_reasoning, explanation, distractors, correct_answer, question_type) to the learner in any field's visible text.
- expected_reasoning and explanation must contain only the derivation or justification of the correct result. Never discuss, compare, number, or name answer options there because the backend assigns and shuffles options after generation.
- Keep the stem short enough to read on a phone screen in a few seconds.
- Difficulty must come from the reasoning, not from reading burden. Do not ask the learner to verify or describe an entire multi-part analysis in one question (e.g., "which statement correctly describes the whole curve" combining monotonicity, extrema, concavity, and inflection points all at once) — that forces every option into a long paragraph and tests reading stamina instead of the target skill.
- Narrow the stem to one specific analytic decision (e.g., only monotonicity on a stated interval, or only the location of one extremum, or only concavity on a stated interval). Make that single decision genuinely hard through a subtle or counter-intuitive case, not through combining many facts.
- Because the stem targets one decision, every option must be one short phrase or short expression stating a candidate answer to that same decision — never a multi-clause paragraph re-deriving multiple properties.
- Keep each option comfortably short for a phone screen (preferably under 120 characters when the mathematics permits).
- Options are answer choices, not mini-solutions: use one claim or expression, avoid sentences containing "because", and do not repeat the full stem in every option.
- Example — avoid: options that each restate increasing/decreasing intervals AND local max/min AND inflection points AND concavity together.
- Example — prefer: stem asks only "which statement about monotonicity is correct" with options like "Decreasing on $(-1,0)$, increasing on $(0,1)$" / "Increasing on $(-1,1)$" / "Decreasing on $(-1,1)$" — short, mutually exclusive, still hard to get right.
- Options should be similar in length and style.
- Do not use "all of the above" or "none of the above".
- Do not make the correct answer obvious by wording length or detail.
- Avoid trick questions and ambiguous wording.
- Use age-appropriate, curriculum-appropriate language.
- Use Markdown and LaTeX when useful.

skill_trace uniqueness example for the current target:
Incorrect — duplicate concept_code entries even though the criteria differ:
[
  {{"concept_code": "{concept.code}", "criterion": "Determine the critical points."}},
  {{"concept_code": "{concept.code}", "criterion": "Determine the increasing intervals."}}
]
Correct — merge all steps belonging to that concept into one entry:
[
  {{"concept_code": "{concept.code}", "criterion": "Determine the critical points and use derivative signs to identify the increasing intervals."}}
]

Difficulty rules:
- Easy: direct recognition or basic one-step application.
- Medium: application in context, simple word problem, equation representation, or choosing the correct operation/strategy; usually 1-2 reasoning steps.
- Hard: multi-step reasoning, error analysis, strategy comparison, table interpretation, missing value/inverse reasoning, transfer, or misconception-heavy application.
- Do not make difficulty differ only by using larger numbers.
{assessment_specific_rules}
{prerequisite_probe_rules}
{diagnostic_path_rules}

Before returning JSON, internally verify:
- exactly one correct answer
- no duplicate questions
- requested language is followed
- questions are ordered according to the requested difficulty sequence
- output schema is valid
- every skill_trace is non-empty and contains only skills genuinely used by that question

Return exactly {len(difficulties)} question objects in the same order as the requested difficulty sequence.
Follow the provided strict response schema; do not repeat fields that are not in it.
{retry_instruction}
""".strip()


def _extract_fresh_questions_payload(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        questions = payload.get("questions")
        if isinstance(questions, list):
            return questions
        if all(key in payload for key in ("stem", "options", "correct_option_id")):
            return [payload]
    return []


def _normalize_fresh_question_payload(
    payload: Any,
    *,
    concept_code: str,
    difficulty: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise QuestionGenerationPayloadError("Question payload must be an object.")
    correct_answer = str(payload.get("correct_answer") or "").strip()
    raw_distractors = payload.get("distractors")
    if not correct_answer:
        raise QuestionGenerationPayloadError("Question is missing correct_answer.")
    if not isinstance(raw_distractors, list) or len(raw_distractors) != 3:
        raise QuestionGenerationPayloadError("Question must provide exactly 3 distractors.")
    answer_choices = [(correct_answer, True)]
    answer_choices.extend((str(item).strip(), False) for item in raw_distractors)
    random.SystemRandom().shuffle(answer_choices)
    options = [
        {
            "label": chr(ord("A") + index),
            "text": text,
            "is_correct": is_correct,
        }
        for index, (text, is_correct) in enumerate(answer_choices)
    ]
    return {
        "concept_code": concept_code,
        "difficulty": difficulty,
        "question_type": str(payload.get("question_type") or "concept_application").strip().lower(),
        "prompt": str(payload.get("prompt") or payload.get("stem") or "").strip(),
        "helper_text": str(payload.get("helper_text") or "").strip(),
        "options": options,
        "explanation": str(payload.get("explanation") or "").strip(),
        "expected_reasoning": str(payload.get("expected_reasoning") or "").strip(),
        "skill_trace": _normalize_skill_trace(payload.get("skill_trace")),
        "rubric": payload.get("rubric") if isinstance(payload.get("rubric"), dict) else {
            "correct_answer_score": 1.0,
            "reasoning_score_available": True,
            "canvas_score_available": False,
        },
    }


def _normalize_fresh_questions_payload(
    questions: list[Any],
    *,
    concept_code: str,
    difficulties: list[str],
) -> list[dict[str, Any]]:
    if len(questions) != len(difficulties):
        raise QuestionGenerationPayloadError(
            f"Expected {len(difficulties)} questions, got {len(questions)}."
        )
    return [
        _normalize_fresh_question_payload(
            question,
            concept_code=concept_code,
            difficulty=difficulty,
        )
        for question, difficulty in zip(questions, difficulties, strict=True)
    ]


def _validate_unique_question_prompts(questions: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for question in questions:
        normalized = " ".join(str(question.get("prompt") or "").lower().split())
        if normalized in seen:
            raise QuestionGenerationPayloadError("Generated questions must not duplicate prompts in the same batch.")
        seen.add(normalized)


_UNRESOLVED_REASONING_MARKERS = (
    "i need to adjust",
    "i need to correct",
    "let me correct",
    "i'll rewrite",
    "i will rewrite",
    "needs to be revised",
    "need to be revised",
    "the correct answer should reflect",
)


def _validate_completed_reasoning(questions: list[dict[str, Any]]) -> None:
    for question in questions:
        diagnostic_text = " ".join(
            (
                str(question.get("expected_reasoning") or ""),
                str(question.get("explanation") or ""),
            )
        ).casefold()
        if any(marker in diagnostic_text for marker in _UNRESOLVED_REASONING_MARKERS):
            raise QuestionGenerationPayloadError(
                "Generated reasoning contains unresolved self-correction or drafting notes."
            )
        if re.search(r"\b(?:option|opsi|pilihan|choice)\b", diagnostic_text) or re.search(
            r"\b(?:first|second|third|fourth|pertama|kedua|ketiga|keempat)\s+(?:statement|answer|pernyataan|jawaban)\b",
            diagnostic_text,
        ):
            raise QuestionGenerationPayloadError(
                "Generated reasoning must not discuss backend-assigned answer options."
            )


_SCHEMA_FIELD_LEAKAGE_MARKERS = (
    "skill_trace",
    "expected_reasoning",
    "correct_answer",
    "distractors",
    "concept_code",
    "question_type",
)

_META_COMMENTARY_MARKERS = (
    "note:",
    "note that this question",
    "the stem includes",
    "the options are",
    "the distractors are",
    "the question is easy because",
    "the question is medium because",
    "the question is hard because",
    "this question tests",
    "this question is designed",
)


def _validate_no_leaked_meta_commentary(questions: list[dict[str, Any]]) -> None:
    for question in questions:
        option_texts = [
            str(option.get("text") or "")
            for option in question.get("options", [])
            if isinstance(option, dict)
        ]
        learner_facing_text = " ".join(
            (
                str(question.get("prompt") or ""),
                str(question.get("helper_text") or ""),
                *option_texts,
            )
        ).casefold()
        if any(marker in learner_facing_text for marker in _SCHEMA_FIELD_LEAKAGE_MARKERS):
            raise QuestionGenerationPayloadError(
                "Generated question leaks internal schema field names into learner-facing text."
            )
        if any(marker in learner_facing_text for marker in _META_COMMENTARY_MARKERS):
            raise QuestionGenerationPayloadError(
                "Generated question stem contains design commentary instead of only the learner-facing question."
            )


def _normalize_skill_trace(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    trace_by_code: dict[str, dict[str, str]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        code = str(item.get("concept_code") or "").strip()
        criterion = str(item.get("criterion") or "").strip()
        existing = trace_by_code.get(code)
        if existing is None:
            trace_by_code[code] = {
                "concept_code": code,
                "criterion": criterion,
            }
            continue
        if criterion and criterion not in existing["criterion"]:
            existing["criterion"] = " ".join(
                part for part in (existing["criterion"], criterion) if part
            )
    return list(trace_by_code.values())


def _validate_skill_traces(
    questions: list[dict[str, Any]],
    *,
    concept_code: str,
    skill_candidates: list[dict[str, Any]] | None,
) -> None:
    allowed_codes = {concept_code}
    allowed_codes.update(
        str(candidate.get("concept_code") or "").strip()
        for candidate in skill_candidates or []
        if str(candidate.get("concept_code") or "").strip()
    )
    for question in questions:
        trace = question.get("skill_trace")
        if not isinstance(trace, list) or not trace:
            raise QuestionGenerationPayloadError(
                "Generated question must provide a non-empty skill_trace."
            )
        seen: set[str] = set()
        for step in trace:
            code = str(step.get("concept_code") or "").strip()
            criterion = str(step.get("criterion") or "").strip()
            if code not in allowed_codes:
                raise QuestionGenerationPayloadError(
                    f"skill_trace concept_code is outside the allowed catalog: {code or 'empty'}."
                )
            if code in seen:
                raise QuestionGenerationPayloadError(
                    f"skill_trace concept_code must be unique within a question: {code}."
                )
            if not criterion:
                raise QuestionGenerationPayloadError(
                    f"skill_trace criterion is missing for {code}."
                )
            seen.add(code)


def _solution_skill_catalog(
    *,
    concept: KnowledgeConcept,
    skill_candidates: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    catalog = [
        {
            "concept_code": concept.code,
            "title": concept.title,
            "description": str(concept.description or "").strip()[:240],
        }
    ]
    seen = {concept.code}
    for candidate in skill_candidates or []:
        code = str(candidate.get("concept_code") or "").strip()
        if not code or code in seen:
            continue
        item = {
                "concept_code": code,
                "title": str(candidate.get("title") or code).strip(),
                "description": str(candidate.get("description") or "").strip()[:240],
            }
        path_order = candidate.get("diagnostic_path_order")
        if isinstance(path_order, int):
            item["diagnostic_path_order"] = path_order
        catalog.append(item)
        seen.add(code)
    return catalog


def _diagnostic_path_candidates(
    skill_candidates: list[dict[str, Any]] | None,
) -> list[dict[str, str]]:
    selected = [
        candidate
        for candidate in skill_candidates or []
        if isinstance(candidate.get("diagnostic_path_order"), int)
    ]
    selected.sort(key=lambda item: int(item["diagnostic_path_order"]))
    return [
        {
            "concept_code": str(item.get("concept_code") or "").strip(),
            "title": str(item.get("title") or item.get("concept_code") or "").strip(),
            "description": str(item.get("description") or "").strip(),
        }
        for item in selected
        if str(item.get("concept_code") or "").strip()
    ]


def _validate_diagnostic_path_trace(
    questions: list[dict[str, Any]],
    *,
    difficulties: list[str],
    skill_candidates: list[dict[str, Any]] | None,
) -> None:
    required_codes = {
        item["concept_code"]
        for item in _diagnostic_path_candidates(skill_candidates)
    }
    if not required_codes:
        return
    for difficulty, question in zip(difficulties, questions, strict=True):
        if difficulty != "hard":
            continue
        actual_codes = {
            str(item.get("concept_code") or "").strip()
            for item in question.get("skill_trace", [])
            if isinstance(item, dict)
        }
        missing = required_codes - actual_codes
        if missing:
            raise QuestionGenerationPayloadError(
                "Hard question skill_trace is missing graph-selected diagnostic path skills: "
                f"{', '.join(sorted(missing))}."
            )


class QuestionGenerationPayloadError(ValueError):
    pass


def _fresh_question_response_format(
    *,
    question_count: int,
    question_types: set[str] | None = None,
) -> dict[str, Any]:
    question_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "stem": {"type": "string"},
            "question_type": {
                "enum": sorted(question_types or VALID_QUESTION_TYPES)
            },
            "correct_answer": {"type": "string"},
            "distractors": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {"type": "string"},
            },
            "skill_trace": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "concept_code": {"type": "string"},
                        "criterion": {"type": "string"},
                    },
                    "required": ["concept_code", "criterion"],
                },
            },
            "expected_reasoning": {"type": "string"},
            "explanation": {"type": "string"},
        },
        "required": [
            "stem",
            "question_type",
            "correct_answer",
            "distractors",
            "skill_trace",
            "expected_reasoning",
            "explanation",
        ],
    }
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "adaptive_pretest_question_batch",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "questions": {
                        "type": "array",
                        "minItems": question_count,
                        "maxItems": question_count,
                        "items": question_schema,
                    }
                },
                "required": ["questions"],
            },
        },
    }


def _fresh_generation_failure_message(metadata: dict[str, Any]) -> str:
    reason = str(metadata.get("reason") or "AI pretest question generation failed.")
    errors = metadata.get("validation_errors")
    if not isinstance(errors, list) or not errors:
        detail = str(metadata.get("reason") or "generation unavailable")
        return f"{reason} Detail: {detail}"
    return f"{reason} Validation: {' | '.join(str(error) for error in errors[-4:])}"


def _correct_label(payload: dict[str, Any]) -> str:
    for option in payload["options"]:
        if option.get("is_correct") is True:
            return str(option["label"])
    return ""


def _extract_pack_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return None
    questions = payload.get("questions")
    return questions if isinstance(questions, dict) else payload


def _max_generation_attempts(*, assessment_type: str | None = None) -> int:
    if assessment_type == "posttest":
        raw_value = os.getenv("WICARA_POSTTEST_LLM_MAX_ATTEMPTS", "").strip()
        default_attempts = 2
    else:
        raw_value = os.getenv("WICARA_PRETEST_LLM_MAX_ATTEMPTS", "").strip()
        default_attempts = 2 if assessment_type == "pretest" else DEFAULT_PACK_GENERATION_MAX_ATTEMPTS
    if not raw_value:
        return default_attempts
    try:
        return max(1, min(8, int(raw_value)))
    except ValueError:
        return default_attempts


def _fresh_generation_max_tokens(*, question_count: int) -> int:
    return 2000 + (4000 * max(1, question_count))


def _fresh_question_type_choices(
    *,
    difficulties: list[str],
    node_role: str,
) -> set[str] | None:
    if node_role == "prerequisite":
        return {"direct_computation"}
    return None


def _fresh_generation_timeout_seconds(
    *,
    assessment_type: str,
    ai_request_timeout_seconds: float,
) -> float:
    if assessment_type != "pretest":
        return ai_request_timeout_seconds
    raw_value = os.getenv("WICARA_PRETEST_LLM_TIMEOUT_SECONDS", "").strip()
    if not raw_value:
        return ai_request_timeout_seconds
    try:
        return max(1.0, min(270.0, float(raw_value)))
    except ValueError:
        return min(ai_request_timeout_seconds, DEFAULT_PRETEST_LLM_GENERATION_TIMEOUT_SECONDS)


def _previous_question_summary(
    session: Session,
    *,
    assessment: AssessmentSession,
    concept: KnowledgeConcept,
) -> list[str]:
    current_rows = list(
        session.scalars(
            select(AssessmentQuestion.prompt)
            .where(AssessmentQuestion.session_id == assessment.id)
            .order_by(AssessmentQuestion.sort_order)
        )
    )
    historical_rows = list(
        session.scalars(
            select(AssessmentQuestion.prompt)
            .join(AssessmentSession, AssessmentQuestion.session_id == AssessmentSession.id)
            .where(
                AssessmentSession.user_id == assessment.user_id,
                AssessmentQuestion.concept_id == concept.id,
                AssessmentQuestion.session_id != assessment.id,
            )
            .order_by(AssessmentQuestion.sort_order.desc())
            .limit(20)
        )
    )
    values = [str(item).strip() for item in [*current_rows, *historical_rows] if str(item).strip()]
    return list(dict.fromkeys(values))


def _normalize_generation_language(language: str | None) -> str:
    normalized = (language or "").strip()
    if not normalized:
        return "English"
    lowered = normalized.lower()
    if lowered in {"en", "eng", "english"}:
        return "English"
    if lowered in {"id", "ind", "indo", "indonesian", "bahasa indonesia", "bahasa"}:
        return "Indonesian"
    return normalized


def _metadata_text(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    return str(value).strip() if value is not None else ""


def _concept_prompt_title(concept: KnowledgeConcept, *, language: str) -> str:
    metadata = concept.metadata_json or {}
    if normalize_language_code(language) == "id":
        return _metadata_text(metadata, "label_id") or concept.title

    explicit = _metadata_text(metadata, "label_en")
    label_id = _metadata_text(metadata, "label_id") or concept.title
    if explicit and explicit.casefold() != label_id.casefold():
        return explicit
    translated = translate_curriculum_label_to_english(label_id)
    return translated or explicit or label_id or concept.title


def _concept_prompt_description(
    concept: KnowledgeConcept,
    *,
    language: str,
    title: str,
) -> str:
    metadata = concept.metadata_json or {}
    if normalize_language_code(language) == "id":
        return (
            concept.id_desc
            or _metadata_text(metadata, "description_id")
            or concept.description
            or f"Memahami dan menerapkan {title}."
        )
    return (
        concept.en_desc
        or _metadata_text(metadata, "description_en")
        or _metadata_text(metadata, "en_desc")
        or f"Understand and apply {title}."
    )


def _concept_prompt_pedagogy(
    concept: KnowledgeConcept,
    *,
    language: str,
    difficulties: list[str],
) -> str:
    metadata = concept.metadata_json or {}
    suffix = "id" if normalize_language_code(language) == "id" else "en"
    evidence = metadata.get(f"assessment_evidence_{suffix}")
    misconceptions = metadata.get(f"common_misconceptions_{suffix}")
    guidance = metadata.get(f"question_generation_guidance_{suffix}")

    evidence_lines = _prompt_list(evidence)
    misconception_lines = _prompt_list(misconceptions)
    guidance_lines: list[str] = []
    if isinstance(guidance, dict):
        for difficulty in dict.fromkeys(difficulties):
            value = str(guidance.get(difficulty) or "").strip()
            if value:
                guidance_lines.append(f"- {difficulty}: {value}")

    return "\n".join(
        [
            "Assessment evidence:",
            *(evidence_lines or ["- none provided"]),
            "Common misconceptions:",
            *(misconception_lines or ["- none provided"]),
            "Difficulty guidance:",
            *(guidance_lines or ["- none provided"]),
        ]
    )


def _prompt_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        f"- {text}"
        for item in value
        if (text := str(item).strip())
    ]


def _difficulty_occurrence_count(difficulties: list[str], difficulty: str) -> int:
    return sum(1 for item in difficulties if item == difficulty)


def _fresh_fallback_questions(
    *,
    fallback_pack: dict[str, dict[str, Any]],
    difficulties: list[str],
) -> list[dict[str, Any]]:
    return [
        _fallback_question_variant(
            fallback_pack[difficulty],
            variant_index=index,
            variant_count=_difficulty_occurrence_count(difficulties, difficulty),
        )
        for index, difficulty in enumerate(difficulties, start=1)
    ]


def _fallback_question_variant(
    question: dict[str, Any],
    *,
    variant_index: int,
    variant_count: int,
) -> dict[str, Any]:
    authored_skill_trace = question.get("skill_trace")
    skill_trace = (
        authored_skill_trace
        if isinstance(authored_skill_trace, list) and authored_skill_trace
        else [
            {
                "concept_code": str(question["concept_code"]),
                "criterion": str(question["expected_reasoning"]),
            }
        ]
    )
    if variant_count <= 1:
        return {
            **question,
            "freshness_note": "Deterministic fallback question; not reusable in future assessments.",
            "misconception_tags": [],
            "skill_trace": skill_trace,
        }
    return {
        **question,
        "prompt": f"{question['prompt']} (Variant {variant_index})",
        "freshness_note": "Deterministic fallback variant; not reusable in future assessments.",
        "misconception_tags": [],
        "skill_trace": skill_trace,
    }


def _allow_dev_fallback_questions() -> bool:
    return os.getenv("WICARA_ASSESSMENT_DEV_FALLBACK_QUESTIONS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
