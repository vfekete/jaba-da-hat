"""Encrypted file sharing for OMEMO chats (XEP-0454): AES-256-GCM files behind ``aesgcm://`` links.

The file is encrypted before upload; key and IV travel only inside the OMEMO-encrypted message, in the
URL fragment (never sent to the web server). The uploaded blob is ciphertext followed by the 16-byte tag.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

TAG = 16
CHUNK = 1 << 20


def is_aesgcm(url: str) -> bool:
    return url.lower().startswith("aesgcm://")


def parse(url: str) -> tuple[str, bytes, bytes]:
    """``aesgcm://host/path#<iv hex><key hex>`` → (https URL without fragment, iv, key)."""
    parts = urlsplit(url)
    frag = parts.fragment
    if len(frag) not in (88, 96) or any(c not in "0123456789abcdefABCDEF" for c in frag):
        raise ValueError("invalid aesgcm link")
    iv, key = bytes.fromhex(frag[:-64]), bytes.fromhex(frag[-64:])  # 12-byte IV (current) or 16 (old clients)
    https = parts._replace(scheme="https", fragment="").geturl()
    return https, iv, key


def make_url(https_url: str, iv: bytes, key: bytes) -> str:
    parts = urlsplit(https_url)
    return parts._replace(scheme="aesgcm", fragment=(iv + key).hex()).geturl()


def encrypt_file(src: str) -> tuple[str, bytes, bytes, int]:
    """Encrypt *src* into a private temp file; returns (path, iv, key, encrypted size). Caller deletes it."""
    key, iv = secrets.token_bytes(32), secrets.token_bytes(12)
    enc = Cipher(algorithms.AES(key), modes.GCM(iv)).encryptor()
    fd, tmp = tempfile.mkstemp(prefix="jdh-upload-", suffix=".bin")  # mode 0600
    try:
        with open(src, "rb") as fin, os.fdopen(fd, "wb") as fout:
            for chunk in iter(lambda: fin.read(CHUNK), b""):
                fout.write(enc.update(chunk))
            fout.write(enc.finalize())
            fout.write(enc.tag)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return tmp, iv, key, os.path.getsize(tmp)


def decrypt_file(enc_path: str, dest: str, iv: bytes, key: bytes) -> None:
    """Decrypt and authenticate *enc_path* into *dest*; on a bad tag nothing is left at *dest*."""
    size = os.path.getsize(enc_path)
    if size < TAG:
        raise ValueError("the downloaded file is too short to be encrypted")
    with open(enc_path, "rb") as fin:
        fin.seek(size - TAG)
        tag = fin.read(TAG)
        fin.seek(0)
        dec = Cipher(algorithms.AES(key), modes.GCM(iv, tag)).decryptor()
        tmp = dest + ".decrypting"
        try:
            with open(tmp, "wb") as fout:
                left = size - TAG
                while left > 0:
                    chunk = fin.read(min(CHUNK, left))
                    if not chunk:
                        raise ValueError("the downloaded file ended early")
                    left -= len(chunk)
                    fout.write(dec.update(chunk))
                dec.finalize()  # raises InvalidTag when the file was altered
            os.replace(tmp, dest)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
