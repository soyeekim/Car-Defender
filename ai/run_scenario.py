"""가이드 117절 PoC 시나리오를 비대화형으로 실행한다 (실제 API 사용).

    cd ai
    python run_scenario.py --video data/mp4/bb_1_220804_vehicle_116_067.mp4 \
        --description "회전교차로에서 택시랑 사고났어." \
        --answers "응 내 차 블랙박스야. 2026년 8월 22일 오후 3시쯤이야." "상대 차량이 깜빡이도 안 켰는데 그건 반영 안돼?" \
        --output logs/scenario_run.json

순서: 영상 분석 → 추가 질문 → 답변 반영 → 유사사례/과실비율 → 후속 질문 → 사건경위서 → 반박의견서
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from service.interface import CarDefenderAI  # noqa: E402


def _progress(message: str) -> None:
    print(f"  · {message}", file=sys.stderr)


def _show(step: str, response) -> dict:
    print(f"\n===== {step} [stage={response.stage} action={response.action}] =====")
    print(response.message)
    for warning in response.warnings:
        print(f"  ! {warning}")
    return {"step": step, "stage": response.stage, "action": response.action, "message": response.message, "warnings": response.warnings, "data_keys": sorted(response.data)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--description", default="사고가 났어요.")
    parser.add_argument("--answers", nargs="*", default=["응 내 차 블랙박스야."])
    parser.add_argument("--review-answers", nargs="*", default=["상대 차량은 깜빡이를 켜지 않았어요. 실선 구간은 아니었어요."],
                        help="유사 심의사례 제시 후 검토 질문에 대한 답변 (순서대로 전송)")
    parser.add_argument("--followup", default="왜 내 과실이 그렇게 나와?")
    parser.add_argument("--opponent-claim", default=None)
    parser.add_argument("--output", type=Path, default=Path("logs") / f"scenario_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    parser.add_argument("--skip-documents", action="store_true")
    args = parser.parse_args()

    service = CarDefenderAI()
    transcript = []
    started = time.monotonic()

    response = service.create_case(args.video, args.description, progress=_progress)
    case_id = response.case_id
    transcript.append(_show("1-4. 영상 분석 + 추가 질문", response))

    for index, answer in enumerate(args.answers, start=1):
        print(f"\n[USER] {answer}")
        response = service.chat(case_id, answer, progress=_progress)
        transcript.append(_show(f"5-6. 답변 {index} 반영", response))

    # 7. 유사 심의사례 제시 + 검토 질문 → 답변 → Agent가 준비되면 종합 판정
    for index, answer in enumerate(args.review_answers, start=1):
        state = service.get_state(case_id)
        if state.fault_assessment is not None or state.current_stage != "CASE_REVIEW":
            break
        print(f"\n[USER] {answer}")
        response = service.chat(case_id, answer, progress=_progress)
        transcript.append(_show(f"7. 심의사례 검토 답변 {index}", response))

    state = service.get_state(case_id)
    if state.fault_assessment is None:
        print("\n[USER] 그래서 몇 대 몇이야?")
        response = service.chat(case_id, "그래서 몇 대 몇이야?", progress=_progress)
        transcript.append(_show("8. 예상 과실비율 (명시 요청)", response))

    print(f"\n[USER] {args.followup}")
    response = service.chat(case_id, args.followup, progress=_progress)
    transcript.append(_show("9-10. 후속 질문", response))

    if not args.skip_documents:
        state = service.get_state(case_id)
        if state.fault_assessment is not None and not state.assessment_invalidated:
            print("\n[USER] 사건경위서 작성해줘")
            response = service.chat(case_id, "사건경위서 작성해줘", progress=_progress)
            transcript.append(_show("11. 사건경위서", response))
            if args.opponent_claim:
                print(f"\n[USER] 상대 보험사 주장: {args.opponent_claim}")
                response = service.chat(case_id, f"상대 보험사는 이렇게 주장해: {args.opponent_claim}", progress=_progress)
                transcript.append(_show("11-1. 상대 주장 반영", response))
            print("\n[USER] 반박의견서 작성해줘")
            response = service.chat(case_id, "반박의견서 작성해줘", progress=_progress)
            transcript.append(_show("12. 반박의견서", response))
        else:
            print("\n판정이 완료되지 않아 문서 단계를 건너뜁니다.")

    state = service.get_state(case_id)
    summary = {
        "case_id": case_id,
        "elapsed_sec": round(time.monotonic() - started, 1),
        "stage": state.current_stage,
        "video_status": state.video_status,
        "video_backend": state.video_analysis.video_backend if state.video_analysis else None,
        "video_passes": [p.model_dump() for p in state.video_analysis.analysis_passes] if state.video_analysis else [],
        "collision_pair": state.video_analysis.collision_pair.model_dump() if state.video_analysis else None,
        "completion": state.video_analysis.analysis_completion.model_dump() if state.video_analysis else None,
        "fault_assessment": state.fault_assessment.model_dump() if state.fault_assessment else None,
        "retrieved_case_ids": [c.case_id for c in state.retrieved_cases],
        "conflicts": [c.description for c in state.conflicts],
        "uncertain_facts": state.uncertain_facts,
        "transcript": transcript,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n시나리오 완료: case_id={case_id}, {summary['elapsed_sec']}초, 결과 저장 {args.output}")


if __name__ == "__main__":
    main()
