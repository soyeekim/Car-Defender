"""focus 재분석 구간 자르기 (video/clip.py) — 시각 보정과 Gemini 분석기 연동."""

import subprocess
from pathlib import Path

import pytest

from fakes import quiet_logger, sample_observation
from models.clients import JSONResponse
from settings import VideoSettings
from telemetry import CallMetrics
from video.clip import cut_clip, plan_clip_window, shift_data, shift_focus, shift_text
from video.frame_sampler import ffmpeg_available, find_ffmpeg, probe_duration
from video.schemas import VideoResult
from video.validation import FocusTarget


def test_shift_data_moves_schema_times_and_text_but_not_ratios():
    data = sample_observation().model_dump()
    shifted = shift_data(data, 3.0)
    assert shifted["collision_pair"]["timestamp"] == "00:08.8"
    assert shifted["collision_window"]["start"] == "00:08.4" and shifted["collision_window"]["most_likely_timestamp"] == "00:08.8"
    assert shifted["vehicles"][0]["braking"]["timestamp"] == "00:08.5"  # 중첩 Observation
    assert shifted["timeline"][0]["start_time"] == "00:03.0" and shifted["timeline"][0]["end_time"] == "00:06.0"
    assert shifted["road_environment"]["signal_observations"][0]["time"] == "00:07.0"
    assert "00:08.8 자차 전면과" in shifted["detailed_description"]  # 서술문 속 시각도 옮긴다
    assert shifted["vehicles"][1]["first_seen"] == "00:03.8"
    assert shift_text("기본비율 50:50, 결정 40:60 이고 00:03~00:06 구간", 2.0) == "기본비율 50:50, 결정 40:60 이고 00:05.0~00:08.0 구간"
    assert shift_data({"recommended_dense_sampling": {"start_sec": 5.0, "end_sec": 6.0}}, -2.0)["recommended_dense_sampling"] == {"start_sec": 3.0, "end_sec": 4.0}


def test_negative_shift_clamps_to_zero_and_roundtrips():
    data = sample_observation().model_dump()
    back = shift_data(shift_data(data, -4.0), 4.0)
    assert back["collision_pair"]["timestamp"] == "00:05.8"
    assert back["timeline"][0]["start_time"] == "00:04.0"  # 클립 이전(00:00.0)은 0으로 고정됐다가 클립 시작 시각이 된다
    focus = shift_focus(FocusTarget(kind="custom", question="00:05.8 충돌 직전을 보라", start_sec=3.8, end_sec=7.8), -3.8)
    assert (focus.start_sec, focus.end_sec) == (0.0, 4.0) and focus.question.startswith("00:02.0")


def _previous(collision: str) -> VideoResult:
    data = sample_observation().model_dump() | {"video_backend": "fake"}
    data["collision_pair"]["timestamp"] = collision
    data["collision"]["timestamp"] = collision
    data["collision_window"]["most_likely_timestamp"] = collision
    return VideoResult.model_validate(data)


def test_plan_clip_window_is_anchored_to_collision_and_purpose():
    prev = _previous("00:06.0")
    kw = dict(impact_pre_roll_sec=1.0, impact_post_roll_sec=1.0, factor_pre_roll_sec=4.0, factor_post_roll_sec=1.0)
    impact = FocusTarget(kind="collision_pair", question="q", start_sec=4.0, end_sec=8.0)
    factor = FocusTarget(kind="factor_sweep", question="q", start_sec=4.0, end_sec=8.0)
    assert plan_clip_window(impact, prev, 12.0, **kw) == (5.0, 7.0)   # 충돌 순간: 앞뒤 1초
    assert plan_clip_window(factor, prev, 12.0, **kw) == (2.0, 7.0)   # 과실 요소: 4초 전 ~ 1초 후
    assert plan_clip_window(factor, None, 12.0, **kw) == (4.0, 8.0)   # 충돌 시각을 모르면 지시서 구간
    assert plan_clip_window(factor, prev, 12.0, min_duration_sec=40.0, **kw) is None  # 길이 기준 미달
    assert plan_clip_window(FocusTarget(kind="custom", question="q"), None, 12.0, **kw) is None  # 구간 없음
    assert plan_clip_window(factor, _previous("00:03.0"), 4.0, **kw) is None  # 사실상 전체


pytestmark_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not available")


@pytest.fixture(scope="module")
def synthetic_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("clipvideo") / "synthetic.mp4"
    subprocess.run(
        [find_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=10", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


@pytestmark_ffmpeg
def test_cut_clip_is_frame_accurate_and_reused(synthetic_video, tmp_path):
    first = cut_clip(synthetic_video, 0.5, 2.0, out_dir=tmp_path / "clips")
    assert first.is_file() and not list((tmp_path / "clips").glob("*.tmp.mp4"))
    duration = probe_duration(first)
    assert duration is not None and 1.3 <= duration <= 1.7
    assert cut_clip(synthetic_video, 0.5, 2.0, out_dir=tmp_path / "clips") == first


class _CapturingGeminiClient:
    model = "fake-gemini"

    def __init__(self, observation: dict):
        self.observation = observation
        self.calls: list[dict] = []

    def analyze_video(self, **kwargs):
        self.calls.append(kwargs)
        return JSONResponse(data=self.observation, metrics=CallMetrics(model=self.model, latency_sec=0.01))


@pytestmark_ffmpeg
def test_gemini_focus_pass_sends_clip_and_restores_original_times(synthetic_video, tmp_path):
    from video.gemini_analyzer import GeminiVideoAnalyzer

    settings = VideoSettings(clip_impact_pre_roll_sec=0.5, clip_impact_post_roll_sec=0.5, clips_dir=tmp_path / "clips", enable_cache=False)
    # 이전 결과의 충돌은 00:01.5 → 충돌 순간 재분석 클립은 [1.0, 2.0]. 모델 응답은 클립 기준 시각(충돌 00:00.5)이다
    observation = sample_observation().model_dump()
    observation["collision_pair"]["timestamp"] = "00:00.5"
    observation["collision"]["timestamp"] = "00:00.5"
    observation["collision_window"] = {"start": "00:00.3", "end": "00:00.8", "most_likely_timestamp": "00:00.5", "confidence": 0.9, "evidence": ["충격"]}
    client = _CapturingGeminiClient(observation)
    analyzer = GeminiVideoAnalyzer(client=client, settings=settings, run_logger=quiet_logger(tmp_path))
    previous = _previous("00:01.5")
    focus = FocusTarget(kind="collision_pair", question="00:01.5 충돌 순간 두 차량의 접촉 부위를 확인하라", start_sec=0.5, end_sec=2.5)

    result = analyzer.analyze(synthetic_video, focus=focus, previous_result=previous)

    call = client.calls[0]
    assert Path(call["video_path"]) != Path(synthetic_video).resolve() and Path(call["video_path"]).parent == tmp_path / "clips"  # 클립을 보냈다
    assert call["start_sec"] is None and call["end_sec"] is None
    assert "[CLIP]" in call["prompt"] and "00:01.0~00:02.0" in call["prompt"]
    assert "0.0s ~ 1.0s" in call["prompt"]  # 지시서 구간은 클립 안으로 맞춰진다
    assert "00:00.5 충돌 순간" in call["prompt"]  # 지시서 본문 시각도 클립 기준으로
    assert '"timestamp": "00:00.5"' in call["prompt"]  # 이전 결과의 충돌 시각(00:01.5)도 클립 기준으로
    assert result.collision_pair.timestamp == "00:01.5"  # 응답의 클립 기준 00:00.5 → 원본 기준
    assert result.collision_window.most_likely_timestamp == "00:01.5"
    assert result.video_path == str(synthetic_video) and result.analysis_passes[0].pass_type == "focus"


@pytestmark_ffmpeg
def test_gemini_focus_pass_keeps_whole_video_when_clip_would_cover_it(synthetic_video, tmp_path):
    from video.gemini_analyzer import GeminiVideoAnalyzer

    settings = VideoSettings(clips_dir=tmp_path / "clips", enable_cache=False)  # 3초 영상, 충돌 00:02.5, 과실 요소 4초 전~1초 후 → 사실상 전체
    observation = sample_observation().model_dump()
    client = _CapturingGeminiClient(observation)
    analyzer = GeminiVideoAnalyzer(client=client, settings=settings, run_logger=quiet_logger(tmp_path))
    focus = FocusTarget(kind="factor_sweep", question="q", start_sec=1.0, end_sec=2.0)
    result = analyzer.analyze(synthetic_video, focus=focus, previous_result=_previous("00:02.5"))
    call = client.calls[0]
    assert Path(call["video_path"]) == Path(synthetic_video).resolve() and "[CLIP]" not in call["prompt"]
    assert result.collision_pair.timestamp == "00:05.8"
