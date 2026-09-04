"""CV Detection / Tracking Fallback 모듈 (가이드 36~41, 57~58절).

기본 경로가 아니라 2차 fallback이다. YOLO + ByteTrack(ultralytics)이 설치되어 있을 때만 동작하며,
없으면 None을 반환하고 Master Agent는 사용자 객관적 확인으로 넘어간다.

이 모듈은 사고 과실을 판단하지 않는다. 프레임별 bbox와 persistent track ID만 만든다.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

VEHICLE_CLASS_NAMES = {"car", "truck", "bus", "motorcycle", "bicycle"}


@dataclass
class TrackedVehicle:
    track_id: str
    cls: str
    first_frame: int
    last_frame: int
    first_time_sec: float
    last_time_sec: float
    sample_boxes: list[dict] = field(default_factory=list)  # {"frame", "time_sec", "bbox":[x1,y1,x2,y2]}


@dataclass
class TrackingContext:
    source: str
    fps: float
    frame_count: int
    tracked_vehicles: list[TrackedVehicle] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_prompt_json(self, max_boxes_per_track: int = 8) -> str:
        payload = {
            "source": self.source,
            "fps": self.fps,
            "frame_count": self.frame_count,
            "tracked_vehicles": [
                {
                    **{key: value for key, value in asdict(item).items() if key != "sample_boxes"},
                    "sample_boxes": _subsample(item.sample_boxes, max_boxes_per_track),
                }
                for item in self.tracked_vehicles
            ],
            "notes": self.notes,
        }
        return json.dumps(payload, ensure_ascii=False)

    def track_ids(self) -> list[str]:
        return [item.track_id for item in self.tracked_vehicles]


def _subsample(items: list[dict], limit: int) -> list[dict]:
    if len(items) <= limit:
        return items
    step = max(1, len(items) // limit)
    return items[::step][:limit]


def cv_tracking_available() -> bool:
    try:
        import ultralytics  # type: ignore  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def run_cv_tracking(
    video_path: str | Path,
    *,
    model_name: Optional[str] = None,
    tracker: str = "bytetrack.yaml",
    max_frames: int = 600,
    confidence: float = 0.3,
    progress=None,
) -> Optional[TrackingContext]:
    """YOLO + ByteTrack으로 차량 track을 만든다. ultralytics가 없으면 None."""
    path = Path(video_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"영상 파일을 찾을 수 없습니다: {path}")
    if not cv_tracking_available():
        if progress:
            progress("CV tracking 모듈(ultralytics)이 설치되어 있지 않아 fallback을 건너뜁니다.")
        return None
    from ultralytics import YOLO  # type: ignore

    weights = model_name or os.getenv("YOLO_MODEL", "yolov8n.pt")
    if progress:
        progress(f"CV tracking 실행: {weights} + {tracker}")
    model = YOLO(weights)
    names = model.names if hasattr(model, "names") else {}
    tracks: dict[str, TrackedVehicle] = {}
    fps = 0.0
    frame_index = -1
    for frame_index, frame in enumerate(
        model.track(source=str(path), stream=True, tracker=tracker, conf=confidence, verbose=False, persist=True)
    ):
        if frame_index >= max_frames:
            break
        if fps == 0.0:
            speed = getattr(frame, "speed", None)
            fps = float(os.getenv("CV_TRACKING_FPS", "30")) if not speed else float(os.getenv("CV_TRACKING_FPS", "30"))
        boxes = getattr(frame, "boxes", None)
        if boxes is None or boxes.id is None:
            continue
        ids = boxes.id.int().tolist()
        classes = boxes.cls.int().tolist()
        xyxy = boxes.xyxy.tolist()
        time_sec = frame_index / fps if fps else float(frame_index)
        for track_id, cls_index, bbox in zip(ids, classes, xyxy):
            cls_name = str(names.get(cls_index, cls_index)) if isinstance(names, dict) else str(cls_index)
            if cls_name not in VEHICLE_CLASS_NAMES:
                continue
            key = f"track_{track_id}"
            entry = tracks.get(key)
            if entry is None:
                entry = TrackedVehicle(
                    track_id=key,
                    cls=cls_name,
                    first_frame=frame_index,
                    last_frame=frame_index,
                    first_time_sec=round(time_sec, 3),
                    last_time_sec=round(time_sec, 3),
                )
                tracks[key] = entry
            entry.last_frame = frame_index
            entry.last_time_sec = round(time_sec, 3)
            entry.sample_boxes.append(
                {"frame": frame_index, "time_sec": round(time_sec, 3), "bbox": [round(v, 1) for v in bbox]}
            )
    context = TrackingContext(
        source=f"yolo:{weights}+{tracker}",
        fps=fps,
        frame_count=frame_index + 1,
        tracked_vehicles=sorted(tracks.values(), key=lambda item: item.first_frame),
    )
    if len(context.tracked_vehicles) == 0:
        context.notes.append("차량 track이 검출되지 않음")
    return context
