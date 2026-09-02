from datetime import UTC, datetime

from app.ids import new_id
from app.models import Verdict
from app.services.presenters import (
    compute_stages,
    ordinal_label,
    rebuttal_doc,
    report_doc,
    size_label,
    verdict_payload,
)


def s(status, job=None, report=False, reb=None):
    return {k: v["state"] for k, v in compute_stages(status, job, report, reb).items()}


def test_stages_table_from_spec_7_2():
    assert s("intake") == {"analysis": "pending", "fault_ratio": "pending", "report": "pending", "rebuttal": "pending"}
    assert s("analyzing", "analysis")["analysis"] == "in_progress"
    assert s("needs_review") == {"analysis": "in_progress", "fault_ratio": "pending", "report": "pending", "rebuttal": "pending"}
    assert s("needs_review", "verdict") == {"analysis": "done", "fault_ratio": "in_progress", "report": "pending", "rebuttal": "pending"}
    assert s("judged") == {"analysis": "done", "fault_ratio": "done", "report": "pending", "rebuttal": "pending"}
    assert s("judged", report=True) == {"analysis": "done", "fault_ratio": "done", "report": "done", "rebuttal": "pending"}
    assert s("judged", report=True, reb="draft")["rebuttal"] == "in_progress"
    assert s("judged", "verdict", report=True, reb="draft") == {"analysis": "done", "fault_ratio": "done", "report": "done", "rebuttal": "in_progress"}
    assert s("sent", report=True, reb="sent") == {"analysis": "done", "fault_ratio": "done", "report": "done", "rebuttal": "done"}
    assert s("closed", report=True, reb="sent")["rebuttal"] == "done"


def test_size_label():
    assert size_label(18874368) == "18MB"
    assert size_label(184320) == "180KB"
    assert size_label(0) == "0KB"


def test_ordinal_label():
    assert ordinal_label(1) == "첫 번째 버전"
    assert ordinal_label(2) == "두 번째 버전"
    assert ordinal_label(5) == "다섯 번째 버전"
    assert ordinal_label(6) == "6번째 버전"


class R:
    version = 2
    page_count = 2


def test_report_doc():
    assert report_doc(None) == {"exists": False, "label": "아직 없음", "version": None, "pageCount": None}
    assert report_doc(R()) == {"exists": True, "label": "두 번째 버전 · 2장", "version": 2, "pageCount": 2}


class Reb:
    def __init__(self, status):
        self.status = status


def test_rebuttal_doc_labels():
    assert rebuttal_doc(False, False, None, None) == {"exists": False, "locked": True, "label": "잠김 · 판정과 경위서가 먼저예요"}
    assert rebuttal_doc(True, False, None, None)["label"] == "잠김 · 경위서를 만들면 열려요"
    assert rebuttal_doc(True, True, None, None) == {"exists": False, "locked": False, "label": "이제 만들 수 있어요"}
    assert rebuttal_doc(True, True, Reb("draft"), None) == {"exists": True, "locked": False, "label": "작성 중"}
    sent_at = datetime(2026, 8, 25, 5, 32, tzinfo=UTC)
    assert rebuttal_doc(True, True, Reb("sent"), sent_at)["label"] == "발송 완료 · 08-25 14:32"


def test_verdict_payload_tolerates_missing_precedents():
    v = Verdict(
        id=new_id(), case_id=new_id(), version=1, ratio_mine=30, ratio_other=70,
        summary="s", basis={"chart": {"name": "차11", "note": ""}, "precedents": None},
        is_active=True, created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    body = verdict_payload(v)
    assert body["basis"]["precedents"] == []
    assert body["basis"]["chart"]["name"] == "차11"
