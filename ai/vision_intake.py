import argparse
import json
import os
import sys
from pathlib import Path

from agent.vision_first_intake_agent import VisionFirstIntakeAgent
from agent.evidence_fusion import fuse_evidence
from agent.state_manager import initialize_state
from models.gemini_video import analyze_video
from rag.pipeline import generate_incident_report, retrieve_similar_sources
from schemas.accident_state import AccidentType
from storage.evidence_store import EvidenceSessionStore


def _parse_args():
    parser = argparse.ArgumentParser(
        description="영상으로 확인되지 않는 정보만 질문하는 Vision-first Intake CLI"
    )
    parser.add_argument("--video", required=True, help="블랙박스 영상 경로")
    parser.add_argument("--description", help="선택적인 사용자 최초 사고 서술")
    return parser.parse_args()


def _save_turn(store, agent, result):
    store.save_user_answers(
        conversation=agent.history,
        extracted_answers=agent.user_state,
        pending_question_slot=agent.pending_question_slot,
        factor_answers=agent.factor_answers,
        pending_factor_id=agent.pending_factor_id,
    )
    store.save_final_facts(
        fusion=agent.fusion,
        accident_type=agent.state.accident_type,
        active_slots=agent.state.active_slots,
        missing_slots=agent.state.missing_slots,
        intake_complete=result["intake_complete"],
        completion_reason=result["completion_reason"],
        fact_plan=agent.fact_plan,
    )


def main():
    args = _parse_args()
    video_path = Path(args.video).expanduser().resolve()

    print("블랙박스 영상을 먼저 분석합니다.")
    try:
        video = analyze_video(video_path, progress=print)
    except Exception as exc:
        print(f"영상 분석 실패: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    store = EvidenceSessionStore()
    model = os.getenv("VIDEO_MODEL")
    fps = float(os.getenv("VIDEO_FPS", "5"))
    store.save_vision(
        video_path=video_path,
        video_model=model,
        video_fps=fps,
        analysis=video,
        analysis_passes=[{"pass": "broad_observation", "result": video.model_dump()}],
    )
    empty_user_state = initialize_state()
    initial_fusion = fuse_evidence(video, empty_user_state)
    store.save_user_answers(
        conversation=[],
        extracted_answers=empty_user_state,
        pending_question_slot=None,
    )
    store.save_final_facts(
        fusion=initial_fusion,
        accident_type=AccidentType(),
        active_slots=[],
        missing_slots=[],
        intake_complete=False,
        completion_reason=None,
    )

    print("\n[VISION SUMMARY]")
    print(video.summary)

    try:
        agent = VisionFirstIntakeAgent(video)
    except Exception as exc:
        print(f"Intake 초기화 실패: {exc}", file=sys.stderr)
        print(f"영상 및 초기 증거 저장 위치: {store.session_dir}", file=sys.stderr)
        raise SystemExit(1) from exc

    recheck_factors = agent.factors_needing_video_recheck()
    analysis_passes = [
        {"pass": "broad_observation", "result": video.model_dump()}
    ]
    if recheck_factors:
        print("\n과실 판단 핵심 요소를 원본 영상에서 다시 확인합니다.")
        focus = [
            {
                "factor_id": factor.factor_id,
                "description": factor.description,
                "instruction": factor.video_recheck_instruction,
            }
            for factor in recheck_factors
        ]
        try:
            targeted = analyze_video(
                video_path,
                progress=print,
                focus_factors=focus,
            )
            agent.apply_targeted_video_analysis(targeted)
            analysis_passes.append(
                {
                    "pass": "targeted_recheck",
                    "requested_factors": focus,
                    "result": targeted.model_dump(),
                }
            )
            store.save_vision(
                video_path=video_path,
                video_model=model,
                video_fps=fps,
                analysis=agent.video_analysis,
                analysis_passes=analysis_passes,
            )
        except Exception as exc:
            print(
                f"Targeted 영상 재확인 실패, 사용자 질문 단계로 계속합니다: {exc}",
                file=sys.stderr,
            )
    result = agent.process(args.description) if args.description else agent.start()
    _save_turn(store, agent, result)

    while not result["intake_complete"]:
        print("\nAgent:")
        print(result["next_question"])
        try:
            user_input = input("\nUser: ").strip()
        except (EOFError, KeyboardInterrupt):
            user_input = "exit"
        if user_input.lower() in {"exit", "quit"}:
            break
        if not user_input:
            continue
        result = agent.process(user_input)
        _save_turn(store, agent, result)

    if result["intake_complete"]:
        print("\n필요한 핵심 정보 수집이 완료되었습니다.")
        if result["disputed_facts"]:
            print("사람의 검토가 필요한 충돌 사실:")
            print(json.dumps(result["disputed_facts"], ensure_ascii=False))
        try:
            print("수집된 증거로 객관적인 사건경위서를 생성합니다.")
            incident_report = generate_incident_report(agent)
            store.save_incident_report(incident_report)
            print("\n[사건경위서]")
            print(incident_report.objective_narrative)
            rag_result = retrieve_similar_sources(
                incident_report,
                progress=print,
            )
            store.save_rag_results(rag_result)
            print("\n[유사 심의사례·인정기준]")
            for item in rag_result.similar_cases:
                source = item.source
                identifier = source.case_number or source.chart_number or "식별번호 없음"
                page_start = source.page_start or source.page_number
                page_end = source.page_end or source.page_number
                page_label = (
                    str(page_start) if page_start == page_end else f"{page_start}~{page_end}"
                )
                print(
                    f"- {identifier} | {source.source_file} "
                    f"PDF {page_label}쪽 | {item.relevance_reason}"
                )
        except Exception as exc:
            store.save_post_intake_error(str(exc))
            print(
                f"사건경위서/RAG 생성 실패: {exc}",
                file=sys.stderr,
            )

    print(f"\n증거 파일 저장 위치: {store.session_dir}")
    print("- vision_analysis.json")
    print("- user_answers.json")
    print("- final_facts.json")
    for filename in ("incident_report.json", "rag_results.json"):
        if (store.session_dir / filename).is_file():
            print(f"- {filename}")


if __name__ == "__main__":
    main()
