"""Video Analysis Agent — Fast Path / Conditional Fallback 정책 (가이드 50~63절).

    first = analyze(video)
    if validate(first): use
    else: focus reanalysis → merge → (CV tracking) → 사용자 객관적 확인
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from settings import Settings, get_settings
from telemetry import RunLogger, get_run_logger
from video.base import BaseVideoAnalyzer, get_video_analyzer
from video.cache import VideoResultCache
from video.frame_sampler import compute_video_hash
from video.merge import merge_video_results
from video.schemas import VideoResult
from video.tracking import run_cv_tracking
from video.validation import (
    FocusTarget,
    build_factor_sweep_focus,
    build_focus_targets,
    evaluate_video_completion,
    focus_pass_usable,
    participant_check,
    should_use_cv_tracking,
    structure_check,
    user_confirmation_question,
    validate_video_result,
)

ProgressCallback = Optional[Callable[[str], None]]


_SEVERE_STRUCTURE_CODES = {"road_type_unknown", "collision_window_missing", "collision_type_unknown"}
RETRY_NOTE = (
    "\n\n[시스템 참고] 직전 분석 응답은 요약문만 있고 road_environment.road_type, vehicles 의 movement, collision_window, "
    "collision_pair, collision.collision_type, timeline, confirmed_facts 가 비어 있었다. 이번에는 이 구조 필드를 모두 채우고, "
    "요약문에 언급한 차량은 vehicles 에 같은 ID로 넣어라."
)


def _structure_ok(result: Optional[VideoResult]) -> bool:
    """응답이 사고의 뼈대(도로 유형, 충돌 구간, 충돌 유형)를 채웠는지. 참가 차량 하나의 진행 방향이 비는 정도는
    (목록에서 빠진 차량을 서술문으로 보정한 경우) 정상으로 본다 — 실제 불량 응답은 충돌 자체가 비어 있었다."""
    if result is None:
        return False
    return not (_SEVERE_STRUCTURE_CODES & set(structure_check(result)))


@dataclass
class VideoDecision:
    status: str  # VALID | NEEDS_USER_CONFIRMATION | UNAVAILABLE
    result: Optional[VideoResult] = None
    passes: int = 0
    reasons: list[str] = field(default_factory=list)
    confirmation_question: Optional[str] = None
    error: Optional[str] = None
    sweep_focus: Optional[str] = None  # 1차 분석 직후 묶어서 재확인한 과실 요소 (있으면)


class VideoAnalysisAgent:
    def __init__(
        self,
        analyzer: Optional[BaseVideoAnalyzer] = None,
        *,
        settings: Optional[Settings] = None,
        cache: Optional[VideoResultCache] = None,
        run_logger: Optional[RunLogger] = None,
        backend: Optional[str] = None,
        factor_sweep: Optional[bool] = None,
    ):
        self.settings = settings or get_settings()
        self.video_settings = self.settings.video
        self.run_logger = run_logger or get_run_logger()
        self._analyzer = analyzer
        self._backend = backend
        self.cache = cache or VideoResultCache(self.video_settings.cache_dir, enabled=self.video_settings.enable_cache)
        self.threshold = self.video_settings.collision_pair_confidence_threshold
        # 1차 분석 직후 과실 요소를 한 번에 묶어 재확인 → 대화 중 영상 호출을 줄인다 (가이드 63절)
        self.factor_sweep = self.video_settings.factor_sweep if factor_sweep is None else factor_sweep

    @property
    def analyzer(self) -> BaseVideoAnalyzer:
        if self._analyzer is None:
            self._analyzer = get_video_analyzer(self._backend, settings=self.video_settings, run_logger=self.run_logger)
        return self._analyzer

    # ------------------------------------------------------------------ cache
    def _prompt_fingerprint(self) -> str:
        versions = {key: value for key, value in self.settings.prompt_versions.items() if key.startswith("video_")}
        return json.dumps(versions, sort_keys=True)

    def _cache_key(self, video_hash: str, focus: Optional[FocusTarget], extra: str = "") -> str:
        return VideoResultCache.make_key(
            video_hash=video_hash,
            backend=self.analyzer.backend,
            model=self.analyzer.model,
            prompt_version=self._prompt_fingerprint(),
            focus_key=focus.cache_key if focus else "",
            extra=extra,
        )

    # ------------------------------------------------------------------ analyze
    def analyze(
        self,
        video_path: str | Path,
        *,
        focus: Optional[FocusTarget | str] = None,
        previous_result: Optional[VideoResult] = None,
        case_id: Optional[str] = None,
        progress: ProgressCallback = None,
        extra_context: str = "",
        use_cache: bool = True,
    ) -> VideoResult:
        if isinstance(focus, str):
            focus = FocusTarget(kind="custom", question=focus)
        video_hash = compute_video_hash(video_path)
        key = self._cache_key(video_hash, focus, extra=extra_context[:200])
        if use_cache:
            cached = self.cache.get(key)
            if cached is not None and self._cached_is_stale(cached, focus, previous_result):
                # 예전 규칙으로 캐시된 부실 응답(충돌 구조가 빈 결과)은 재사용하지 않는다
                self.run_logger.log(agent="video_agent", task="video_cache_stale", case_id=case_id, model=self.analyzer.model,
                                    extra={"video_hash": video_hash, "focus": focus.kind if focus else None, "reasons": cached.analysis_completion.reasons[:3]})
                cached = None
            if cached is not None:
                if progress:
                    progress("캐시된 영상 분석 결과 사용")
                self.run_logger.log(agent="video_agent", task="video_cache_hit", case_id=case_id, model=self.analyzer.model, extra={"video_hash": video_hash, "focus": focus.kind if focus else None})
                return cached
        result = self.analyzer.analyze(
            video_path,
            focus=focus,
            previous_result=previous_result,
            extra_context=extra_context,
            progress=progress,
            case_id=case_id,
        )
        # 충돌 pair를 못 찾은 1차 결과는 캐시하지 않는다 — 나쁜 결과가 고정되면 이후 세션마다 같은 실패를 반복한다.
        # focus 응답도 기준 결과가 멀쩡한데 구조가 비어 돌아왔으면(모델 불안정) 캐시하지 않는다
        if focus is None:
            # 충돌 pair 는 찾았어도 도로·진행·충돌 구조를 못 채운 응답(모델 불안정)은 고정하지 않는다
            cacheable = validate_video_result(result, self.threshold)[0] and _structure_ok(result)
        else:
            cacheable = (focus_pass_usable(result) and (_structure_ok(result) or not _structure_ok(previous_result))) or previous_result is None or not focus_pass_usable(previous_result)
        if cacheable:
            self.cache.put(key, result)
        else:
            self.run_logger.log(agent="video_agent", task="video_cache_skip", case_id=case_id, model=self.analyzer.model,
                                extra={"video_hash": video_hash, "reasons": validate_video_result(result, self.threshold)[1]})
        return result

    @staticmethod
    def _cached_is_stale(cached: VideoResult, focus: Optional[FocusTarget], previous_result: Optional[VideoResult]) -> bool:
        if focus is None:
            return not _structure_ok(cached)
        return previous_result is not None and focus_pass_usable(previous_result) and (not focus_pass_usable(cached) or (_structure_ok(previous_result) and not _structure_ok(cached)))

    def _focus_analyze(self, video_path, focus: FocusTarget, previous: VideoResult, *, case_id, progress) -> Optional[VideoResult]:
        """focus 재분석 1회. 기준 결과가 멀쩡한데 응답의 차량·충돌 pair 구조가 비어 있으면 한 번 다시 묻고,
        그래도 비어 있으면 None (호출자는 기준 결과를 그대로 쓴다). 불량 응답은 analyze() 가 캐시하지 않는다."""
        baseline_ok = focus_pass_usable(previous)
        baseline_structure = _structure_ok(previous)
        for attempt in (1, 2):
            second = self.analyze(video_path, focus=focus, previous_result=previous, case_id=case_id, progress=progress, use_cache=(attempt == 1))
            if not baseline_ok or (focus_pass_usable(second) and (_structure_ok(second) or not baseline_structure)):
                return second
            self.run_logger.log(agent="video_agent", task="video_focus_degraded", case_id=case_id, model=self.analyzer.model,
                                extra={"attempt": attempt, "focus": focus.kind, "reasons": participant_check(second, threshold=0.0)})
            if attempt == 1 and progress:
                progress("재분석 응답이 불완전해서 한 번 더 확인")
        return None

    def recompute_completion(self, result: VideoResult) -> VideoResult:
        result.analysis_completion = evaluate_video_completion(
            result,
            threshold=self.threshold,
            score_threshold=self.video_settings.completion_score_threshold,
        )
        result.fault_relevant_factors_checked = result.analysis_completion.gate.get("fault_relevant_factor_check", False)
        return result

    def recheck(
        self,
        video_path: str | Path,
        previous_result: VideoResult,
        focus: FocusTarget | str,
        *,
        case_id: Optional[str] = None,
        progress: ProgressCallback = None,
    ) -> VideoResult:
        """특정 쟁점 focus 재분석 후 기존 결과와 병합한다."""
        if isinstance(focus, str):
            from video.validation import collision_window_seconds

            start, end = collision_window_seconds(previous_result)
            focus = FocusTarget(kind="custom", question=focus, start_sec=start, end_sec=end)
        second = self._focus_analyze(video_path, focus, previous_result, case_id=case_id, progress=progress)
        if second is None:
            return self.recompute_completion(previous_result)
        merged = merge_video_results(previous_result, second)
        return self.recompute_completion(merged)

    def _factor_sweep(self, video_path, result: VideoResult, passes: int, *, case_id, progress, sweep_planner=None):
        """충돌 pair가 확정된 뒤 영상을 한 번 더 본다.

        무엇을 다시 볼지는 sweep_planner(Master Agent의 LLM 추론)가 정하고, 없거나 실패하면
        코드 규칙(build_factor_sweep_focus)이 대신 정한다.
        """
        if not self.factor_sweep:
            return result, passes, None
        focus = None
        if sweep_planner is not None:
            try:
                focus = sweep_planner(result)
            except Exception as exc:  # noqa: BLE001
                self.run_logger.log(agent="video_agent", task="video_gap_plan_error", case_id=case_id, extra={"error": str(exc)[:400]})
                focus = None
        if focus is None:
            focus = build_factor_sweep_focus(result)
        if focus is None:
            return result, passes, None
        if progress:
            progress(f"{'Agent가 계획한' if focus.kind == 'agent_gap_fill' else '과실 요소'} 2차 영상 분석 ({len(focus.reasons)}개 항목, 1회 호출)")
        try:
            sweep = self._focus_analyze(video_path, focus, result, case_id=case_id, progress=progress)
        except Exception as exc:  # noqa: BLE001
            self.run_logger.log(agent="video_agent", task="video_factor_sweep_error", case_id=case_id, extra={"error": str(exc)[:400]})
            return result, passes, None
        if sweep is None:
            return result, passes, None  # 두 번 다 불완전 → 1차 결과만 쓴다 (대화 중 재확인 이력에도 남기지 않는다)
        merged = self.recompute_completion(merge_video_results(result, sweep))
        self.run_logger.log(agent="video_agent", task="video_factor_sweep", case_id=case_id, model=self.analyzer.model,
                            extra={"items": focus.reasons, "changes": merged.changes_from_previous[:6]})
        return merged, passes + 1, focus.question

    # ------------------------------------------------------------------ policy
    def run_policy(
        self,
        video_path: str | Path,
        *,
        case_id: Optional[str] = None,
        progress: ProgressCallback = None,
        extra_context: str = "",
        sweep_planner=None,
    ) -> VideoDecision:
        """가이드 60절 최종 Video Decision Policy."""
        try:
            first = self.analyze(video_path, case_id=case_id, progress=progress, extra_context=extra_context)
        except Exception as exc:  # noqa: BLE001
            self.run_logger.log(agent="video_agent", task="video_policy_error", case_id=case_id, extra={"error": str(exc)[:400]})
            return VideoDecision(status="UNAVAILABLE", error=str(exc), reasons=["video_analysis_failed"])

        passes = 1
        if not _structure_ok(first):
            # 1차 응답이 요약문은 썼지만 도로·진행·충돌 구조를 비워 보냈다(모델 불안정) → 한 번 다시 묻고 더 나은 쪽을 쓴다
            self.run_logger.log(agent="video_agent", task="video_full_degraded", case_id=case_id, model=self.analyzer.model,
                                extra={"score": first.analysis_completion.score, "reasons": first.analysis_completion.reasons})
            if progress:
                progress("1차 분석 응답이 불완전해서 한 번 더 분석")
            try:
                # 같은 입력을 그대로 다시 보내면 같은 실패가 반복되기 쉽다 → 무엇이 비었는지 짚어 준다
                retry = self.analyze(video_path, case_id=case_id, progress=progress, extra_context=extra_context + RETRY_NOTE, use_cache=False)
            except Exception as exc:  # noqa: BLE001
                self.run_logger.log(agent="video_agent", task="video_full_retry_error", case_id=case_id, extra={"error": str(exc)[:400]})
                retry = None
            if retry is not None and retry.analysis_completion.score > first.analysis_completion.score:
                first = retry
            passes += 1
        valid, reasons = validate_video_result(first, self.threshold)
        if valid:
            current, passes, sweep_focus = self._factor_sweep(video_path, first, passes, case_id=case_id, progress=progress, sweep_planner=sweep_planner)
            return VideoDecision(status="VALID", result=current, passes=passes, reasons=reasons, sweep_focus=sweep_focus)

        current = first
        for _ in range(max(0, self.video_settings.max_reanalysis_rounds)):
            targets = build_focus_targets(current, self.threshold)
            if not targets:
                break
            focus = targets[0]
            if progress:
                progress(f"Focus 재분석: {focus.kind}")
            try:
                second = self.analyze(video_path, focus=focus, previous_result=current, case_id=case_id, progress=progress)
            except Exception as exc:  # noqa: BLE001
                self.run_logger.log(agent="video_agent", task="video_focus_error", case_id=case_id, extra={"error": str(exc)[:400]})
                break
            passes += 1
            current = self.recompute_completion(merge_video_results(current, second))
            valid, reasons = validate_video_result(current, self.threshold)
            if valid:
                current, passes, sweep_focus = self._factor_sweep(video_path, current, passes, case_id=case_id, progress=progress, sweep_planner=sweep_planner)
                return VideoDecision(status="VALID", result=current, passes=passes, reasons=reasons, sweep_focus=sweep_focus)

        if self.video_settings.enable_cv_tracking and should_use_cv_tracking(current, self.threshold):
            tracking = None
            try:
                tracking = run_cv_tracking(video_path, progress=progress)
            except Exception as exc:  # noqa: BLE001
                self.run_logger.log(agent="video_agent", task="cv_tracking_error", case_id=case_id, extra={"error": str(exc)[:400]})
            if tracking is not None and tracking.tracked_vehicles:
                try:
                    third = self.analyzer.analyze_with_tracking(video_path, tracking, previous_result=current, progress=progress, case_id=case_id)
                    passes += 1
                    current = self.recompute_completion(merge_video_results(current, third))
                    valid, reasons = validate_video_result(current, self.threshold)
                    if valid:
                        return VideoDecision(status="VALID", result=current, passes=passes, reasons=reasons)
                except Exception as exc:  # noqa: BLE001
                    self.run_logger.log(agent="video_agent", task="video_tracking_pass_error", case_id=case_id, extra={"error": str(exc)[:400]})

        return VideoDecision(
            status="NEEDS_USER_CONFIRMATION",
            result=current,
            passes=passes,
            reasons=reasons,
            confirmation_question=user_confirmation_question(current),
        )
