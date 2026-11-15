"""XEP-0454 file encryption helpers."""

import os

import pytest

from jaba_da_hat.backend import aesgcm


def test_round_trip_and_url(tmp_path):
    src = tmp_path / "photo.jpg"
    data = os.urandom(3 * aesgcm.CHUNK + 123)
    src.write_bytes(data)
    enc, iv, key, size = aesgcm.encrypt_file(str(src))
    try:
        assert size == len(data) + aesgcm.TAG and oct(os.stat(enc).st_mode & 0o777) == "0o600"
        assert open(enc, "rb").read(64) != data[:64]
        url = aesgcm.make_url("https://up.example.org/x/photo.jpg", iv, key)
        assert url.startswith("aesgcm://up.example.org/x/photo.jpg#") and len(url.split("#")[1]) == 88
        https, iv2, key2 = aesgcm.parse(url)
        assert (https, iv2, key2) == ("https://up.example.org/x/photo.jpg", iv, key)
        dest = tmp_path / "out.jpg"
        aesgcm.decrypt_file(enc, str(dest), iv, key)
        assert dest.read_bytes() == data
    finally:
        os.unlink(enc)


def test_tampered_file_is_rejected_and_nothing_kept(tmp_path):
    src = tmp_path / "a.txt"
    src.write_bytes(b"hello world" * 100)
    enc, iv, key, _ = aesgcm.encrypt_file(str(src))
    with open(enc, "r+b") as fh:
        fh.seek(5)
        fh.write(b"X")
    dest = tmp_path / "b.txt"
    with pytest.raises(Exception):
        aesgcm.decrypt_file(enc, str(dest), iv, key)
    assert not dest.exists() and not (tmp_path / "b.txt.decrypting").exists()
    os.unlink(enc)


def test_old_16_byte_iv_links_parse():
    url = "aesgcm://h/f#" + "00" * 16 + "11" * 32
    _, iv, key = aesgcm.parse(url)
    assert len(iv) == 16 and len(key) == 32
    with pytest.raises(ValueError):
        aesgcm.parse("aesgcm://h/f#abc")
