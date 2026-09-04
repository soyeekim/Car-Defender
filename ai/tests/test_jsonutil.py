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
