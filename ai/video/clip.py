"""focus 재분석용 구간 클립 — 긴 영상에서만 충돌 앞뒤 구간을 잘라 보낸다.

왜 코드가 시각을 책임지는가: 클립을 받은 모델은 시각을 클립 시작(00:00.0) 기준으로 적는다. 모델에게 "원본 시각으로
바꿔 적어라"라고 시키면 산수를 틀리므로, 이 모듈이 (1) 프롬프트에 넣는 이전 결과·지시서의 시각을 클립 기준으로 옮기고
(2) 응답의 시각을 원본 기준으로 되돌린다. 클립은 ffmpeg 재인코딩으로 자른다 — 스트림 복사(-c copy)는 키프레임에 맞춰
시작점이 앞으로 밀려 오프셋을 알 수 없게 된다.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

from common.timeutil import format_timestamp, parse_timestamp
from video.frame_sampler import compute_video_hash, find_ffmpeg
from video.schemas import VideoResult
from video.validation import FocusTarget

# 스키마에서 시각을 담는 키. 값은 "MM:SS.s" 문자열이다
_TIME_KEYS = {"timestamp", "time", "first_seen", "last_seen", "start", "end", "most_likely_timestamp", "start_time", "end_time"}
# 초 단위 숫자 키 (recommended_dense_sampling)
_SEC_KEYS = {"start_sec", "end_sec"}
# 본문 속 시각 표기("00:05.8", "00:03~00:06"). 비율("50:50")과 구분하려고 소수점이 있거나 분 자리가 0으로 시작하는 두 자리일 때만 시각으로 본다
_TEXT_TIME = re.compile(r"(?<![\d.:])(\d{1,2}):(\d{2})(\.\d+)?(?![\d.:])")


@dataclass
class ClipWindow:
    start_sec: float
    end_sec: float
    path: Path

    @property
    def length_sec(self) -> float:
        return self.end_sec - self.start_sec

    @property
    def label(self) -> str:
        return f"{format_timestamp(self.start_sec)}~{format_timestamp(self.end_sec)}"


# 충돌 순간만 보면 되는 재분석 (충돌 차량 조합·충돌 부위). 그 외(factor_sweep, agent_gap_fill, signal, lane, custom …)는 충돌 전 정황이 필요하다
IMPACT_FOCUS_KINDS = {"collision_pair", "collision_parts"}


def collision_instant(result: Optional[VideoResult]) -> Optional[float]:
    """이전 결과에서 충돌 시각(초). pair → collision → window 최유력 시각 순."""
    if result is None:
        return None
    for value in (result.collision_pair.timestamp, result.collision.timestamp, result.collision_window.most_likely_timestamp):
        parsed = parse_timestamp(value)
        if parsed is not None:
            return parsed
    return None


def plan_clip_window(
    focus: Optional[FocusTarget],
    previous: Optional[VideoResult],
    duration_sec: Optional[float],
    *,
    min_duration_sec: float = 0.0,
    impact_pre_roll_sec: float = 1.0,
    impact_post_roll_sec: float = 1.0,
    factor_pre_roll_sec: float = 4.0,
    factor_post_roll_sec: float = 1.0,
) -> Optional[tuple[float, float]]:
    """자를 구간 (start, end). 충돌 시각을 알면 그 앞뒤로, 모르면 focus 의 구간을 그대로 쓴다.
    짧은 영상(min_duration 미만)·구간 없음·사실상 전체이면 None (자르지 않는다)."""
    if focus is None or not duration_sec or duration_sec < min_duration_sec:
        return None
    instant = collision_instant(previous)
    if instant is not None:
        if focus.kind in IMPACT_FOCUS_KINDS:
            start, end = instant - impact_pre_roll_sec, instant + impact_post_roll_sec
        else:
            start, end = instant - factor_pre_roll_sec, instant + factor_post_roll_sec
    elif focus.start_sec is not None and focus.end_sec is not None:
        start, end = float(focus.start_sec), float(focus.end_sec)
    else:
        return None
    start, end = max(0.0, start), min(float(duration_sec), end)
    # 0.1초 단위로 맞춰야 "MM:SS.s" 표기와 오프셋 보정이 왕복해도 어긋나지 않는다
    start, end = round(start, 1), round(end, 1)
    if end - start < 1.0:
        return None
    if start <= 0.0 and end >= duration_sec - 0.05:
        return None
    return start, end


def cut_clip(video_path: str | Path, start_sec: float, end_sec: float, *, out_dir: str | Path, video_hash: Optional[str] = None, ffmpeg: Optional[str] = None) -> Path:
    """[start, end) 구간을 재인코딩해 mp4 로 자른다. 같은 영상·구간은 재사용한다 (완성된 파일만 남기므로 조각이 재사용되지 않는다)."""
    path = Path(video_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"영상 파일을 찾을 수 없습니다: {path}")
    binary = ffmpeg or find_ffmpeg()
    if not binary:
        raise RuntimeError("ffmpeg를 찾을 수 없습니다. `pip install imageio-ffmpeg` 또는 FFMPEG_BINARY를 설정하세요.")
    digest = video_hash or compute_video_hash(path)
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest[:12]}_s{start_sec:.1f}_e{end_sec:.1f}.mp4"
    if target.is_file() and target.stat().st_size > 0:
        return target
    temporary = directory / f"{target.stem}.tmp.mp4"
    duration = max(0.1, float(end_sec) - float(start_sec))
    base = [binary, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start_sec:.3f}", "-i", str(path), "-t", f"{duration:.3f}"]
    audio = ["-c:a", "aac", "-b:a", "64k"]
    attempts = [
        base + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"] + audio + ["-movflags", "+faststart", str(temporary)],
        base + ["-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p"] + audio + ["-movflags", "+faststart", str(temporary)],
    ]
    error = ""
    for command in attempts:
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if completed.returncode == 0 and temporary.is_file() and temporary.stat().st_size > 0:
            temporary.replace(target)
            return target
        error = completed.stderr.strip()[:400]
        temporary.unlink(missing_ok=True)
    raise RuntimeError(f"ffmpeg 구간 자르기 실패: {error}")


# ---------------------------------------------------------------- timestamp shifting
def shift_time_value(value: Any, offset_sec: float) -> Any:
    parsed = parse_timestamp(value) if isinstance(value, str) else None
    if parsed is None:
        return value
    return format_timestamp(parsed + offset_sec)


def _looks_like_time(minutes: str, seconds: str, fraction: Optional[str]) -> bool:
    if int(seconds) >= 60 or int(minutes) >= 60:
        return False
    return bool(fraction) or (len(minutes) == 2 and minutes.startswith("0"))


def shift_text(text: str, offset_sec: float) -> str:
    """본문 속 "00:05.8" 표기를 옮긴다. 비율 표기는 건드리지 않는다."""
    if not text or ":" not in text:
        return text

    def _sub(match: "re.Match[str]") -> str:
        minutes, seconds, fraction = match.group(1), match.group(2), match.group(3)
        if not _looks_like_time(minutes, seconds, fraction):
            return match.group(0)
        total = int(minutes) * 60 + int(seconds) + (float(fraction) if fraction else 0.0)
        return format_timestamp(total + offset_sec) or match.group(0)

    return _TEXT_TIME.sub(_sub, text)


def shift_data(data: Any, offset_sec: float, *, key: Optional[str] = None) -> Any:
    """영상 결과(dict/list)의 모든 시각을 offset 만큼 옮긴다. 음수가 되면 00:00.0 으로 고정된다."""
    if isinstance(data, dict):
        return {name: shift_data(value, offset_sec, key=name) for name, value in data.items()}
    if isinstance(data, list):
        return [shift_data(item, offset_sec, key=key) for item in data]
    if isinstance(data, str):
        if key in _TIME_KEYS:
            return shift_time_value(data, offset_sec)
        return shift_text(data, offset_sec)
    if isinstance(data, (int, float)) and not isinstance(data, bool) and key in _SEC_KEYS:
        return round(max(0.0, float(data) + offset_sec), 3)
    return data


def shift_result(result: VideoResult, offset_sec: float) -> VideoResult:
    return VideoResult.model_validate(shift_data(result.model_dump(), offset_sec))


def shift_focus(focus: FocusTarget, offset_sec: float) -> FocusTarget:
    return replace(
        focus,
        question=shift_text(focus.question, offset_sec),
        start_sec=None if focus.start_sec is None else round(max(0.0, focus.start_sec + offset_sec), 1),
        end_sec=None if focus.end_sec is None else round(max(0.0, focus.end_sec + offset_sec), 1),
    )
