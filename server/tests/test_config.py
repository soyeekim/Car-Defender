import pytest

from app.config import Settings


def test_prod_rejects_default_jwt_secret():
    with pytest.raises(ValueError):
        Settings(app_env="prod", jwt_secret="change-me")


def test_prod_rejects_default_long_jwt_secret_placeholder():
    with pytest.raises(ValueError):
        Settings(app_env="prod", jwt_secret="change-me-to-a-long-random-string")


def test_prod_rejects_short_jwt_secret():
    with pytest.raises(ValueError):
        Settings(app_env="prod", jwt_secret="x" * 10)


def test_prod_accepts_long_random_jwt_secret():
    Settings(app_env="prod", jwt_secret="x" * 40)


def test_dev_allows_short_jwt_secret():
    Settings(app_env="dev", jwt_secret="change-me")
