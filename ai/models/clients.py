"""모델 Client 추상화 (가이드 120절).

- OpenAITextClient : Master / Document Agent용 텍스트 + Structured Output
- GPTVisionClient  : GPT Frames Video Path용 (timestamped frame 이미지 시퀀스 입력)
- GeminiVideoClient: Gemini Native Video Path용 (MP4 직접 입력)

모든 client는 JSON 응답을 로컬 pydantic schema로 검증하고, 실패 시 1회 repair 호출을 수행한다.
모델명은 settings(.env)에서 주입하며 여기서는 하드코딩하지 않는다.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol, Sequence

from pydantic import BaseModel, ValidationError

from common.jsonutil import compact_json, parse_json_object
from telemetry import CallMetrics


@dataclass
class JSONResponse:
    data: dict[str, Any]
    metrics: CallMetrics
    raw_text: str = ""


@dataclass
class TextResponse:
    text: str
    metrics: CallMetrics


class TextClient(Protocol):
    model: str

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        schema: Any = None,
        task: str = "",
        temperature: Optional[float] = None,
    ) -> JSONResponse: ...

    def generate_text(
        self,
        *,
        system: str,
        user: str,
        task: str = "",
        temperature: Optional[float] = None,
    ) -> TextResponse: ...


class FrameLike(Protocol):
    path: Path
    label: str


def is_transient_error(exc: Exception) -> bool:
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
            "rate limit",
            "unavailable",
            "high demand",
            "temporarily",
            "overloaded",
            "timeout",
            "timed out",
        )
    )


def schema_to_dict(schema: Any) -> Optional[dict[str, Any]]:
    if schema is None:
        return None
    if isinstance(schema, dict):
        return schema
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_json_schema()
    raise TypeError("schema는 pydantic 모델 클래스 또는 dict여야 합니다.")


def validate_against(schema: Any, data: dict[str, Any]) -> dict[str, Any]:
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_validate(data).model_dump()
    return data


def compact_schema_for_prompt(schema: dict[str, Any], *, inline_refs: bool = False) -> dict[str, Any]:
    """prompt에 삽입할 schema: title/default 제거, anyOf[T,null] → nullable, (선택) $ref 인라인.

    Observation 같은 공통 정의는 $defs로 두는 편이 짧다(인라인하면 30회 반복). 입력 토큰을 줄이고
    모델이 따라가기 쉬운 형태로 만든다.
    """
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node and inline_refs:
            merged = dict(defs.get(node["$ref"].split("/")[-1], {}))
            for key, value in node.items():
                if key != "$ref":
                    merged.setdefault(key, value)
            return resolve(merged)
        drop = {"title", "default", "examples"} | ({"$defs"} if inline_refs else set())
        out = {key: resolve(value) for key, value in node.items() if key not in drop}
        if "anyOf" in out:
            options = [item for item in out["anyOf"] if not (isinstance(item, dict) and item.get("type") == "null")]
            if len(options) == 1:
                base = dict(options[0])
                base.update({key: value for key, value in out.items() if key != "anyOf"})
                out = base
                if len(options) != len(node.get("anyOf", [])):
                    out["nullable"] = True
        return out

    return resolve(schema)


def schema_instruction(schema_dict: Optional[dict[str, Any]]) -> str:
    if not schema_dict:
        return "\n\n반드시 JSON 객체 하나만 출력하라."
    return (
        "\n\n반드시 다음 JSON Schema와 호환되는 JSON 객체 하나만 출력하라. 설명문을 덧붙이지 않는다. "
        "nullable 필드는 확인 불가 시 null로 둔다.\n"
        + compact_json(compact_schema_for_prompt(schema_dict))
    )


def _sleep_backoff(attempt: int) -> None:
    time.sleep(min(2 * (2**attempt), 15))


# --------------------------------------------------------------------------- OpenAI


class OpenAITextClient:
    def __init__(
        self,
        model: str,
        *,
        api_key: Optional[str] = None,
        temperature: float = 0.0,
        max_retries: int = 2,
        timeout: float = 120.0,
    ):
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout = timeout
        self._client = None

    def _sdk(self):
        if self._client is None:
            if not self.api_key:
                raise RuntimeError("OPENAI_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요.")
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key, timeout=self.timeout)
        return self._client

    @staticmethod
    def _usage(response) -> tuple[int, int, int]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return 0, 0, 0
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        total = int(getattr(usage, "total_tokens", 0) or (prompt + completion))
        return prompt, completion, total

    def _create(
        self,
        messages: list[dict[str, Any]],
        *,
        response_format: Optional[dict[str, Any]],
        temperature: float,
        max_output_tokens: Optional[int],
    ):
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format:
            kwargs["response_format"] = response_format
        if max_output_tokens:
            kwargs["max_completion_tokens"] = max_output_tokens
        return self._sdk().chat.completions.create(**kwargs)

    def _call_with_retry(self, messages, *, response_format, temperature, max_output_tokens):
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._create(
                    messages,
                    response_format=response_format,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                ), attempt + 1
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt >= self.max_retries or not is_transient_error(exc):
                    raise
                _sleep_backoff(attempt)
        raise RuntimeError(f"OpenAI 호출 실패: {last_exc}")

    def _json_request(
        self,
        messages: list[dict[str, Any]],
        *,
        schema: Any,
        task: str,
        temperature: Optional[float],
        max_output_tokens: Optional[int],
        repair: bool,
    ) -> JSONResponse:
        schema_dict = schema_to_dict(schema)
        temp = self.temperature if temperature is None else temperature
        started = time.monotonic()
        structured_mode = "json_object"
        attempts = 0
        response = None

        if schema_dict:
            try:
                response, attempts = self._call_with_retry(
                    messages,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": (task or "output")[:60].replace("/", "_") or "output",
                            "schema": schema_dict,
                            "strict": False,
                        },
                    },
                    temperature=temp,
                    max_output_tokens=max_output_tokens,
                )
                structured_mode = "json_schema"
            except Exception as exc:  # noqa: BLE001
                if is_transient_error(exc):
                    raise
                response = None

        if response is None:
            fallback_messages = [dict(item) for item in messages]
            last = fallback_messages[-1]
            if isinstance(last.get("content"), str):
                last["content"] = last["content"] + schema_instruction(schema_dict)
            elif isinstance(last.get("content"), list):
                last["content"] = list(last["content"]) + [
                    {"type": "text", "text": schema_instruction(schema_dict)}
                ]
            response, attempts = self._call_with_retry(
                fallback_messages,
                response_format={"type": "json_object"},
                temperature=temp,
                max_output_tokens=max_output_tokens,
            )
            structured_mode = "json_object"

        text = response.choices[0].message.content or ""
        prompt_tokens, completion_tokens, total_tokens = self._usage(response)
        try:
            data = validate_against(schema, parse_json_object(text))
        except (ValueError, ValidationError, json.JSONDecodeError) as exc:
            if not repair:
                raise
            repair_messages = list(messages) + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": (
                        "직전 출력이 요구된 JSON schema를 만족하지 않았다. 오류:\n"
                        f"{str(exc)[:1500]}\n\n같은 내용을 schema에 맞는 JSON 객체 하나로만 다시 출력하라."
                        + schema_instruction(schema_dict)
                    ),
                },
            ]
            response, extra_attempts = self._call_with_retry(
                repair_messages,
                response_format={"type": "json_object"},
                temperature=0.0,
                max_output_tokens=max_output_tokens,
            )
            attempts += extra_attempts
            text = response.choices[0].message.content or ""
            p2, c2, t2 = self._usage(response)
            prompt_tokens, completion_tokens, total_tokens = (
                prompt_tokens + p2,
                completion_tokens + c2,
                total_tokens + t2,
            )
            data = validate_against(schema, parse_json_object(text))
            structured_mode += "+repair"

        metrics = CallMetrics(
            model=self.model,
            latency_sec=time.monotonic() - started,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            total_tokens=total_tokens,
            attempts=attempts,
            structured_mode=structured_mode,
        )
        return JSONResponse(data=data, metrics=metrics, raw_text=text)

    def generate_json(
        self,
        *,
        system: str,
        user: str,
        schema: Any = None,
        task: str = "",
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        repair: bool = True,
    ) -> JSONResponse:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self._json_request(
            messages,
            schema=schema,
            task=task,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            repair=repair,
        )

    def generate_text(
        self,
        *,
        system: str,
        user: str,
        task: str = "",
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
    ) -> TextResponse:
        temp = self.temperature if temperature is None else temperature
        started = time.monotonic()
        response, attempts = self._call_with_retry(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=None,
            temperature=temp,
            max_output_tokens=max_output_tokens,
        )
        text = (response.choices[0].message.content or "").strip()
        prompt_tokens, completion_tokens, total_tokens = self._usage(response)
        return TextResponse(
            text=text,
            metrics=CallMetrics(
                model=self.model,
                latency_sec=time.monotonic() - started,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                total_tokens=total_tokens,
                attempts=attempts,
                structured_mode="text",
            ),
        )


def image_to_data_uri(path: str | Path) -> str:
    file_path = Path(path)
    mime = mimetypes.guess_type(file_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class GPTVisionClient(OpenAITextClient):
    """timestamped frame 시퀀스를 GPT Vision 모델에 입력한다."""

    def analyze_frames(
        self,
        *,
        system: str,
        prompt: str,
        frames: Sequence[FrameLike],
        schema: Any = None,
        task: str = "",
        detail: str = "high",
        temperature: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
    ) -> JSONResponse:
        if not frames:
            raise ValueError("분석할 프레임이 없습니다.")
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for frame in frames:
            content.append({"type": "text", "text": frame.label})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_to_data_uri(frame.path), "detail": detail},
                }
            )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]
        return self._json_request(
            messages,
            schema=schema,
            task=task,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            repair=True,
        )


# --------------------------------------------------------------------------- Gemini


def _file_state_name(uploaded) -> str:
    state = getattr(uploaded, "state", None)
    if state is None:
        return "STATE_UNSPECIFIED"
    return getattr(state, "name", str(state).split(".")[-1])


def _is_schema_compatibility_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "400" in message and ("invalid_argument" in message or "schema" in message)


def _dump_raw_response(text: str, *, tag: str) -> Optional[Path]:
    """파싱 실패한 원문을 logs/에 남겨 prompt/모델 디버깅에 쓴다."""
    try:
        from settings import get_settings

        directory = get_settings().logs_dir / "raw_responses"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{tag}_{int(time.time())}.txt"
        path.write_text(text or "", encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001
        return None


class GeminiVideoClient:
    def __init__(
        self,
        model: str,
        *,
        api_key: Optional[str] = None,
        fps: float = 5.0,
        inline_max_mb: float = 19.0,
        processing_timeout: float = 300.0,
        retries: int = 3,
        sdk_retry_attempts: int = 1,
        use_remote_schema: bool = True,
        max_output_tokens: int = 16384,
    ):
        self.model = model
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.fps = fps
        self.max_output_tokens = max_output_tokens
        self.inline_max_mb = inline_max_mb
        self.processing_timeout = processing_timeout
        self.retries = retries
        self.sdk_retry_attempts = max(1, sdk_retry_attempts)
        self.use_remote_schema = use_remote_schema
        self.last_schema_error: Optional[str] = None
        self._client = None

    def _sdk(self):
        if self._client is None:
            if not self.api_key:
                raise RuntimeError("GEMINI_API_KEY가 설정되어 있지 않습니다. .env 파일을 확인하세요.")
            from google import genai
            from google.genai import types

            self._client = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(
                    retry_options=types.HttpRetryOptions(attempts=self.sdk_retry_attempts)
                ),
            )
        return self._client

    @staticmethod
    def _usage(response) -> tuple[int, int, int]:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return 0, 0, 0
        prompt = int(getattr(usage, "prompt_token_count", 0) or 0)
        completion = int(getattr(usage, "candidates_token_count", 0) or 0)
        total = int(getattr(usage, "total_token_count", 0) or (prompt + completion))
        return prompt, completion, total

    def _video_part(self, client, path: Path, *, start_sec, end_sec, fps, progress):
        from google.genai import types

        mime = mimetypes.guess_type(path.name)[0] or "video/mp4"
        if not mime.startswith("video/"):
            mime = "video/mp4"
        size_mb = path.stat().st_size / (1024 * 1024)
        uploaded = None
        if size_mb <= self.inline_max_mb:
            part = types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)
        else:
            if progress:
                progress(f"Gemini에 영상 업로드 중: {path.name} ({size_mb:.1f}MB)")
            uploaded = client.files.upload(
                file=path, config={"mime_type": mime, "display_name": path.name}
            )
            deadline = time.monotonic() + self.processing_timeout
            while True:
                state_name = _file_state_name(uploaded)
                if state_name == "ACTIVE":
                    break
                if state_name == "FAILED":
                    raise RuntimeError("Gemini가 업로드된 영상 처리에 실패했습니다.")
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Gemini 영상 처리 시간이 {self.processing_timeout:.0f}초를 초과했습니다."
                    )
                time.sleep(2)
                uploaded = client.files.get(name=uploaded.name)
            part = types.Part.from_uri(file_uri=uploaded.uri, mime_type=uploaded.mime_type or mime)

        metadata_kwargs: dict[str, Any] = {"fps": float(fps or self.fps)}
        if start_sec is not None:
            metadata_kwargs["start_offset"] = f"{max(0.0, float(start_sec)):.3f}s"
        if end_sec is not None:
            metadata_kwargs["end_offset"] = f"{max(0.0, float(end_sec)):.3f}s"
        part.video_metadata = types.VideoMetadata(**metadata_kwargs)
        return part, uploaded

    def analyze_video(
        self,
        *,
        video_path: str | Path,
        system: str,
        prompt: str,
        schema: Any = None,
        task: str = "",
        start_sec: Optional[float] = None,
        end_sec: Optional[float] = None,
        fps: Optional[float] = None,
        progress=None,
        temperature: float = 0.0,
    ) -> JSONResponse:
        from google.genai import types

        path = Path(video_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"영상 파일을 찾을 수 없습니다: {path}")
        client = self._sdk()
        schema_dict = schema_to_dict(schema)
        started = time.monotonic()
        uploaded = None
        try:
            part, uploaded = self._video_part(
                client, path, start_sec=start_sec, end_sec=end_sec, fps=fps, progress=progress
            )
            use_schema = bool(schema_dict) and self.use_remote_schema
            response = None
            attempts = 0
            for attempt in range(self.retries + 1):
                attempts += 1
                config_kwargs: dict[str, Any] = {
                    "system_instruction": system,
                    "temperature": temperature,
                    "response_mime_type": "application/json",
                    "max_output_tokens": self.max_output_tokens,
                    "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
                }
                text_prompt = prompt
                if use_schema:
                    config_kwargs["response_json_schema"] = schema_dict
                else:
                    text_prompt = prompt + schema_instruction(schema_dict)
                try:
                    if progress:
                        progress(f"{self.model}로 영상 분석 중 (attempt {attempts}, schema={'remote' if use_schema else 'prompt'})")
                    response = client.models.generate_content(
                        model=self.model,
                        contents=[part, text_prompt],
                        config=types.GenerateContentConfig(**config_kwargs),
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    if use_schema and _is_schema_compatibility_error(exc):
                        # 이 모델이 schema를 거부하면 이후 호출은 처음부터 prompt schema를 쓴다 (호출 낭비 방지)
                        use_schema = False
                        self.use_remote_schema = False
                        self.last_schema_error = str(exc)[:400]
                        if progress:
                            progress("Gemini가 response_json_schema를 거부하여 prompt schema 모드로 전환")
                        continue
                    if attempt >= self.retries or not is_transient_error(exc):
                        raise
                    _sleep_backoff(attempt)
            if response is None:
                raise RuntimeError("Gemini 영상 분석 응답을 받지 못했습니다.")

            text = response.text or ""
            try:
                parsed = getattr(response, "parsed", None)
                raw = parsed if isinstance(parsed, dict) else parse_json_object(text)
                data = validate_against(schema, raw)
                mode = "response_json_schema" if use_schema else "prompt_schema"
            except (ValueError, ValidationError, json.JSONDecodeError) as exc:
                dump_path = _dump_raw_response(text, tag="gemini")
                if progress:
                    progress(f"Gemini 출력이 schema/JSON을 만족하지 않아 repair 호출 (원문: {dump_path})")
                repair_prompt = (
                    text_prompt
                    + "\n\n직전 출력이 유효한 JSON 또는 schema를 만족하지 않았다. 오류:\n"
                    + str(exc)[:1500]
                    + "\n\n주석·후행 콤마·설명문 없이 schema에 맞는 완전한 JSON 객체 하나만 다시 출력하라. "
                    "문자열 안의 큰따옴표는 반드시 escape 한다."
                )
                response = client.models.generate_content(
                    model=self.model,
                    contents=[part, repair_prompt],
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=0.0,
                        response_mime_type="application/json",
                        max_output_tokens=self.max_output_tokens,
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    ),
                )
                attempts += 1
                text = response.text or ""
                try:
                    data = validate_against(schema, parse_json_object(text))
                except (ValueError, ValidationError, json.JSONDecodeError) as repair_exc:
                    dump_path = _dump_raw_response(text, tag="gemini_repair")
                    raise RuntimeError(
                        f"Gemini 영상 분석 출력을 repair 후에도 파싱하지 못했습니다: {str(repair_exc)[:200]} (원문: {dump_path})"
                    ) from repair_exc
                mode = "prompt_schema+repair"
            prompt_tokens, completion_tokens, total_tokens = self._usage(response)
            return JSONResponse(
                data=data,
                raw_text=text,
                metrics=CallMetrics(
                    model=self.model,
                    latency_sec=time.monotonic() - started,
                    input_tokens=prompt_tokens,
                    output_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    attempts=attempts,
                    structured_mode=mode,
                ),
            )
        finally:
            if uploaded is not None and getattr(uploaded, "name", None):
                try:
                    client.files.delete(name=uploaded.name)
                except Exception:  # noqa: BLE001
                    pass


# --------------------------------------------------------------------------- factories


def build_text_client(model: str, *, temperature: float = 0.0) -> OpenAITextClient:
    return OpenAITextClient(model, temperature=temperature)


def build_vision_client(model: str) -> GPTVisionClient:
    return GPTVisionClient(model, temperature=0.0)


def build_gemini_client(model: str, **kwargs) -> GeminiVideoClient:
    return GeminiVideoClient(model, **kwargs)
