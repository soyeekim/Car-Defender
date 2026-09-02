import builtins
import os

import pytest

from app.storage import get_storage, reset_storage
from app.storage.local import LocalStorage


async def test_local_storage_roundtrip(tmp_path):
    st = LocalStorage(tmp_path / "store")
    src = tmp_path / "in.bin"
    src.write_bytes(b"0123456789")
    assert await st.put_file("videos/c/v.mp4", src) == 10
    assert await st.size("videos/c/v.mp4") == 10
    chunks = [c async for c in st.read_range("videos/c/v.mp4", 2, 5)]
    assert b"".join(chunks) == b"2345"
    assert await st.read_bytes("videos/c/v.mp4") == b"0123456789"
    async with st.local_path("videos/c/v.mp4") as p:
        assert p.read_bytes() == b"0123456789"
    await st.delete("videos/c/v.mp4")
    with pytest.raises(FileNotFoundError):
        await st.size("videos/c/v.mp4")
    await st.delete("videos/c/v.mp4")  # 두 번 지워도 조용


async def test_local_storage_put_bytes_and_key_traversal_blocked(tmp_path):
    st = LocalStorage(tmp_path / "store")
    await st.put_bytes("pdfs/c/r.pdf", b"%PDF")
    assert await st.read_bytes("pdfs/c/r.pdf") == b"%PDF"
    with pytest.raises(ValueError):
        await st.put_bytes("../escape", b"x")


def test_get_storage_returns_local_by_default(test_env):
    reset_storage()
    assert isinstance(get_storage(), LocalStorage)
    assert get_storage().healthy()


@pytest.mark.parametrize("bad_key", ["..\\escape", "C:\\x\\y", "a\\b"])
def test_validate_key_rejects_windows_path_tricks(bad_key):
    from app.storage.base import validate_key

    with pytest.raises(ValueError):
        validate_key(bad_key)


@pytest.mark.parametrize("bad_key", ["..\\escape", "C:\\x\\y", "a\\b"])
async def test_local_storage_put_bytes_rejects_windows_path_tricks(tmp_path, bad_key):
    st = LocalStorage(tmp_path / "store")
    with pytest.raises(ValueError):
        await st.put_bytes(bad_key, b"x")


def test_local_storage_healthy_true_for_writable_dir(tmp_path):
    st = LocalStorage(tmp_path / "store")
    assert st.healthy() is True


def test_local_storage_healthy_false_when_root_is_a_file(tmp_path):
    file_path = tmp_path / "not_a_dir"
    file_path.write_text("x")
    st = LocalStorage(file_path)
    assert st.healthy() is False


def test_local_storage_healthy_false_when_probe_write_fails(tmp_path, monkeypatch):
    st = LocalStorage(tmp_path / "store")
    probe = tmp_path / "store" / ".health"
    real_open = builtins.open

    def boom_open(path, mode="r", *args, **kwargs):
        if os.fspath(path) == os.fspath(probe) and "w" in mode:
            raise OSError("boom")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", boom_open)

    assert st.healthy() is False


async def test_local_storage_read_range_empty_when_start_after_end(tmp_path):
    st = LocalStorage(tmp_path / "store")
    await st.put_bytes("a.bin", b"0123456789")
    chunks = [c async for c in st.read_range("a.bin", 5, 2)]
    assert chunks == []
