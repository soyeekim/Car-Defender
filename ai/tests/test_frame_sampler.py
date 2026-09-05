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
