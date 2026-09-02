import pytest

from app.agent.loader import load_agent_class


def test_load_agent_class():
    assert load_agent_class("builtins:object") is object


def test_load_agent_class_bad_path():
    with pytest.raises(ImportError):
        load_agent_class("no.such.module:Thing")
    with pytest.raises(ImportError):
        load_agent_class("builtins:NoSuchClass")
