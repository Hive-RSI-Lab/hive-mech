"""Hand an enclosure to Onshape: a new document with the STEP imported.

    mech onshape check                         who the key belongs to
    mech onshape upload out/enclosure.step [--name "Lite enclosure"] [--dry-run]

The key pair is the person's. It comes only from the environment
(ONSHAPE_ACCESS_KEY, ONSHAPE_SECRET_KEY; ONSHAPE_URL for an enterprise
host, default https://cad.onshape.com) and never goes in a file, a commit or
a message. With no key, upload --dry-run prints what it would send.
Onshape takes an API key pair as HTTP basic auth.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

API = "/api/v10"


class Onshape:
    def __init__(self, access: str, secret: str, url: str = "https://cad.onshape.com", opener=None):
        self.url = url.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{access}:{secret}".encode()).decode()
        self.open = opener or urllib.request.urlopen

    @classmethod
    def from_env(cls, opener=None) -> "Onshape":
        a, s = os.environ.get("ONSHAPE_ACCESS_KEY"), os.environ.get("ONSHAPE_SECRET_KEY")
        if not a or not s:
            raise SystemExit("onshape: set ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY "
                             "(the person's key pair; ask them through the lead, never store it)")
        return cls(a, s, os.environ.get("ONSHAPE_URL", "https://cad.onshape.com"), opener)

    def request(self, method: str, path: str, body: bytes | None = None, ctype: str = "application/json") -> dict:
        req = urllib.request.Request(self.url + path, data=body, method=method)
        req.add_header("Authorization", self.auth)
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", ctype)
        with self.open(req) as r:
            data = r.read()
        return json.loads(data) if data else {}

    def session(self) -> dict:
        return self.request("GET", f"{API}/users/sessioninfo")

    def new_document(self, name: str) -> tuple[str, str]:
        d = self.request("POST", f"{API}/documents", json.dumps({"name": name, "isPublic": False}).encode())
        return d["id"], d["defaultWorkspace"]["id"]

    def import_step(self, did: str, wid: str, step: Path) -> dict:
        body, ctype = multipart({"formatName": "STEP", "flattenAssemblies": "false",
                                 "storeInDocument": "false", "translate": "true"},
                                "file", step.name, step.read_bytes())
        return self.request("POST", f"{API}/translations/d/{did}/w/{wid}", body, ctype)

    def wait(self, tid: str, timeout: float = 300.0) -> dict:
        end = time.time() + timeout
        while True:
            t = self.request("GET", f"{API}/translations/{tid}")
            if t.get("requestState") in ("DONE", "FAILED") or time.time() > end:
                return t
            time.sleep(2)


def multipart(fields: dict, fname: str, filename: str, data: bytes) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    out.append(f'--{b}\r\nContent-Disposition: form-data; name="{fname}"; filename="{filename}"\r\n'
               f'Content-Type: application/octet-stream\r\n\r\n'.encode() + data + b"\r\n")
    out.append(f"--{b}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={b}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mech onshape")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    u = sub.add_parser("upload")
    u.add_argument("step")
    u.add_argument("--name")
    u.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "upload":
        step = Path(a.step)
        name = a.name or step.stem
        if a.dry_run:
            print(f"would POST {API}/documents name={name!r}, then POST {API}/translations/d/<did>/w/<wid> "
                  f"with {step} ({step.stat().st_size} bytes, STEP), then poll the translation")
            return 0
        o = Onshape.from_env()
        did, wid = o.new_document(name)
        t = o.import_step(did, wid, step)
        t = o.wait(t["id"])
        print(f"{o.url}/documents/{did}/w/{wid}: import {t.get('requestState')}")
        return 0 if t.get("requestState") == "DONE" else 1
    s = Onshape.from_env().session()
    print(f"onshape: key works for {s.get('name') or s.get('email') or s.get('id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
