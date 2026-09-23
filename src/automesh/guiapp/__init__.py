"""Masaüstü arayüzü."""

import os
import sys
import tempfile
import traceback
from typing import Optional

from .runner import BackgroundRun  # noqa: F401
from .state import GuiSettings  # noqa: F401

#: Penceresiz başlatmada (pyw) hatanın yazıldığı dosya. Konsol olmadığı
#: için ekrana hiçbir şey düşmez; başlatıcı bu dosyayı gösterir.
CRASH_LOG = os.path.join(tempfile.gettempdir(), "automesh-hata.txt")


def main(geometry: Optional[str] = None) -> int:
    """Arayüzü başlat.

    ``geometry`` verilirse (kısayola sürükle-bırak ya da ``automesh gui
    parca.scdoc``) geometri alanı dolu gelir.

    Konsolsuz başlatıldığında (``pyw -m automesh.guiapp``) hiçbir hata
    ekrana düşmez; pencere açılmaz ve sebebi görünmez. Bu yüzden her
    istisna hem :data:`CRASH_LOG` dosyasına hem de -varsa- bir uyarı
    kutusuna yazılır.
    """
    try:
        from .app import main as app_main
    except ImportError as exc:
        return _fail(
            "Arayüz için Tkinter gerekli ama bulunamadı ({0}).\n\n"
            "Windows'ta python.org kurulumu Tkinter'ı içerir; mevcut "
            "kurulumu 'Modify' ile onarıp \"tcl/tk and IDLE\" kutusunu "
            "işaretleyin. Linux'ta: sudo apt install python3-tk".format(exc))

    try:
        return app_main(geometry)
    except Exception:
        return _fail(traceback.format_exc())


def _fail(message: str) -> int:
    """Hatayı kaybetmeden bildir: dosya + konsol + (mümkünse) uyarı kutusu."""
    try:
        with open(CRASH_LOG, "w", encoding="utf-8") as handle:
            handle.write(message)
        message += "\n\nAyrıntı: {0}".format(CRASH_LOG)
    except OSError:
        pass

    try:
        sys.stderr.write(message + "\n")
    except Exception:      # konsolsuz başlatmada stderr olmayabilir
        pass

    try:
        from tkinter import messagebox

        messagebox.showerror("AutoMesh başlatılamadı", message)
    except Exception:      # Tkinter yoksa zaten dosyaya yazdık
        pass
    return 1
