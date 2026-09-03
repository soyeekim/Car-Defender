from schemas.accident_state import AccidentState
from schemas.video_analysis import VIDEO_STATE_SLOTS, VideoAnalysisResult


_NORMALIZED_TERMS = {
    "있습니다": "있음",
    "없습니다": "없음",
    "예": "있음",
    "아니오": "없음",
    "무신호": "없음",
    "오른쪽": "우측",
    "왼쪽": "좌측",
}


def _normalize(value: str) -> str:
    normalized = value.lower().replace(" ", "")
    for source, target in _NORMALIZED_TERMS.items():
        normalized = normalized.replace(source, target)
    return normalized


def compare_user_and_vision(
    user_state: AccidentState, video: VideoAnalysisResult
) -> dict:
    matches = []
    differences = []
    user_only = []
    vision_only = []

    for slot_name in VIDEO_STATE_SLOTS:
        user_value = getattr(user_state, slot_name).value
        vision_slot = getattr(video, slot_name)
        vision_value = vision_slot.value

        if user_value is not None and vision_value is not None:
            item = {
                "slot": slot_name,
                "user_value": user_value,
                "vision_value": vision_value,
                "vision_confidence": vision_slot.confidence,
                "vision_evidence": vision_slot.evidence,
                "vision_timestamp": vision_slot.timestamp,
            }
            if _normalize(user_value) == _normalize(vision_value):
                matches.append(item)
            else:
                differences.append(item)
        elif user_value is not None:
            user_only.append({"slot": slot_name, "user_value": user_value})
        elif vision_value is not None:
            vision_only.append(
                {
                    "slot": slot_name,
                    "vision_value": vision_value,
                    "vision_confidence": vision_slot.confidence,
                    "vision_evidence": vision_slot.evidence,
                    "vision_timestamp": vision_slot.timestamp,
                }
            )

    return {
        "matches": matches,
        "differences_requiring_review": differences,
        "user_only": user_only,
        "vision_only": vision_only,
    }
