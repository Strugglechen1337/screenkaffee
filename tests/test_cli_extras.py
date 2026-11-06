import json
import sys
import uuid
import xml.etree.ElementTree as ET
import pytest
from screenkaffee import cli, policy


class FakeRecorder:
    created = []

    def __init__(self, error_after=None):
        self.started = None
        self.polls = 0
        self.error_after = error_after
        self.stopped = False
        FakeRecorder.created.append(self)

    def start(self, folder, **options):
        self.started = (folder, options)
        self.output = folder / "aufnahme.mp4"
        self.output.write_bytes(b"video")

    def error_if_stopped(self):
        self.polls += 1
        return "ffmpeg beendet" if self.error_after and self.polls >= self.error_after else None

    def stop(self):
        self.stopped = True
        return self.output


@pytest.fixture(autouse=True)
def reset():
    FakeRecorder.created = []


def test_video_region_records_until_the_delay_and_copies_the_result(tmp_path):
    target = tmp_path / "ordner" / "film.mp4"
    code = cli.run_capture(["--videoregion", "--rect", "10;20;640;480", "--delay", "0.02", "--filepath", str(target)],
                           recorder_factory=FakeRecorder, sleep=lambda seconds: None)
    recorder = FakeRecorder.created[0]
    assert code == 0 and recorder.stopped and target.read_bytes() == b"video"
    assert recorder.started[1]["region"] == [10, 20, 640, 480] and recorder.polls == 3        # 1.2 s in 0.5 s steps


def test_video_region_whole_desktop_and_interrupt_still_save(tmp_path):
    def interrupted(seconds):
        raise KeyboardInterrupt
    target = tmp_path / "voll.mp4"
    cli.run_capture(["-videoregion", "-rect", "-1;-1;-1;-1", "-filepath", str(target)], recorder_factory=FakeRecorder, sleep=interrupted)
    assert FakeRecorder.created[0].started[1]["region"] is None and target.exists()


def test_video_region_validates_input_and_reports_recorder_errors(tmp_path):
    with pytest.raises(SystemExit):
        cli.run_capture(["--videoregion"], recorder_factory=FakeRecorder)
    with pytest.raises(ValueError):
        cli.run_capture(["--videoregion", "--rect", "0;0;100;100", "--delay", "0"], recorder_factory=FakeRecorder)
    with pytest.raises(RuntimeError, match="ffmpeg beendet"):
        cli.run_capture(["--videoregion", "--rect", "0;0;100;100", "--filepath", str(tmp_path / "x.mp4")],
                        recorder_factory=lambda: FakeRecorder(error_after=2), sleep=lambda s: None)
    with pytest.raises(SystemExit):                          # screenshot and video together are ambiguous
        cli.run_capture(["--screenshotregion", "--videoregion", "--rect", "0;0;10;10"])


def test_policy_templates_are_valid_and_consistent(tmp_path):
    admx, adml, reg = policy.write_policy_templates(tmp_path)
    ns = {"p": "http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions"}
    definitions = ET.parse(admx).getroot()
    resources = ET.parse(adml).getroot()
    names = [p.get("name") for p in definitions.findall("p:policies/p:policy", ns)]
    assert names == list(policy.POLICY_FEATURES) and all(p.get("key") == policy.POLICY_KEY for p in definitions.findall("p:policies/p:policy", ns))
    strings = {s.get("id") for s in resources.findall("p:resources/p:stringTable/p:string", ns)}
    for name in names:
        assert {name, name + "_Help"} <= strings
    assert "Category" in strings
    text = reg.read_text(encoding="utf-8")
    assert text.startswith("Windows Registry Editor Version 5.00") and '"NoVideo"=dword:00000000' in text


def test_main_dumps_policy_templates(tmp_path, monkeypatch, capsys):
    from screenkaffee.__main__ import main
    monkeypatch.setattr(sys, "argv", ["screenkaffee", "--policies", str(tmp_path / "vorlagen")])
    assert main() == 0
    assert sorted(p.name for p in (tmp_path / "vorlagen").iterdir()) == ["ScreenkaffeePolicies.adml", "ScreenkaffeePolicies.admx", "ScreenkaffeePolicies.reg"]
    assert "ScreenkaffeePolicies.admx" in capsys.readouterr().out


@pytest.mark.skipif(sys.platform != "win32", reason="Registry nur unter Windows")
def test_registry_policies_are_merged_with_policy_json(tmp_path):
    import winreg
    parent = r"Software\screenkaffee-test-" + uuid.uuid4().hex[:8]
    key_path = parent + r"\Policies"
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "NoVideo", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoPrint", 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(key, "LockedSettings", 0, winreg.REG_SZ, json.dumps({"tone_mapping": "highlights"}))
        file = tmp_path / "policy.json"
        file.write_text(json.dumps({"locked": {"capture_cursor": True}, "disabled": ["android", "video"]}), encoding="utf-8")
        merged = policy.load_policy(file, key_path=key_path)
        assert merged["disabled"] == ["android", "video"] and merged["locked"] == {"capture_cursor": True, "tone_mapping": "highlights"}
        only_registry = policy.load_policy(tmp_path / "keine.json", key_path=key_path)
        assert only_registry["disabled"] == ["video"]
        assert policy.load_policy(file, registry=False, key_path=key_path)["locked"] == {"capture_cursor": True}
        assert policy.registry_policy(parent + r"\gibt-es-nicht") == {"locked": {}, "disabled": []}
    finally:
        for path in (key_path, parent):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except FileNotFoundError:
                pass


def test_newinstance_and_multiinstances_control_channel(qt_app):
    from PySide6.QtNetwork import QLocalServer
    name = "screenkaffee-test-ipc-" + uuid.uuid4().hex[:8]
    received = []
    server = QLocalServer()
    QLocalServer.removeServer(name)
    assert server.listen(name)
    def on_new():
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            conn.waitForReadyRead(200)
            cmd = bytes(conn.readAll()).decode().strip()
            received.append(cmd)
            conn.disconnectFromServer()
            if cmd == "quit":
                server.close()
                QLocalServer.removeServer(name)
    server.newConnection.connect(on_new)
    try:
        assert cli.ask_to_quit(name, wait_ms=1500) is True
        assert received == ["quit"]
    finally:
        server.close()
    assert cli.ask_to_quit(name + "-missing", wait_ms=200) is False
