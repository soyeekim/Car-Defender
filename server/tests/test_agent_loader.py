import pytest

from app.agent.base import Chart, Precedent
from app.agent.loader import load_agent_class


def test_load_agent_class():
    assert load_agent_class("builtins:object") is object


def test_load_agent_class_bad_path():
    with pytest.raises(ImportError):
        load_agent_class("no.such.module:Thing")
    with pytest.raises(ImportError):
        load_agent_class("builtins:NoSuchClass")


def test_precedent_and_chart_optional_text_defaults():
    p = Precedent(id="p1", title="유사 판례")
    assert p.body_text == ""
    assert Chart(name="차11").note == ""
