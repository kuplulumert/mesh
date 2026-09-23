"""Ortam teşhisi: neyin kurulu olduğunu ve neyin eksik olduğunu söyler.

Kurumsal makinelerde PyFluent ve ANSYS çoğu zaman standart olmayan yerlere
kuruluyor.  Bu modül ne bulduğunu tek ekranda gösterir ve eksik yolu kalıcı
olarak eklemek için ``.pth`` dosyası yazabilir - böylece her yeni komut
isteminde ``set PYTHONPATH=...`` yazmak gerekmez.
"""

from __future__ import annotations

import os
import site
import sys
import sysconfig
from typing import List, Optional, Tuple

#: ``--add-path`` ile yazılan dosyanın adı.
PTH_NAME = "automesh-extra-paths.pth"

OK = "  [+]"
WARN = "  [!]"
FAIL = "  [x]"
INFO = "      "


def _site_packages() -> str:
    """``.pth`` dosyasının yazılacağı klasör."""
    path = sysconfig.get_paths().get("purelib") or ""
    if path and os.path.isdir(path):
        return path
    try:
        user = site.getusersitepackages()
        if isinstance(user, str):
            return user
    except AttributeError:      # pragma: no cover - çok eski Python
        pass
    return path


def pth_path() -> str:
    return os.path.join(_site_packages(), PTH_NAME)


#: ``.pth`` dosyasında yolları işaretleyen yorum satırı.
_MARKER = "# path: "


def _render_pth(paths: List[str]) -> str:
    """``.pth`` dosyasının içeriğini üret.

    Düz bir yol listesi yazmak yetmiyor: ``site`` modülü o yolları
    ``sys.path``'in **sonuna** ekler, oysa ``PYTHONPATH`` **başına** ekler.
    Aynı paketin yarım bir kopyası daha önce geliyorsa düz liste çalışmaz.
    ``.pth`` dosyalarında ``import`` ile başlayan satırlar çalıştırıldığı
    için yolları öne almak mümkün - ve tam olarak ``PYTHONPATH`` davranışını
    elde ederiz.
    """
    lines = [
        "# AutoMesh tarafından oluşturuldu - automesh doctor --add-path",
        "# Yollar sys.path'in BAŞINA eklenir (PYTHONPATH ile aynı öncelik).",
    ]
    lines.extend(_MARKER + path for path in paths)
    literals = ", ".join("r'{0}'".format(path.replace("'", "")) for path in paths)
    lines.append(
        "import sys, os; _amp = [{0}]; "
        "[sys.path.insert(0, _p) for _p in reversed(_amp) "
        "if os.path.isdir(_p) and _p not in sys.path]".format(literals)
    )
    return "\n".join(lines) + "\n"


def existing_extra_paths() -> List[str]:
    """``.pth`` dosyasında hâlihazırda kayıtlı yollar."""
    path = pth_path()
    if not os.path.isfile(path):
        return []
    found: List[str] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped.startswith(_MARKER):
                found.append(stripped[len(_MARKER):].strip())
            elif stripped and not stripped.startswith("#") \
                    and not stripped.startswith("import "):
                found.append(stripped)          # eski düz biçim
    return found


def remove_path(directory: str) -> Tuple[bool, str]:
    """``directory``'yi kalıcı yol listesinden çıkar."""
    directory = os.path.abspath(os.path.expandvars(os.path.expanduser(directory)))
    current = existing_extra_paths()
    remaining = [p for p in current
                 if os.path.normcase(p) != os.path.normcase(directory)]
    if len(remaining) == len(current):
        return False, "Listede yok: {0}".format(directory)

    target = pth_path()
    try:
        if remaining:
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(_render_pth(remaining))
        elif os.path.isfile(target):
            os.remove(target)
    except OSError as exc:
        return False, "Güncellenemedi ({0}): {1}".format(target, exc)
    return True, "Çıkarıldı: {0}".format(directory)


def inspect_pyfluent() -> List[str]:
    """PyFluent'in neden import edilemediğini ayrıntılandır.

    "Bulunamadı" ile "bulundu ama eksik" çok farklı iki sorundur ve
    çözümleri de farklıdır; bu ayrımı açıkça göstermek gerekiyor.
    """
    lines: List[str] = []
    try:
        import ansys                                   # noqa: F401
    except Exception:
        lines.append("{0} 'ansys' paketi hiç bulunamadı.".format(INFO))
        lines.append("{0} Kurulum:  py -m pip install ansys-fluent-core".format(INFO))
        return lines

    for name in ("ansys", "ansys.fluent", "ansys.fluent.core"):
        try:
            module = __import__(name, fromlist=["__path__"])
        except Exception as exc:
            lines.append("{0} {1}: import edilemiyor ({2})".format(WARN, name, exc))
            break
        paths = list(getattr(module, "__path__", []) or [])
        lines.append("{0} {1} -> {2}".format(
            INFO, name, ", ".join(paths) if paths else "(yol yok)"))

        if name == "ansys.fluent.core" and paths:
            root = paths[0]
            missing = [sub for sub in ("solver", "meshing", "session")
                       if not os.path.exists(os.path.join(root, sub))
                       and not os.path.exists(os.path.join(root, sub + ".py"))]
            if missing:
                lines.append("{0} Eksik alt paketler: {1}".format(
                    WARN, ", ".join(missing)))
                lines.append("{0} Bu klasördeki kopya yarım; pip ile düzgün "
                             "kurulum gerekiyor:".format(INFO))
                lines.append("{0}   py -m pip install ansys-fluent-core".format(INFO))
                lines.append("{0} Kurduktan sonra yarım kopyayı yoldan "
                             "çıkarın:".format(INFO))
                lines.append("{0}   automesh doctor --remove-path {1}".format(
                    INFO, os.path.dirname(os.path.dirname(os.path.dirname(root)))))
    return lines


def _has_complete_core(fluent_dir: str) -> Tuple[bool, List[str]]:
    """``<...>/ansys/fluent`` altındaki ``core`` paketi tam mı?"""
    core = os.path.join(fluent_dir, "core")
    if not os.path.isdir(core):
        return False, ["core"]
    missing = [sub for sub in ("solver", "meshing", "session")
               if not os.path.isdir(os.path.join(core, sub))
               and not os.path.isfile(os.path.join(core, sub + ".py"))]
    return not missing, missing


def _diagnose_shadowing(fluent_paths: List[str]) -> List[str]:
    """Birden fazla ansys kurulumu varsa hangisinin işe yaradığını söyle."""
    lines: List[str] = []
    complete: List[str] = []
    broken: List[str] = []
    for path in fluent_paths:
        ok, missing = _has_complete_core(path)
        if ok:
            complete.append(path)
            lines.append("{0} tam kurulum: {1}".format(OK, path))
        else:
            broken.append(path)
            lines.append("{0} eksik kurulum: {1}  (yok: {2})".format(
                WARN, path, ", ".join(missing)))

    if len(fluent_paths) > 1:
        lines.append("{0} Birden fazla ansys kurulumu var; listedeki İLK sıra "
                     "kazanır.".format(WARN))

    if complete and broken:
        # Önerilen yol hiçbir şeyi silmez: tam kurulumu sys.path'in başına
        # alır, eksik kopya olduğu yerde kalır.  Kurumsal makinelerde
        # site-packages'e dokunmak çoğu zaman ne mümkün ne de istenir.
        good_root = os.path.dirname(os.path.dirname(complete[0]))
        lines.append("{0} Çözüm (hiçbir şey silinmez, geri alınabilir):".format(INFO))
        lines.append("{0}   automesh doctor --add-path {1}".format(INFO, good_root))
        lines.append("{0}   Tam kurulum sys.path'in başına alınır; eksik kopya "
                     "yerinde kalır.".format(INFO))
        lines.append("{0} Geri almak için: automesh doctor --remove-path {1}".format(
            INFO, good_root))
    elif broken and not complete:
        lines.append("{0} Hiçbir kopya tam değil; eksiksiz bir kurulum "
                     "gerekiyor.".format(INFO))
        lines.append("{0} Ortak bir klasöre kurabilirsiniz (site-packages'e "
                     "dokunmadan):".format(INFO))
        lines.append("{0}   py -m pip install ansys-fluent-core "
                     "--target D:\\Work\\plm".format(INFO))
        lines.append("{0}   automesh doctor --add-path D:\\Work\\plm".format(INFO))
    return lines


def add_path(directory: str) -> Tuple[bool, str]:
    """``directory``'yi kalıcı olarak ``sys.path``'e ekle.

    ``(başarılı, mesaj)`` döndürür.  Var olan kayıtlar korunur.
    """
    directory = os.path.abspath(os.path.expandvars(os.path.expanduser(directory)))
    if not os.path.isdir(directory):
        return False, "Klasör bulunamadı: {0}".format(directory)

    target = pth_path()
    current = existing_extra_paths()
    already = any(os.path.normcase(p) == os.path.normcase(directory)
                  for p in current)
    if not already:
        current.append(directory)
    # Zaten kayıtlı olsa bile dosyayı yeniden yazarız: eski sürümün yazdığı
    # düz biçim yolları sys.path'in sonuna ekliyordu, yenisi başına ekliyor.
    # Erken dönmek bu yükseltmeyi hiç uygulamamak demekti.
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(_render_pth(current))
    except OSError as exc:
        return False, "Yazılamadı ({0}): {1}".format(target, exc)
    verb = "Güncellendi" if already else "Eklendi"
    message = "{0}: {1}\n{2}Dosya: {3}\n{2}Yol sys.path'in başına " \
              "eklenir (PYTHONPATH ile aynı öncelik).".format(
                  verb, directory, INFO, target)

    # .pth yalnızca onu yazan yorumlayıcıda geçerli. AutoMesh.pyw dosyasını
    # başka bir Python açabildiği için aynı klasörü yorumlayıcıdan bağımsız
    # listeye de yazıyoruz.
    from .launcher import remember_path

    ok, note = remember_path(directory)
    message += "\n{0}{1}{2}".format(INFO, "" if ok else "Uyarı: ", note)
    return True, message


# --------------------------------------------------------------------------

def _check_import(module: str) -> Tuple[bool, str]:
    try:
        imported = __import__(module, fromlist=["__version__"])
    except Exception as exc:
        return False, str(exc)
    version = getattr(imported, "__version__", "")
    location = getattr(imported, "__file__", "") or ""
    return True, "{0}  {1}".format(version, location).strip()


def report(lines: Optional[List[str]] = None) -> List[str]:
    """Ortamın tam dökümü."""
    out: List[str] = lines if lines is not None else []
    add = out.append

    add("Python")
    add("{0} {1}".format(INFO, sys.version.split()[0]))
    add("{0} {1}".format(INFO, sys.executable))
    add("")

    add("AutoMesh")
    ok, detail = _check_import("automesh")
    add("{0} {1}".format(OK if ok else FAIL, detail if ok else "import edilemiyor"))
    if not ok:
        add("{0} Kurulum:  py -m pip install -e <depo klasörü>".format(INFO))
    add("")

    add("PyFluent (ansys-fluent-core)")
    ok, detail = _check_import("ansys.fluent.core")
    if ok:
        add("{0} {1}".format(OK, detail))
    else:
        add("{0} import edilemiyor".format(FAIL))
        add("{0} {1}".format(INFO, detail))
        for line in inspect_pyfluent():
            add(line)
    add("")

    add("Tkinter (arayüz için)")
    ok, detail = _check_import("tkinter")
    add("{0} {1}".format(OK if ok else WARN,
                         "var" if ok else "yok - arayüz açılmaz, CLI çalışır"))
    add("")

    add("Ek Python yolları (.pth)")
    extras = existing_extra_paths()
    if extras:
        for entry in extras:
            mark = OK if os.path.isdir(entry) else WARN
            add("{0} {1}{2}".format(mark, entry,
                                    "" if os.path.isdir(entry) else "  (klasör yok)"))
        add("{0} Dosya: {1}".format(INFO, pth_path()))
    else:
        add("{0} kayıt yok".format(INFO))
        add("{0} Yazılacağı yer: {1}".format(INFO, pth_path()))
    add("")

    add("PYTHONPATH ortam değişkeni")
    env = os.environ.get("PYTHONPATH", "")
    if env:
        for entry in env.split(os.pathsep):
            if entry.strip():
                add("{0} {1}".format(INFO, entry))
        add("{0} Not: bu yalnızca bu komut isteminde geçerli.".format(WARN))
        add("{0} Kalıcı yapmak için: automesh doctor --add-path <klasör>".format(INFO))
    else:
        add("{0} tanımlı değil".format(INFO))
    add("")

    add("ANSYS kurulumları")
    roots = sorted((k, v) for k, v in os.environ.items() if k.startswith("AWP_ROOT"))
    if roots:
        for key, value in roots:
            mark = OK if os.path.isdir(value) else WARN
            add("{0} {1} = {2}".format(mark, key, value))
    else:
        add("{0} AWP_ROOT* değişkeni yok - Fluent sürümünü --version-ansys "
            "ile verin".format(WARN))
    add("")

    add("SpaceClaim")
    try:
        from .geometry.spaceclaim import discover_spaceclaim

        found = discover_spaceclaim()
    except Exception as exc:       # pragma: no cover - savunma amaçlı
        found = None
        add("{0} arama başarısız: {1}".format(WARN, exc))
    if found:
        add("{0} {1}  (ScriptAPI {2})".format(OK, found[0], found[1]))
    else:
        add("{0} bulunamadı - .scdoc yerine .stp verin ya da".format(WARN))
        add("{0} geometry.spaceclaim_exe ayarını elle girin".format(INFO))
    return out


def summary() -> Tuple[bool, List[str]]:
    """``(her şey hazır mı, satırlar)``."""
    lines = report()
    ready = _check_import("automesh")[0] and _check_import("ansys.fluent.core")[0]
    return ready, lines
