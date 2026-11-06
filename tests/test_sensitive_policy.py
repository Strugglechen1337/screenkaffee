import pytest
from screenkaffee.ocr import is_sensitive, luhn_ok


@pytest.mark.parametrize("text", [
    "max@example.de", "DE89 3704 0044 0532 0130 00", "4111 1111 1111 1111", "+49 170 1234567",
    "0170 1234567", "192.168.1.10", "ghp_abcdefghijklmnopqrstuvwxyz1234", "AKIAIOSFODNN7EXAMPLE",
    "sk-abcdefghijklmnopqrstuv", "2001:0db8:85a3:0000:0000:8a2e:0370:7334", "fe80::1ff:fe23:4567",
    "https://admin:hunter2@example.com/x", "Passwort: geheim123", "password=abc123", "PIN: 4711",
    "-----BEGIN RSA PRIVATE KEY-----", "Authorization: Bearer abcdefghijklmnop1234", "123-45-6789"])
def test_sensitive_positive(text):
    assert is_sensitive(text)


@pytest.mark.parametrize("text", ["Hallo Welt", "Version 2.3.10", "4111 1111 1111 1112", "12:30 Uhr", "300 x 200", "1.2.3.456",
    "12:30:45", "Passwort ändern", "Bearer", "Pinguin: 12", "666-12-3456", "https://example.com/pfad"])
def test_sensitive_negative(text):
    assert not is_sensitive(text)


def test_luhn():
    assert luhn_ok("4111111111111111") and not luhn_ok("4111111111111112")


def test_feature_blocked_by_policy(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from screenkaffee.app import MainWindow
    from screenkaffee.workspace import Workspace
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a))
    window = MainWindow(Workspace(tmp_path))
    window.policy = {"locked": {}, "disabled": ["print", "android"]}
    assert window.blocked("print") and window.blocked("android")
    assert not window.blocked("video")
