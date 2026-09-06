import pytest

from common.jsonutil import parse_json_object


def test_parses_fenced_json():
    assert parse_json_object('설명\n```json\n{"a": 1}\n```') == {"a": 1}


def test_repairs_trailing_commas_and_comments():
    text = '{"a": [1, 2,], // note\n "b": {"c": "x",},}'
    assert parse_json_object(text) == {"a": [1, 2], "b": {"c": "x"}}


def test_closes_truncated_output():
    text = '{"vehicles": [{"id": "vehicle_1", "description": "블랙박스"}, {"id": "vehicle_2", "descr'
    parsed = parse_json_object(text)
    assert parsed["vehicles"][0]["id"] == "vehicle_1"


def test_extracts_object_from_surrounding_text():
    assert parse_json_object('결과: {"ok": true} 끝') == {"ok": True}


def test_raises_on_garbage():
    with pytest.raises(ValueError):
        parse_json_object("no json here")


def test_mid_document_error_is_not_silently_truncated_in_strict_mode():
    """vehicles 배열 중간에 `]` 가 잘못 들어간 응답: 관대한 파싱은 앞부분만 살리지만, truncated_only 모드는 오류를 낸다 (→ repair)."""
    import pytest

    from common.jsonutil import MidDocumentJSONError, parse_json_object

    # Gemini 실제 실수: 첫 객체를 `}` 대신 `]` 로 닫음 → 국소 수정으로 뒤쪽 필드까지 전부 살린다 (관대/엄격 모드 모두)
    broken = '{"vehicles": [{"id": "vehicle_1", "movement": "straight"} ], {"id": "vehicle_2", "movement": "left_turn"}], "collision_pair": {"participants": ["vehicle_1", "vehicle_2"]}}'
    for strict in (False, True):
        parsed = parse_json_object(broken, truncated_only=strict)
        assert [v["id"] for v in parsed["vehicles"]] == ["vehicle_1", "vehicle_2"] and parsed["collision_pair"]["participants"] == ["vehicle_1", "vehicle_2"]
    # 값 사이 콤마 누락도 같은 방식으로
    missing_comma = '{"a": {"x": 1} "b": [1, 2] "c": 3}'
    assert parse_json_object(missing_comma) == {"a": {"x": 1}, "b": [1, 2], "c": 3}
    # 고칠 수 없는 중간 오류: 관대 모드는 앞부분만 살리고, 엄격 모드는 오류를 낸다 (→ repair 호출)
    unfixable = '{"vehicles": [{"id": "vehicle_1"} 잘못된 토큰 {"id": "vehicle_2"}], "collision_pair": {"participants": ["vehicle_1", "vehicle_2"]}}'
    lenient = parse_json_object(unfixable)
    assert [v["id"] for v in lenient["vehicles"]] == ["vehicle_1"] and "collision_pair" not in lenient
    with pytest.raises(MidDocumentJSONError):
        parse_json_object(unfixable, truncated_only=True)
    truncated = '{"vehicles": [{"id": "vehicle_1"}, {"id": "vehicle_2"}], "collision_pair": {"participants": ["vehicle_1", "vehicle_2"'
    recovered = parse_json_object(truncated, truncated_only=True)  # 진짜 잘림은 살린다 (마지막 미완성 값만 버린다)
    assert [v["id"] for v in recovered["vehicles"]] == ["vehicle_1", "vehicle_2"] and recovered["collision_pair"]["participants"][0] == "vehicle_1"

