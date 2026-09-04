import re

from app.agent.base import (
    AnalyzeInput,
    AnalyzeResult,
    Basis,
    Chart,
    ChatInput,
    ChatResult,
    ExplainInput,
    ExplainResult,
    JudgeInput,
    JudgeResult,
    Precedent,
    Ratio,
    Section,
    VideoMeta,
    WriteInput,
    WriteResult,
)

RATIO_RE = re.compile(r"(\d{1,3})\s*[:대]\s*(\d{1,3})")

PRECEDENTS = [
    Precedent(
        id="2019-018856",
        title="신호위반 직진 충돌",
        body_text=(
            "신호기 있는 교차로에서 직진 차량과 적색 신호에 진입한 이륜차가 충돌한 사례예요. 보험사는 직진차 30 : 이륜차 70을 주장했지만, "
            "블랙박스로 상대 신호위반이 입증되어 직진차 0 : 이륜차 100으로 뒤집혔어요.\n\n"
            "내 사건과 신호 상태·진입 방향·충돌 형태가 같고, 상대가 이륜차라는 점까지 동일해요."
        ),
    ),
    Precedent(
        id="2021-004312",
        title="이륜차 교차로 진입",
        body_text=(
            "교차로에 먼저 진입한 승용차와 우측에서 진입한 이륜차의 충돌 사례예요. 이륜차의 신호위반이 확인되어 승용차 0 : 이륜차 100이 인정됐어요.\n\n"
            "내 사건처럼 상대가 우측에서 진입했고 충돌 부위가 앞펜더라는 점이 같아요."
        ),
    ),
]

SUMMARY = (
    "영상을 분석했어요. 내 차는 2차로에서 직진 중이었고, 상대는 우측에서 적색 신호에 진입했어요. "
    "내 속도는 약 48km/h예요. 충돌 부위와 정지선 통과 시점은 영상만으로는 확인이 어려워요."
)
Q1 = "판정까지 두 가지만 더 물어볼게요. 차량 어느 부분에 충돌했나요? 1/2"
Q2 = "마지막 하나만 더 여쭤볼게요. 정지선을 지날 때 내 신호는 무엇이었나요? 2/2"


class MockAgent:
    async def analyze(self, inp: AnalyzeInput) -> AnalyzeResult:
        return AnalyzeResult(
            summary_text=SUMMARY,
            facts={"my_lane": "2차로 직진", "opponent_entry": "우측", "opponent_signal": "red", "my_speed_kph": 48},
            questions=[Q1],
            title="교차로 직진 충돌 · 08-22",
            video_meta=VideoMeta(speed_kph=48, impact_at_sec=31),
        )

    async def chat(self, inp: ChatInput) -> ChatResult:
        msg = inp.new_message
        if not inp.has_video:
            return ChatResult(reply=(
                "많이 놀라셨겠어요. 지금 설명만으로는 정확한 비율을 말씀드리기 어려워요 — "
                "블랙박스 영상을 올려 주시면 1~2분 안에 예상 과실비율을 근거와 함께 보여 드릴게요."
            ))
        if inp.verdict is None:
            if "impact_part" not in inp.facts:
                return ChatResult(reply=f"{msg.rstrip('.요')}로 적어 뒀어요. {Q2}", fact_updates={"impact_part": msg})
            if "my_signal" not in inp.facts:
                return ChatResult(reply="알겠어요. 과실비율을 계산할게요.", next_action="verdict", fact_updates={"my_signal": msg})
            return ChatResult(reply="네, 확인했어요.")
        if "경위서" in msg:
            return ChatResult(reply="사건경위서를 만들게요.", next_action="create_report")
        if "반박" in msg:
            return ChatResult(reply="반박의견서를 준비할게요.", next_action="create_rebuttal")
        if "황색" in msg or "노란" in msg:
            return ChatResult(
                reply="상대 신호를 황색으로 반영할게요. 신호위반 일방과실이 아니게 되어 비율을 다시 계산해요.",
                next_action="rejudge",
                fact_updates={"opponent_signal": "yellow"},
            )
        m = RATIO_RE.search(msg)
        if m:
            mine, other = int(m.group(1)), int(m.group(2))
            return ChatResult(reply=f"상대 보험사 주장 나 {mine} : 상대 {other}으로 적어 뒀어요. 판정 카드에서 비교해 보세요.", fact_updates={"opponent_claim": {"mine": mine, "other": other}})
        return ChatResult(reply="네, 확인했어요. 더 궁금한 점이 있으면 말씀해 주세요.")

    async def judge(self, inp: JudgeInput) -> JudgeResult:
        claim = inp.facts.get("opponent_claim")
        opponent_claim = Ratio(**claim) if claim else None
        chart = Chart(name="신호기 있는 교차로 · 신호위반", note="사고 유형별 기본 비율을 정해 둔 표 · 차대이륜차 편")
        if inp.facts.get("opponent_signal") == "yellow":
            return JudgeResult(
                ratio_mine=20, ratio_other=80,
                summary="상대 황색 신호 진입으로 기본 과실이 적용돼요. 내 차의 전방 주시 의무가 일부 반영됐어요.",
                change_reason="상대 신호가 황색으로 바뀌어 신호위반 일방과실 대신 기본 과실이 적용됐어요.",
                opponent_claim=opponent_claim,
                basis=Basis(chart=Chart(name="신호기 있는 교차로 · 황색 신호 진입", note=chart.note), precedents=PRECEDENTS),
            )
        return JudgeResult(
            ratio_mine=0, ratio_other=100,
            summary="상대 신호위반 일방과실이에요. 내 차가 미리 알아차리거나 피할 수 없었던 것으로 판단돼요.",
            change_reason="상대 신호를 다시 적색으로 반영해 신호위반 일방과실로 돌아왔어요." if inp.previous_verdict else None,
            opponent_claim=opponent_claim,
            basis=Basis(chart=chart, precedents=PRECEDENTS),
        )

    async def write(self, inp: WriteInput) -> WriteResult:
        v = inp.verdict
        if inp.kind == "report":
            sections = [
                Section(index=1, title="사고 일시 및 장소", body="2026년 8월 22일 14시경, 서울시 강남구 논현사거리 교차로에서 발생한 사고입니다."),
                Section(index=2, title="사고 경위", body="본인은 2차로에서 정상 신호에 따라 직진 중이었습니다. 우측에서 교차로에 진입한 이륜차가 본인 차량의 우측 앞펜더를 충격하였습니다."),
                Section(index=3, title="블랙박스 영상 분석 결과", body="영상에서 본인 차량의 2차로 직진, 상대 차량의 교차로 진입, 주행 속도 약 48km/h가 확인됩니다."),
                Section(index=4, title="주장 요지", body=f"상대 차량의 진입으로 생긴 사고이므로, 나 {v.ratio_mine} : 상대 {v.ratio_other}의 과실비율 적용을 요청드립니다."),
            ]
            if inp.revision_request:
                sections[1] = Section(index=2, title="사고 경위", body="본인은 2차로 직진 중 우측에서 진입한 이륜차와 충돌하였습니다.")
            caveat = None if inp.facts.get("my_signal") else "정지선 통과 시점 한 가지는 아직 확인 중이에요."
            return WriteResult(sections=sections, caveat=caveat, page_count=2)
        claim = v.opponent_claim or {"mine": 30, "other": 70}
        body = (
            f"귀사는 나 {claim['mine']} : 상대 {claim['other']}을 제시하셨습니다. 블랙박스 영상에서 상대 차량의 교차로 진입 상황이 확인됩니다. "
            f"인정기준 도표({v.basis.get('chart', {}).get('name', '')})와 심의사례 2019-018856 · 2021-004312에 비추어 "
            f"나 {v.ratio_mine} : 상대 {v.ratio_other}이 타당합니다."
        )
        return WriteResult(body=body)

    async def explain(self, inp: ExplainInput) -> ExplainResult:
        for p in PRECEDENTS:
            if p.id == inp.precedent_id:
                return ExplainResult(body_text=p.body_text)
        return ExplainResult(body_text="해당 심의사례 설명을 찾지 못했어요.")
