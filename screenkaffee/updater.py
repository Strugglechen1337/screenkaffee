"""Selbst-Update aus den GitHub-Releases für alle drei Pakete (Windows x64, Windows ARM64, Linux x86_64).

Ablauf: Die laufende App lädt das passende Archiv nach ``<App>/_update``, prüft die SHA256-Summe und entpackt es.
Dann startet sie das *neue* Programm mit ``--apply-update``, beendet sich, und das neue Programm tauscht die
Dateien aus (alte Version nach ``_update/old``, Datenordner bleibt unberührt) und startet screenkaffee neu.
Beim ersten Start danach zeigt die App, was neu ist, und räumt ``_update`` auf.
"""
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile

from .paths import application_root
from .i18n import N_, tr

REPOSITORY = "Strugglechen1337/screenkaffee"
ASSETS = {"windows-x64": "screenkaffee-Windows-x64.zip", "windows-arm64": "screenkaffee-Windows-arm64.zip",
          "linux-x86_64": "screenkaffee-Linux-x86_64.tar.gz"}
LAUNCHERS = {"windows-x64": "screenkaffee.exe", "windows-arm64": "screenkaffee.exe", "linux-x86_64": "screenkaffee"}
STAGING = "_update"
PREVIOUS = "_previous"              # vorige Version zum Wiederherstellen (eine Generation)
PREVIOUS_INFO = "_previous.json"
KEEP = {"data", STAGING, PREVIOUS, PREVIOUS_INFO}     # wird beim Austausch nie angefasst
MODES = [("off", N_("Aus – nur über „Mehr → Nach Updates suchen …“")),
         ("ask", N_("Beim Start prüfen und nachfragen")),
         ("auto", N_("Beim Start automatisch herunterladen und installieren"))]   # Anzeige: tr(label)


class UpdateError(RuntimeError):
    pass


@dataclass
class Release:
    tag: str
    version: tuple
    notes: str = ""
    url: str = ""
    assets: dict = field(default_factory=dict)      # Name -> {"url", "size", "digest"}


def parse_version(text):
    """'v0.6.1' -> (0, 6, 1); unbrauchbare Teile zählen als 0."""
    parts = []
    for piece in str(text).strip().lstrip("vV").split("-")[0].split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def current_version():
    from . import __version__
    return parse_version(__version__)


def installed_variant(root=None):
    """Welches Paket hier läuft; None bei einer Quellcode-Version (dort per git aktualisieren)."""
    root = Path(root or application_root())
    if sys.platform == "win32":
        if (root / "runtime" / "pythonw.exe").is_file() and (root / "screenkaffee.exe").is_file():
            return "windows-arm64"
        if (root / "_internal").is_dir() and (root / "screenkaffee.exe").is_file():
            return "windows-x64"
    elif sys.platform.startswith("linux"):
        if (root / "runtime" / "bin").is_dir() and (root / "screenkaffee").is_file():
            return "linux-x86_64"
    return None


# --- GitHub-Zugang ---------------------------------------------------------------------------------------------

def _request(url, accept="application/vnd.github+json"):
    """Öffentliches Repository: keine Anmeldung, kein Token."""
    return urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "screenkaffee-updater",
                                                "X-GitHub-Api-Version": "2022-11-28"})


def _open(request, opener=None, timeout=30):
    try:
        return (opener or urllib.request.urlopen)(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise UpdateError(tr("GitHub hat zu viele Anfragen gezählt. Bitte später noch einmal versuchen.")) from exc
        if exc.code == 404:
            raise UpdateError(tr("Kein passendes Release auf GitHub gefunden.")) from exc
        raise UpdateError(tr("GitHub antwortet mit Fehler {code}.", code=exc.code)) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(tr("GitHub ist nicht erreichbar: {reason}", reason=getattr(exc, "reason", exc))) from exc


def latest_release(repo=REPOSITORY, opener=None):
    return _release(f"https://api.github.com/repos/{repo}/releases/latest", opener)


def release_by_tag(tag, repo=REPOSITORY, opener=None):
    """Ein bestimmtes Release, z. B. für Erweiterungen, die zur laufenden Version passen müssen."""
    return _release(f"https://api.github.com/repos/{repo}/releases/tags/{tag}", opener)


def _release(url, opener):
    with _open(_request(url), opener) as response:
        data = json.loads(response.read().decode("utf-8"))
    assets = {asset["name"]: {"url": asset["url"], "size": asset.get("size", 0), "digest": asset.get("digest") or ""}
              for asset in data.get("assets", [])}
    return Release(data.get("tag_name", ""), parse_version(data.get("tag_name", "")), data.get("body") or "",
                   data.get("html_url", ""), assets)


def expected_sha256(release, name, opener=None):
    """SHA256 aus dem Asset-Digest von GitHub oder aus der mitveröffentlichten verification.json."""
    digest = release.assets.get(name, {}).get("digest", "")
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1].lower()
    if "verification.json" in release.assets:
        request = _request(release.assets["verification.json"]["url"], "application/octet-stream")
        with _open(request, opener) as response:
            data = json.loads(response.read().decode("utf-8"))
        for artifact in data.get("artifacts", []):
            if artifact.get("name") == name and artifact.get("sha256"):
                return artifact["sha256"].lower()
    raise UpdateError(tr("Für das Update fehlt eine Prüfsumme. Aus Sicherheitsgründen wird es nicht installiert."))


def download(release, name, target, opener=None, progress=None):
    asset = release.assets.get(name)
    if not asset:
        raise UpdateError(tr("Das Release {tag} enthält kein Paket „{name}“.", tag=release.tag, name=name))
    expected = expected_sha256(release, name, opener)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest, done = hashlib.sha256(), 0
    with _open(_request(asset["url"], "application/octet-stream"), opener, timeout=60) as response, \
            open(target, "wb") as output:
        while chunk := response.read(1 << 20):
            output.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, asset.get("size") or 0)
    if digest.hexdigest() != expected:
        target.unlink(missing_ok=True)
        raise UpdateError(tr("Die Prüfsumme des heruntergeladenen Pakets stimmt nicht. Das Update wurde verworfen."))
    return target


def extract(archive, destination, progress=None):
    """Entpackt nur Einträge innerhalb von destination und liefert den Paketordner 'screenkaffee'.

    progress(fertige Einträge, alle Einträge) wird zwischendurch aufgerufen (höchstens etwa 200-mal)."""
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if str(archive).endswith(".zip"):
        with zipfile.ZipFile(archive) as source:
            members = source.infolist()
            for member in members:
                if not (destination / member.filename).resolve().is_relative_to(destination):
                    raise UpdateError(tr("Das Update-Archiv enthält ungültige Pfade."))
            step = max(1, len(members) // 200)
            for index, member in enumerate(members, 1):
                source.extract(member, destination)
                if progress and (index % step == 0 or index == len(members)):
                    progress(index, len(members))
    else:
        with tarfile.open(archive) as source:
            members = source.getmembers()
            step = max(1, len(members) // 200)
            try:
                for index, member in enumerate(members, 1):
                    source.extract(member, destination, filter="data")
                    if progress and (index % step == 0 or index == len(members)):
                        progress(index, len(members))
            except tarfile.FilterError as exc:
                raise UpdateError(tr("Das Update-Archiv enthält ungültige Pfade.")) from exc
    package = destination / "screenkaffee"
    if not package.is_dir():
        raise UpdateError(tr("Das Update-Archiv hat nicht den erwarteten Aufbau."))
    return package


def prepare(release, variant, root=None, opener=None, progress=None, stage=None):
    """Lädt, prüft und entpackt das Update; liefert den Ordner der neuen Version.

    stage("download") / stage("extract") meldet den Schritt, progress(fertig, gesamt) den Fortschritt darin.
    Bricht progress per Ausnahme ab, wird _update wieder entfernt."""
    root = Path(root or application_root())
    staging = root / STAGING
    shutil.rmtree(staging, ignore_errors=True)
    name = ASSETS[variant]
    try:
        if stage:
            stage("download")
        archive = download(release, name, staging / "download" / name, opener, progress)
        if stage:
            stage("extract")
        package = extract(archive, staging / "new", progress)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    archive.unlink(missing_ok=True)
    if not (package / LAUNCHERS[variant]).is_file():
        raise UpdateError(tr("Im Update fehlt das Startprogramm."))
    (staging / "release.json").write_text(json.dumps({"tag": release.tag, "notes": release.notes, "url": release.url},
                                                     ensure_ascii=False), encoding="utf-8")
    from . import __version__
    (staging / "previous.json").write_text(json.dumps({"version": __version__}), encoding="utf-8")
    return package


# --- Austausch (läuft im neuen Programm) -------------------------------------------------------------------------

def clean_environment():
    env = dict(os.environ)
    for key in ("SCREENKAFFEE_APPDIR", "SCREENKAFFEE_DATA", "PYTHONPATH", "_MEIPASS2", "_PYI_APPLICATION_HOME_DIR",
                "_PYI_ARCHIVE_FILE", "_PYI_PARENT_PROCESS_LEVEL"):
        env.pop(key, None)
    return env


def _detached():
    if sys.platform == "win32":
        return {"creationflags": 0x00000008 | 0x00000200}      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return {"start_new_session": True}


def helper_command(package, variant, root, pid, data_dir=None):
    command = [str(Path(package) / LAUNCHERS[variant]), "--apply-update", str(root), "--wait-pid", str(pid)]
    if data_dir:
        command += ["--relaunch-data", str(data_dir)]
    return command


def launch_helper(command):
    subprocess.Popen(command, cwd=str(Path(command[0]).parent), env=clean_environment(), close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_detached())


def wait_for_exit(pid, timeout=120):
    deadline = time.monotonic() + timeout
    if sys.platform == "win32":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x00100000, False, int(pid))         # SYNCHRONIZE
        if handle:
            try:
                if kernel.WaitForSingleObject(handle, int(timeout * 1000)) != 0:
                    raise UpdateError(tr("screenkaffee hat sich nicht rechtzeitig beendet."))
            finally:
                kernel.CloseHandle(handle)
        return
    while time.monotonic() < deadline:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        time.sleep(0.2)
    raise UpdateError(tr("screenkaffee hat sich nicht rechtzeitig beendet."))


def _retry(action, attempts=40):
    for attempt in range(attempts):
        try:
            return action()
        except PermissionError:          # Virenscanner oder ein letzter Prozess hält die Datei noch kurz fest
            if attempt == attempts - 1:
                raise
            time.sleep(0.5)


def _remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def apply(package, root, pid=None, wait=wait_for_exit):
    """Ersetzt die Programmdateien in root durch package; bei Fehlern wird die alte Version zurückgelegt."""
    package, root = Path(package), Path(root)
    if pid:
        wait(pid)
    backup = root / STAGING / "old"
    shutil.rmtree(backup, ignore_errors=True)
    backup.mkdir(parents=True)
    moved, copied = [], []
    try:
        for item in sorted(package.iterdir()):
            if item.name in KEEP:
                continue
            current = root / item.name
            if current.exists() or current.is_symlink():
                _retry(lambda: current.rename(backup / item.name))
                moved.append(item.name)
            copied.append(item.name)
            if item.is_dir() and not item.is_symlink():
                shutil.copytree(item, current, symlinks=True)
            else:
                shutil.copy2(item, current, follow_symlinks=False)
    except Exception:
        for name in copied:
            try:
                _remove(root / name)
            except OSError:
                pass
        for name in moved:
            try:
                (backup / name).rename(root / name)
            except OSError:
                pass
        raise
    try:                                 # die Dateien sind getauscht; fehlende Notizen machen das Update nicht ungültig
        info = json.loads((root / STAGING / "release.json").read_text(encoding="utf-8"))
    except (ValueError, OSError):
        info = {}
    (root / STAGING / "done.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")


def relaunch(root, variant, data_dir=None):
    command = [str(Path(root) / LAUNCHERS[variant])]
    if data_dir:
        command += ["--data-dir", str(data_dir)]
    subprocess.Popen(command, cwd=str(root), env=clean_environment(), close_fds=True, **_detached())


def run_helper(argv):
    """`screenkaffee --apply-update <App-Ordner> --wait-pid N [--relaunch-data Ordner]` (vom neuen Paket aus)."""
    import argparse
    parser = argparse.ArgumentParser(prog="screenkaffee --apply-update")
    parser.add_argument("--apply-update", dest="root", type=Path, required=True)
    parser.add_argument("--wait-pid", type=int)
    parser.add_argument("--relaunch-data", type=Path)
    args = parser.parse_args(argv)
    package = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None
    package = package or Path(__file__).resolve().parent.parent.parent       # ARM64/Linux: <Paket>/source/screenkaffee
    variant = installed_variant(package)
    if variant is None:
        print(tr("Fehler: --apply-update läuft nur aus einem entpackten Update-Paket."), file=sys.stderr)
        return 1
    root = args.root.resolve()
    try:
        apply(package, root, args.wait_pid)
    except Exception as exc:
        (root / STAGING).mkdir(exist_ok=True)
        (root / STAGING / "update-error.log").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
    relaunch(root, variant, args.relaunch_data)
    return 0


def _keep_previous(root, staging):
    """Legt die gerade ersetzte Version als _previous ab (ersetzt eine ältere). False, wenn noch etwas gesperrt ist."""
    old = staging / "old"
    if not old.is_dir() or not any(old.iterdir()):
        return False
    target = root / PREVIOUS
    for _ in range(10):                  # ein gerade endender Helfer (Wiederherstellen) kann noch Dateien halten
        shutil.rmtree(target, ignore_errors=True)
        if not target.exists():
            break
        time.sleep(0.5)
    else:
        return False
    try:
        old.rename(target)
    except OSError:
        return False
    try:
        version = json.loads((staging / "previous.json").read_text(encoding="utf-8")).get("version", "")
    except (ValueError, OSError):
        version = ""
    (root / PREVIOUS_INFO).write_text(json.dumps({"version": version}), encoding="utf-8")
    return True


def finish_previous(root=None, keep_previous=True):
    """Nach dem Neustart: liefert ({tag, notes, url} | {'error': text} | None) und räumt _update auf.

    Mit keep_previous bleibt die ersetzte Version in _previous, bis zum nächsten Update."""
    root = Path(root or application_root())
    staging = root / STAGING
    if not staging.is_dir():
        return None
    result = None
    if (staging / "done.json").is_file():
        try:
            result = json.loads((staging / "done.json").read_text(encoding="utf-8"))
        except (ValueError, OSError):
            result = {}
        if keep_previous:
            _keep_previous(root, staging)
    elif (staging / "update-error.log").is_file():
        result = {"error": (staging / "update-error.log").read_text(encoding="utf-8", errors="replace").strip()}
    shutil.rmtree(staging, ignore_errors=True)          # ein gerade endender Update-Helfer darf Reste behalten
    return result


def previous_version(root=None, variant=None):
    """{'version', 'package'} der aufbewahrten Version oder None."""
    root = Path(root or application_root())
    variant = variant or installed_variant(root)
    package = root / PREVIOUS
    if variant is None or not (package / LAUNCHERS[variant]).is_file() or installed_variant(package) != variant:
        return None
    try:
        version = json.loads((root / PREVIOUS_INFO).read_text(encoding="utf-8")).get("version", "")
    except (ValueError, OSError):
        version = ""
    if not version:                      # 0.7.0 schrieb die Version noch nicht mit; jedes Paket enthält source/
        try:
            text = (package / "source" / "screenkaffee" / "__init__.py").read_text(encoding="utf-8")
            version = text.split("__version__", 1)[1].split('"')[1]
        except (OSError, IndexError):
            version = ""
    return {"version": version or "unbekannt", "package": package}


def prepare_restore(root=None):
    """Bereitet das Zurückspielen von _previous vor; liefert den Paketordner für helper_command."""
    root = Path(root or application_root())
    info = previous_version(root)
    if info is None:
        raise UpdateError(tr("Es ist keine vorige Version vorhanden. Sie wird beim nächsten Update aufbewahrt."))
    staging = root / STAGING
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    from . import __version__
    notes = tr("Die vorige Version {version} wurde wiederhergestellt (vorher installiert: {installed}). "
               "Automatische Updates sind ausgeschaltet, damit sie nicht gleich wieder ersetzt wird.",
               version=info["version"], installed=__version__)
    (staging / "release.json").write_text(json.dumps({"tag": "v" + info["version"], "notes": notes}, ensure_ascii=False),
                                          encoding="utf-8")
    (staging / "previous.json").write_text(json.dumps({"version": __version__}), encoding="utf-8")
    return info["package"]


# --- Altes GitHub-Token (bis 0.10.0 für das private Repository) ----------------------------------------------

def forget_token(data_dir):
    """Ein früher gespeichertes Token löschen; das Repository ist öffentlich, ein altes Token würde nur stören."""
    try:
        (Path(data_dir) / "github-token.bin").unlink(missing_ok=True)
    except OSError:
        pass
