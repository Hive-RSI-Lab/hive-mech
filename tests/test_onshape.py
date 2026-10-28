"""The Onshape handoff without a network or a key: what it would send."""
import base64
import io
import json

import pytest

from mech import onshape


class Fake:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def __call__(self, req):
        self.sent.append(req)
        return io.BytesIO(json.dumps(self.replies.pop(0)).encode())


def test_upload_creates_document_and_imports(tmp_path, monkeypatch):
    step = tmp_path / "enc.step"
    step.write_bytes(b"ISO-10303-21;")
    f = Fake([{"id": "D1", "defaultWorkspace": {"id": "W1"}}, {"id": "T1"}, {"requestState": "DONE"}])
    o = onshape.Onshape("ak", "sk", opener=f)
    did, wid = o.new_document("Lite")
    t = o.wait(o.import_step(did, wid, step)["id"])
    assert t["requestState"] == "DONE"
    urls = [r.full_url for r in f.sent]
    assert urls == ["https://cad.onshape.com/api/v10/documents",
                    "https://cad.onshape.com/api/v10/translations/d/D1/w/W1",
                    "https://cad.onshape.com/api/v10/translations/T1"]
    auth = f.sent[0].get_header("Authorization")
    assert base64.b64decode(auth.split()[1]) == b"ak:sk"
    body = f.sent[1].data
    assert b'name="formatName"\r\n\r\nSTEP' in body and b"ISO-10303-21;" in body


def test_no_key_says_whose_it_is(monkeypatch):
    monkeypatch.delenv("ONSHAPE_ACCESS_KEY", raising=False)
    monkeypatch.delenv("ONSHAPE_SECRET_KEY", raising=False)
    with pytest.raises(SystemExit, match="the person's key pair"):
        onshape.Onshape.from_env()


def test_dry_run_needs_no_key(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ONSHAPE_ACCESS_KEY", raising=False)
    step = tmp_path / "enc.step"
    step.write_bytes(b"x")
    assert onshape.main(["upload", str(step), "--dry-run"]) == 0
    assert "would POST" in capsys.readouterr().out
