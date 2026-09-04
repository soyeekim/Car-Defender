"""Car-Defender AI PoC CLI (가이드 117~118절).

    cd ai
    python demo.py --video data/mp4/sample.mp4 [--description "사거리에서 직진하다가 사고났어."] [--backend gemini_native|gpt_frames]

명령:
    /assess    예상 과실비율 판정
    /cases     유사 심의사례 보기
    /report    사건경위서 생성
    /rebuttal  반박의견서 생성
    /state     현재 Case State 요약
    /video     영상 분석 결과 요약
    exit       종료
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from service.interface import CarDefenderAI  # noqa: E402
from settings import get_settings  # noqa: E402


def _progress(message: str) -> None:
    print(f"  · {message}", file=sys.stderr)


def _print_agent(message: str) -> None:
    print("\n[AGENT]")
    print(message)


def main() -> None:
    parser = argparse.ArgumentParser(description="Car-Defender AI 멀티턴 사고 상담 Agent 데모")
    parser.add_argument("--video", help="블랙박스 영상 경로 (mp4)")
    parser.add_argument("--description", default="", help="초기 사고 설명")
    parser.add_argument("--backend", choices=["gemini_native", "gpt_frames"], help="Video backend (기본: .env VIDEO_BACKEND)")
    parser.add_argument("--case-id", help="기존 case_id로 이어서 대화")
    parser.add_argument("--no-llm", action="store_true", help="LLM 없이 규칙 기반으로만 동작 (오프라인 점검용)")
    args = parser.parse_args()

    if args.backend:
        os.environ["VIDEO_BACKEND"] = args.backend
        get_settings(reload=True)

    from agents.master_agent import MasterAccidentAgent

    service = CarDefenderAI(agent=MasterAccidentAgent(use_llm=not args.no_llm))

    print("=" * 60)
    print("Car-Defender AI — 블랙박스 사고 분석 · 과실비율 상담 Agent")
    print("=" * 60)

    if args.case_id:
        case_id = args.case_id
        state = service.get_state(case_id)
        print(f"case {case_id} 이어서 진행 (stage={state.current_stage})")
    else:
        description = args.description
        if not description:
            try:
                description = input("\n[USER] 사고 상황을 간단히 설명해주세요: ").strip()
            except (EOFError, KeyboardInterrupt):
                description = ""
        if args.video and not Path(args.video).is_file():
            print(f"영상 파일을 찾을 수 없습니다: {args.video}", file=sys.stderr)
            raise SystemExit(1)
        print("\n[AGENT]\n영상을 분석하고 있습니다...")
        response = service.create_case(args.video, description, progress=_progress)
        case_id = response.case_id
        _print_agent(response.message)
        for warning in response.warnings:
            print(f"  ! {warning}", file=sys.stderr)

    while True:
        try:
            user_input = input("\n[USER] ").strip()
        except (EOFError, KeyboardInterrupt):
            user_input = "exit"
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "/exit"}:
            print(f"\n대화를 종료합니다. case_id={case_id} (data/cases/{case_id}.json)")
            break
        try:
            if user_input == "/state":
                state = service.get_state(case_id)
                print(json.dumps(state.compact(), ensure_ascii=False, indent=2))
                continue
            if user_input == "/video":
                state = service.get_state(case_id)
                if state.video_analysis:
                    print(json.dumps(state.video_analysis.model_dump(exclude={"tracking_timeline"}), ensure_ascii=False, indent=2)[:6000])
                else:
                    print("영상 분석 결과가 없습니다.")
                continue
            if user_input == "/assess":
                response = service.chat(case_id, "예상 과실비율을 판정해줘. 몇 대 몇이야?", progress=_progress)
            elif user_input == "/cases":
                response = service.chat(case_id, "유사 심의사례를 보여줘.", progress=_progress)
            elif user_input == "/report":
                response = service.chat(case_id, "사건경위서를 작성해줘.", progress=_progress)
            elif user_input == "/rebuttal":
                response = service.chat(case_id, "반박의견서를 작성해줘.", progress=_progress)
            else:
                response = service.chat(case_id, user_input, progress=_progress)
        except Exception as exc:  # noqa: BLE001
            print(f"\nAgent 처리 중 오류가 발생했습니다: {exc}", file=sys.stderr)
            continue
        _print_agent(response.message)
        print(f"  [stage={response.stage} action={response.action}]", file=sys.stderr)
        for warning in response.warnings:
            print(f"  ! {warning}", file=sys.stderr)


if __name__ == "__main__":
    main()
