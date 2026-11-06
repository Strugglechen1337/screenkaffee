"""Englische Wörterbücher: deutscher Originaltext -> englischer Text.

Jede Datei gehört zu einem Bereich der App, damit mehrere Leute gleichzeitig daran
arbeiten können. Statisch importiert, damit der Paketbau sie sicher mitnimmt.
"""
from . import app, editor, general

CATALOG = {}
for _part in (general, app, editor):
    CATALOG.update(_part.TEXTS)
