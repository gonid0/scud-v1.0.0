#!/usr/bin/env python3
"""
Provisioning CLI for SCUD reader firmware.

Usage:
    provisioner.py --port /dev/ttyUSB0 \
                   --server https://scud.example.com \
                   --admin-api-key sk_admin_... \
                   --group-id <uuid> \
                   --display-name "Entrance reader"

Requires: pyserial, requests
"""
import argparse
import base64
import re
import sys
import time
import uuid

import serial
import requests


def send(ser: serial.Serial, cmd: str, wait: float = 0.3) -> str:
    ser.write((cmd + "\r\n").encode())
    ser.flush()
    time.sleep(wait)
    return ser.read_all().decode(errors="replace")


def ask(ser: serial.Serial, cmd: str) -> str:
    r = send(ser, cmd)
    print(f"> {cmd}\n{r}")
    return r


def extract_pubkey(resp: str) -> bytes:
    m = re.search(r"pubkey=([0-9a-fA-F]{64})", resp)
    if not m:
        raise RuntimeError(f"pubkey not found in SHOW-PUBKEY response:\n{resp}")
    return bytes.fromhex(m.group(1))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--server", required=True, help="Base URL of SCUD backend")
    parser.add_argument("--admin-api-key", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--group-id", required=True, help="UUID of reader group")
    parser.add_argument("--description", default="")
    parser.add_argument("--lock-duration-ms", type=int, default=3000)
    args = parser.parse_args()

    ser = serial.Serial(args.port, args.baud, timeout=2)
    time.sleep(2)     # let ESP32 settle after reset
    ser.reset_input_buffer()

    print("[1] Generating reader keypair on device...")
    ask(ser, "GEN-KEYPAIR")

    resp = send(ser, "SHOW-PUBKEY")
    reader_pubkey = extract_pubkey(resp)
    print(f"    reader pubkey = {reader_pubkey.hex()}")

    reader_id_bytes = uuid.uuid4().bytes
    group_id_bytes  = uuid.UUID(args.group_id).bytes

    print("[2] Enrolling reader at backend...")
    r = requests.post(
        f"{args.server.rstrip('/')}/api/v1/admin/readers/enroll",
        headers={"X-Api-Key": args.admin_api_key},
        json={
            "reader_id": reader_id_bytes.hex(),
            "reader_pubkey": base64.b64encode(reader_pubkey).decode(),
            "reader_group_id": str(uuid.UUID(bytes=group_id_bytes)),
            "display_name": args.display_name,
            "description": args.description,
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    server_ed = base64.b64decode(data["server_ed25519_pubkey"])
    server_x  = base64.b64decode(data["server_x25519_pubkey"])

    print("[3] Writing config to device...")
    ask(ser, f"SET-READER-ID {reader_id_bytes.hex()}")
    ask(ser, f"SET-GROUP-ID {group_id_bytes.hex()}")
    ask(ser, f"SET-SERVER-ED-PUB {server_ed.hex()}")
    ask(ser, f"SET-SERVER-X-PUB {server_x.hex()}")
    ask(ser, f"SET-LOCK-DURATION {args.lock_duration_ms}")
    ask(ser, f"SET-TIME {int(time.time())}")

    print("[4] COMMIT")
    resp = ask(ser, "COMMIT")
    if "OK" not in resp:
        print("ERROR: commit failed")
        return 1

    print(f"\nReader provisioned: reader_id={reader_id_bytes.hex()}")
    print("Reboot the device now (RST button).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
