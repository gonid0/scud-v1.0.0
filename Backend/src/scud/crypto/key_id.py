# key_id computation — MUST use BLAKE2s-128.
# Shared §5.1: key_id = BLAKE2s(reader_id || phone_pubkey || issued_at_8LE || serial_4LE, 16)
# NOTE: hashlib.blake2s is the correct implementation; nacl's crypto_generichash gives BLAKE2b
# and would produce different results (shared §7.2).

import hashlib


def compute_key_id(
    reader_id: bytes,
    phone_pubkey: bytes,
    issued_at: int,
    serial: int,
) -> bytes:
    """Return 16-byte BLAKE2s key_id per shared §5.1."""
    data = (
        reader_id
        + phone_pubkey
        + issued_at.to_bytes(8, "little")
        + serial.to_bytes(4, "little")
    )
    return hashlib.blake2s(data, digest_size=16).digest()
