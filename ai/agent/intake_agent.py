from agent.accident_classifier import classify_accident
from agent.question_generator import generate_next_question, pick_target_slot
from agent.slot_extractor import extract_slots
from agent.state_manager import (
    activate_slots,
    check_intake_complete,
    get_missing_slots,
    initialize_state,
    update_state,
)


class IntakeAgent:
    def __init__(self):
        self.state = initialize_state()
        self.history = []
        self.pending_question_slot = None

    def process(self, user_input: str) -> dict:
        self.history.append({"role": "user", "content": user_input})

        extracted = extract_slots(
            state=self.state,
            user_input=user_input,
            target_slot=self.pending_question_slot,
        )
        self.state = update_state(self.state, extracted)

        self.state.accident_type = classify_accident(self.state)

        active_slots = activate_slots(self.state)
        self.state.active_slots = active_slots

        missing_slots = get_missing_slots(self.state, active_slots)
        self.state.missing_slots = missing_slots

        debug = {
            "extracted": extracted,
            "slot_values": {
                slot_name: getattr(self.state, slot_name).value
                for slot_name in active_slots
            },
            "accident_type": self.state.accident_type.model_dump(),
            "active_slots": active_slots,
            "missing_slots": missing_slots,
            "next_question_target": None,
        }

        if check_intake_complete(self.state, missing_slots):
            self.pending_question_slot = None
            return {
                "intake_complete": True,
                "completion_reason": "required_slots_sufficient",
                "state": self.state.model_dump(),
                "debug": debug,
            }

        target_slot = pick_target_slot(self.state, missing_slots)
        next_question = generate_next_question(
            state=self.state,
            missing_slots=missing_slots,
            history=self.history,
            target_slot=target_slot,
        )
        self.pending_question_slot = target_slot
        debug["next_question_target"] = target_slot
        self.history.append({"role": "assistant", "content": next_question})

        return {
            "intake_complete": False,
            "completion_reason": None,
            "next_question": next_question,
            "state": self.state.model_dump(),
            "debug": debug,
        }
