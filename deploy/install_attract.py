"""Run on the cabinet after copying the project and installing its dependencies.

Adds Jukebox to the existing Fruit Machine display without touching attract.cfg.
Does not launch or stop any application, or copy account credentials.
"""

from datetime import datetime
from pathlib import Path
import shutil

home = Path.home()
project = home / "jukebox"
attract = home / ".attract"
romlist = attract / "romlists" / "Fruit Machine.txt"
if not romlist.is_file() or not (project / ".venv/bin/python").is_file():
    raise SystemExit("Expected the Fruit Machine romlist and installed jukebox virtualenv")

stamp = datetime.now().strftime("%Y%m%d-%H%M%S")


def write(path, content):
    if path.exists():
        if path.read_text() == content:
            return
        shutil.copy2(path, path.with_name(path.name + ".backup-" + stamp))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


launcher = project / "cabinet-launch.sh"
write(launcher, '''#!/bin/sh
cd /home/mame/jukebox || exit 1
export PATH="/home/mame/.local/bin:/home/mame/jukebox/.venv/bin:$PATH"
export JUKEBOX_AUTO_UPDATE=1
mkdir -p /home/mame/.local/state/jukebox
exec .venv/bin/python -m jukebox >>/home/mame/.local/state/jukebox/app.log 2>&1
''')
launcher.chmod(0o755)
write(project / "games" / "jukebox.launch", "JukeTube\n")
write(attract / "emulators" / "Jukebox.cfg", f'''# JukeTube; returns to Attract when the player quits.
executable           {launcher}
args
rompath              {project / 'games'}
romext               .launch
system               Jukebox
''')

existing = romlist.read_text()
lines = existing.splitlines()
for i, line in enumerate(lines):
    fields = line.split(";")
    if len(fields) >= 3 and fields[0] == "jukebox" and fields[2] == "Jukebox":
        fields[1] = "JukeTube"
        lines[i] = ";".join(fields)
        break
else:
    columns = ["jukebox", "JukeTube", "Jukebox", "", "2026", "", "Music",
               "2", "0", "joystick", "good", "1", "raster", "", "", "", "3", "", "", "GB", ""]
    lines.append(";".join(columns))
write(romlist, "\n".join(lines) + "\n")
print("Added JukeTube to Fruit Machine. Change displays and return to reload the list.")
