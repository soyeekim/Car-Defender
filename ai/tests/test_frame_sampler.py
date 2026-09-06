import subprocess

import pytest

from video.frame_sampler import compute_video_hash, ffmpeg_available, find_ffmpeg, probe_duration, sample_frames

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not available")


@pytest.fixture(scope="module")
def synthetic_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("video") / "synthetic.mp4"
    subprocess.run(
        [find_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=10", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def test_probe_duration_and_hash(synthetic_video):
    duration = probe_duration(synthetic_video)
    assert duration is not None and 2.5 <= duration <= 3.5
    digest = compute_video_hash(synthetic_video)
    assert len(digest) == 64


def test_sample_frames_default_interval(synthetic_video, tmp_path):
    frames = sample_frames(synthetic_video, interval_sec=0.5, output_dir=tmp_path / "f05", max_width=160)
    assert 5 <= len(frames) <= 7
    assert frames[0].timestamp_sec == 0.0
    assert frames[1].timestamp_sec == 0.5
    assert frames[0].label.startswith("Frame 01 — 00.0 sec")
    assert frames[0].path.is_file()


def test_dense_resampling_of_window(synthetic_video, tmp_path):
    frames = sample_frames(synthetic_video, interval_sec=0.1, start_sec=1.0, end_sec=1.5, output_dir=tmp_path / "dense", max_width=160)
    assert 4 <= len(frames) <= 7
    assert frames[0].timestamp_sec == 1.0
    assert all(1.0 <= frame.timestamp_sec <= 1.7 for frame in frames)


def test_sample_frames_reuses_existing_directory(synthetic_video, tmp_path):
    directory = tmp_path / "reuse"
    first = sample_frames(synthetic_video, interval_sec=1.0, output_dir=directory, max_width=160)
    second = sample_frames(synthetic_video, interval_sec=1.0, output_dir=directory, max_width=160)
    assert [f.path for f in first] == [f.path for f in second]


def test_partial_extraction_is_not_reused(synthetic_video, tmp_path):
    """이전 추출이 중간에 끊겨 프레임 한 장만 남은 디렉터리(마커 없음)는 재사용하지 않고 다시 뽑는다."""
    directory = tmp_path / "partial"
    directory.mkdir()
    (directory / "frame_0000.jpg").write_bytes(b"broken")
    frames = sample_frames(synthetic_video, interval_sec=1.0, output_dir=directory, max_width=160)
    assert len(frames) >= 3
    assert (directory / "frame_0000.jpg").stat().st_size > len(b"broken")  # 조각이 새 프레임으로 덮어써졌다
    assert (directory / ".complete").read_text(encoding="utf-8") == str(len(frames))


def test_missing_frame_after_completion_triggers_reextraction(synthetic_video, tmp_path):
    """마커는 있는데 프레임 개수가 다르면(파일이 지워짐) 다시 뽑는다."""
    directory = tmp_path / "mismatch"
    first = sample_frames(synthetic_video, interval_sec=1.0, output_dir=directory, max_width=160)
    first[-1].path.unlink()
    second = sample_frames(synthetic_video, interval_sec=1.0, output_dir=directory, max_width=160)
    assert len(second) == len(first)
    assert all(frame.path.is_file() for frame in second)

