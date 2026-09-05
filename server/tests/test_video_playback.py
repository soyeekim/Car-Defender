"""재생본(H.264) 판별과 변환.

브라우저는 컨테이너와 코덱을 둘 다 지원해야 재생한다. 여기서 쓰는 ffprobe 출력은
실제 파일을 찍어서 가져온 값이다 — 특히 .mp4 와 .mov 는 format_name 이 똑같이
"mov,mp4,m4a,3gp,3g2,mj2" 로 나오고 major_brand 로만 갈린다.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.video import parse_probe, playback_video_key, transcode_to_h264

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg 가 없는 환경")


def probe_payload(codec="h264", brand="isom", fmt="mov,mp4,m4a,3gp,3g2,mj2", audio="aac", **fmt_tags):
    streams = [{"codec_type": "video", "codec_name": codec}]
    if audio is not None:
        streams.append({"codec_type": "audio", "codec_name": audio})
    tags = {**fmt_tags}
    if brand is not None:
        tags["major_brand"] = brand
    return {"format": {"format_name": fmt, "duration": "12.500000", "tags": tags}, "streams": streams}


def test_parse_probe_reads_duration_recorded_at_and_codec():
    payload = probe_payload(creation_time="2026-08-22T05:02:17.000000Z")
    probe = parse_probe(payload)
    assert probe.duration_sec == 12
    assert probe.recorded_at is not None and probe.recorded_at.year == 2026
    assert probe.video_codec == "h264"


def test_h264_in_real_mp4_is_playable_as_is():
    assert parse_probe(probe_payload()).browser_playable is True


def test_mp4v_needs_transcode():
    """예시 영상 3개가 이 경우다. 확장자는 .mp4 인데 안은 MPEG-4 Part 2."""
    assert parse_probe(probe_payload(codec="mpeg4")).browser_playable is False


def test_hevc_needs_transcode():
    assert parse_probe(probe_payload(codec="hevc")).browser_playable is False


def test_quicktime_brand_needs_transcode_even_with_h264():
    """아이폰 .mov. format_name 은 .mp4 와 구별되지 않으니 major_brand 로 걸러야 한다."""
    assert parse_probe(probe_payload(brand="qt  ")).browser_playable is False


def test_avi_needs_transcode():
    assert parse_probe(probe_payload(fmt="avi", brand=None)).browser_playable is False


def test_missing_brand_needs_transcode():
    """ftyp 을 못 읽었으면 .mp4 인지 확신할 수 없다. 안전한 쪽으로 변환한다."""
    assert parse_probe(probe_payload(brand=None)).browser_playable is False


def test_unreadable_probe_needs_transcode():
    assert parse_probe({}).browser_playable is False


def test_no_audio_track_is_still_playable():
    """소리 없는 블랙박스 영상이 흔하다. 영상만 h264 면 그대로 쓴다."""
    assert parse_probe(probe_payload(audio=None)).browser_playable is True


def test_exotic_audio_needs_transcode():
    """영상은 보이는데 소리만 안 나는 상태를 막는다."""
    assert parse_probe(probe_payload(audio="ac3")).browser_playable is False


def test_playback_key_is_separate_from_original():
    original = "videos/CASE1/VID1.mp4"
    playback = playback_video_key("CASE1", "VID1")
    assert playback != original
    assert playback.endswith(".mp4")


def video_codec_of(path: Path) -> str:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return out.strip()


def make_video(path: Path, codec: str) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=320x240:rate=10:duration=1", "-c:v", codec, str(path)],
        check=True, capture_output=True,
    )
    return path


@needs_ffmpeg
def test_transcode_turns_mp4v_into_h264(tmp_path):
    src = make_video(tmp_path / "src.mp4", "mpeg4")
    assert video_codec_of(src) == "mpeg4"

    dst = tmp_path / "play.mp4"
    assert transcode_to_h264(src, dst) is True
    assert video_codec_of(dst) == "h264"


@needs_ffmpeg
def test_transcode_puts_moov_up_front(tmp_path):
    """+faststart. 없으면 파일을 끝까지 받아야 첫 프레임이 뜬다."""
    dst = tmp_path / "play.mp4"
    assert transcode_to_h264(make_video(tmp_path / "src.mp4", "mpeg4"), dst) is True

    head = dst.read_bytes()[:512]
    assert b"moov" in head, "moov 가 파일 앞에 없다"


@needs_ffmpeg
def test_transcode_shrinks_oversized_video_to_1280(tmp_path):
    src = tmp_path / "big.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=1920x1080:rate=10:duration=1", "-c:v", "mpeg4", str(src)],
        check=True, capture_output=True,
    )
    dst = tmp_path / "play.mp4"
    assert transcode_to_h264(src, dst) is True

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(dst)],
        capture_output=True, text=True, check=True,
    ).stdout
    stream = json.loads(out)["streams"][0]
    assert stream["width"] == 1280 and stream["height"] == 720


def test_transcode_of_garbage_reports_failure(tmp_path):
    src = tmp_path / "not_a_video.mp4"
    src.write_bytes(b"\x00" * 2048)
    dst = tmp_path / "play.mp4"

    assert transcode_to_h264(src, dst) is False


def test_transcode_of_missing_binary_reports_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FFMPEG_BIN", "ffmpeg-does-not-exist")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        src = tmp_path / "src.mp4"
        src.write_bytes(b"\x00" * 16)
        assert transcode_to_h264(src, tmp_path / "play.mp4") is False
    finally:
        get_settings.cache_clear()


@needs_ffmpeg
def test_transcode_that_runs_too_long_reports_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSCODE_TIMEOUT_SECONDS", "0")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        src = make_video(tmp_path / "src.mp4", "mpeg4")
        assert transcode_to_h264(src, tmp_path / "play.mp4") is False
    finally:
        get_settings.cache_clear()
