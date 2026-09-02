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
