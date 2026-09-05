"""Video Path A — Gemini Native Video (가이드 11.1 / 49.1 / 51절).

10초 내외 블랙박스 MP4를 직접 입력하여 1회 통합 분석을 수행한다.
focus 재분석은 같은 영상에 focus 프롬프트와 더 높은 fps를 적용한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from models.clients import GeminiVideoClient
from prompts.loader import load_prompt
from settings import VideoSettings, get_settings
from telemetry import RunLogger
from video.base import BaseVideoAnalyzer, ProgressCallback, build_focus_prompt
from video.schemas import VideoObservation, VideoResult
from video.validation import FocusTarget


class GeminiVideoAnalyzer(BaseVideoAnalyzer):
    backend = "gemini_native"

    def __init__(
        self,
        client: Optional[GeminiVideoClient] = None,
        *,
        settings: Optional[VideoSettings] = None,
        run_logger: Optional[RunLogger] = None,
        clip_focus_window: bool = False,
    ):
        super().__init__(settings=settings, run_logger=run_logger)
        app = get_settings()
        self.client = client or GeminiVideoClient(
            self.settings.gemini_model,
            api_key=app.gemini_api_key,
            fps=self.settings.gemini_fps,
            inline_max_mb=self.settings.inline_upload_max_mb,
            processing_timeout=self.settings.processing_timeout_sec,
            retries=self.settings.retries,
            use_remote_schema=self.settings.gemini_remote_schema,
            max_output_tokens=self.settings.gemini_max_output_tokens,
        )
        self.model = getattr(self.client, "model", self.settings.gemini_model)
        # clip을 켜면 Gemini가 구간만 보게 되어 timestamp 기준이 달라질 수 있으므로 기본은 끔
        self.clip_focus_window = clip_focus_window

    def analyze(
        self,
        video_path: str | Path,
        *,
        focus: Optional[FocusTarget] = None,
        previous_result: Optional[VideoResult] = None,
        extra_context: str = "",
        tracking_context: Optional[Any] = None,
        progress: ProgressCallback = None,
        case_id: Optional[str] = None,
    ) -> VideoResult:
        video_hash, duration = self.video_identity(video_path)
        system = self.system_prompt()
        start_sec = end_sec = None
        fps = self.settings.gemini_fps

        if focus is None:
            task = load_prompt("video_agent", "full_analysis")
            prompt_text = task.render(
                duration_sec=f"{duration:.1f}" if duration else "약 10",
                extra_context=extra_context or "없음",
            )
            prompt_version = f"{system.version_id}+{task.version_id}"
            pass_type = "full"
        else:
            prompt_text, focus_versions = build_focus_prompt(focus, previous_result)
            prompt_version = f"{system.version_id}+{focus_versions}"
            pass_type = "focus"
            fps = self.settings.gemini_focus_fps
            if self.clip_focus_window and focus.start_sec is not None and focus.end_sec is not None:
                start_sec, end_sec = focus.start_sec, focus.end_sec

        if tracking_context is not None:
            tracking_text, tracking_version = self.tracking_prompt(tracking_context)
            prompt_text = f"{prompt_text}\n\n{tracking_text}"
            prompt_version += f"+{tracking_version}"
            pass_type = "tracking"

        response = self.client.analyze_video(
            video_path=video_path,
            system=system.text,
            prompt=prompt_text,
            schema=VideoObservation,
            task=f"video_{pass_type}",
            start_sec=start_sec,
            end_sec=end_sec,
            fps=fps,
            progress=progress,
        )
        result = self.finalize(
            response.data,
            video_path=video_path,
            video_hash=video_hash,
            duration_sec=duration,
            prompt_version=prompt_version,
            metrics=response.metrics,
            pass_type=pass_type,
            focus=focus,
            previous_result=previous_result,
        )
        self.log(
            task=f"video_{pass_type}",
            prompt_version=prompt_version,
            metrics=response.metrics,
            case_id=case_id,
            extra={
                "video_hash": video_hash,
                "backend": self.backend,
                "focus": focus.kind if focus else None,
                "completion": result.analysis_completion.model_dump(),
            },
        )
        return result
