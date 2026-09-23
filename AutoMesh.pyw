"""AutoMesh - çift tıklayın, arayüz açılır (konsol penceresi çıkmaz).

`.exe` ve `.bat` grup ilkesiyle engellenmiş makineler için: bu bir Python
dosyası, çalıştıran da zaten izinli olan `pythonw.exe`. Kurulum gerekmez.

Hata görmek isterseniz `AutoMesh-konsol.py` dosyasını kullanın.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
if os.path.isdir(SRC) and SRC not in sys.path:
    sys.path.insert(0, SRC)

try:
    from automesh.launcher import launch_gui
except Exception:                      # automesh hiç bulunamadı
    import tempfile
    import traceback

    detail = traceback.format_exc()
    note = ("AutoMesh modülü bulunamadı.\n\n"
            "Bu dosya depo kökünde (yanında 'src' klasörü olacak şekilde) "
            "durmalı.\nAranan yer: {0}\n\n{1}".format(SRC, detail))
    path = os.path.join(tempfile.gettempdir(), "automesh-hata.txt")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(note)
    except OSError:
        pass
    try:
        from tkinter import messagebox

        messagebox.showerror("AutoMesh başlatılamadı", note)
    except Exception:
        sys.stderr.write(note + "\n")
    raise SystemExit(1)

if __name__ == "__main__":
    raise SystemExit(launch_gui(__file__, sys.argv))
