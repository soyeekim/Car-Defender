import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from agent.accident_classifier import classify_accident
from agent.evidence_comparator import compare_user_and_vision
from agent.slot_extractor import extract_slots
from agent.state_manager import (
    activate_slots,
    get_missing_slots,
    initialize_state,
    update_state,
)
from models.gemini_video import analyze_video

ROOT = Path(__file__).resolve().parent


def _parse_args():
    parser = argparse.ArgumentParser(
        description="블랙박스 영상과 사용자 사고 서술을 구조화하여 비교합니다."
    )
    parser.add_argument("--video", required=True, help="분석할 블랙박스 영상 경로")
    parser.add_argument(
        "--description",
        help="사용자의 사고 서술. 생략하면 영상만 분석합니다.",
    )
    parser.add_argument("--output", help="결과 JSON 저장 경로")
    return parser.parse_args()


def _analyze_description(description: str):
    state = initialize_state()
    extracted = extract_slots(state, description)
    update_state(state, extracted, source="user")
    state.accident_type = classify_accident(state)
    state.active_slots = activate_slots(state)
    state.missing_slots = get_missing_slots(state, state.active_slots)
    return state


def main():
    args = _parse_args()
    video_path = Path(args.video).expanduser().resolve()
    try:
        video_result = analyze_video(
            video_path, progress=lambda message: print(message, file=sys.stderr)
        )
    except Exception as exc:
        print(f"영상 분석 실패: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    result = {
        "video_path": str(video_path),
        "video_model": os.getenv("VIDEO_MODEL"),
        "video_fps": float(os.getenv("VIDEO_FPS", "5")),
        "video_analysis": video_result.model_dump(),
        "user_statement_state": None,
        "comparison": None,
    }

    if args.description:
        user_state = _analyze_description(args.description)
        result["user_statement_state"] = user_state.model_dump()
        result["comparison"] = compare_user_and_vision(user_state, video_result)

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_path = ROOT / "logs" / f"video_analysis_{timestamp}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n결과 저장: {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
