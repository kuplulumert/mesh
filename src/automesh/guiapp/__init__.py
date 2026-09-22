"""Masaüstü arayüzü."""

from .runner import BackgroundRun  # noqa: F401
from .state import GuiSettings  # noqa: F401


def main() -> int:
    """Arayüzü başlat (Tkinter yoksa anlaşılır bir hata verir)."""
    try:
        from .app import main as app_main
    except ImportError as exc:
        raise SystemExit(
            "Arayüz için Tkinter gerekli ama bulunamadı ({0}).\n"
            "Windows'ta python.org kurulumu Tkinter'ı içerir; Linux'ta "
            "'sudo apt install python3-tk' ile kurulur.".format(exc))
    return app_main()
