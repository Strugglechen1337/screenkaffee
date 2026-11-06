"""Isolate Qt's Linux audio initialization before opening the media editor.

Qt 6.12 can dereference a null PipeWire context. Do not override an explicitly
selected backend; a successful probe retains Qt's normal backend selection.
"""
import logging
import os
import signal
import subprocess
import sys
from .i18n import tr

_PROBE = '''
import resource
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
from PySide6.QtMultimedia import QAudioOutput
output = QAudioOutput()
'''


def prepare_audio():
    if sys.platform != 'linux' or 'QT_AUDIO_BACKEND' in os.environ:
        return
    try:
        result = subprocess.run([sys.executable, '-c', _PROBE],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(tr('Qt-Audio-Prüfung konnte nicht abgeschlossen werden')) from exc
    if result.returncode == -signal.SIGSEGV:
        logging.warning('Qt-Audio-Prüfung abgestürzt; versuche PulseAudio statt Standardbackend')
        os.environ['QT_AUDIO_BACKEND'] = 'pulseaudio'
    elif result.returncode:
        raise RuntimeError(tr("Qt-Audio-Prüfung fehlgeschlagen ({code})", code=result.returncode))
