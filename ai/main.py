import json
from datetime import datetime
from pathlib import Path

from agent.intake_agent import IntakeAgent

_LOGS_DIR = Path(__file__).resolve().parent / "logs"


def save_log(conversation, final_state, accident_type, missing_slots):
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = _LOGS_DIR / f"session_{timestamp}.json"

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "conversation": conversation,
                "final_state": final_state,
                "accident_type": accident_type,
                "missing_slots": missing_slots,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    return log_path


def main():
    agent = IntakeAgent()

    print("=" * 40)
    print("교통사고 과실비율 분석 도우미 Agent")
    print("=" * 40)
    print("\nAgent:")
    print("사고 상황을 자유롭게 설명해주세요. (종료: exit)")

    while True:
        try:
            user_input = input("\nUser: ").strip()
        except (EOFError, KeyboardInterrupt):
            user_input = "exit"

        if not user_input:
            continue

        if user_input.lower() in ["exit", "quit"]:
            print("\n대화를 종료합니다.")
            if agent.history:
                state = agent.state.model_dump()
                log_path = save_log(
                    conversation=agent.history,
                    final_state=state,
                    accident_type=state["accident_type"],
                    missing_slots=state["missing_slots"],
                )
                print(f"대화 로그 저장: {log_path}")
            break

        try:
            result = agent.process(user_input)
        except Exception as exc:
            print(f"\nAgent 처리 중 오류가 발생했습니다: {exc}")
            continue

        if result.get("debug"):
            print("\n[DEBUG]")
            print(json.dumps(result["debug"], ensure_ascii=False, indent=2))

        if result["intake_complete"]:
            print("\nAgent:")
            print("필요한 사고 정보가 충분히 수집되었습니다.")
            print("\n최종 사고 State:")
            print(json.dumps(result["state"], ensure_ascii=False, indent=2))

            log_path = save_log(
                conversation=agent.history,
                final_state=result["state"],
                accident_type=result["state"]["accident_type"],
                missing_slots=result["state"]["missing_slots"],
            )
            print(f"\n대화 로그 저장: {log_path}")
            break

        print("\nAgent:")
        print(result["next_question"])


if __name__ == "__main__":
    main()
