---
name: debug
description: Systematisches Debugging im EEG-Optimizer. Erst die Ursache reproduzieren und analysieren, dann fixen und einen Regressionstest ergänzen. Verwenden bei roten Tests, Fehlern in init.sh, Exceptions in der App oder falschen Kennzahlen.
---

# debug

Nie raten und nie „auf Verdacht“ ändern. Die Reihenfolge ist fest:

1. **Reproduzieren:** den kleinsten Befehl finden, der den Fehler zeigt, zum Beispiel `.venv/bin/pytest -q tests/unit/test_x.py::test_y -x`, ein Python-Snippet oder Playwright-Schritte. Exakte Fehlermeldung und Stacktrace notieren. Lässt sich der Fehler nicht reproduzieren, wird nichts geändert. Stattdessen in progress.md dokumentieren, was versucht wurde.
2. **Eingrenzen:** Was hat sich geändert (`git log -p -5 -- <pfad>`, `git diff`)? Welche Annahme ist verletzt? Zwischenwerte ausgeben und Einheiten (kW/kWh), Zeitzone, DST-Tage und NaN prüfen.
3. **Ursache benennen:** Ein Satz der Form „X passiert, weil Y“. Kann man den nicht schreiben, ist Schritt 2 noch nicht fertig.
4. **Regressionstest zuerst:** einen Test schreiben, der den Fehler zeigt und **jetzt rot** ist. Rot-Lauf ausführen.
5. **Fixen:** minimal, an der Ursache und nicht am Symptom. Fehler nicht verschlucken (kein `except: pass`, kein stiller Default).
6. **Verifizieren:** Regressionstest grün, `scripts/check.sh` grün.
7. **Notieren:** Ursache und Fix kurz in progress.md eintragen. Ist derselbe Fehlertyp schon früher in progress.md oder `docs/progress_archive.md` vorgekommen (mit dem Grep-Tool in beiden suchen), wird zusätzlich der Skill `lessons-learned` angewendet.
