import subprocess
from types import SimpleNamespace
from screenkaffee import integrations as I


def test_android_recorder_flow(tmp_path, monkeypatch):
    calls = []

    class Proc:
        def wait(self, timeout=None):
            return 0

    def fake_run(args, timeout=30, input=None):
        calls.append(args[3:] if len(args) > 3 else args)
        if args[1] == "devices":
            return SimpleNamespace(returncode=0, stdout=b"List of devices attached\nABC\tdevice\n", stderr=b"")
        if "pull" in args:
            open(args[-1], "wb").write(b"video")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(I, "tool", lambda name: "adb")
    monkeypatch.setattr(I, "run", fake_run)
    monkeypatch.setattr(I.subprocess, "Popen", lambda *a, **k: Proc())
    r = I.AndroidRecorder()
    r.start()
    assert r.active
    path = r.stop(tmp_path)
    assert path.read_bytes() == b"video" and not r.active
    assert r.stop(tmp_path) is None
