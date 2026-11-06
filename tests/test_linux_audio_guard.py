import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest


def test_segfault_probe_selects_pulse_only_on_linux(monkeypatch):
    from screenkaffee import linux_audio_guard as guard
    monkeypatch.setattr(guard.sys, 'platform', 'linux')
    monkeypatch.delenv('QT_AUDIO_BACKEND', raising=False)
    monkeypatch.setattr(guard.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=-signal.SIGSEGV))
    guard.prepare_audio()
    assert guard.os.environ['QT_AUDIO_BACKEND'] == 'pulseaudio'


@pytest.mark.parametrize('backend', ['', 'pipewire', 'pulseaudio', 'custom'])
def test_explicit_backend_is_never_overridden(monkeypatch, backend):
    from screenkaffee import linux_audio_guard as guard
    monkeypatch.setattr(guard.sys, 'platform', 'linux')
    monkeypatch.setenv('QT_AUDIO_BACKEND', backend)
    def forbidden(*args, **kwargs):
        raise AssertionError('explicit setting should skip probe')
    monkeypatch.setattr(guard.subprocess, 'run', forbidden)
    guard.prepare_audio()
    assert guard.os.environ['QT_AUDIO_BACKEND'] == backend


def test_healthy_probe_keeps_default(monkeypatch):
    from screenkaffee import linux_audio_guard as guard
    monkeypatch.setattr(guard.sys, 'platform', 'linux')
    monkeypatch.delenv('QT_AUDIO_BACKEND', raising=False)
    monkeypatch.setattr(guard.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0))
    guard.prepare_audio()
    assert 'QT_AUDIO_BACKEND' not in guard.os.environ


@pytest.mark.parametrize('result', [1, -signal.SIGABRT])
def test_other_failures_fail_closed(monkeypatch, result):
    from screenkaffee import linux_audio_guard as guard
    monkeypatch.setattr(guard.sys, 'platform', 'linux')
    monkeypatch.delenv('QT_AUDIO_BACKEND', raising=False)
    monkeypatch.setattr(guard.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=result))
    with pytest.raises(RuntimeError):
        guard.prepare_audio()
    assert 'QT_AUDIO_BACKEND' not in guard.os.environ


def test_timeout_fails_closed(monkeypatch):
    from screenkaffee import linux_audio_guard as guard
    monkeypatch.setattr(guard.sys, 'platform', 'linux')
    monkeypatch.delenv('QT_AUDIO_BACKEND', raising=False)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('probe', 10)
    monkeypatch.setattr(guard.subprocess, 'run', timeout)
    with pytest.raises(RuntimeError):
        guard.prepare_audio()


def test_other_platform_skips_probe(monkeypatch):
    from screenkaffee import linux_audio_guard as guard
    monkeypatch.setattr(guard.sys, 'platform', 'win32')
    monkeypatch.delenv('QT_AUDIO_BACKEND', raising=False)
    monkeypatch.setattr(guard.subprocess, 'run', lambda *a, **k: pytest.fail('unexpected probe'))
    guard.prepare_audio()
