"""VideoAnalysisAgent — 불량 focus 응답(차량·충돌 pair 구조 없음) 처리."""

from fakes import FakeVideoAnalyzer, quiet_logger, sample_observation
from agents.video_agent import VideoAnalysisAgent
from video.cache import VideoResultCache
from video.validation import FocusTarget, focus_pass_usable


class _FlakyFocus(FakeVideoAnalyzer):
    """focus 호출을 `bad_calls` 번은 요약문만 있고 vehicles·collision_pair 가 빈 응답으로 돌려준다."""

    def __init__(self, bad_calls: int, **kwargs):
        super().__init__(**kwargs)
        self.bad_calls = bad_calls

    def analyze(self, video_path, *, focus=None, previous_result=None, extra_context="", tracking_context=None, progress=None, case_id=None):
        if focus is not None and self.bad_calls > 0:
            self.bad_calls -= 1
            self.calls.append({"focus": focus.kind, "degraded": True})
            observation = sample_observation()
            observation.vehicles = []
            observation.collision_pair.participants = []
            observation.timeline = []
            observation.changes_from_previous = ["(모델이 구조를 비워 보냄)"]
            return self.finalize(observation, video_path=video_path, video_hash="h", duration_sec=10.0, prompt_version="fake", metrics=__import__("telemetry").CallMetrics(model=self.model), pass_type="focus", focus=focus, previous_result=previous_result)
        return super().analyze(video_path, focus=focus, previous_result=previous_result, extra_context=extra_context, tracking_context=tracking_context, progress=progress, case_id=case_id)


def _agent(tmp_path, analyzer):
    return VideoAnalysisAgent(analyzer=analyzer, cache=VideoResultCache(tmp_path / "cache", enabled=True), run_logger=quiet_logger(tmp_path), factor_sweep=True)


def _video(tmp_path):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"fake")
    return str(path)


def test_degraded_focus_pass_is_retried_once_and_not_cached(tmp_path):
    analyzer = _FlakyFocus(bad_calls=1, run_logger=quiet_logger(tmp_path))
    agent = _agent(tmp_path, analyzer)
    decision = agent.run_policy(_video(tmp_path), sweep_planner=lambda r: FocusTarget(kind="agent_gap_fill", question="방향지시등 확인", start_sec=3.0, end_sec=7.0))
    kinds = [(c["focus"], c.get("degraded", False)) for c in analyzer.calls]
    assert kinds == [(None, False), ("agent_gap_fill", True), ("agent_gap_fill", False)]  # 불량 1회 → 재시도 1회
    assert decision.status == "VALID" and decision.passes == 2 and decision.sweep_focus
    assert focus_pass_usable(decision.result) and decision.result.collision_pair.participants == ["vehicle_1", "vehicle_3"]
    assert "(모델이 구조를 비워 보냄)" not in decision.result.changes_from_previous
    cached = [p for p in (tmp_path / "cache").glob("*.json")]
    assert len(cached) == 2  # 1차 + 정상 focus 만 캐시, 불량 응답은 캐시하지 않는다


def test_focus_pass_degraded_twice_keeps_first_result(tmp_path):
    analyzer = _FlakyFocus(bad_calls=2, run_logger=quiet_logger(tmp_path))
    agent = _agent(tmp_path, analyzer)
    decision = agent.run_policy(_video(tmp_path), sweep_planner=lambda r: FocusTarget(kind="agent_gap_fill", question="방향지시등 확인", start_sec=3.0, end_sec=7.0))
    assert len(analyzer.calls) == 3 and decision.passes == 1 and decision.sweep_focus is None
    assert decision.result.collision_pair.participants == ["vehicle_1", "vehicle_3"] and len(decision.result.vehicles) == 3
    assert not any("비워" in item for item in decision.result.changes_from_previous)


def test_recheck_with_degraded_focus_keeps_previous(tmp_path):
    analyzer = _FlakyFocus(bad_calls=2, run_logger=quiet_logger(tmp_path))
    agent = _agent(tmp_path, analyzer)
    first = agent.analyze(_video(tmp_path))
    merged = agent.recheck(_video(tmp_path), first, "충돌 직전 상대 차량 방향지시등")
    assert merged.collision_pair.participants == first.collision_pair.participants and len(merged.vehicles) == 3


class _WeakFirstPass(FakeVideoAnalyzer):
    """1차 응답이 요약문은 쓰지만 도로 유형·충돌 구간·충돌 유형을 비워 보내는 모델 (오늘 실제 관측). 두 번째 호출은 정상."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.weak_calls = 1

    def analyze(self, video_path, *, focus=None, previous_result=None, extra_context="", tracking_context=None, progress=None, case_id=None):
        if focus is None and self.weak_calls > 0:
            self.weak_calls -= 1
            self.calls.append({"focus": None, "weak": True})
            observation = sample_observation()
            observation.road_environment.road_type.status = "UNKNOWN"
            observation.collision_window = type(observation.collision_window)()
            observation.collision.timestamp = None
            observation.collision.collision_type = None
            observation.collision.relative_direction = None
            return self.finalize(observation, video_path=video_path, video_hash="h", duration_sec=10.0, prompt_version="fake", metrics=__import__("telemetry").CallMetrics(model=self.model), pass_type="full", focus=None)
        return super().analyze(video_path, focus=focus, previous_result=previous_result, extra_context=extra_context, tracking_context=tracking_context, progress=progress, case_id=case_id)


def test_weak_first_pass_is_retried_once_and_better_result_kept(tmp_path):
    analyzer = _WeakFirstPass(run_logger=quiet_logger(tmp_path))
    agent = VideoAnalysisAgent(analyzer=analyzer, cache=VideoResultCache(tmp_path / "cache", enabled=True), run_logger=quiet_logger(tmp_path), factor_sweep=False)
    decision = agent.run_policy(_video(tmp_path))
    assert [c.get("weak", False) for c in analyzer.calls] == [True, False]  # 불량 1차 → 전체 분석 1회 재시도
    assert decision.status == "VALID" and decision.passes == 2
    assert decision.result.collision_pair.timestamp == "00:05.8" and decision.result.road_environment.road_type.value == "intersection"
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1  # 불량 1차는 캐시하지 않고 정상 결과만 캐시


def test_reconciled_vehicle_without_movement_does_not_trigger_retry(tmp_path):
    """목록에서 빠진 상대 차량을 서술문으로 보정하면 그 차의 진행 방향만 비는데, 그건 재시도 사유가 아니다 (한 번 분석 원칙)."""
    from agents.video_agent import _structure_ok

    observation = sample_observation()
    observation.vehicles[2].movement = "unknown"
    result = FakeVideoAnalyzer(run_logger=quiet_logger(tmp_path)).finalize(observation, video_path="v.mp4", video_hash="h", duration_sec=10.0, prompt_version="fake", metrics=__import__("telemetry").CallMetrics(model="fake"), pass_type="full", focus=None)
    assert _structure_ok(result)


def test_stale_weak_cache_entry_is_ignored(tmp_path):
    """예전 규칙으로 캐시된 부실 1차 결과(충돌 구조 없음)는 캐시 적중이어도 다시 분석한다."""
    analyzer = FakeVideoAnalyzer(run_logger=quiet_logger(tmp_path))
    cache = VideoResultCache(tmp_path / "cache", enabled=True)
    agent = VideoAnalysisAgent(analyzer=analyzer, cache=cache, run_logger=quiet_logger(tmp_path), factor_sweep=False)
    video = _video(tmp_path)
    weak = sample_observation()
    weak.collision_window = type(weak.collision_window)()
    weak.collision.timestamp = None
    weak.collision.collision_type = None
    weak_result = analyzer.finalize(weak, video_path=video, video_hash="h", duration_sec=10.0, prompt_version="fake", metrics=__import__("telemetry").CallMetrics(model="fake"), pass_type="full", focus=None)
    from video.frame_sampler import compute_video_hash
    cache.put(agent._cache_key(compute_video_hash(video), None, extra=""), weak_result)
    decision = agent.run_policy(video)
    assert len(analyzer.calls) == 1 and decision.result.collision_pair.timestamp == "00:05.8"

