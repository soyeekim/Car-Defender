"""Car-Defender AI 설정.

모델명, 임계값, 프롬프트 버전은 코드에 하드코딩하지 않고 `.env`와
`config/prompt_versions.json`에서 읽는다. 레거시 변수명(VIDEO_MODEL,
REASONING_MODEL, DOCUMENT_MODEL)은 새 변수명이 없을 때 fallback으로 사용한다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

AI_ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = AI_ROOT / "prompts"
CONFIG_DIR = AI_ROOT / "config"
DATA_DIR = AI_ROOT / "data"
LOGS_DIR = AI_ROOT / "logs"

load_dotenv(AI_ROOT / ".env")
load_dotenv()


def _env(name: str, default: Optional[str] = None, *fallbacks: str) -> Optional[str]:
    value = os.getenv(name)
    if value:
        return value
    for fallback in fallbacks:
        value = os.getenv(fallback)
        if value:
            return value
    return default


def _env_float(name: str, default: float, *fallbacks: str) -> float:
    raw = _env(name, None, *fallbacks)
    try:
        return float(raw) if raw is not None else default
    except ValueError:
        return default


def _env_int(name: str, default: int, *fallbacks: str) -> int:
    raw = _env(name, None, *fallbacks)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, None)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class VideoSettings:
    default_backend: str = "gemini_native"
    gemini_model: str = "gemini-3.8-flash"
    gpt_vision_model: str = "gpt-4.1"
    gemini_fps: float = 5.0
    gemini_focus_fps: float = 10.0
    gemini_remote_schema: bool = False
    gemini_max_output_tokens: int = 16384
    inline_upload_max_mb: float = 19.0
    processing_timeout_sec: float = 300.0
    retries: int = 3
    default_frame_interval_sec: float = 0.5
    high_res_frame_interval_sec: float = 0.25
    dense_frame_interval_sec: float = 0.1
    frame_max_width: int = 768
    frame_detail: str = "high"
    collision_pair_confidence_threshold: float = 0.80
    completion_score_threshold: int = 80
    max_reanalysis_rounds: int = 1
    factor_sweep: bool = True
    enable_cv_tracking: bool = False
    enable_cache: bool = True
    cache_dir: Path = DATA_DIR / "cache" / "video"
    frames_dir: Path = DATA_DIR / "cache" / "frames"


@dataclass
class AgentSettings:
    master_model: str = "gpt-4.1"
    document_model: str = "gpt-4.1"
    master_temperature: float = 0.0
    document_temperature: float = 0.2
    recent_message_window: int = 8
    max_questions_per_turn: int = 1
    max_fact_question_rounds: int = 2
    max_review_rounds: int = 3
    max_master_rechecks: int = 0
    max_critical_rechecks: int = 1
    llm_sufficiency_refinement: bool = True


@dataclass
class RagSettings:
    embedding_model: str = "text-embedding-3-small"
    reranker_model: Optional[str] = None
    index_dir: Path = DATA_DIR / "rag_index"
    case_documents_path: Path = DATA_DIR / "rag_index" / "case_documents.jsonl"
    candidate_top_k: int = 20
    final_top_k: int = 3
    deliberation_first: bool = True
    min_case_relevance: float = 0.5
    min_semantic_score: float = 0.3


@dataclass
class Settings:
    openai_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    video: VideoSettings = field(default_factory=VideoSettings)
    agent: AgentSettings = field(default_factory=AgentSettings)
    rag: RagSettings = field(default_factory=RagSettings)
    prompt_versions: dict[str, str] = field(default_factory=dict)
    cases_dir: Path = DATA_DIR / "cases"
    logs_dir: Path = LOGS_DIR
    run_log_path: Path = LOGS_DIR / "agent_runs.jsonl"


def load_prompt_versions(path: Optional[Path] = None) -> dict[str, str]:
    target = path or (CONFIG_DIR / "prompt_versions.json")
    if not target.is_file():
        return {}
    data = json.loads(target.read_text(encoding="utf-8"))
    versions = data.get("prompt_versions", data)
    return {str(key): str(value) for key, value in versions.items()}


def build_settings() -> Settings:
    video = VideoSettings(
        default_backend=_env("VIDEO_BACKEND", "gemini_native") or "gemini_native",
        gemini_model=_env("GEMINI_VIDEO_MODEL", "gemini-3.8-flash", "VIDEO_MODEL") or "gemini-3.8-flash",
        gpt_vision_model=_env("GPT_VISION_MODEL", "gpt-4.1", "MASTER_AGENT_MODEL", "REASONING_MODEL") or "gpt-4.1",
        gemini_fps=_env_float("VIDEO_FPS", 5.0),
        gemini_focus_fps=_env_float("VIDEO_FOCUS_FPS", 10.0),
        # Gemini는 schema 복잡도 제한이 있어 전체 Video schema(~250 properties)를 response_json_schema로 거부한다.
        # 기본은 prompt schema + 로컬 pydantic 검증이며, 작은 schema/새 모델에서만 opt-in 한다.
        gemini_remote_schema=_env_bool("GEMINI_REMOTE_SCHEMA", False),
        gemini_max_output_tokens=_env_int("GEMINI_MAX_OUTPUT_TOKENS", 16384),
        inline_upload_max_mb=_env_float("GEMINI_INLINE_MAX_MB", 19.0),
        processing_timeout_sec=_env_float("VIDEO_PROCESSING_TIMEOUT", 300.0),
        retries=_env_int("VIDEO_ANALYSIS_RETRIES", 3),
        default_frame_interval_sec=_env_float("GPT_FRAME_INTERVAL_SEC", 0.5),
        high_res_frame_interval_sec=_env_float("GPT_HIGH_RES_FRAME_INTERVAL_SEC", 0.25),
        dense_frame_interval_sec=_env_float("GPT_DENSE_FRAME_INTERVAL_SEC", 0.1),
        frame_max_width=_env_int("GPT_FRAME_MAX_WIDTH", 768),
        frame_detail=_env("GPT_FRAME_DETAIL", "high") or "high",
        collision_pair_confidence_threshold=_env_float("COLLISION_PAIR_CONFIDENCE_THRESHOLD", 0.80),
        completion_score_threshold=_env_int("VIDEO_COMPLETION_SCORE_THRESHOLD", 80),
        max_reanalysis_rounds=_env_int("VIDEO_MAX_REANALYSIS_ROUNDS", 1),
        factor_sweep=_env_bool("VIDEO_FACTOR_SWEEP", True),
        enable_cv_tracking=_env_bool("ENABLE_CV_TRACKING", False),
        enable_cache=_env_bool("VIDEO_RESULT_CACHE", True),
        cache_dir=Path(_env("VIDEO_CACHE_DIR", str(DATA_DIR / "cache" / "video"))),
        frames_dir=Path(_env("FRAME_CACHE_DIR", str(DATA_DIR / "cache" / "frames"))),
    )
    agent = AgentSettings(
        master_model=_env("MASTER_AGENT_MODEL", "gpt-4.1", "REASONING_MODEL", "INTAKE_MODEL") or "gpt-4.1",
        document_model=_env("DOCUMENT_AGENT_MODEL", "gpt-4.1", "DOCUMENT_MODEL") or "gpt-4.1",
        master_temperature=_env_float("MASTER_AGENT_TEMPERATURE", 0.0),
        document_temperature=_env_float("DOCUMENT_AGENT_TEMPERATURE", 0.2),
        recent_message_window=_env_int("RECENT_MESSAGE_WINDOW", 8),
        max_questions_per_turn=max(1, _env_int("MAX_QUESTIONS_PER_TURN", 1)),
        max_fact_question_rounds=max(0, _env_int("MAX_FACT_QUESTION_ROUNDS", 2)),
        max_review_rounds=max(0, _env_int("MAX_REVIEW_ROUNDS", 3)),
        # 2차 분석(gap fill)이 1차 직후 이미 수행되므로 대화 중 Agent 재분석은 기본 0회.
        # 사용자가 "영상 다시 확인해줘"라고 명시하거나 충돌 차량 식별 같은 critical 공백은 별도로 허용된다.
        max_master_rechecks=max(0, _env_int("MASTER_MAX_RECHECKS", 0)),
        llm_sufficiency_refinement=_env_bool("LLM_SUFFICIENCY_REFINEMENT", True),
    )
    rag = RagSettings(
        embedding_model=_env("EMBEDDING_MODEL", "text-embedding-3-small") or "text-embedding-3-small",
        reranker_model=_env("RERANKER_MODEL", None),
        index_dir=Path(_env("RAG_INDEX_DIR", str(DATA_DIR / "rag_index"))),
        case_documents_path=Path(
            _env("CASE_DOCUMENTS_PATH", str(DATA_DIR / "rag_index" / "case_documents.jsonl"))
        ),
        candidate_top_k=_env_int("RAG_CANDIDATE_TOP_K", 20),
        final_top_k=max(1, _env_int("RAG_FINAL_TOP_K", 3)),
        deliberation_first=_env_bool("RAG_DELIBERATION_FIRST", True),
        min_case_relevance=_env_float("RAG_MIN_CASE_RELEVANCE", 0.5),
        min_semantic_score=_env_float("RAG_MIN_SEMANTIC_SCORE", 0.3),
    )
    return Settings(
        openai_api_key=_env("OPENAI_API_KEY"),
        gemini_api_key=_env("GEMINI_API_KEY"),
        video=video,
        agent=agent,
        rag=rag,
        prompt_versions=load_prompt_versions(),
        cases_dir=Path(_env("CASE_DB_PATH", str(DATA_DIR / "cases"))),
        logs_dir=LOGS_DIR,
        run_log_path=Path(_env("AGENT_RUN_LOG_PATH", str(LOGS_DIR / "agent_runs.jsonl"))),
    )


_settings: Optional[Settings] = None


def get_settings(reload: bool = False) -> Settings:
    global _settings
    if _settings is None or reload:
        _settings = build_settings()
    return _settings
