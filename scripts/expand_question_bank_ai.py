"""Expand the WICARA question bank with AI-generated items.

For every concept already present in bank_soal/seeds, ensure it has at least
TARGET_MEDIUM medium and TARGET_HARD hard multiple-choice questions (the
posttest needs 3 medium + 7 hard). Missing items are generated via OpenRouter,
validated locally against the same rules the importer enforces, and written to
companion ``*.aigen.v1.json`` files (curated seed files are never modified).

Usage (from backend root):
    python scripts/expand_question_bank_ai.py --dry-run 2     # generate for 2 concepts, print, no write
    python scripts/expand_question_bank_ai.py                 # full run, writes *.aigen.v1.json
    python scripts/expand_question_bank_ai.py --only mathematics.senior_high
    python scripts/expand_question_bank_ai.py --model deepseek/deepseek-v4-flash --workers 6

Reads OPENROUTER_API_KEY / OPENROUTER_BASE_URL / AI_MODEL from .env or env.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SEEDS_DIR = BACKEND_ROOT / "bank_soal" / "seeds"
ENV_PATH = BACKEND_ROOT / ".env"

TARGET_MEDIUM = 3
TARGET_HARD = 7
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"

SUPPORTED_DIFFICULTIES = {"easy", "medium", "hard"}
ASSESSMENT_TYPES = ["pretest", "daily_quiz", "posttest", "workspace_quiz"]
OPTION_LABELS = ["A", "B", "C", "D"]


# --------------------------------------------------------------------------- env
def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


# ----------------------------------------------------------------------- seeds io
def load_seed_files() -> list[dict]:
    """Return list of {path, data} for every seed file (incl. prior aigen output,
    so re-runs compute deficits against what already exists and only top up)."""
    packs = []
    for path in sorted(SEEDS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if "items" not in data:
            continue
        packs.append({"path": path, "data": data})
    return packs


def group_key(data: dict) -> tuple[str, str, str]:
    return (
        str(data.get("subject_code")),
        str(data.get("education_level")),
        str(data.get("language", "en")),
    )


# ------------------------------------------------------------------ openrouter
def call_openrouter(api_key: str, base_url: str, model: str, prompt: str,
                    timeout: float = 90.0, retries: int = 3) -> str:
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an expert assessment item writer for "
             "the Indonesian Kurikulum Merdeka. You output ONLY valid JSON, no prose, no markdown fences."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                base_url.rstrip("/") + "/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://wicara.local",
                    "X-Title": "WICARA Question Bank Expansion",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.load(resp)
            return payload["choices"][0]["message"]["content"]
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, TimeoutError) as exc:
            last_err = exc
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"openrouter call failed after {retries} tries: {last_err}")


def extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    # Try direct, then locate first {...} or [...]
    for candidate in (text,):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    raise ValueError("no JSON found in model output")


# --------------------------------------------------------------- prompt + parse
def build_prompt(concept_title: str, subject_code: str, education_level: str,
                 language: str, difficulty: str, count: int) -> str:
    lang_name = {"en": "English", "id": "Indonesian"}.get(language, "English")
    level_name = education_level.replace("_", " ")
    return f"""Write {count} ORIGINAL multiple-choice questions in {lang_name} for {level_name}
students studying the concept "{concept_title}" (subject: {subject_code}, Kurikulum Merdeka).

Difficulty: {difficulty}.
- "medium" = direct application of the concept to a concrete problem.
- "hard" = multi-step reasoning, problem solving, or analysis that combines the concept with related ideas.

Each question MUST:
- Have exactly 4 answer options labelled A, B, C, D, with EXACTLY ONE correct.
- Be a genuine subject question (NOT "which topic is this"), self-contained, unambiguous.
- Include a concise worked explanation of why the answer is correct.
- Include cognitive_level (one of: understand, apply, analyze, evaluate).
- Include helper_text (a short hint) and a rubric with one "correct" sentence and 1-2 "common_misconceptions".

Return ONLY a JSON object of this exact shape:
{{"questions": [
  {{
    "prompt": "...",
    "options": [{{"label":"A","text":"..."}},{{"label":"B","text":"..."}},{{"label":"C","text":"..."}},{{"label":"D","text":"..."}}],
    "answer_key": "A",
    "explanation": "...",
    "cognitive_level": "apply",
    "helper_text": "...",
    "rubric": {{"correct": "...", "common_misconceptions": ["...", "..."]}}
  }}
]}}"""


def coerce_item(raw: dict, *, concept_code, concept_title, subject_code,
                education_level, grade_band, language, difficulty, ext_id) -> dict | None:
    try:
        options = raw["options"]
        if not isinstance(options, list) or len(options) != 4:
            return None
        norm_opts = []
        for label, opt in zip(OPTION_LABELS, options):
            text = str(opt.get("text", "")).strip() if isinstance(opt, dict) else str(opt).strip()
            if not text:
                return None
            norm_opts.append({"label": label, "text": text})
        answer = str(raw.get("answer_key", "")).strip().upper()[:1]
        if answer not in OPTION_LABELS:
            return None
        prompt = str(raw.get("prompt", "")).strip()
        explanation = str(raw.get("explanation", "")).strip()
        if not prompt or not explanation:
            return None
        cog = str(raw.get("cognitive_level", "apply")).strip().lower()
        if cog not in {"understand", "apply", "analyze", "evaluate"}:
            cog = "apply"
        rubric = raw.get("rubric") or {}
        return {
            "id": ext_id,
            "subject_code": subject_code,
            "concept_code": concept_code,
            "concept_title": concept_title,
            "education_level": education_level,
            "grade_band": grade_band,
            "language": language,
            "assessment_types": ASSESSMENT_TYPES,
            "question_type": "multiple_choice",
            "difficulty": difficulty,
            "cognitive_level": cog,
            "prompt": prompt,
            "helper_text": str(raw.get("helper_text", "")).strip() or "Read carefully and pick the best answer.",
            "options": norm_opts,
            "answer_key": answer,
            "explanation": explanation,
            "rubric": {
                "correct": str(rubric.get("correct", "")).strip() or "Learner applies the concept correctly.",
                "common_misconceptions": [str(x).strip() for x in (rubric.get("common_misconceptions") or [])][:3]
                or ["Applies a related but incorrect rule."],
            },
            "tags": [subject_code, education_level, concept_code, difficulty, "ai_generated"],
            "status": "active",
            "metadata": {"source_pack": "ai_generated_v1", "estimated_seconds": 60 if difficulty == "hard" else 45},
        }
    except (KeyError, TypeError, ValueError):
        return None


def validate_item(item: dict) -> bool:
    required = ["subject_code", "concept_code", "concept_title", "education_level",
                "grade_band", "language", "assessment_types", "question_type",
                "difficulty", "prompt", "options", "answer_key", "explanation", "status"]
    if any(f not in item for f in required):
        return False
    if item["difficulty"] not in SUPPORTED_DIFFICULTIES:
        return False
    opts = item["options"]
    if not isinstance(opts, list) or len(opts) != 4:
        return False
    labels = [o["label"] for o in opts]
    if len(set(labels)) != 4:
        return False
    if item["answer_key"] not in labels:
        return False
    return bool(str(item["explanation"]).strip())


# --------------------------------------------------------------------- planning
def build_plan(packs: list[dict], only: str | None):
    """Return list of work units: dict(group, concept_code, concept_title, meta, need_medium, need_hard)."""
    groups: dict[tuple, dict] = {}
    for pack in packs:
        data = pack["data"]
        gk = group_key(data)
        g = groups.setdefault(gk, {
            "subject_code": data.get("subject_code"),
            "education_level": data.get("education_level"),
            "grade_band": data.get("grade_band", data.get("education_level")),
            "language": data.get("language", "en"),
            "concepts": defaultdict(lambda: {"title": None, "medium": 0, "hard": 0,
                                             "easy": 0, "ai_medium": 0, "ai_hard": 0}),
        })
        for it in data["items"]:
            cc = str(it.get("concept_code", "")).strip()
            if not cc:
                continue
            c = g["concepts"][cc]
            c["title"] = c["title"] or it.get("concept_title") or cc
            diff = str(it.get("difficulty", "")).lower()
            if diff in c:
                c[diff] += 1
            # track prior AI-generated counts so re-runs continue id numbering
            if it.get("metadata", {}).get("source_pack") == "ai_generated_v1":
                if diff == "medium":
                    c["ai_medium"] += 1
                elif diff == "hard":
                    c["ai_hard"] += 1

    plan = []
    for gk, g in groups.items():
        tag = f"{g['subject_code']}.{g['education_level']}.{g['language']}"
        if only and only not in tag:
            continue
        for cc, c in g["concepts"].items():
            need_medium = max(0, TARGET_MEDIUM - c["medium"])
            need_hard = max(0, TARGET_HARD - c["hard"])
            if need_medium or need_hard:
                plan.append({
                    "group": gk, "subject_code": g["subject_code"],
                    "education_level": g["education_level"], "grade_band": g["grade_band"],
                    "language": g["language"], "concept_code": cc, "concept_title": c["title"],
                    "need_medium": need_medium, "need_hard": need_hard,
                    "offset_medium": c["ai_medium"], "offset_hard": c["ai_hard"],
                })
    return plan


# ------------------------------------------------------------------------- main
def generate_for_concept(unit, api_key, base_url, model):
    out = []
    for difficulty, count in (("medium", unit["need_medium"]), ("hard", unit["need_hard"])):
        if count <= 0:
            continue
        prompt = build_prompt(unit["concept_title"], unit["subject_code"],
                              unit["education_level"], unit["language"], difficulty, count)
        try:
            content = call_openrouter(api_key, base_url, model, prompt)
            parsed = extract_json(content)
            qs = parsed.get("questions") if isinstance(parsed, dict) else parsed
            if not isinstance(qs, list):
                continue
        except Exception as exc:  # noqa: BLE001
            print(f"    ! {unit['concept_code']}/{difficulty}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        lang_part = "" if unit["language"] == "en" else f"_{unit['language']}"
        offset = unit["offset_medium"] if difficulty == "medium" else unit["offset_hard"]
        for i, raw in enumerate(qs[:count], start=offset + 1):
            ext_id = f"{unit['subject_code']}_{unit['education_level']}{lang_part}_{unit['concept_code']}_aigen_{difficulty}_{i:02d}"
            item = coerce_item(raw, concept_code=unit["concept_code"], concept_title=unit["concept_title"],
                               subject_code=unit["subject_code"], education_level=unit["education_level"],
                               grade_band=unit["grade_band"], language=unit["language"],
                               difficulty=difficulty, ext_id=ext_id)
            if item and validate_item(item):
                out.append(item)
    return unit, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", type=int, default=0, metavar="N",
                    help="generate for first N concepts, print results, do not write files")
    ap.add_argument("--only", type=str, default=None, help="filter group tag substring, e.g. mathematics.senior_high")
    ap.add_argument("--model", type=str, default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="cap number of concepts (0=all)")
    args = ap.parse_args()

    env = load_env()
    api_key = env.get("OPENROUTER_API_KEY", "")
    base_url = env.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    model = args.model or env.get("AI_MODEL") or DEFAULT_MODEL
    if not api_key:
        sys.exit("OPENROUTER_API_KEY not found in .env")

    packs = load_seed_files()
    plan = build_plan(packs, args.only)
    plan.sort(key=lambda u: (u["subject_code"], u["education_level"], u["concept_code"]))
    if args.limit:
        plan = plan[: args.limit]
    if args.dry_run:
        plan = plan[: args.dry_run]

    total_q = sum(u["need_medium"] + u["need_hard"] for u in plan)
    print(f"model={model}  concepts_to_fill={len(plan)}  questions_to_generate={total_q}")
    if not plan:
        print("Nothing to do — every concept already meets the target.")
        return

    results: dict[tuple, list] = defaultdict(list)
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(generate_for_concept, u, api_key, base_url, model) for u in plan]
        for fut in as_completed(futs):
            unit, items = fut.result()
            done += 1
            results[unit["group"]].extend(items)
            print(f"[{done}/{len(plan)}] {unit['subject_code']}/{unit['education_level']}/"
                  f"{unit['concept_code']}: +{len(items)} items")

    if args.dry_run:
        sample = next(iter(results.values()), [])
        print("\n===== DRY RUN SAMPLE (first concept) =====")
        print(json.dumps(sample, indent=2, ensure_ascii=False))
        print(f"\nTotal generated (not written): {sum(len(v) for v in results.values())}")
        return

    written = 0
    for gk, items in results.items():
        if not items:
            continue
        subject_code, education_level, language = gk
        grade_band = items[0]["grade_band"]
        lang_suffix = "" if language == "en" else f".{language}"
        out_path = SEEDS_DIR / f"{subject_code}.{education_level}.all_topics{lang_suffix}.aigen.v1.json"
        existing = []
        if out_path.exists():
            existing = json.loads(out_path.read_text(encoding="utf-8")).get("items", [])
        seen = {it["id"] for it in existing}
        new_items = [it for it in items if it["id"] not in seen]
        merged = existing + new_items
        payload = {
            "version": "2026-06-21",
            "source": "wicara_question_bank_ai_expansion_v1",
            "language": language,
            "subject_code": subject_code,
            "education_level": education_level,
            "grade_band": grade_band,
            "items": merged,
        }
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        written += len(new_items)
        print(f"wrote {out_path.name}: +{len(new_items)} new ({len(merged)} total)")
    print(f"\nDONE. Total new items written: {written}")


if __name__ == "__main__":
    main()
