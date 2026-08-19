# WICARA Backend

FastAPI backend for WICARA, a prerequisite-first adaptive tutoring system that helps students find the missing foundations behind a STEM learning struggle.

WICARA is not designed as a generic chatbot. The backend coordinates curriculum graph lookup, adaptive diagnosis, learning goal resolution, workspace events, assessments, mastery state, generated media jobs, and reporting so the mobile app can guide each learner through a structured 5E flow: Engage, Explore, Explain, Elaborate, and Evaluate.

## What WICARA Solves

Many students give up on STEM because they start believing they are "bad at math" or "not a science person." In many cases the real issue is a hidden prerequisite gap. A student struggling with derivatives may be missing earlier foundations in functions, exponents, graph interpretation, or algebra. If the system never diagnoses that gap, every new lesson feels harder and confidence drops.

WICARA starts from a different assumption: students often do not know what they do not know. The backend therefore focuses on diagnosing the root missing concept, not only answering the latest question.

The project was validated through a demo visit to Yayasan Kampus Diakoneia Modern (KDM), a non-profit foundation in Bekasi, Indonesia that supports underprivileged and street-connected children. That visit reinforced the need for a tutor that can guide students who do not yet know how to prompt an LLM toward the right explanation.

## Current Implementation

| Area | Status | Backend responsibility |
|---|---:|---|
| Auth | Implemented | Supabase-backed sign-in, registration, refresh, Google token flow, current user lookup. |
| Profile/onboarding | Implemented | Learner profile persistence and onboarding update endpoint. |
| Curriculum graph | Implemented | Subjects, knowledge map, concept lookup, seeded Kurikulum Merdeka graph data. |
| Learning goals | Implemented | Goal resolution, confirmation, history, path selection, active goal management. |
| Adaptive pretests | Implemented | Start/read/answer/finalize flows plus adaptive diagnosis services. |
| Posttests | Implemented | Start/read/answer/finalize flows for lesson checks. |
| Home/queue/tracks | Implemented | Home summary, queue, tracks, modules, module state updates. |
| Workspace | Implemented | Workspace creation, event timeline, phase advance, posttest start, media job queueing. |
| Evidence upload | Implemented | Image asset endpoint for worksheet/canvas evidence. |
| Media artifacts | Implemented | Media artifact list/detail/status and worker-backed render lifecycle. |
| Reports | Implemented | Weekly report endpoints and snapshot-backed reporting. |
| On-device Gemma via LiteRT-LM | Implemented in mobile app repo | Mobile runs Gemma locally and calls backend services for structured context/data. |
| OpenRouter provider | Legacy | Older backend-side AI integration retained in this repo from a previous architecture. |

## Architecture

```text
Flutter mobile app
  |
  | REST JSON, Bearer token
  v
FastAPI backend
  |
  |-- Supabase Auth / JWT verification
  |-- PostgreSQL via SQLAlchemy 2.x
  |-- Alembic migrations
  |-- Curriculum graph and question-bank seeds
  |-- OpenRouter provider for Gemma 4 reasoning
  |-- Media job queue: Redis or noop
  |-- Media rendering: Manim / Remotion templates, gTTS voiceover, FFmpeg post-process
  |-- Media storage: local filesystem or Supabase Storage
```

### AI model configuration

The current backend AI provider is OpenRouter. The default model is defined in `app/modules/ai/config.py`:

```text
AI_PROVIDER=openrouter
AI_MODEL=deepseek/deepseek-v4-flash
AI_IMAGE_MODEL=qwen/qwen3.7-flash
AI_REASONING_EFFORT=high
```

Text requests use `AI_MODEL`; requests with an image input automatically use
`AI_IMAGE_MODEL`. Override either setting in `.env`.

The hackathon writeup describes Gemma 4 via LiteRT-LM as the local-first target architecture. This repository does not currently load a LiteRT runtime, DLL, `.task`, `.tflite`, `.gguf`, or other local model file. If the team adds on-device LiteRT later, document the model download source, runtime library placement, checksum, and mobile build flags in the mobile README.

## Repository Structure

```text
backend/
  app/
    main.py                         FastAPI app, CORS, health check, static media mount
    api/v1/                         HTTP routes for auth, curriculum, learning, profile, workspace
    core/                           settings, language helpers, path resolution
    db/                             SQLAlchemy base/session and Alembic migrations
    modules/
      accounts/                     Supabase auth and account/profile contracts
      ai/                           OpenRouter client and Gemma model config
      curriculum/                   subjects, concepts, graph seed/service
      evidence/                     image evidence upload
      inputs/                       normalized input event models/services
      learning/                     tracks, media artifacts, reports, animation jobs
      learning_goal_resolution/     goal resolver and selected concept flow
      pretests/                     adaptive pretest workflow
      posttests/                    lesson posttest workflow
      workspaces/                   workspace timeline, tutor flow, mastery updates
  bank_soal/seeds/                  question-bank seed JSON files
  tests/                            API, service, model, provider tests
  wicara_mvp_10_manim_templates/    Manim template library and samples
  wicara_remotion_templates/        Remotion template library
  alembic.ini
  pyproject.toml
  .env.example
```

## Requirements

Minimum:

- Python 3.11 or newer
- PostgreSQL database, local or Supabase pooler
- `pip`
- Git

Recommended for the full media pipeline:

- Redis, unless `MEDIA_JOB_QUEUE_BACKEND=noop`
- FFmpeg and FFprobe available in `PATH`
- Manim system dependencies
- Optional SoX if your local `manim-voiceover` setup requires it
- Node.js and `npx` for Remotion templates

For the Docker workflow:

- Docker Desktop or Docker Engine with Compose v2
- This backend directory contains `Dockerfile`, `.dockerignore`, and `docker-compose.yml`
- This backend directory must also contain `.env`

## Quick Start: Docker Backend

Use this path for backend-only Docker runs. It builds and runs the FastAPI backend on the same API port used during local development.

From this backend directory:

```powershell
cd "C:\Users\Asus\Documents\Wicara\wicara-backend"
```

Make sure the env file exists:

```powershell
Test-Path .\.env
```

If it does not exist yet:

```powershell
Copy-Item .\.env.example .\.env
```

Edit `.env`, then build and run:

```powershell
docker compose up --build -d
```

Open:

```text
Backend: http://127.0.0.1:8000
Health:  http://127.0.0.1:8000/health
Docs:    http://127.0.0.1:8000/docs
```

Check container status:

```powershell
docker compose ps
```

Expected status:

```text
backend   Up ... (healthy)   0.0.0.0:8000->8000/tcp
```

Expected health response:

```json
{"status":"ok"}
```

If you run Flutter separately, point it to this Docker backend:

```powershell
cd "C:\Users\Asus\Documents\Wicara\wicara-mobile"
flutter run -d chrome --dart-define=WICARA_API_BASE_URL=http://127.0.0.1:8000
```

For Flutter web, keep `WICARA_API_BASE_URL` as `http://127.0.0.1:8000`. The API request is made by the user's browser, so `http://backend:8000` will not work there even though it is valid inside the Docker network.

### Docker Database Setup

If the connected PostgreSQL database is fresh, run migrations:

```powershell
docker compose run --rm backend alembic upgrade head
```

Seed or refresh the question bank:

```powershell
docker compose run --rm backend python -m app.modules.question_bank.seed
```

Preview the seed without writing:

```powershell
docker compose run --rm backend python -m app.modules.question_bank.seed --dry-run --strict
```

### Docker Logs and Stop

Follow logs:

```powershell
docker compose logs -f backend
```

Stop containers without deleting data volumes:

```powershell
docker compose down
```

Stop containers and delete named volumes such as `backend-tmp`:

```powershell
docker compose down -v
```

Use `docker compose down -v` only when you intentionally want to delete generated local media/cache volume data.

### Optional Docker Media Worker

The default command starts the API only. To also run the media worker:

```powershell
docker compose --profile worker up --build -d
```

With `MEDIA_JOB_QUEUE_BACKEND=noop`, the worker polls queued jobs from the database. With `MEDIA_JOB_QUEUE_BACKEND=redis`, add a Redis service or point `REDIS_URL` at a reachable Redis instance.

### Docker Equivalent Commands

The backend service runs the equivalent of:

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Quick Start: Local API Without Media Worker

Use this path when you only need the API, auth, curriculum, learning, workspace, and assessment endpoints running.

```powershell
cd "D:\Gemma Hackathon\backend"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
Copy-Item .env.example .env
```

Edit `.env`, then run:

```powershell
alembic upgrade head
python -m app.modules.question_bank.seed
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/docs
```

Expected health response:

```json
{"status":"ok"}
```

## Quick Start: API Plus Media Worker

Install render dependencies:

```powershell
python -m pip install -e ".[test,render]"
```

Use these env values for the simplest local demo:

```dotenv
MEDIA_JOB_QUEUE_BACKEND=noop
MEDIA_STORAGE_BACKEND=local
MEDIA_STORAGE_LOCAL_DIR=tmp/media_storage
MEDIA_STORAGE_PUBLIC_BASE_URL=/media-storage
MEDIA_TTS_PROVIDER=gtts_voiceover
MEDIA_TTS_REQUIRED=false
MEDIA_FFMPEG_BINARY=ffmpeg
MEDIA_FFPROBE_BINARY=ffprobe
```

Run the API in terminal 1:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Run the media worker in terminal 2:

```powershell
python -m app.workers.media_worker
```

With `MEDIA_JOB_QUEUE_BACKEND=noop`, the worker falls back to polling queued jobs from the database. With Redis, job IDs are pushed to the configured Redis list.

## Environment Variables

Create `.env` from `.env.example`. Do not commit real credentials.

### Minimum local development env

This is enough for the API to boot and use local media storage:

```dotenv
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/wicara
REDIS_URL=redis://localhost:6379/0

MEDIA_JOB_QUEUE_BACKEND=noop
MEDIA_STORAGE_BACKEND=local
MEDIA_STORAGE_LOCAL_DIR=tmp/media_storage
MEDIA_STORAGE_PUBLIC_BASE_URL=/media-storage
MEDIA_TTS_PROVIDER=gtts_voiceover
MEDIA_TTS_REQUIRED=false
MEDIA_FFMPEG_BINARY=ffmpeg
MEDIA_FFPROBE_BINARY=ffprobe

SUPABASE_PROJECT_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_JWKS_URL=https://YOUR_PROJECT_REF.supabase.co/auth/v1/.well-known/jwks.json
SUPABASE_ISSUER=https://YOUR_PROJECT_REF.supabase.co/auth/v1
SUPABASE_JWT_AUDIENCE=authenticated
SUPABASE_ANON_KEY=replace-with-your-anon-key
SUPABASE_SERVICE_ROLE_KEY=replace-with-your-service-role-key

OPENROUTER_API_KEY=replace-with-your-openrouter-key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
AI_PROVIDER=openrouter
AI_MODEL=deepseek/deepseek-v4-flash
AI_IMAGE_MODEL=qwen/qwen3.7-flash
AI_REASONING_EFFORT=high
```

### Supabase pooler database URL

If your database password contains special characters such as `@`, the settings layer will quote credentials automatically. Still, the safest form is:

```dotenv
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:6543/postgres?sslmode=require
```

For Supabase transaction pooler, use the `*.pooler.supabase.com` host from the Supabase dashboard.

### Full media env reference

```dotenv
MEDIA_JOB_QUEUE_BACKEND=noop
MEDIA_JOBS_QUEUE_KEY=wicara:media:jobs
MEDIA_JOB_DEQUEUE_TIMEOUT_SECONDS=5
MEDIA_RENDER_OUTPUT_DIR=tmp/media_renders
MEDIA_RENDER_TIMEOUT_SECONDS=240
MEDIA_RENDER_MAX_ATTEMPTS=2

MEDIA_REMOTION_PROJECT_DIR=wicara_remotion_templates
MEDIA_REMOTION_ENTRY=src/index.ts
MEDIA_REMOTION_TIMEOUT_SECONDS=600
MEDIA_NPX_BINARY=npx
MEDIA_NODE_BINARY=node
MEDIA_REMOTION_CONCURRENCY=2

MEDIA_TTS_PROVIDER=gtts_voiceover
MEDIA_TTS_REQUIRED=false
MEDIA_FFMPEG_BINARY=ffmpeg
MEDIA_FFPROBE_BINARY=ffprobe
MEDIA_POSTPROCESS_TIMEOUT_SECONDS=180
MEDIA_POSTPROCESS_MAX_ATTEMPTS=2

MEDIA_STORAGE_BACKEND=local
MEDIA_STORAGE_LOCAL_DIR=tmp/media_storage
MEDIA_STORAGE_PUBLIC_BASE_URL=/media-storage
MEDIA_STORAGE_UPLOAD_TIMEOUT_SECONDS=120
MEDIA_UPLOAD_MAX_ATTEMPTS=3
MEDIA_STORAGE_SUPABASE_BUCKET=media-artifacts

MEDIA_DURATION_POLICY_MODE=soft_fail
MEDIA_DURATION_MIN_SECONDS_SD=60
MEDIA_DURATION_MIN_SECONDS_SMP=90
MEDIA_DURATION_MIN_SECONDS_SMA=120
MEDIA_DURATION_MIN_SECONDS_DEFAULT=90
```

Allowed values:

| Variable | Values | Notes |
|---|---|---|
| `MEDIA_JOB_QUEUE_BACKEND` | `noop`, `redis` | `noop` is easiest for demos; `redis` is better for deployed workers. |
| `MEDIA_STORAGE_BACKEND` | `local`, `supabase` | `local` serves files from `/media-storage`; `supabase` uploads to Supabase Storage. |
| `MEDIA_TTS_PROVIDER` | `gtts_voiceover`, `none` | Several older aliases normalize to `gtts_voiceover`. |
| `MEDIA_DURATION_POLICY_MODE` | `off`, `soft_fail`, `hard_fail` | Controls whether too-short generated videos fail or only record warnings. |

## Database and Seed Commands

Run migrations:

```powershell
alembic upgrade head
```

Rollback one revision:

```powershell
alembic downgrade -1
```

Show current DB revision:

```powershell
alembic current
```

Seed or refresh question bank data:

```powershell
python -m app.modules.question_bank.seed
```

Preview question-bank import without writing:

```powershell
python -m app.modules.question_bank.seed --dry-run --strict
```

## Docker Compose

The Docker files for backend-only runs are inside this backend repository:

- `docker-compose.yml` orchestrates the backend API and optional media worker.
- `Dockerfile` builds the backend image.
- `.dockerignore` prevents `.env`, caches, generated media, and Remotion `node_modules` from being copied into the image.

Use the full workflow in [Quick Start: Docker Backend](#quick-start-docker-backend).

The Compose services expose:

| Service | Port | Purpose |
|---|---:|---|
| `backend` | `8000` | FastAPI API, `/health`, `/docs`, static local media mount. |
| `media-worker` | none | Optional worker, enabled with `--profile worker`. |

## Deployment

The backend deploys as two pieces, because the API and the media pipeline have
incompatible runtime needs. The stateless API runs on Vercel; media rendering runs on
a container host.

| Component | Host | Why |
|---|---|---|
| FastAPI API (`app.main:app`) | Vercel | Stateless, database-backed JSON. Fits a serverless function. |
| Media worker (`app.workers.media_worker`) | Container host | Persistent loop, system binaries, multi-minute renders. |

Both hosts share the same Supabase Postgres, Redis queue, and Supabase Storage bucket.

### Why the media pipeline cannot run on Vercel

- `app/modules/learning/render_engine.py` invokes Manim as `sys.executable -m manim`
  through `subprocess`. That needs cairo, pango, and FFmpeg installed at the system
  level, and Vercel's Python runtime has no way to install them.
- `app/modules/learning/remotion_render_engine.py` shells out to `npx`, which needs a
  Node toolchain and headless Chromium.
- `MEDIA_FFMPEG_BINARY` and `MEDIA_FFPROBE_BINARY` both assume FFmpeg on `PATH`.
- `app/workers/media_worker.py` is a long-lived `while True` loop. Serverless has
  nowhere to run a daemon.
- Render timeouts exceed the platform ceiling. `MEDIA_REMOTION_TIMEOUT_SECONDS`
  defaults to `600` and `MEDIA_RENDER_TIMEOUT_SECONDS` to `240`, against a Vercel
  maximum of 300 seconds on Hobby.

### Vercel checklist for the API

- Set `MEDIA_STORAGE_BACKEND=supabase`. The `local` backend writes to disk, and
  `app/main.py` both creates `MEDIA_STORAGE_LOCAL_DIR` at import time and mounts it
  through `StaticFiles`. Vercel's filesystem is read-only outside `/tmp`, so that
  mount has to stay disabled in serverless.
- Point `DATABASE_URL` at the Supabase transaction pooler on port `6543`. See
  [Supabase pooler database URL](#supabase-pooler-database-url). A direct connection
  will exhaust Postgres under serverless concurrency.
- Set `MEDIA_JOB_QUEUE_BACKEND=redis` against a managed Redis, so API requests can
  enqueue render jobs for the worker to pick up.
- Drop `manim` from the dependency set installed for the Vercel build. No API code
  imports it; the worker only ever invokes it as a subprocess. Keeping it risks the
  250 MB unzipped function limit.
- Run Alembic migrations out of band. They are not a build step. See
  [Database and Seed Commands](#database-and-seed-commands).

### Media host

Use the existing `Dockerfile`, which already carries the system dependencies, and run
the worker service described in
[Optional Docker Media Worker](#optional-docker-media-worker). Any container host
works: Railway, Render, Fly, or a plain VM.

## API Surface

All v1 endpoints are mounted under `/api/v1`.

### Auth and profile

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/auth/supabase` | Exchange Supabase access token for backend session response. |
| POST | `/api/v1/auth/sign-in` | Email/password sign-in through Supabase. |
| POST | `/api/v1/auth/register` | Register through Supabase. |
| POST | `/api/v1/auth/google` | Google identity flow through Supabase. |
| POST | `/api/v1/auth/refresh` | Refresh current Supabase session. |
| GET | `/api/v1/auth/me` | Current auth account. |
| GET | `/api/v1/me` | Current account/profile summary. |
| GET | `/api/v1/me/profile` | Learner profile. |
| PUT | `/api/v1/me/profile/onboarding` | Save onboarding profile. |

### Curriculum and knowledge map

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/subjects` | List supported subjects. |
| GET | `/api/v1/knowledge-map` | Return curriculum graph with learner status. |
| GET | `/api/v1/knowledge-map/concepts/{concept_code}` | Return one concept. |
| GET | `/api/v1/materials/search` | Search candidate materials/concepts. |

### Learning goals and tracks

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/learning-goals/resolve` | Resolve raw learner goal to candidate concept nodes. |
| POST | `/api/v1/learning-goals/resolve/{resolution_id}/confirm` | Confirm a proposed concept. |
| POST | `/api/v1/learning-goals/resolve/{resolution_id}/reprompt` | Ask resolver for another candidate. |
| POST | `/api/v1/learning-goals/resolve/{resolution_id}/select` | Select one candidate manually. |
| GET | `/api/v1/learning-goals/active` | Active goal. |
| GET | `/api/v1/learning-goals/history` | Goal/session history. |
| POST | `/api/v1/learning-goals/from-concept` | Create goal from a known concept. |
| POST | `/api/v1/learning-goals/{learning_goal_id}/cancel` | Cancel goal. |
| POST | `/api/v1/learning-goals/{learning_goal_id}/archive` | Archive goal. |
| POST | `/api/v1/learning-goals/{learning_goal_id}/path-selection` | Save selected path. |
| POST | `/api/v1/learning-goals` | Create learning goal. |
| GET | `/api/v1/learning-goals/{learning_goal_id}` | Read learning goal. |
| GET | `/api/v1/tracks` | List tracks. |
| GET | `/api/v1/tracks/{track_id}/modules` | Read modules for a track. |
| PATCH | `/api/v1/tracks/{track_id}/modules/{module_id}/state` | Update module state. |
| GET | `/api/v1/home` | Home dashboard summary. |
| GET | `/api/v1/learning-queue` | Learner queue. |

### Assessments

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/pretests/{learning_goal_id}` | Read legacy learning-goal pretest shape. |
| POST | `/api/v1/pretests/{assessment_session_id}/answers` | Submit legacy pretest answer. |
| POST | `/api/v1/pretests/{assessment_session_id}/reasoning` | Submit legacy reasoning/canvas context. |
| POST | `/api/v1/pretests/start` | Start adaptive pretest session. |
| GET | `/api/v1/pretests/{session_id}` | Read adaptive pretest session. |
| POST | `/api/v1/pretests/{session_id}/answers` | Submit adaptive pretest answer. |
| POST | `/api/v1/pretests/{session_id}/finalize` | Finalize adaptive pretest. |
| POST | `/api/v1/posttests/start` | Start posttest. |
| GET | `/api/v1/posttests/{session_id}` | Read posttest. |
| POST | `/api/v1/posttests/{session_id}/answers` | Submit posttest answer. |
| POST | `/api/v1/posttests/{session_id}/finalize` | Finalize posttest. |
| GET | `/api/v1/daily-evaluations/today` | Daily review session. |
| POST | `/api/v1/daily-evaluations/{assessment_session_id}/answers` | Submit daily review answer. |

Some route names overlap between the legacy mobile-compatible learning API and the newer adaptive pretest module. Check `/docs` for the active OpenAPI schema generated from the running code.

### Workspace, media, reports, evidence

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/workspaces` | Workspace session history. |
| POST | `/api/v1/workspaces` | Create workspace. |
| GET | `/api/v1/workspaces/{workspace_id}` | Read workspace timeline. |
| POST | `/api/v1/workspaces/{workspace_id}/events` | Submit chat/quiz/canvas/image event. |
| POST | `/api/v1/workspaces/{workspace_id}/advance-phase` | Advance 5E phase. |
| POST | `/api/v1/workspaces/{workspace_id}/start-posttest` | Start posttest from workspace. |
| POST | `/api/v1/workspaces/{workspace_id}/generate-video` | Queue generated video/media job. |
| POST | `/api/v1/evidence/image-assets` | Upload image evidence. |
| GET | `/api/v1/media-artifacts` | List media artifacts. |
| GET | `/api/v1/media-artifacts/{artifact_id}` | Read media artifact. |
| GET | `/api/v1/media-artifacts/{artifact_id}/status` | Poll media artifact status. |
| GET | `/api/v1/reports/weekly/latest` | Latest weekly report. |
| GET | `/api/v1/reports/weekly` | Weekly report. |

## Core Feature Map

| Feature | Backend implementation | Gemma 4 role |
|---|---|---|
| Curriculum knowledge graph and prerequisite diagnosis | Curriculum seed/service, goal resolver, adaptive pretest modules, mastery services. | Generate/refine diagnostic reasoning, interpret responses, recommend starting node. |
| Adaptive multimodal workspace and 5E flow | Workspace sessions, workspace events, phase advance, evidence upload, tutor services. | Interpret text/image/canvas evidence and generate phase-aligned feedback. |
| Template-guided visualization | Media artifacts, animation jobs, Manim/Remotion template registries, worker. | Generate compact scene specs, examples, labels, formulas, narration text. |
| Posttest and mastery update | Posttest routes/services and workspace mastery helpers. | Generate and evaluate targeted questions aligned to the remediation path. |
| Daily evaluation | Daily evaluation endpoints, report snapshots, mastery/review data. | Generate review questions and feedback for scheduled concepts. |

## Media Pipeline

The media pipeline is intentionally template-guided:

1. Backend creates a media artifact and render job.
2. Gemma/OpenRouter can generate a compact scene specification.
3. A tested Manim or Remotion template renders the visual.
4. gTTS/manim-voiceover can generate narration.
5. FFmpeg/FFprobe finalize, inspect, thumbnail, and duration-check output.
6. Storage writes to local disk or Supabase Storage.
7. Mobile polls the artifact status and plays `video_url`.

This avoids fragile raw animation-code generation while still allowing personalized examples, labels, language, and narration.

## Testing

Run all tests:

```powershell
python -m pytest
```

Run focused groups:

```powershell
python -m pytest tests\api
python -m pytest tests\services
python -m pytest tests\modules
```

Run one file:

```powershell
python -m pytest tests\api\test_workspace_api.py
```

## Troubleshooting

### `OPENROUTER_API_KEY is missing`

Add a valid key to `.env`:

```dotenv
OPENROUTER_API_KEY=replace-with-your-openrouter-key
```

### Supabase token verification fails

Check:

- `SUPABASE_PROJECT_URL`
- `SUPABASE_JWKS_URL`
- `SUPABASE_ISSUER`
- `SUPABASE_JWT_AUDIENCE`
- `SUPABASE_ANON_KEY`
- `SUPABASE_JWT_SECRET` only if your Supabase project still signs access tokens with HS256

### Supabase storage upload fails

If `MEDIA_STORAGE_BACKEND=supabase`, set:

```dotenv
SUPABASE_SERVICE_ROLE_KEY=replace-with-service-role-key
MEDIA_STORAGE_SUPABASE_BUCKET=media-artifacts
```

The bucket must exist in Supabase Storage.

### Redis connection fails

For local demos, avoid Redis:

```dotenv
MEDIA_JOB_QUEUE_BACKEND=noop
```

For Redis-backed workers, start Redis and set:

```dotenv
MEDIA_JOB_QUEUE_BACKEND=redis
REDIS_URL=redis://localhost:6379/0
```

### FFmpeg or FFprobe not found

Install FFmpeg and confirm:

```powershell
ffmpeg -version
ffprobe -version
```

Or point directly to binaries:

```dotenv
MEDIA_FFMPEG_BINARY=C:\path\to\ffmpeg.exe
MEDIA_FFPROBE_BINARY=C:\path\to\ffprobe.exe
```

### `ModuleNotFoundError: No module named 'pkg_resources'`

Install render extras:

```powershell
python -m pip install -e ".[render]"
```

## Project Links

- Mobile repository: https://github.com/brianaltan/wicara-mobile
- Backend repository: https://github.com/Nadekoooo/WICARA-BE
- Demo video: https://www.youtube.com/watch?v=7fYYomch5Wk
- Kaggle writeup: https://www.kaggle.com/competitions/gemma-4-good-hackathon/writeups/wicara-adaptive-ai-tutoring-gemma

## Authors

- Rahardi Salim
- Anthony Edbert Feriyanto
- Christian Yudistira Hermawan
- Vincent Davis Leonard
- Brian Altan

## License and Citation

The writeup text is released under the Attribution 4.0 International (CC BY 4.0) license.

Citation:

```text
Rahardi Salim, Anthony Edbert Feriyanto, Christian Yudistira Hermawan,
Brian Altan, Vincent Davis Leonard. Wicara: A Learning System That Finds
What Students Are Missing.
https://www.kaggle.com/competitions/gemma-4-good-hackathon/writeups/wicara-adaptive-ai-tutoring-gemma.
2026. Kaggle.
```
