# Firmware host conformance test

Proves the firmware C/C++ crypto reproduces the **shared golden protocol
vectors** (`docs/test_vectors/protocol_v1.json`) — the third side of the
byte-exact contract alongside the Backend (PyNaCl) and Android (BouncyCastle)
conformance tests. A single divergent bit breaks Ed25519 verify against a real
reader, so all three implementations must agree exactly.

Covered (pure modules, no Arduino / ESP-IDF needed):
- **Primitives:** 12 signing domain tags, `key_id` (BLAKE2s), raw BLAKE2s-128,
  MurmurHash3 x86_32, bloom membership, Ed25519 domain-separated signatures
  (Monocypher — byte-identical to PyNaCl and BouncyCastle).
- **Wire structures** (parse exact offsets + verify the signature): `issued_key`
  (151 B, server-sig DOMAIN_KEY), `time_grant` (148 B, DOMAIN_TGR),
  `passage_receipt` (192 B, reader-sig DOMAIN_PSG), `INFO` (146 B, reader-sig
  DOMAIN_INF), `delivery_receipt` (112 B, reader-sig DOMAIN_RCP), `FDI` (241 B,
  reader-sig DOMAIN_FDI).
- **BLE framing** (§16.5): both sides of `[seq][flags][total_len]` — the produce
  side (`ble_frame_message`, used by `notify_chunked`) reproduces the golden PDU
  sequences, and the consume side (`ble_reasm_feed`, used by the OpWrite callback)
  reassembles them back to the payload + rejects a seq gap — for the `ble_framing`
  vectors.
- **APDU/NFC framing** (§3.2, §4): the `apdu_framing` data bodies (OP_CHUNKED /
  OP_SINGLE FETCH responses, READ_CHUNK cmd+resp, PUSH_CHUNK cmd, REFERENCE)
  parsed at the exact offsets transfer.cpp uses — reconciled byte-identical with
  Android.

`vectors_generated.h` is **generated** from the JSON corpus — do not edit it.

## Regenerate the vectors header

```sh
python docs/test_vectors/generate.py        # JSON corpus (from the backend reference)
python docs/test_vectors/gen_c_header.py    # -> test_host/vectors_generated.h
```

## Build & run

From `ESP32/firmware/`:

**Linux / CI (gcc):**
```sh
g++ -std=c++17 -I src/crypto -I src/ble -I test_host -I lib/Monocypher \
    test_host/test_conformance.cpp \
    src/crypto/blake2s.cpp src/crypto/murmur3.cpp src/crypto/bloom.cpp \
    src/crypto/key_id.cpp src/crypto/domains.cpp src/crypto/ed25519.cpp \
    src/ble/ble_frame.cpp \
    lib/Monocypher/monocypher.c lib/Monocypher/monocypher-ed25519.c \
    -o test_host/conformance && ./test_host/conformance
```

**Windows (MSVC):** run from a *Developer Command Prompt* (or `call vcvars64.bat`):
```bat
cl /EHsc /std:c++17 /I src\crypto /I src\ble /I test_host /I lib\Monocypher ^
   test_host\test_conformance.cpp ^
   src\crypto\blake2s.cpp src\crypto\murmur3.cpp src\crypto\bloom.cpp ^
   src\crypto\key_id.cpp src\crypto\domains.cpp src\crypto\ed25519.cpp ^
   src\ble\ble_frame.cpp ^
   lib\Monocypher\monocypher.c lib\Monocypher\monocypher-ed25519.c ^
   /Fe:test_host\conformance.exe && test_host\conformance.exe
```

Exit code 0 = all vectors match (primitives + 6 wire structures + BLE framing).
Verified locally with MSVC 19.x.

### op_sink + verify-from-flash test (N2/B6)

`test_op_sink.cpp` proves the **two-pass verify-from-flash** logic (large-packet
receive streamed to a flash A/B slot) on a **RAM-backed fake `flash_slot_sink`** —
no hardware. It builds a valid golden `filter_package`, streams it through the
fake sink in awkward chunk sizes, runs `verify_filter_sink_sig` (sig from the
tail, body re-read from the start — the exact passes the device runs against
SPIFFS), and asserts VALID; then flips a body/sig byte and asserts INVALID.

**Windows (MSVC):**
```bat
cl /EHsc /std:c++17 /I src\crypto /I src\transport /I test_host /I lib\Monocypher ^
   test_host\test_op_sink.cpp ^
   src\transport\op_sink.cpp src\crypto\domains.cpp src\crypto\ed25519.cpp ^
   lib\Monocypher\monocypher.c lib\Monocypher\monocypher-ed25519.c ^
   /Fe:test_host\op_sink_test.exe && test_host\op_sink_test.exe
```
**Linux / CI (gcc):** swap `cl … /Fe:…` for the equivalent
`g++ -std=c++17 -I src/crypto -I src/transport -I test_host -I lib/Monocypher … -o test_host/op_sink_test`.

Exit code 0 = the verify-from-flash core agrees (VALID golden package; INVALID on
any body/signature/domain tamper; undersized sink rejected). The real SPIFFS I/O
and the BLE host-task flash streaming stay hardware-verified.

## Follow-ups
- Wire this into `.github/workflows/firmware.yml` (the ubuntu runner has gcc):
  build & run the command above so the third implementation's conformance gates
  CI like the backend/android ones do.
- Optionally re-express as a PlatformIO `[env:native]` + Unity test (`TESTIN-02`)
  to also unit-test pure firmware logic (serialization, access decision) on the
  host without an ESP32.
