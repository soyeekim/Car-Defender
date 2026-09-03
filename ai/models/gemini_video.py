import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv

from schemas.video_analysis import VIDEO_STATE_SLOTS, VideoAnalysisResult

load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_PATH = _ROOT / "prompts" / "analyze_video.txt"
_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name}가 설정되어 있지 않습니다. .env 파일을 확인하세요.")
    return value


def _file_state_name(uploaded_file) -> str:
    state = getattr(uploaded_file, "state", None)
    if state is None:
        return "STATE_UNSPECIFIED"
    return getattr(state, "name", str(state).split(".")[-1])


def _is_transient_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "429",
            "500",
            "502",
            "503",
            "504",
            "resource_exhausted",
            "unavailable",
            "high demand",
            "temporarily",
        )
    )


def _is_schema_compatibility_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "400" in message and "invalid_argument" in message


def analyze_video(
    video_path: str | Path,
    *,
    progress: Optional[Callable[[str], None]] = None,
    processing_timeout: Optional[float] = None,
    focus_factors: Optional[list[dict]] = None,
) -> VideoAnalysisResult:
    """Upload a dashcam video and return schema-validated objective observations."""
    path = Path(video_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"영상 파일을 찾을 수 없습니다: {path}")
    if path.suffix.lower() not in {".mp4", ".mov", ".avi", ".webm", ".mpeg", ".mpg"}:
        raise ValueError(f"지원하지 않는 영상 확장자입니다: {path.suffix}")

    api_key = _required_env("GEMINI_API_KEY")
    model = _required_env("VIDEO_MODEL")
    timeout = processing_timeout
    if timeout is None:
        timeout = float(os.getenv("VIDEO_PROCESSING_TIMEOUT", "300"))

    from google import genai
    from google.genai import types

    sdk_attempts = max(1, int(os.getenv("GEMINI_SDK_RETRY_ATTEMPTS", "1")))
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=sdk_attempts)
        ),
    )
    uploaded = None
    try:
        if progress:
            progress(f"Gemini에 영상 업로드 중: {path.name}")
        uploaded = client.files.upload(
            file=path,
            config={"mime_type": "video/mp4", "display_name": path.name},
        )

        deadline = time.monotonic() + timeout
        while True:
            state_name = _file_state_name(uploaded)
            if state_name == "ACTIVE":
                break
            if state_name == "FAILED":
                raise RuntimeError("Gemini가 업로드된 영상 처리에 실패했습니다.")
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Gemini 영상 처리 시간이 {timeout:.0f}초를 초과했습니다."
                )
            if progress:
                progress(f"Gemini 영상 처리 대기 중: {state_name}")
            time.sleep(2)
            uploaded = client.files.get(name=uploaded.name)

        if progress:
            progress(f"{model}로 사고 장면 분석 중")
        analysis_prompt = _PROMPT
        if focus_factors:
            analysis_prompt += (
                "\n\n이번 분석은 Fact Planning Agent가 요청한 다음 요소를 원본 영상에서 "
                "재확인하는 targeted pass입니다. 결론을 추측하지 말고 각 요소의 장면 근거를 "
                "집중 확인하세요.\n"
                + json.dumps(focus_factors, ensure_ascii=False)
            )
        video_fps = float(os.getenv("VIDEO_FPS", "5"))
        video_part = types.Part.from_uri(
            file_uri=uploaded.uri,
            mime_type=uploaded.mime_type or "video/mp4",
        )
        video_part.video_metadata = types.VideoMetadata(fps=video_fps)
        retries = max(0, int(os.getenv("VIDEO_ANALYSIS_RETRIES", "3")))
        response = None
        use_schema = os.getenv("VIDEO_REMOTE_SCHEMA", "false").lower() in {
            "1",
            "true",
            "yes",
        }
        for attempt in range(retries + 1):
            try:
                config = {
                    "temperature": 0,
                    "response_mime_type": "application/json",
                }
                prompt = analysis_prompt
                if use_schema:
                    config["response_json_schema"] = (
                        VideoAnalysisResult.model_json_schema()
                    )
                else:
                    prompt = (
                        f"{analysis_prompt}\n\n반드시 다음 JSON Schema와 호환되는 JSON 객체만 "
                        "출력하세요.\n"
                        + json.dumps(
                            VideoAnalysisResult.model_json_schema(),
                            ensure_ascii=False,
                        )
                    )
                response = client.models.generate_content(
                    model=model,
                    contents=[video_part, prompt],
                    config=config,
                )
                break
            except Exception as exc:
                if use_schema and _is_schema_compatibility_error(exc):
                    use_schema = False
                    if progress:
                        progress(
                            "Gemini 스키마 호환 모드로 전환하여 로컬 검증 후 재시도"
                        )
                    continue
                if attempt >= retries or not _is_transient_error(exc):
                    raise
                delay = min(3 * (2**attempt), 15)
                if progress:
                    progress(
                        f"Gemini 일시 오류로 {delay}초 후 재시도 "
                        f"({attempt + 1}/{retries})"
                    )
                time.sleep(delay)

        if response is None:
            raise RuntimeError("Gemini 영상 분석 응답을 받지 못했습니다.")

        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, VideoAnalysisResult):
            return parsed
        if isinstance(parsed, dict):
            return VideoAnalysisResult.model_validate(parsed)
        if not response.text:
            raise ValueError("Gemini 영상 분석 응답이 비어 있습니다.")
        return VideoAnalysisResult.model_validate_json(response.text)
    finally:
        if uploaded is not None and getattr(uploaded, "name", None):
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
        client.close()


def _vision_value_score(item) -> float:
    if item.value is None:
        return -1.0
    source_weight = {
        "sensor_readout": 3.0,
        "direct_visual": 3.0,
        "inferred": 1.0,
        "not_observable": 0.0,
    }[item.observation_type]
    evidence_weight = 0.2 if item.evidence and item.timestamp else 0.0
    return source_weight + item.confidence + evidence_weight


def merge_video_analyses(
    initial: VideoAnalysisResult, targeted: VideoAnalysisResult
) -> VideoAnalysisResult:
    merged = initial.model_copy(deep=True)
    for fact_key in VIDEO_STATE_SLOTS:
        current = getattr(merged, fact_key)
        candidate = getattr(targeted, fact_key)
        if _vision_value_score(candidate) > _vision_value_score(current):
            setattr(merged, fact_key, candidate)

    events = {event.event_id: event for event in merged.timeline_events}
    events.update({event.event_id: event for event in targeted.timeline_events})
    merged.timeline_events = list(events.values())

    observations = {
        item.fact_key: item for item in merged.additional_observations
    }
    for item in targeted.additional_observations:
        previous = observations.get(item.fact_key)
        if previous is None or _vision_value_score(item) > _vision_value_score(previous):
            observations[item.fact_key] = item
    merged.additional_observations = list(observations.values())
    merged.observable_events = list(
        dict.fromkeys(merged.observable_events + targeted.observable_events)
    )
    merged.limitations = list(dict.fromkeys(merged.limitations + targeted.limitations))
    if targeted.summary and targeted.summary != initial.summary:
        merged.summary = f"{initial.summary}\nTargeted recheck: {targeted.summary}"
    return merged
