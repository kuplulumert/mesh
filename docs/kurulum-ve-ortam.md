# Kurulum ve ortam ayarları

## 1. Python tarafı

```bash
python -m pip install -e ".[all]"
```

Ekstra gruplar ayrı ayrı da kurulabilir:

| Grup | Ne için |
|---|---|
| `.[fluent]` | `ansys-fluent-core` — gerçek mesh üretimi |
| `.[cad]` | `trimesh`, `numpy` — STL/OBJ analizi |
| `.[yaml]` | `PyYAML` — YAML konfigürasyon |
| `.[advisor]` | `anthropic` — Claude danışmanı |
| `.[dev]` | `pytest` |

Çekirdek kod standart kütüphane dışında hiçbir şeye ihtiyaç duymaz; kurulu
olmayan bir opsiyon sessizce devre dışı kalır ve rapora not düşülür.

## 2. Fluent / PyFluent

PyFluent, Fluent'i bulmak için ANSYS kurulumunun ortam değişkenlerini kullanır.
Windows'ta ANSYS kurulumu bunları zaten ayarlar:

```
AWP_ROOT242 = C:\Program Files\ANSYS Inc\v242
```

Belirli bir sürümü zorlamak için:

```bash
automesh run parca.stp --version-ansys 24.2.0
# veya config'te:
#   fluent:
#     product_version: "24.2.0"
```

Faydalı ayarlar:

```yaml
fluent:
  processor_count: 16        # paralel çekirdek
  precision: double
  ui_mode: no_gui            # izlemek isterseniz: gui
  launch_timeout_s: 900      # lisans sunucusu yavaşsa artırın
  env:                       # Fluent'e geçirilecek ek ortam değişkenleri
    ANSYSLMD_LICENSE_FILE: "1055@lisans-sunucu"
```

### Fluent'i izleyerek çalıştırma

İlk denemelerde ne olduğunu görmek faydalıdır:

```bash
automesh run parca.scdoc --gui --cores 4
```

## 3. SpaceClaim headless

SpaceClaim backend'i şunları yapar: dosyayı açar, gövdeleri ölçer, sonuçları
JSON olarak yazar ve modeli STEP olarak dışa aktarır.

Gereken:

- Windows,
- ANSYS SpaceClaim kurulumu (`<AWP_ROOT>\scdm\SpaceClaim.exe`),
- geçerli bir SpaceClaim/Discovery lisansı (headless çalışma da lisans tüketir).

AutoMesh `AWP_ROOT*` değişkenlerini ve standart kurulum dizinlerini tarayarak en
yeni sürümü bulur. Bulamazsa elle verin:

```yaml
geometry:
  spaceclaim_exe: "C:/Program Files/ANSYS Inc/v251/scdm/SpaceClaim.exe"
  spaceclaim_script_api: "251"     # kurulu sürümle aynı olmalı
  spaceclaim_timeout_s: 900
  export_format: step              # step | iges | parasolid | stl | scdoc | none
```

Çalıştırılan komut şuna denk gelir:

```
SpaceClaim.exe /Headless=True /Splash=False /Welcome=False
               /ExitAfterScript=True /ScriptAPI=251
               /RunScript="...\spaceclaim_analyze.py"
```

Betik `src/automesh/geometry/scripts/spaceclaim_analyze.py` dosyasındadır ve
IronPython 2.7 ile çalışır. Kendi ölçümlerinizi eklemek isterseniz bu dosyayı
düzenleyin; çıktı JSON'una eklediğiniz alanlar `metrics.raw` altında rapora
taşınır.

> **Not:** SpaceClaim API'si tüm uzunlukları **metre** cinsinden verir; betik de
> metre yazar. Birim dönüşümü yalnızca Fluent'e gönderirken yapılır.

### SpaceClaim yoksa

Sorun değil:

- `.step` / `.stp` dosyaları dahili STEP okuyucuyla analiz edilir (hacim ve alan
  bilinmez, sınır kutusu ve yarıçaplar bilinir),
- `.stl` / `.obj` dosyaları tessellated analizden geçer,
- diğer formatlarda Fluent'in içe aktarma sonrası bildirdiği sınır kutusu
  kullanılır ve plan o noktada yeniden hesaplanır.

Her durumda rapor hangi yöntemin kullanıldığını ve neyin bilinmediğini yazar.

## 4. Claude danışmanı

```bash
export ANTHROPIC_API_KEY=sk-...
automesh run parca.stp --advisor
```

Anahtar ortamda yoksa SDK `ant auth login` profilini de kullanabilir. Danışman
yalnızca kural tabanı eşleşmediğinde devreye girer ve tanımlı eylem sözlüğü
dışına çıkamaz.
