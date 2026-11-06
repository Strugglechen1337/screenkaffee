"""Fortschrittsfenster für Update/Erweiterung und dauerhaft sichtbare Version."""
import pytest

from screenkaffee import __version__, progress, updater
from tests.test_updater import fake_github, make_zip, release_payload


def test_describe_shows_megabytes_speed_and_remaining_time():
    assert progress.describe(0, 0, 0) == "0 MB geladen"
    assert progress.describe(45e6, 110e6, 0.5) == "45 von 110 MB"            # zu früh für eine Schätzung
    assert progress.describe(40e6, 110e6, 10) == "40 von 110 MB · 4,0 MB/s · noch ca. 18 s"
    assert progress.describe(110e6, 110e6, 20) == "110 von 110 MB · 5,5 MB/s"
    assert progress.duration(75) == "1 min 15 s" and progress.duration(900) == "15 min"


def test_overall_percent_spans_both_steps():
    phases = [("download", 0.85), ("extract", 0.15)]
    assert progress.overall_percent("download", 0, 100, phases) == 0
    assert progress.overall_percent("download", 50, 100, phases) == 42
    assert progress.overall_percent("extract", 0, 0, phases) == 85
    assert progress.overall_percent("extract", 10, 10, phases) == 100


def test_progress_state_steps_and_cancel():
    clock = iter([0.0, 0.0, 10.0, 10.0, 10.0]).__next__
    state = progress.Progress([("download", 0.85, "Herunterladen und prüfen"), ("extract", 0.15, "Entpacken")], clock)
    assert state.step_text() == "Schritt 1 von 2: Herunterladen und prüfen"
    state.report(40e6, 110e6)
    assert "40 von 110 MB" in state.detail_text()
    state.stage("extract")
    state.report(5, 10)
    assert state.step_text() == "Schritt 2 von 2: Entpacken" and state.detail_text() == "5 von 10 Dateien"
    assert state.percent() == 92
    state.state["cancel"] = True
    with pytest.raises(progress.Cancelled):
        state.report(6, 10)


def test_prepare_reports_both_steps_and_cleans_up_on_cancel(tmp_path):
    data = make_zip({"screenkaffee/screenkaffee.exe": b"neu", **{f"screenkaffee/_internal/{i}.dll": b"x" for i in range(30)}})
    opener = fake_github(release_payload(data))
    release = updater.latest_release("o/r", opener)
    stages, reports = [], []
    updater.prepare(release, "windows-x64", tmp_path / "ok", opener=opener,
                    progress=lambda done, total: reports.append((done, total)), stage=stages.append)
    assert stages == ["download", "extract"]
    assert (len(data), len(data)) in reports and reports[-1] == (31, 31)

    def cancel(done, total):
        raise progress.Cancelled()
    with pytest.raises(progress.Cancelled):
        updater.prepare(release, "windows-x64", tmp_path / "abbruch", opener=opener, progress=cancel)
    assert not (tmp_path / "abbruch" / "_update").exists()


def test_progress_window_hides_cancels_and_finishes(qt_app):
    state = progress.Progress([("download", 0.85, "Herunterladen und prüfen"), ("extract", 0.15, "Entpacken")])
    window = progress.ProgressWindow("Update", "screenkaffee 9.9.9 wird geladen", state)
    window.show()
    state.report(50, 100)
    window.refresh()
    assert window.bar.value() == 42 and window.step.text().startswith("Schritt 1 von 2")
    window.close()                                   # während des Downloads nur ausblenden
    assert window.isHidden() and window.finished_text is None
    assert window.status_line() == "screenkaffee 9.9.9 wird geladen – 42 %"
    window.cancel_button.click()
    assert state.state["cancel"] and not window.cancel_button.isEnabled()
    window.finish("Fertig.")
    assert window.bar.value() == 100 and window.close_button.isVisibleTo(window)
    assert not window.cancel_button.isVisibleTo(window)
    window.close()


def test_version_is_always_visible(qt_app, tmp_path, monkeypatch):
    from tests.test_pin_setup_crash import window_for
    window = window_for(tmp_path, monkeypatch)
    window.show()
    assert __version__ in window.windowTitle()
    assert window.version_button.text() == f"Version {__version__}" and window.version_button.isVisible()
    window.statusBar().showMessage("Irgendeine Meldung")
    assert window.version_button.isVisible()        # feste Anzeige bleibt neben Meldungen stehen
    window.show_version("v99.0.0")
    assert window.version_button.text() == f"Version {__version__} · Update 99.0.0 verfügbar"
    window.show_version()
    assert window.version_button.text() == f"Version {__version__}"
    window.close()


def test_update_download_shows_progress_window(qt_app, tmp_path, monkeypatch):
    from tests.test_pin_setup_crash import window_for
    window = window_for(tmp_path, monkeypatch)
    monkeypatch.setattr(window, "task", lambda operation, done, message: None)    # kein echter Download
    release = updater.Release("v99.0.0", (99, 0, 0), "", "", {})
    window.download_update(release, "windows-x64")
    assert window.update_window.isVisible() and "99.0.0" in window.update_window.heading.text()
    window.update_downloaded(progress.Cancelled(), release, "windows-x64")
    assert "abgebrochen" in window.update_window.finished_text and not window.update_busy
    window.update_window.close()
    window.close()
