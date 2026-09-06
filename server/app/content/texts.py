# 사용자에게 보이는 여러 문장짜리 글은 문장(온점) 뒤에서 줄을 바꾼다 — 프론트가 whitespace-pre-line 으로 그린다
DISCLAIMER = "본 결과는 참고용이며, 최종 과실비율은 보험사·분쟁심의위원회 결정에 따릅니다."
VIDEO_NOTICE = "영상은 이 사건 처리에만 쓰이며, 사건을 지우면 함께 지워집니다."
LIMITS_LABEL = "mp4 권장 · 최대 200MB · 3분 이내"
VERDICT_PLACEHOLDER = "아직 판정 전이에요."
NEED_DESCRIPTION_TEXT = "영상 잘 받았어요.\n사고 상황을 한두 문장으로 알려 주시면 바로 분석을 시작할게요."
UPLOAD_CTA = {"type": "upload_video", "label": "영상 올리기"}

GUIDE_CARD = {
    "text": "안녕하세요, Fairway예요.\n사고 상황을 말로 설명하고, 블랙박스 영상을 올려 주세요.\n둘이 모이면 분석이 자동으로 시작돼요.",
    "notice": VIDEO_NOTICE,
    "limitsLabel": LIMITS_LABEL,
}

STATUS_LABELS = {
    "intake": "접수중",
    "analyzing": "분석중",
    "needs_review": "확인 필요",
    "judged": "판정 완료",
    "sent": "발송 완료",
    "closed": "종결",
}

REBUTTAL_LOCKED_CARD = {
    "text": "반박의견서에는 사건경위서가 첨부돼요.\n먼저 경위서를 만들면 보낼 수 있어요.",
    "buttonLabel": "반박의견서 보내기",
    "buttonHint": "경위서를 만들면 열려요",
}

SENT_NOTICE = "보낸 문서는 그대로 보관되고 수정할 수 없어요.\n다시 보내려면 새 문서로 만들어요."
SENT_NEXT_STEPS = [
    "보험사 회신을 기다려요 (보통 3~7일)",
    "회신이 오면 채팅에 붙여넣어 주세요 — 함께 따져 볼게요",
    "받아들여지지 않으면 내 보험사에 분쟁심의 청구를 요청하는 방법을 안내해 드려요",
]

CLAIM_NUMBER_HINT = "보험사 접수 문자나 메일에 있어요 · 이걸 넣어야 보험사가 사건을 찾을 수 있어요"
CLAIM_NUMBER_HINT_SHORT = "보험사 접수 문자나 메일에 있어요"
RECIPIENT_PLACEHOLDER = "아직 안 정했어요"
ATTACHMENT_NOTICE_CARD = "영상에는 다른 차량 번호판이 담길 수 있어요 · 다음 창에서 ×로 뺄 수 있어요"
ATTACHMENT_NOTICE_FULL = "영상에는 상대 차량 번호판 등 다른 사람의 정보가 담길 수 있어요.\n보험사 담당자에게만 보내 주세요.\n×를 누르면 빼고 보낼 수 있어요."
ATTACHMENT_TOO_LARGE_NOTE = "용량이 커서 첨부할 수 없어요"
REPORT_INTRO = "채팅에서 나눈 대화와 영상 분석 결과를 바탕으로 쓴 {label}이에요."
REPORT_REVISION_PLACEHOLDER = "예: 2번을 더 간단하게"
