"""Sealed-box KDF tests (CRYPTO-05 "blender").

The sealed box (reader -> server encryption for FDI/BLK) derives the
ChaCha20-Poly1305 key from the raw X25519 shared secret via
``key = BLAKE2b(shared, digest_size=32)`` (KDF), applied IDENTICALLY in
backend (decrypt + reference encrypt) and firmware (reader encrypt).

These tests lock that contract:

* a plain encrypt -> decrypt round-trip with the KDF, and
* a deterministic golden vector with a FIXED ephemeral private key, so the
  exact ciphertext bytes are pinned. Any drift in the KDF, nonce derivation,
  or blob layout on either side breaks this vector. The firmware encrypt is
  pure crypto (Monocypher X25519 + BLAKE2b + ChaCha20-Poly1305) and must
  reproduce this same blob byte-for-byte; firmware-encrypt runtime is
  hardware-verified, but the golden vector here pins the interop.
"""

from __future__ import annotations

import hashlib

import pytest
from nacl.bindings import crypto_scalarmult, crypto_scalarmult_base
from nacl.bindings.crypto_aead import crypto_aead_chacha20poly1305_ietf_encrypt
from nacl.public import PrivateKey

from scud.crypto.sealed_box import decrypt_sealed_blob, encrypt_sealed_blob


# ---------------------------------------------------------------------------
# Round-trip with the KDF
# ---------------------------------------------------------------------------

def test_sealed_box_kdf_roundtrip():
    """encrypt -> decrypt recovers plaintext under the BLAKE2b(shared,32) KDF."""
    server = PrivateKey.generate()
    server_priv = bytes(server)
    server_pub = bytes(server.public_key)

    plaintext = b"reader->server FDI/BLK payload, arbitrary length 0123456789"
    blob = encrypt_sealed_blob(plaintext, server_pub)
    recovered = decrypt_sealed_blob(blob, server_priv, server_pub)
    assert recovered == plaintext


def test_sealed_box_kdf_blob_layout_unchanged():
    """Blob layout stays eph_pub(32) || ct+tag(len+16) — KDF must not touch it."""
    server = PrivateKey.generate()
    plaintext = b"x" * 40
    blob = encrypt_sealed_blob(plaintext, bytes(server.public_key))
    assert len(blob) == 32 + len(plaintext) + 16


def test_sealed_box_uses_blake2b_kdf_not_raw_secret():
    """Decrypt must use the KDF'd key; the raw secret must NOT decrypt the box.

    Reconstruct the box manually with the RAW shared secret as the AEAD key
    (the pre-CRYPTO-05 behaviour) and assert backend decrypt rejects it.
    """
    from nacl.exceptions import CryptoError

    server = PrivateKey.generate()
    server_priv = bytes(server)
    server_pub = bytes(server.public_key)

    eph = PrivateKey.generate()
    eph_priv = bytes(eph)
    eph_pub = bytes(eph.public_key)

    shared = crypto_scalarmult(eph_priv, server_pub)
    nonce12 = hashlib.blake2b(eph_pub + server_pub, digest_size=24).digest()[:12]

    # RAW-secret box (no KDF) — must fail under the new decrypt.
    raw_box = eph_pub + crypto_aead_chacha20poly1305_ietf_encrypt(
        b"legacy", aad=None, nonce=nonce12, key=shared
    )
    with pytest.raises((CryptoError, Exception)):
        decrypt_sealed_blob(raw_box, server_priv, server_pub)


# ---------------------------------------------------------------------------
# Deterministic golden vector (fixed ephemeral private key)
# ---------------------------------------------------------------------------

# Fixed ephemeral private key (RFC 7748 §6.1 "Alice" scalar) and a fixed server
# X25519 private key (RFC 7748 "Bob" scalar). X25519 clamps internally, so the
# derived public keys and the whole box are fully deterministic.
GV_EPH_PRIV = bytes.fromhex(
    "a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4"
)
GV_SERVER_PRIV = bytes.fromhex(
    "5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb"
)
GV_SERVER_PUB = bytes.fromhex(
    "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"
)
GV_EPH_PUB = bytes.fromhex(
    "1c9fd88f45606d932a80c71824ae151d15d73e77de38e8e000852e614fae7019"
)
GV_SHARED = bytes.fromhex(
    "709e32d6ae0a5731fea82113324fc01e686dc28cb7781e61487752faf875490c"
)
GV_KEY = bytes.fromhex(
    "377a00b2eeb06c580073b6f07fcb40333d7758a6bd70330a7282fe68116779f7"
)
GV_PLAINTEXT = b"CRYPTO-05 golden vector :: FDI/BLK sealed box"
GV_BLOB = bytes.fromhex(
    "1c9fd88f45606d932a80c71824ae151d15d73e77de38e8e000852e614fae7019"
    "a1eeca88923ad5bb81fb2211b8debe011af9021863e8fe00f20bfda2e2afedc6"
    "a039579392c740f034f37a1ea3a7f3fe2c41be45b6bb02b84025fbfc2c"
)


def test_golden_vector_self_consistent():
    """The hardcoded vector is internally consistent (pins the KDF derivation)."""
    server_pub = crypto_scalarmult_base(GV_SERVER_PRIV)
    eph_pub = crypto_scalarmult_base(GV_EPH_PRIV)
    assert server_pub == GV_SERVER_PUB
    assert eph_pub == GV_EPH_PUB

    shared = crypto_scalarmult(GV_EPH_PRIV, GV_SERVER_PUB)
    assert shared == GV_SHARED
    # The KDF: key = BLAKE2b(shared, 32).
    assert hashlib.blake2b(shared, digest_size=32).digest() == GV_KEY


def test_golden_vector_backend_decrypt_recovers_plaintext():
    """Backend decrypt of the fixed-ephemeral golden blob recovers plaintext.

    This is the firmware<->backend interop lock: the firmware encrypt (same
    X25519 + BLAKE2b-32 KDF + ChaCha20-Poly1305 with nonce BLAKE2b-24[:12])
    must produce GV_BLOB byte-for-byte for these fixed inputs.
    """
    recovered = decrypt_sealed_blob(GV_BLOB, GV_SERVER_PRIV, GV_SERVER_PUB)
    assert recovered == GV_PLAINTEXT


def test_golden_vector_blob_is_reproducible():
    """Re-deriving the box from fixed inputs reproduces GV_BLOB exactly."""
    shared = crypto_scalarmult(GV_EPH_PRIV, GV_SERVER_PUB)
    key = hashlib.blake2b(shared, digest_size=32).digest()
    nonce12 = hashlib.blake2b(
        GV_EPH_PUB + GV_SERVER_PUB, digest_size=24
    ).digest()[:12]
    ct_and_tag = crypto_aead_chacha20poly1305_ietf_encrypt(
        GV_PLAINTEXT, aad=None, nonce=nonce12, key=key
    )
    assert GV_EPH_PUB + ct_and_tag == GV_BLOB
