# -*- coding: utf-8 -*-
"""install.py - one-step setup for Quota Governor for Claude Code.

Copies the status line hook to ~/.claude-quota-meter/statusline.py (a stable place that survives skill updates) and
wires it in ~/.claude/settings.json. Shows the change first; writes only with --apply.

    python install.py            # show what would change, write nothing
    python install.py --apply    # copy the hook and wire the status line (backs up settings.json first)

If a status line already exists it is never replaced: the script prints the one line to add to your own status line
script instead. Standard library only.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="copy the hook and edit settings.json")
    ap.add_argument("--home", default=str(Path.home()), help="home folder (for testing)")
    a = ap.parse_args(argv)

    casa = Path(a.home)
    destino = casa / ".claude-quota-meter" / "statusline.py"
    settings = casa / ".claude" / "settings.json"
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe":
        py = py.with_name("python.exe")
    comando = '"%s" "%s"' % (py.as_posix(), destino.as_posix())

    cfg = {}
    if settings.exists():
        try:
            cfg = json.loads(settings.read_text(encoding="utf-8") or "{}")
        except ValueError:
            print("settings.json is not valid JSON: %s. Fix it first; nothing was changed." % settings)
            return 2
    existe = "statusLine" in cfg

    print("1. Copy the hook to   %s" % destino)
    if existe:
        print("2. A status line already exists in %s. It will NOT be replaced." % settings)
        print("   Add this to your own status line script, passing it the same stdin JSON:")
        print("       %s --quiet" % comando)
    else:
        print("2. Add to %s:" % settings)
        print('   "statusLine": {"type": "command", "command": %s, "refreshInterval": 60}' % json.dumps(comando))

    if not a.apply:
        print("\nNothing written. Run again with --apply after the user agrees.")
        return 0

    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(AQUI / "statusline.py", destino)
    print("\nhook copied: %s" % destino)
    if not existe:
        settings.parent.mkdir(parents=True, exist_ok=True)
        if settings.exists():
            copia = settings.with_name("settings.json.bak-quota-governor")
            shutil.copy2(settings, copia)
            print("backup:      %s" % copia)
        cfg["statusLine"] = {"type": "command", "command": comando, "refreshInterval": 60}
        settings.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("status line wired in %s" % settings)
    print("Done. The first reading is saved after Claude Code's next reply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
