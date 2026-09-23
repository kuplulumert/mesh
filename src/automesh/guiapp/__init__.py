"""Masaüstü arayüzü."""

import traceback
from typing import Optional

from ..launcher import CRASH_LOG, report  # noqa: F401  (başlatıcıyla ortak)
from .runner import BackgroundRun  # noqa: F401
from .state import GuiSettings  # noqa: F401


def main(geometry: Optional[str] = None) -> int:
    """Arayüzü başlat.

    ``geometry`` verilirse (kısayola sürükle-bırak ya da ``automesh gui
    parca.scdoc``) geometri alanı dolu gelir.

    Konsolsuz başlatıldığında (``pythonw AutoMesh.pyw``) hiçbir hata ekrana
    düşmez; pencere açılmaz ve sebebi görünmez. Bu yüzden her istisna
    :func:`automesh.launcher.report` ile dosyaya ve uyarı kutusuna yazılır.
    """
    try:
        from .app import main as app_main
    except ImportError as exc:
        return report(
            "Arayüz için Tkinter gerekli ama bulunamadı ({0}).\n\n"
            "Windows'ta python.org kurulumu Tkinter'ı içerir; mevcut "
            "kurulumu 'Modify' ile onarıp \"tcl/tk and IDLE\" kutusunu "
            "işaretleyin. Linux'ta: sudo apt install python3-tk".format(exc))

    try:
        return app_main(geometry)
    except Exception:
        return report(traceback.format_exc())
