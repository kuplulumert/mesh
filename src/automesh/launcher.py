"""Çift tıkla çalışan başlatıcının ortak mantığı.

Kurumsal makinelerde `.exe` ve `.bat/.cmd/.ps1` çoğu zaman grup ilkesiyle
(AppLocker) engellenir; Python dosyaları engellenmez, çünkü çalıştıran şey
zaten izinli olan `pythonw.exe`'dir. Bu yüzden başlatıcı bir Python dosyası:
depo kökündeki ``AutoMesh.pyw``.

Burada iki iş var:

* :func:`launch_gui` - modül yollarını hazırlayıp arayüzü açar ve açılış
  hatasını kaybetmeden bildirir (konsolsuz başlatmada ekrana hiçbir şey
  düşmez).
* :func:`create_shortcuts` - masaüstüne ve Başlat menüsüne kısayol koyar.
  Önce gerçek bir ``.lnk`` denenir (ctypes ile IShellLink); olmazsa düz
  metin ``.url`` yazılır. İkisi de olmazsa elle yapma yolu anlatılır.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import traceback
from typing import List, Optional, Tuple

#: Kullanıcının ek modül klasörlerini yazdığı dosya (PyFluent başka yerdeyse).
PATHS_FILE = "automesh-yollar.txt"

#: Konsolsuz başlatmada hatanın yazıldığı dosya.
CRASH_LOG = os.path.join(tempfile.gettempdir(), "automesh-hata.txt")

#: Yorumlayıcıdan bağımsız yol kaydı. ``doctor --add-path`` buraya da yazar;
#: böylece ``py`` ile çalışan kurulumda bulunan PyFluent, ``.pyw`` dosyasını
#: açan başka bir Python'da da bulunur.
USER_PATHS_FILE = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~/.config"),
    "automesh", "yollar.txt")

SHORTCUT_NAME = "AutoMesh"
LAUNCHER_NAME = "AutoMesh.pyw"


# ---------------------------------------------------------------------------
# modül yolları
# ---------------------------------------------------------------------------

def repo_root(script: str) -> str:
    """Başlatıcının bulunduğu klasör (depo kökü)."""
    return os.path.dirname(os.path.abspath(script))


def _read_list(path: str) -> List[str]:
    """Her satırda bir klasör; boş satırlar ve ``#`` yorumları atlanır."""
    if not os.path.isfile(path):
        return []
    try:
        with io.open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    out = []
    for line in lines:
        entry = line.strip().strip('"')
        if entry and not entry.startswith("#"):
            out.append(os.path.expandvars(entry))
    return out


def extra_paths(root: str) -> List[str]:
    """``src`` + depo listesi + kullanıcı geneli liste, yazıldıkları sırada."""
    found: List[str] = []
    src = os.path.join(root, "src")
    if os.path.isdir(src):
        found.append(src)

    for entry in _read_list(os.path.join(root, PATHS_FILE)):
        if entry not in found:
            found.append(entry)
    for entry in _read_list(USER_PATHS_FILE):
        if entry not in found:
            found.append(entry)
    return found


def remember_path(directory: str) -> Tuple[bool, str]:
    """Bir klasörü kullanıcı geneli listeye ekle (``doctor --add-path``).

    ``.pth`` kaydı yalnızca onu yazan yorumlayıcıda geçerlidir; bu dosya
    ise hangi Python başlatırsa başlatsın okunur.
    """
    directory = os.path.abspath(os.path.expandvars(os.path.expanduser(directory)))
    current = _read_list(USER_PATHS_FILE)
    if any(os.path.normcase(entry) == os.path.normcase(directory)
           for entry in current):
        return True, "Zaten kayıtlı: {0}".format(USER_PATHS_FILE)
    current.append(directory)
    try:
        os.makedirs(os.path.dirname(USER_PATHS_FILE), exist_ok=True)
        with io.open(USER_PATHS_FILE, "w", encoding="utf-8") as handle:
            handle.write("# AutoMesh basit modda okunan ek modul klasorleri\n")
            for entry in current:
                handle.write(entry + "\n")
    except OSError as exc:
        return False, "Yazılamadı ({0}): {1}".format(USER_PATHS_FILE, exc)
    return True, "Eklendi: {0}".format(USER_PATHS_FILE)


def prepare_path(root: str) -> List[str]:
    """Ek klasörleri ``sys.path``'in başına, sırasını koruyarak ekle."""
    added: List[str] = []
    for entry in extra_paths(root):
        if entry not in sys.path:
            sys.path.insert(len(added), entry)
            added.append(entry)
    return added


# ---------------------------------------------------------------------------
# arayüzü başlat
# ---------------------------------------------------------------------------

def launch_gui(script: str, argv: Optional[List[str]] = None) -> int:
    """``AutoMesh.pyw``'nin yaptığı iş.

    ``script`` başlatıcının kendi yolu (``__file__``), ``argv`` ise komut
    satırı: üzerine sürüklenip bırakılan geometri buradan gelir.
    """
    root = repo_root(script)
    prepare_path(root)

    try:
        from .guiapp import main as gui_main
    except Exception:
        return report(
            traceback.format_exc()
            + "\nAutoMesh modülü yüklenemedi.\n\n"
              "- PyFluent başka bir klasörde kuruluysa o klasörü "
              "{0} dosyasına yazın (örnek: automesh-yollar.ornek.txt).\n"
              "- Tkinter eksikse python.org kurulumunu 'Modify' ile onarıp "
              "\"tcl/tk and IDLE\" kutusunu işaretleyin.".format(PATHS_FILE))

    geometry = None
    if argv:
        for candidate in argv[1:]:
            if candidate and not candidate.startswith("-"):
                geometry = candidate
                break
    return gui_main(geometry)


def report(message: str) -> int:
    """Hatayı kaybetme: dosyaya yaz, stderr'e yaz, uyarı kutusu göster."""
    try:
        with io.open(CRASH_LOG, "w", encoding="utf-8") as handle:
            handle.write(message)
        message += "\n\nAyrıntı: {0}".format(CRASH_LOG)
    except OSError:
        pass
    try:
        sys.stderr.write(message + "\n")
    except Exception:          # konsolsuz başlatmada stderr olmayabilir
        pass
    try:
        from tkinter import messagebox

        messagebox.showerror("AutoMesh başlatılamadı", message)
    except Exception:
        pass
    return 1


# ---------------------------------------------------------------------------
# kısayollar
# ---------------------------------------------------------------------------

def interpreter() -> str:
    """Konsol açmayan yorumlayıcı (``pythonw.exe``), yoksa normali."""
    exe = sys.executable or "python"
    folder, name = os.path.split(exe)
    if name.lower() == "python.exe":
        windowless = os.path.join(folder, "pythonw.exe")
        if os.path.isfile(windowless):
            return windowless
    return exe


def shell_folder(kind: str) -> str:
    """Masaüstü / Başlat menüsü klasörü.

    Masaüstü OneDrive'a taşınmış olabilir; bu yüzden yol tahmin edilmez,
    Windows'a sorulur (CSIDL_DESKTOPDIRECTORY / CSIDL_PROGRAMS).
    """
    csidl = {"desktop": 0x0010, "programs": 0x0002}[kind]
    try:
        import ctypes
        from ctypes import wintypes

        buffer = ctypes.create_unicode_buffer(260)
        ctypes.windll.shell32.SHGetFolderPathW(
            None, csidl, None, 0, buffer)          # type: ignore[attr-defined]
        if buffer.value:
            return buffer.value
    except Exception:
        pass
    fallback = {"desktop": os.path.join(os.path.expanduser("~"), "Desktop"),
                "programs": os.path.join(os.path.expanduser("~"), "Desktop")}
    return fallback[kind]


def create_shortcuts(root: str, kinds: Tuple[str, ...] = ("desktop", "programs")
                     ) -> List[Tuple[bool, str]]:
    """Kısayolları oluştur; her biri için (başarı, açıklama) döndür."""
    target = os.path.join(root, LAUNCHER_NAME)
    if not os.path.isfile(target):
        return [(False, "Başlatıcı bulunamadı: {0}".format(target))]

    results: List[Tuple[bool, str]] = []
    for kind in kinds:
        folder = shell_folder(kind)
        if not folder or not os.path.isdir(folder):
            results.append((False, "Klasör bulunamadı: {0}".format(folder)))
            continue
        path = os.path.join(folder, SHORTCUT_NAME + ".lnk")
        try:
            write_lnk(path, interpreter(), '"{0}"'.format(target), root)
            results.append((True, path))
            continue
        except Exception as exc:
            reason = exc
        # .lnk yazılamadı: düz metin .url her yerde çalışır.
        try:
            path = os.path.join(folder, SHORTCUT_NAME + ".url")
            write_url(path, target)
            results.append((True, path + "   (.lnk olmadı: {0})".format(reason)))
        except Exception as exc:
            results.append((False, "{0}: {1}".format(folder, exc)))
    return results


def write_url(path: str, target: str) -> None:
    """Düz metin kısayol. Hiçbir COM/şablon gerektirmez, her zaman yazılır."""
    from urllib.request import pathname2url

    text = ("[InternetShortcut]\r\n"
            "URL=file:{0}\r\n"
            "IconIndex=0\r\n"
            "IconFile={1}\r\n").format(
        pathname2url(os.path.abspath(target)),
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                     "System32", "shell32.dll"))
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def write_lnk(path: str, target: str, arguments: str, workdir: str,
              description: str = "AutoMesh - otonom Fluent meshing") -> None:
    """Gerçek bir ``.lnk`` yaz (ctypes ile IShellLink).

    PowerShell'e dokunmaz: AppLocker açıkken PowerShell kısıtlı dil kipine
    düşer ve ``New-Object -ComObject`` çalışmaz. Python'un COM çağrısı bu
    kısıttan etkilenmez.
    """
    import ctypes
    from ctypes import POINTER, byref, c_void_p
    from ctypes.wintypes import BYTE, DWORD, WORD

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", DWORD), ("Data2", WORD), ("Data3", WORD),
                    ("Data4", BYTE * 8)]

    def guid(text: str) -> GUID:
        value = GUID()
        ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text),
                                            byref(value))
        return value

    def method(pointer, index, *argtypes):
        table = ctypes.cast(pointer, POINTER(POINTER(c_void_p)))[0]
        proto = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)
        return proto(table[index])

    ole32 = ctypes.oledll.ole32
    ole32.CoInitialize(None)
    try:
        link = c_void_p()
        ole32.CoCreateInstance(
            byref(guid("{00021401-0000-0000-C000-000000000046}")),  # ShellLink
            None, 1,                                   # CLSCTX_INPROC_SERVER
            byref(guid("{000214F9-0000-0000-C000-000000000046}")),  # IShellLinkW
            byref(link))
        try:
            # IShellLinkW sanal tablo sırası (IUnknown'dan sonra):
            # 3 GetPath  8 GetWorkingDirectory  9 SetWorkingDirectory
            # 11 SetArguments  7 SetDescription  17 SetIconLocation  20 SetPath
            method(link, 20, ctypes.c_wchar_p)(link, target)
            method(link, 11, ctypes.c_wchar_p)(link, arguments)
            method(link, 9, ctypes.c_wchar_p)(link, workdir)
            method(link, 7, ctypes.c_wchar_p)(link, description)
            icon = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                "System32", "shell32.dll")
            method(link, 17, ctypes.c_wchar_p, ctypes.c_int)(link, icon, 15)

            persist = c_void_p()
            method(link, 0, c_void_p, c_void_p)(
                link,
                ctypes.cast(byref(guid("{0000010B-0000-0000-C000-000000000046}")),
                            c_void_p),
                ctypes.cast(byref(persist), c_void_p))
            try:
                # IPersistFile: 6 = Save(pszFileName, fRemember)
                method(persist, 6, ctypes.c_wchar_p, ctypes.c_int)(
                    persist, path, 1)
            finally:
                method(persist, 2)(persist)        # Release
        finally:
            method(link, 2)(link)                  # Release
    finally:
        ctypes.windll.ole32.CoUninitialize()
