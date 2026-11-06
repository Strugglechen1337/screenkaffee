"""Policies: an optional policy.json next to the app and, on Windows, registry values that administrators can
set with group policy (ADMX template from `screenkaffee --policies`).

policy.json: {"locked": {"tone_mapping": "neutral"}, "disabled": ["settings_import", "settings_export"]}
Registry:    HKLM\\SOFTWARE\\screenkaffee\\Policies (or HKCU) with DWORD values such as NoVideo=1, plus the optional
             string LockedSettings = '{"tone_mapping": "neutral"}'. Machine policies win over user policies.
"""
import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape
from .paths import application_root

POLICY_KEY = r"SOFTWARE\screenkaffee\Policies"
# registry value -> (feature name used by the app, German label)
POLICY_FEATURES = {
    "NoVideo": ("video", "Videoaufnahme"),
    "NoAndroid": ("android", "Android-Aufnahme"),
    "NoWebpage": ("webpage", "Ganzseitenaufnahme von Webseiten"),
    "NoPrint": ("print", "Drucken"),
    "NoLanShare": ("lan_share", "Aufs Handy senden (WLAN)"),
    "NoSettingsImport": ("settings_import", "Einstellungen importieren"),
    "NoSettingsExport": ("settings_export", "Einstellungen exportieren"),
    "NoExternalEditor": ("external_editor", "Externe Bildeditoren"),
    "NoUpdate": ("update", "Updates aus GitHub"),
}


def registry_policy(key_path=None):
    """Policy read from the registry (HKCU first, then HKLM overrides); empty off Windows or without the key."""
    result = {"locked": {}, "disabled": []}
    if sys.platform != "win32":
        return result
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            key = winreg.OpenKey(hive, key_path or POLICY_KEY)
        except OSError:
            continue
        with key:
            for name, (feature, _) in POLICY_FEATURES.items():
                try:
                    value = winreg.QueryValueEx(key, name)[0]
                except OSError:
                    continue
                if value and feature not in result["disabled"]:
                    result["disabled"].append(feature)
            try:
                locked = json.loads(winreg.QueryValueEx(key, "LockedSettings")[0])
                if isinstance(locked, dict):
                    result["locked"].update(locked)
            except (OSError, ValueError):
                pass
    return result


def load_policy(path=None, registry=True, key_path=None):
    path = Path(path) if path else application_root() / "policy.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    locked = data.get("locked", {})
    disabled = data.get("disabled", [])
    policy = {"locked": dict(locked) if isinstance(locked, dict) else {},
              "disabled": [str(x) for x in disabled] if isinstance(disabled, list) else []}
    if registry:
        extra = registry_policy(key_path)
        policy["locked"].update(extra["locked"])
        policy["disabled"] += [feature for feature in extra["disabled"] if feature not in policy["disabled"]]
    return policy


def apply_policy(settings, policy):
    settings.update(policy["locked"])
    return settings


def admx_text():
    policies = "".join(
        f'    <policy name="{name}" class="Machine" displayName="$(string.{name})" explainText="$(string.{name}_Help)" '
        f'key="{POLICY_KEY}" valueName="{name}">\n'
        '      <parentCategory ref="screenkaffee"/>\n'
        '      <supportedOn ref="windows:SUPPORTED_Windows7"/>\n'
        '      <enabledValue><decimal value="1"/></enabledValue>\n'
        '      <disabledValue><decimal value="0"/></disabledValue>\n'
        '    </policy>\n' for name in POLICY_FEATURES)
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<policyDefinitions revision="1.0" schemaVersion="1.0" xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">\n'
            '  <policyNamespaces>\n    <target prefix="screenkaffee" namespace="screenkaffee.Policies"/>\n'
            '    <using prefix="windows" namespace="Microsoft.Policies.Windows"/>\n  </policyNamespaces>\n'
            '  <resources minRequiredRevision="1.0"/>\n'
            '  <categories>\n    <category name="screenkaffee" displayName="$(string.Category)"/>\n  </categories>\n'
            f'  <policies>\n{policies}  </policies>\n</policyDefinitions>\n')


def adml_text():
    strings = '    <string id="Category">screenkaffee</string>\n' + "".join(
        f'    <string id="{name}">{escape(label)} deaktivieren</string>\n'
        f'    <string id="{name}_Help">Aktiviert: {escape(label)} ist für alle Benutzer gesperrt.</string>\n'
        for name, (_, label) in POLICY_FEATURES.items())
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<policyDefinitionResources revision="1.0" schemaVersion="1.0" xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">\n'
            '  <displayName>screenkaffee</displayName>\n  <description>Richtlinien für screenkaffee</description>\n'
            f'  <resources>\n  <stringTable>\n{strings}  </stringTable>\n  </resources>\n</policyDefinitionResources>\n')


def reg_text():
    lines = ["Windows Registry Editor Version 5.00", "", f"[HKEY_LOCAL_MACHINE\\{POLICY_KEY}]"]
    lines += [f'"{name}"=dword:00000000' for name in POLICY_FEATURES]
    lines += ['"LockedSettings"="{}"', ""]
    return "\r\n".join(lines)


def write_policy_templates(folder):
    """Write ScreenkaffeePolicies.admx/.adml/.reg for administrators; returns the three paths."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    files = {"ScreenkaffeePolicies.admx": admx_text(), "ScreenkaffeePolicies.adml": adml_text(), "ScreenkaffeePolicies.reg": reg_text()}
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return [folder / name for name in files]
