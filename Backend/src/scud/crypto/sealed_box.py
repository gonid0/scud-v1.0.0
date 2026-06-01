# X25519 + ChaCha20-Poly1305-IETF sealed box decryption.
# Follows shared §2.4: nonce_24 = BLAKE2b(ephemeral_pub || server_x25519_pub, 24),
# then chacha nonce = nonce_24[:12] (IETF variant uses 12-byte nonce).

import hashlib

from nacl.bindings import crypto_scalarmult
from nacl.bindings.crypto_aead import crypto_aead_chacha20poly1305_ietf_decrypt


def decrypt_sealed_blob(
    blob: bytes,
    server_x25519_priv: bytes,
    server_x25519_pub: bytes,
) -> bytes:
    """Decrypt a sealed blob produced by the reader.

    blob layout: ephemeral_pub(32) || ciphertext+tag(N+16)
    """
    if len(blob) < 48:
        raise ValueError(f"sealed blob too short: {len(blob)} B")

    ephemeral_pub = blob[:32]
    ct_and_tag = blob[32:]

    # X25519 key exchange
    shared = crypto_scalarmult(server_x25519_priv, ephemeral_pub)

    # KDF (CRYPTO-05 "blender"): derive a uniform AEAD key from the raw X25519
    # secret via BLAKE2b(shared, 32). MUST match firmware-encrypt exactly.
    key = hashlib.blake2b(shared, digest_size=32).digest()

    # Derive 12-byte nonce from BLAKE2b(ephemeral_pub || server_pub, 24)
    nonce_24 = hashlib.blake2b(
        ephemeral_pub + server_x25519_pub, digest_size=24
    ).digest()
    nonce_12 = nonce_24[:12]

    return crypto_aead_chacha20poly1305_ietf_decrypt(
        ct_and_tag,
        aad=None,
        nonce=nonce_12,
        key=key,
    )


def encrypt_sealed_blob(
    plaintext: bytes,
    server_x25519_pub: bytes,
) -> bytes:
    """Encrypt plaintext for server — used in tests as reference."""
    from nacl.bindings.crypto_aead import crypto_aead_chacha20poly1305_ietf_encrypt
    from nacl.public import PrivateKey

    ephemeral_key = PrivateKey.generate()
    ephemeral_priv = bytes(ephemeral_key)
    ephemeral_pub = bytes(ephemeral_key.public_key)

    shared = crypto_scalarmult(ephemeral_priv, server_x25519_pub)

    # KDF (CRYPTO-05 "blender"): same as decrypt — BLAKE2b(shared, 32).
    key = hashlib.blake2b(shared, digest_size=32).digest()

    nonce_24 = hashlib.blake2b(
        ephemeral_pub + server_x25519_pub, digest_size=24
    ).digest()
    nonce_12 = nonce_24[:12]

    ct_and_tag = crypto_aead_chacha20poly1305_ietf_encrypt(
        plaintext,
        aad=None,
        nonce=nonce_12,
        key=key,
    )
    return ephemeral_pub + ct_and_tag
