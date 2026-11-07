import zipfile
from pathlib import Path
from unittest.mock import patch
from scripts import package_setup


def test_setup_parameters_and_assets(tmp_path):
    script = (package_setup.ROOT / "installer/screenkaffee.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in script
    assert "DefaultDirName={localappdata}\\Programs\\screenkaffee" in script
    assert 'Name: "german"' in script and 'Name: "english"' in script
    assert "MB_DEFBUTTON2" in script and "not UninstallSilent" in script
    assert package_setup.ASSETS["x64"] == "screenkaffee-Setup-Windows-x64.exe"
    assert package_setup.ASSETS["arm64"] == "screenkaffee-Setup-Windows-arm64.exe"
    for variant, marker, arch in [("x64", "_internal/a", "x64compatible"), ("arm64", "runtime/pythonw.exe", "arm64")]:
        archive = tmp_path / f"{variant}.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("screenkaffee/screenkaffee.exe", b"exe")
            z.writestr("screenkaffee/" + marker, b"data")
        with patch.object(package_setup.subprocess, "run") as run:
            result = package_setup.build(variant, archive, tmp_path, iscc=Path("ISCC.exe"))
        command = run.call_args.args[0]
        assert f"/DArchitecture={arch}" in command
        assert f"/DVariant={variant}" in command
        assert any(x.startswith("/DAppVersion=") for x in command)
        assert result.name == package_setup.ASSETS[variant]
