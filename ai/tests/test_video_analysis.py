from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.evidence_comparator import compare_user_and_vision
from agent.state_manager import update_state
from models.gemini_video import analyze_video
from schemas.accident_state import AccidentState
from schemas.video_analysis import VideoAnalysisResult, VisionSlotValue


class FakeFiles:
    def __init__(self):
        self.deleted = None

    def upload(self, **kwargs):
        return SimpleNamespace(
            name="files/test-video",
            state=SimpleNamespace(name="ACTIVE"),
            uri="test://video",
            mime_type="video/mp4",
        )

    def get(self, **kwargs):
        raise AssertionError("ACTIVE 파일에는 get을 호출하면 안 됩니다.")

    def delete(self, *, name):
        self.deleted = name


class FakeModels:
    def __init__(self, result):
        self.result = result

    def generate_content(self, **kwargs):
        return SimpleNamespace(parsed=self.result, text=None)


class TransientFakeModels(FakeModels):
    def __init__(self, result):
        super().__init__(result)
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("503 UNAVAILABLE: high demand")
        return super().generate_content(**kwargs)


class SchemaFallbackFakeModels(FakeModels):
    def __init__(self, result):
        super().__init__(result)
        self.configs = []

    def generate_content(self, **kwargs):
        self.configs.append(kwargs["config"])
        if len(self.configs) == 1:
            raise RuntimeError("400 INVALID_ARGUMENT")
        return super().generate_content(**kwargs)


class FakeClient:
    def __init__(self, result):
        self.files = FakeFiles()
        self.models = FakeModels(result)
        self.closed = False

    def close(self):
        self.closed = True


def test_video_analyzer_validates_schema_and_cleans_up_uploaded_file(tmp_path):
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"fake video")
    parsed = VideoAnalysisResult(
        summary="자차가 직진 중 상대 차량과 충돌함",
        accident_target=VisionSlotValue(
            value="차대차", confidence=0.9, evidence="차량 두 대가 보임"
        ),
    )
    client = FakeClient(parsed)

    with patch("google.genai.Client", return_value=client):
        result = analyze_video(video_path)

    assert result.accident_target.value == "차대차"
    assert client.files.deleted == "files/test-video"
    assert client.closed is True


def test_video_analyzer_rejects_missing_file():
    with pytest.raises(FileNotFoundError):
        analyze_video("does-not-exist.mp4")


def test_video_analyzer_retries_transient_generation_error(tmp_path):
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"fake video")
    parsed = VideoAnalysisResult(summary="재시도 성공")
    client = FakeClient(parsed)
    client.models = TransientFakeModels(parsed)

    with (
        patch("google.genai.Client", return_value=client),
        patch("models.gemini_video.time.sleep"),
        patch.dict("os.environ", {"VIDEO_ANALYSIS_RETRIES": "1"}),
    ):
        result = analyze_video(video_path)

    assert result.summary == "재시도 성공"
    assert client.models.calls == 2


def test_video_analyzer_falls_back_when_remote_schema_is_rejected(tmp_path):
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"fake video")
    parsed = VideoAnalysisResult(summary="스키마 대체 성공")
    client = FakeClient(parsed)
    client.models = SchemaFallbackFakeModels(parsed)

    with (
        patch("google.genai.Client", return_value=client),
        patch.dict("os.environ", {"VIDEO_REMOTE_SCHEMA": "true"}),
    ):
        result = analyze_video(video_path)

    assert result.summary == "스키마 대체 성공"
    assert "response_json_schema" in client.models.configs[0]
    assert "response_json_schema" not in client.models.configs[1]


def test_user_vision_comparison_keeps_differences_for_review():
    state = AccidentState()
    update_state(
        state,
        {
            "accident_target": "차대차",
            "ego_collision_area": "오른쪽 측면",
            "ego_signal": "녹색",
        },
    )
    video = VideoAnalysisResult(
        summary="테스트",
        accident_target=VisionSlotValue(value="차대차", confidence=0.99),
        ego_collision_area=VisionSlotValue(value="우측 측면", confidence=0.8),
        ego_signal=VisionSlotValue(
            value="적색", confidence=0.6, evidence="원거리 신호등", timestamp="00:03"
        ),
        opponent_maneuver=VisionSlotValue(value="좌회전", confidence=0.9),
    )

    comparison = compare_user_and_vision(state, video)

    matched_slots = {item["slot"] for item in comparison["matches"]}
    different_slots = {
        item["slot"] for item in comparison["differences_requiring_review"]
    }
    vision_only_slots = {item["slot"] for item in comparison["vision_only"]}
    assert {"accident_target", "ego_collision_area"} <= matched_slots
    assert different_slots == {"ego_signal"}
    assert "opponent_maneuver" in vision_only_slots
