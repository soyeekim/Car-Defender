from dataclasses import dataclass
from datetime import datetime

from app.clock import kst_date_label, kst_datetime_label, to_kst_iso
from app.content.texts import DISCLAIMER, STATUS_LABELS, VERDICT_PLACEHOLDER
from app.models import Case, Job, Message, Rebuttal, Report, Verdict, Video

ORDINALS = {1: "첫", 2: "두", 3: "세", 4: "네", 5: "다섯"}


def ordinal_label(n: int) -> str:
    return f"{ORDINALS[n]} 번째 버전" if n in ORDINALS else f"{n}번째 버전"


def size_label(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{round(n / (1024 * 1024))}MB"
    return f"{round(n / 1024)}KB"


def compute_stages(status: str, active_job_kind: str | None, has_report: bool, rebuttal_status: str | None) -> dict:
    analysis = fault = report = rebuttal = "pending"
    if status == "analyzing":
        analysis = "in_progress"
    elif status == "needs_review":
        if active_job_kind == "verdict":
            analysis, fault = "done", "in_progress"
        else:
            analysis = "in_progress"
    elif status in ("judged", "sent", "closed"):
        analysis = fault = "done"
        report = "done" if has_report else "pending"
        if status in ("sent", "closed"):
            report, rebuttal = "done", "done"
        elif rebuttal_status == "draft":
            rebuttal = "in_progress"
    return {
        "analysis": {"state": analysis},
        "fault_ratio": {"state": fault},
        "report": {"state": report},
        "rebuttal": {"state": rebuttal},
    }


def report_doc(latest: Report | None) -> dict:
    if latest is None:
        return {"exists": False, "label": "아직 없음", "version": None, "pageCount": None}
    return {
        "exists": True,
        "label": f"{ordinal_label(latest.version)} · {latest.page_count}장",
        "version": latest.version,
        "pageCount": latest.page_count,
    }


def rebuttal_doc(has_verdict: bool, has_report: bool, rebuttal: Rebuttal | None, sent_at: datetime | None) -> dict:
    if rebuttal is not None and rebuttal.status == "sent":
        label = "발송 완료" + (f" · {kst_datetime_label(sent_at)}" if sent_at else "")
        return {"exists": True, "locked": False, "label": label}
    if rebuttal is not None:
        return {"exists": True, "locked": False, "label": "작성 중"}
    if not has_verdict:
        return {"exists": False, "locked": True, "label": "잠김 · 판정과 경위서가 먼저예요"}
    if not has_report:
        return {"exists": False, "locked": True, "label": "잠김 · 경위서를 만들면 열려요"}
    return {"exists": False, "locked": False, "label": "이제 만들 수 있어요"}


def verdict_summary(v: Verdict | None) -> dict | None:
    if v is None:
        return None
    return {"verdictId": v.id, "version": v.version, "ratio": {"mine": v.ratio_mine, "other": v.ratio_other}}


def verdict_payload(v: Verdict) -> dict:
    basis = dict(v.basis or {})
    precedents = [{"id": p.get("id"), "title": p.get("title")} for p in basis.get("precedents", [])]
    return {
        "verdictId": v.id,
        "version": v.version,
        "changeReason": v.change_reason,
        "ratio": {"mine": v.ratio_mine, "other": v.ratio_other},
        "summary": v.summary,
        "opponentClaim": v.opponent_claim,
        "basis": {"chart": basis.get("chart"), "precedents": precedents},
        "canCreateReport": True,
        "disclaimer": DISCLAIMER,
    }


def video_summary(video: Video | None) -> dict | None:
    if video is None:
        return None
    return {"id": video.id, "filename": video.filename, "durationSec": video.duration_sec, "sizeLabel": size_label(video.size_bytes)}


def job_summary(job: Job | None) -> dict | None:
    if job is None:
        return None
    return {"jobId": job.id, "kind": job.kind, "status": job.status}


@dataclass
class CaseBundle:
    case: Case
    video: Video | None
    verdict: Verdict | None
    latest_report: Report | None
    rebuttal: Rebuttal | None
    active_job: Job | None
    sent_at: datetime | None


def case_detail(b: CaseBundle) -> dict:
    c = b.case
    subtitle = f"접수 {kst_date_label(c.created_at)}" + (" · 블랙박스 1건" if b.video else "")
    has_report = b.latest_report is not None
    return {
        "id": c.id,
        "title": c.title,
        "status": c.status,
        "statusLabel": STATUS_LABELS[c.status],
        "subtitle": subtitle,
        "stages": compute_stages(c.status, b.active_job.kind if b.active_job else None, has_report, b.rebuttal.status if b.rebuttal else None),
        "verdict": verdict_summary(b.verdict),
        "verdictPlaceholder": None if b.verdict else VERDICT_PLACEHOLDER,
        "documents": {
            "report": report_doc(b.latest_report),
            "rebuttal": rebuttal_doc(b.verdict is not None, has_report, b.rebuttal, b.sent_at),
        },
        "video": video_summary(b.video),
        "activeJob": job_summary(b.active_job),
        "disclaimer": DISCLAIMER,
        "createdAt": to_kst_iso(c.created_at),
        "updatedAt": to_kst_iso(c.updated_at),
    }


def case_list_item(c: Case) -> dict:
    return {"id": c.id, "title": c.title, "status": c.status, "statusLabel": STATUS_LABELS[c.status], "updatedAt": to_kst_iso(c.updated_at)}


def message_dict(m: Message) -> dict:
    return {"id": m.id, "caseId": m.case_id, "role": m.role, "type": m.type, "payload": m.payload, "createdAt": to_kst_iso(m.created_at)}
