"""GPT Frames Path용 프레임 샘플러 (가이드 49.3절).

10초 MP4 → 일정 간격의 timestamped JPEG 프레임. 충돌 후보 구간은
start/end를 지정하여 더 촘촘하게(0.1초) 재추출한다.

ffmpeg 바이너리는 FFMPEG_BINARY 환경변수 → imageio-ffmpeg 번들 → PATH 순으로 찾는다.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from settings import get_settings

_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


@dataclass
class SampledFrame:
    index: int
    timestamp_sec: float
    path: Path

    @property
    def label(self) -> str:
        return f"Frame {self.index + 1:02d} — {self.timestamp_sec:04.1f} sec"


def find_ffmpeg() -> Optional[str]:
    configured = os.getenv("FFMPEG_BINARY")
    if configured and Path(configured).is_file():
        return configured
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        pass
    return shutil.which("ffmpeg")


def ffmpeg_available() -> bool:
    return find_ffmpeg() is not None


def compute_video_hash(video_path: str | Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(video_path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def probe_duration(video_path: str | Path, ffmpeg: Optional[str] = None) -> Optional[float]:
    binary = ffmpeg or find_ffmpeg()
    if not binary:
        return None
    completed = subprocess.run(
        [binary, "-hide_banner", "-i", str(video_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    match = _DURATION.search(completed.stderr or "")
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


_COMPLETE_MARKER = ".complete"


def _completed_frames(directory: Path) -> list[Path]:
    """추출이 끝까지 마쳐진 디렉터리의 프레임 목록.

    ffmpeg 가 중간에 죽거나 프로세스가 끊기면 프레임 몇 장만 남는다. 예전에는 파일이 하나라도 있으면 그대로 썼기 때문에
    그 뒤로는 매번 앞부분 몇 장만 모델에 보내는 문제가 있었다. 지금은 추출을 마친 뒤에 쓰는 마커(프레임 개수)가 있고
    실제 파일 수가 그 개수와 같을 때만 재사용한다. 그 외(마커 없음·개수 불일치·0장)는 빈 목록을 돌려줘 다시 뽑게 한다."""
    marker = directory / _COMPLETE_MARKER
    if not directory.is_dir() or not marker.is_file():
        return []
    try:
        expected = int(marker.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return []
    frames = sorted(directory.glob("frame_*.jpg"))
    if expected <= 0 or len(frames) != expected:
        return []
    return frames


def _clear_partial_frames(directory: Path) -> None:
    """중단된 추출의 조각(프레임·마커)만 지운다. 디렉터리 자체나 다른 파일은 건드리지 않는다."""
    if not directory.is_dir():
        return
    for stale in directory.glob("frame_*.jpg"):
        stale.unlink(missing_ok=True)
    (directory / _COMPLETE_MARKER).unlink(missing_ok=True)


def _frames_dir(video_hash: str, interval_sec: float, start_sec, end_sec, max_width: int) -> Path:
    base = get_settings().video.frames_dir
    tag = f"{video_hash[:12]}_i{interval_sec:g}_s{start_sec if start_sec is not None else 'all'}_e{end_sec if end_sec is not None else 'all'}_w{max_width}"
    return base / tag


def sample_frames(
    video_path: str | Path,
    *,
    interval_sec: float = 0.5,
    start_sec: Optional[float] = None,
    end_sec: Optional[float] = None,
    output_dir: Optional[str | Path] = None,
    max_width: int = 768,
    jpeg_quality: int = 3,
    max_frames: int = 120,
    ffmpeg: Optional[str] = None,
    video_hash: Optional[str] = None,
) -> list[SampledFrame]:
    """영상에서 interval_sec 간격으로 프레임을 추출하고 timestamp를 붙여 반환한다."""
    path = Path(video_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"영상 파일을 찾을 수 없습니다: {path}")
    if interval_sec <= 0:
        raise ValueError("interval_sec는 0보다 커야 합니다.")
    binary = ffmpeg or find_ffmpeg()
    if not binary:
        raise RuntimeError(
            "ffmpeg를 찾을 수 없습니다. `pip install imageio-ffmpeg` 또는 FFMPEG_BINARY를 설정하세요."
        )

    digest = video_hash or compute_video_hash(path)
    directory = Path(output_dir) if output_dir else _frames_dir(digest, interval_sec, start_sec, end_sec, max_width)
    start = max(0.0, float(start_sec)) if start_sec is not None else 0.0

    existing = _completed_frames(directory)
    if not existing:
        # 마커가 없거나 개수가 맞지 않으면 이전 추출이 끝까지 가지 못한 것이다 → 조각을 지우고 처음부터 다시 뽑는다
        _clear_partial_frames(directory)
        directory.mkdir(parents=True, exist_ok=True)
        command = [binary, "-y", "-hide_banner", "-loglevel", "error"]
        if start_sec is not None:
            command += ["-ss", f"{start:.3f}"]
        command += ["-i", str(path)]
        if end_sec is not None:
            duration = max(0.05, float(end_sec) - start)
            command += ["-t", f"{duration:.3f}"]
        command += [
            "-vf",
            f"fps=1/{interval_sec:g},scale='min(iw,{max_width})':-2",
            "-q:v",
            str(jpeg_quality),
            "-start_number",
            "0",
            "-frames:v",
            str(max_frames),
            str(directory / "frame_%04d.jpg"),
        ]
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"ffmpeg 프레임 추출 실패: {completed.stderr.strip()[:500]}")
        existing = sorted(directory.glob("frame_*.jpg"))
        # 추출이 정상 종료된 뒤에만 마커를 남긴다. 위에서 예외가 나면 마커가 없으므로 다음 호출이 다시 뽑는다
        (directory / _COMPLETE_MARKER).write_text(str(len(existing)), encoding="utf-8")

    frames = []
    for index, frame_path in enumerate(existing[:max_frames]):
        frames.append(
            SampledFrame(index=index, timestamp_sec=round(start + index * interval_sec, 3), path=frame_path)
        )
    return frames


def frame_index_text(frames: list[SampledFrame]) -> str:
    return "\n".join(f"{frame.label} → {frame.path.name}" for frame in frames)
