import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel


class EvidenceSessionStore:
    def __init__(self, base_dir: str | Path | None = None, session_id: str | None = None):
        root = Path(__file__).resolve().parent.parent
        base = Path(base_dir) if base_dir else root / "logs"
        if session_id is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            session_id = f"evidence_session_{timestamp}"
        self.session_dir = base.expanduser().resolve() / session_id
        self.session_dir.mkdir(parents=True, exist_ok=False)

    @staticmethod
    def _serializable(value):
        if isinstance(value, BaseModel):
            return value.model_dump()
        return value

    def _write(self, filename: str, payload: dict) -> Path:
        path = self.session_dir / filename
        temporary_path = self.session_dir / f".{filename}.tmp"
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary_path.replace(path)
        return path

    def save_vision(
        self,
        *,
        video_path: str | Path,
        video_model: str,
        video_fps: float,
        analysis,
        analysis_passes: list | None = None,
    ) -> Path:
        return self._write(
            "vision_analysis.json",
            {
                "video_path": str(Path(video_path).expanduser().resolve()),
                "video_model": video_model,
                "video_fps": video_fps,
                "analysis": self._serializable(analysis),
                "analysis_passes": analysis_passes or [],
            },
        )

    def save_user_answers(
        self,
        *,
        conversation: list,
        extracted_answers,
        pending_question_slot: str | None,
        factor_answers: dict | None = None,
        pending_factor_id: str | None = None,
    ) -> Path:
        return self._write(
            "user_answers.json",
            {
                "conversation": conversation,
                "extracted_answers": self._serializable(extracted_answers),
                "pending_question_slot": pending_question_slot,
                "factor_answers": {
                    key: self._serializable(value)
                    for key, value in (factor_answers or {}).items()
                },
                "pending_factor_id": pending_factor_id,
            },
        )

    def save_final_facts(
        self,
        *,
        fusion,
        accident_type,
        active_slots: list[str],
        missing_slots: list[str],
        intake_complete: bool,
        completion_reason: str | None,
        fact_plan=None,
    ) -> Path:
        fusion_payload = self._serializable(fusion)
        return self._write(
            "final_facts.json",
            {
                "facts": fusion_payload["facts"],
                "observed_events": fusion_payload.get("observed_events", []),
                "accident_type": self._serializable(accident_type),
                "active_slots": active_slots,
                "missing_slots": missing_slots,
                "intake_complete": intake_complete,
                "completion_reason": completion_reason,
                "fact_plan": self._serializable(fact_plan) if fact_plan else None,
            },
        )

    def save_incident_report(self, report) -> Path:
        return self._write(
            "incident_report.json",
            {"incident_report": self._serializable(report)},
        )

    def save_rag_results(self, rag_result) -> Path:
        return self._write(
            "rag_results.json",
            {"rag": self._serializable(rag_result)},
        )

    def save_post_intake_error(self, message: str) -> Path:
        return self._write(
            "rag_results.json",
            {"rag": None, "status": "failed", "error": message},
        )
