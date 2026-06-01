# Ed25519 signing/verification with domain separation tags.
# All domain tags are 16-byte ASCII, zero-padded (shared §2.3).

from nacl.signing import SigningKey, VerifyKey

DOMAIN_KEY = b"RDR-KEY-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_INF = b"RDR-INF-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_RSP = b"RDR-RSP-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_FLT = b"RDR-FLT-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_RCP = b"RDR-RCP-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_BLK = b"RDR-BLK-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_FDI = b"RDR-FDI-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_TGR = b"RDR-TGR-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_TIM = b"RDR-TIM-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_REV = b"RDR-REV-v1\x00\x00\x00\x00\x00\x00"
DOMAIN_PSG = b"RDR-PSG-v1\x00\x00\x00\x00\x00\x00"  # passage_receipt (shared §15)
DOMAIN_BLE = b"RDR-BLE-v1\x00\x00\x00\x00\x00\x00"  # BLE session_token (shared §17)

# Verify all tags are exactly 16 bytes
for _d in [DOMAIN_KEY, DOMAIN_INF, DOMAIN_RSP, DOMAIN_FLT, DOMAIN_RCP,
           DOMAIN_BLK, DOMAIN_FDI, DOMAIN_TGR, DOMAIN_TIM, DOMAIN_REV,
           DOMAIN_PSG, DOMAIN_BLE]:
    assert len(_d) == 16, f"domain tag must be 16 B, got {len(_d)}"


def sign_detached(privkey_bytes: bytes, domain: bytes, payload: bytes) -> bytes:
    """Return 64-byte Ed25519 signature over domain || payload."""
    sk = SigningKey(privkey_bytes)
    return bytes(sk.sign(domain + payload).signature)


def verify_detached(
    pubkey_bytes: bytes,
    domain: bytes,
    payload: bytes,
    signature: bytes,
) -> bool:
    """Return True if signature is valid; False otherwise."""
    try:
        vk = VerifyKey(pubkey_bytes)
        vk.verify(domain + payload, signature)
        return True
    except Exception:
        return False
