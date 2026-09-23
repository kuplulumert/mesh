"""AutoMesh - arayüzü konsol penceresiyle açar.

`AutoMesh.pyw` ile aynı iş; tek farkı günlüğün konsolda da görünmesi.
Pencere hiç açılmıyorsa sebebini burada görürsünüz.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
if os.path.isdir(SRC) and SRC not in sys.path:
    sys.path.insert(0, SRC)

if __name__ == "__main__":
    from automesh.launcher import launch_gui

    code = launch_gui(os.path.join(HERE, "AutoMesh.pyw"), sys.argv)
    if code:
        input("\nHatayı okuduktan sonra Enter'a basın...")
    raise SystemExit(code)
