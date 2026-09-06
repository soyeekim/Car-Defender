"""PDF 에서 뽑은 한국어 문장 다듬기.

한국어 조판은 단어 중간에서도 줄을 바꾸므로("…2차로로 진" / "로변경을…"), 줄을 이을 때 공백을 넣을지 말지를
단어 사전으로 판단한다. 이미 공백이 끼어든 낱말("사 고로서", "도 표")과 홀로 떨어진 조사("의무 가")도 붙인다.
"""

from __future__ import annotations

import re

# 심의사례·인정기준 문서에 자주 나오는 낱말. 줄바꿈으로 갈라진 두 조각을 붙였을 때 이 안의 낱말이 되면 공백 없이 잇는다.
VOCAB = frozenset(
    (
        "진로 진로변경 진행 진입 진출 차로 차로변경 차선 차선변경 차량 청구 청구차량 피청구 피청구차량 회전 회전교차로 교차로 사거리 삼거리 "
        "사고 충돌 충격 접촉 추돌 신호 신호등 신호기 신호위반 정지선 횡단보도 보행자 자전거 이륜차 오토바이 직진 좌회전 우회전 유턴 후진 "
        "실선 점선 구간 도로 우선 우선권 통행 통행우선권 양보 의무 주의 주의의무 과실 과실비율 비율 기본 기본과실 결정 수정 수정요소 요소 인정 인정기준 기준 "
        "심의 위원회 도표 방향 지시등 방향지시등 감속 서행 제동 정차 주차 출발 급정지 급진입 급차로변경 안전 안전지대 중앙선 변경 위반 통과 "
        "확인 판단 고려 결과 사례 관련 규정 도로교통법 운전 운전자 정상 정상적 상황 상태 지점 시점 부분 전방 후방 좌측 우측 측면 전면 후면 "
        "앞범퍼 뒷범퍼 범퍼 손상 동영상 사진 현장 약도 자료 입증 쟁점 근거 이유 주장 내용 개요 번호 결정비율 기본비율 청구인 피청구인 "
        "무리 무리하게 일방 일방과실 타당 타당함 명시 명시적 적용 검토 가산 감산 조정 예상 발생 위치 속도 거리 시야 야간 주간 우천 노면 "
        "동일 동일방향 반대 맞은편 측방 후행 선행 선진입 후진입 동시 동시진입 회피 회피조치 조치 대로 소로 이면도로 골목 주차장 고속도로 진출입로 "
        "녹색 적색 황색 점멸 좌회전신호 직진신호 비보호 신호변경 꼬리물기 정지 출입 출구 입구 교통 교통섬 원형 외측 내측 차로형 "
        # 자주 갈라지는 용언·어미 조각 (줄 끝 '주의하' + 줄 머리 '여' 처럼)
        "하여 하였 하던 하는 하고 하며 하면 하지 되어 되는 되었 되지 있는 있고 있어 없는 없고 이며 이고 이므로 으므로 하므로 되므로 "
        "경우 위해 위한 위해서 때문 통해 따라 따른 대한 대해 여부 여기 그러 그리 및 또는 아니 않고 않은 않아 못한 못하 "
        "경로 신뢰 신뢰하 운행 주행 침범 통과한 지만 이지만 이었고 이었으며 였고 였으며 있었고 하였고 되었고 했고 됐고 하였으나 이었으나 였으나 "
        "이라고 이라는 이므로 인지 인한 인하여 의한 의하여 의하면 따르면 위하여 하기 되기 보이 보인 보임 판단 판단하 예상 예상하"
    ).split()
)
_DIGIT_UNITS = ("차로", "차선", "차", "시", "분", "초", "월", "일", "년", "대", "건", "회", "m", "km", "%")
# 줄 머리에 홀로 오면 앞 낱말의 조사·어미인 토큰 ("…경우" / "에는 진입2차로를")
_PARTICLE_HEADS = frozenset("에는 에서 에서는 에게 으로 으로는 까지 부터 이며 이고 이므로 에도 에만".split())
_MAX_PIECE = 4
_SPLIT_WORD = re.compile("|".join(sorted({f"{w[:i]} {w[i:]}" for w in VOCAB if len(w) >= 2 for i in range(1, len(w))}, key=len, reverse=True)))
_DANGLING_PARTICLE = re.compile(r"(?<=[가-힣]) (을|를|로|의|에|와|과|도|가|은|는|에서|으로|에게|까지|부터|이며|이고)(?=[ ,.;)]|$)")
_SENTENCE_END = ("다.", "임.", "함.", "음.", "됨.", "임", "함", "음", "됨", ".", ",", ")", ":", "·")


def join_wrapped(prev: str, nxt: str) -> str:
    """줄바꿈으로 나뉜 두 줄을 잇는다. 경계의 조각을 붙여 사전 낱말이 되면 공백 없이, 아니면 공백을 두고 잇는다."""
    a, b = prev.rstrip(), nxt.lstrip()
    if not a:
        return b
    if not b:
        return a
    if a.endswith(_SENTENCE_END) or b[0] in "•●▪◦(-" or not (a[-1].isalnum() or "가" <= a[-1] <= "힣"):
        return a + " " + b
    tail, head = a.split(" ")[-1], b.split(" ")[0]
    if tail.isdigit() and head.startswith(_DIGIT_UNITS):
        return a + b  # "2" + "차로로" → "2차로로"
    if "가" <= tail[-1] <= "힣":
        # '으로/으므로/으며' 는 낱말 첫머리에 오지 않는다 → 앞 낱말의 조사·어미
        if head.startswith("으") or head in _PARTICLE_HEADS:
            return a + b
        # 명사 + 하다/되다 활용 ("진입" + "하면서", "충돌" + "되어")
        if len(head) >= 2 and ((head[0] == "하" and head[1] in "여였던는고며면지기니") or (head[0] == "되" and head[1] in "어었는고며면지")):
            return a + b
    for k in range(1, _MAX_PIECE + 1):
        if k > len(tail):
            break
        for m in range(1, _MAX_PIECE + 1):
            if m > len(head):
                break
            if tail[-k:] + head[:m] in VOCAB:
                return a + b
    return a + " " + b


def join_lines(lines: list[str]) -> str:
    text = ""
    for line in lines:
        text = join_wrapped(text, line)
    return text


def repair_spacing(text: str) -> str:
    """'사 고로서' → '사고로서', '도 표' → '도표', '의무 가' → '의무가' 처럼 공백이 잘못 끼어든 곳을 붙인다."""
    if not text:
        return text
    fixed = re.sub(r"\s+", " ", text)
    fixed = _SPLIT_WORD.sub(lambda m: m.group(0).replace(" ", ""), fixed)
    return _DANGLING_PARTICLE.sub(r"\1", fixed).strip()
