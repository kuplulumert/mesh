# Sorun giderme

## Önce: automesh doctor

Ortamla ilgili her sorunda ilk komut bu olsun:

```bat
automesh doctor
```

Python, AutoMesh, PyFluent, Tkinter, ANSYS ve SpaceClaim durumunu tek ekranda
gösterir; `[x]` satırları eksikleri işaretler.

## Her yeni CMD'de PyFluent bulunamıyor

PyFluent standart `site-packages` yerine başka bir klasöre kurulmuşsa Python
orayı kendiliğinden bilmez; `set PYTHONPATH=...` yalnızca o komut istemi için
geçerlidir. Kalıcı çözüm:

```bat
automesh doctor --add-path D:\Work\plm
```

Python'un `site-packages` klasörüne bir `.pth` dosyası yazar; Python her
açılışta okur.

Yazılan dosya yolu `sys.path`'in **başına** ekler, tıpkı `PYTHONPATH` gibi.
Bu önemli: `site` modülü düz yol listelerini `sys.path`'in **sonuna** ekler,
dolayısıyla aynı paketin yarım bir kopyası daha önce geliyorsa düz liste
işe yaramaz. `--target` ile kurulmuş PyFluent kurulumlarında tam olarak bu
olur.

Yeni bir CMD açıp doğrulayın:

```bat
py -c "import ansys.fluent.core as p; print(p.__version__)"
```

Alternatifi `setx PYTHONPATH "D:\Work\plm"` ile kalıcı ortam değişkeni
yapmaktır, ama o makinedeki **her** Python'u etkiler ve başka projelerde
çakışma yaratabilir; `.pth` yalnızca o kurulumu etkilediği için tercih edilir.

## PyFluent bulunuyor ama "No module named ansys.fluent.core.solver"

Bu "bulunamadı"dan farklı bir sorundur: paketin klasörü yolda, ama içi
eksik. Genellikle bir kaynak ağacının ya da yarım kopyalanmış bir kurulumun
işaret edilmesinden olur.

`automesh doctor` bu durumda hangi klasörün işaret edildiğini ve hangi alt
paketlerin eksik olduğunu yazar. Çözüm, düzgün bir kurulum yapmak:

```bat
py -m pip install ansys-fluent-core
```

Kurulum bittikten sonra yarım kopyayı yoldan çıkarın, yoksa ikisi
karışabilir:

```bat
automesh doctor --remove-path D:\Work\plm
automesh doctor
```

PyFluent ortak bir klasöre `--target` ile kurulduysa kurulumun **tam**
olduğundan emin olun:

```bat
py -m pip install ansys-fluent-core --target "D:\Work\plm" --no-cache-dir
automesh doctor
```

`--target` kurulumları `site-packages`'e kaydolmaz; o yüzden klasörü
`automesh doctor --add-path` ile bir kez kaydetmek gerekir. Kurumsal ağda pip
PyPI'ye erişemiyorsa (proxy/sertifika hatası) BT'den `ansys-fluent-core`
paketini iç depodan kurmasını isteyin.

## Agent hiç başlamıyor

**`PyFluent kurulu değil`**
`pip install ansys-fluent-core`. Sadece agent'ın kararlarını görmek istiyorsanız
`--dry-run` yeterlidir, ANSYS gerekmez.

**`Fluent başlatılamadı: ...`**
Sırayla kontrol edin: lisans sunucusuna erişim, `AWP_ROOT*` değişkeni,
`--version-ansys` ile doğru sürüm, `fluent.launch_timeout_s` (yavaş lisans
sunucularında 900–1200 s gerekebilir).

**`SpaceClaim.exe bulunamadı`**
`geometry.spaceclaim_exe` ayarını elle verin ya da `geometry.analyzer: step`
diyerek SpaceClaim'i tamamen atlayın.

## Mesh çok büyük / bellek yetmiyor

```bash
automesh run parca.stp --max-cells 8000000
automesh run parca.stp --target-cells 5000000
```

Agent tahmini hücre sayısını bütçeye sığdırana kadar boyutları iteratif olarak
kabalaştırır ve bunu rapora not düşer. Çalışma sırasında Fluent bellek hatası
verirse `out_of_memory` kuralı devreye girer ve kademeli olarak kabalaştırır.

## Mesh çok kaba / detaylar kayboluyor

En olası neden: en küçük özellik tespit edilememiş. `automesh analyze parca.stp`
çıktısındaki "En küçük özellik" ve "Özellik aralığı" satırlarına bakın.

- STEP backend'i yarıçapları okur; dosyada yay/silindir yoksa tahmin zayıflar.
- SpaceClaim backend'i çok daha iyi ölçer — mümkünse `.scdoc` verin.
- Elle zorlamak için:

```bash
automesh run parca.stp --set planning.min_cells_across_feature=5 \
                       --set planning.max_size_divisor_complex=300
```

## Kalite bir türlü tutmuyor

Rapordaki "Deneme geçmişi" bölümü her denemede neyin değiştiğini gösterir.
Sık görülen üç durum:

1. **Yüzey ağı baştan kötü.** Kaynak CAD'de çakışan/üst üste binen yüzeyler
   vardır. `--workflow fault-tolerant` ile başlayın.
2. **Prizma katmanları kaliteyi aşağı çekiyor.** `--set planning.layer_count_range=[3,6]`
   ya da hızlı bir kontrol için `--no-boundary-layers`.
3. **Eşikler sizin işiniz için gereğinden sıkı.** Örneğin harici aerodinamikte
   `quality.max_skewness_accept=0.93` çoğu zaman kabul edilebilir:

```bash
automesh run parca.stp --set quality.max_skewness_accept=0.93 \
                       --set quality.min_orthogonal_accept=0.08
```

## "Tanınmayan hata" görüyorum

Agent bilmediği bir hatayla karşılaşınca genel çare olarak kabalaştırır. Daha
iyisini yapmak için iki yol var:

1. `--advisor` ile Claude'a danışmasını sağlayın.
2. Hatayı kural tabanına ekleyin: `src/automesh/diagnostics/knowledge_base.py`
   içindeki `RULES` listesine yeni bir `Rule` yazın. Her kuralın kademeli bir
   çare merdiveni (`steps`) olmalıdır; ilk kademe en ucuz çare olsun.

Eklediğiniz kuralı elinizdeki gerçek log ile doğrulayın:

```bash
automesh diagnose eski-calisma/transcript.log --stage volume
```

## TUI komutu "desteklenmiyor" diyor

Fluent sürümleri arasında komut yolları değişir. AutoMesh her komut için bildiği
yazımları sırayla dener ve hiçbiri tutmazsa bunu hata değil "desteklenmiyor"
olarak kaydedip devam eder. Sizin sürümünüzdeki doğru yolu
`src/automesh/fluent/tui.py` içindeki ilgili fonksiyona ekleyin — düzenlenmesi
gereken tek dosya orasıdır.

## Arayüz açılmıyor

**`Arayüz için Tkinter gerekli ama bulunamadı`**
Windows'ta python.org kurulumu Tkinter'ı içerir; Microsoft Store sürümünde
bazen eksik olur. Çözüm: python.org'dan kurulum yapın ya da mevcut kurulumu
**Değiştir (Modify)** ile onarıp "tcl/tk and IDLE" kutusunu işaretleyin.

**`.exe` ve `.bat` çalıştırmak yasak**
`AutoMesh.pyw` dosyasını kullanın: Python dosyası olduğu için AppLocker'ın
betik kuralları (`.bat`, `.cmd`, `.ps1`, `.vbs`) kapsamına girmez. Kısayol
için `py -m automesh kisayol`.

**`AutoMesh.pyw` çift tıklayınca hiçbir şey olmuyor**
Açılış hatası `%TEMP%\automesh-hata.txt` dosyasındadır. Aynı hatayı ekranda
görmek için `AutoMesh-konsol.py` dosyasını çift tıklayın.

**`.pyw` dosyası Not Defteri'nde açılıyor**
Dosya ilişkilendirmesi bozulmuş. Sağ tık → *Birlikte aç* → *Başka bir
uygulama seç* → `pythonw.exe` (Python kurulum klasöründe) → *Her zaman
bunu kullan*.

**`AutoMesh.bat` çift tıklayınca pencere açılıp kapanıyor**
Başlatıcı pencereyi konsolsuz açar; açılış hatası `%TEMP%\automesh-hata.txt`
dosyasına yazılır. Aynı hatayı ekranda görmek için:

```bat
AutoMesh.bat konsol
```

**`calisan bir Python bulunamadi`**
`py -V` çalışmıyordur. Python başka bir klasördeyse yolunu bir kez tanıtın:

```bat
setx AUTOMESH_PYTHON "D:\Python311\python.exe"
```

**Başlatıcı açılıyor ama `No module named ansys`**
PyFluent başka bir klasöre kurulu. `automesh-yollar.ornek.txt` dosyasını
`automesh-yollar.txt` adıyla kopyalayıp o klasörü yazın (her satıra bir
klasör); başlatıcı bunları `PYTHONPATH`'in başına ekler.

**Masaüstü kısayolu oluşturulamadı**
PowerShell kısıtlı olabilir. Elle: `AutoMesh.bat` dosyasına sağ tık → *Kısayol
oluştur* → oluşan kısayolu masaüstüne sürükleyin.

**Arayüz donuyor gibi görünüyor**
Donmaz — mesh üretimi ayrı bir iş parçacığında koşar. Fluent uzun bir adımda
(örneğin hacim ağı) takılıysa günlük akışı durur ama pencere yanıt verir.
İlerlemeyi görmek için "Fluent penceresini göster" kutusunu işaretleyin.

**Durdur'a bastım ama hemen durmadı**
Durdurma bir sonraki kontrol noktasında devreye girer; Fluent'in o anki adımı
(örneğin hacim ağı üretimi) yarıda kesilmez. Böylece Fluent düzgün kapatılır
ve rapor yine yazılır.

## Uzunluklar metre cinsinden görünüyor

Gösterim birimi varsayılan olarak mm'dir. Metre görüyorsanız eski bir ayar
dosyası kalmış olabilir; arayüzde **Gösterim birimi**'ni `mm` yapın ya da:

```bat
automesh run parca.scdoc --show-unit mm
```

Not: **Geometri birimi** alanını değiştirmeyin. O, Fluent'e dosyanın gerçek
birimini bildirir; yanlış verirseniz model 1000 kat büyük/küçük içe aktarılır.

## Fluent penceresi açılmıyor

Arayüzde "Fluent penceresini göster" kutusunun işaretli olduğundan emin olun
(varsayılan açık). Komut satırında `--gui` ekleyin. Uzak masaüstü ya da
servis hesabı üzerinden çalışıyorsanız Fluent penceresi hiç görünmeyebilir;
o durumda ilerlemeyi agent günlüğünden takip edin.

Mesh bitince pencere hemen kapanıyorsa "Bitince Fluent açık kalsın" kutusunu
(ya da `--keep-open`) kullanın.

## Gelişmiş akış takıldı, sadece mesh almak istiyorum

**Basit** sekmesine geçin: yüzey isimlendirme/gruplama hiç çalışmaz,
SpaceClaim dokümanına dokunulmaz, ara ekran çıkmaz. Geometriyi seçip
"Mesh oluştur"a basmanız yeterli.

```bat
automesh run parca.scdoc --cores 8 --simple
```

Boyutlandırma algoritması iki modda da aynıdır; basit modda yalnızca yüzey
gruplarına özel kontroller devre dışıdır.

## "Yüzey boyutları" ekranı açılmıyor, grup yok diyor

Analiz çalıştı ama boyut verilecek yüzey grubu çıkmadı. Arayüz artık
sebebini yazar; günlükte `--- Yüzey grubu oluşmadı ---` bölümüne bakın:

```
Sebep: hicbir yuzeyden olcum alinamadi (yariçap/cevre/kesit hepsi bos)
1 gövde, 240 yüzey görüldü, 0 ölçülebildi, 0 kaba
Okunabilen: geometri 240, alan 240, çevre 0, yarıçap 0
```

Bu satırlar hangi SpaceClaim API alanının okunamadığını gösterir:

| Sebep | Anlamı | Ne yapmalı |
|---|---|---|
| `hic yuzey okunamadi` | `body.Faces` boş döndü | SpaceClaim sürümü farklı; günlüğü paylaşın |
| `hicbir yuzeyden olcum alinamadi` | Yüzeyler var ama yarıçap/çevre/alan okunamıyor | Aynı; hangi alanın 0 olduğu satırda yazar |
| `tum olcumler global boyutla zaten cozuluyor` | Parça için ayrı kontrol gereksiz | Normal; mesh global boyutla çalışır |
| `bantlarda yeterli yuzey yok` | Her banda 1 yüzey düşmüş | `local_sizing.min_faces_per_group: 1` deneyin |
| `gruplama kapali` | Ayar kapalı | "Yüzey gruplarına özel boyut ver" kutusunu işaretleyin |

Grup çıkmasa da **mesh çalışır**: tüm yüzeyler global boyutu kullanır.

## Çalışmayı tekrar üretmek istiyorum

Her çalışma `journal.py` üretir. Bu, aynı adımları tekrarlayan bir PyFluent
scriptidir; Fluent'te elle çalıştırabilir, adım adım inceleyebilir veya
meslektaşınıza gönderebilirsiniz. `--dry-run` ile üretilen journal de geçerlidir:
provayı ANSYS'siz yapıp scripti gerçek Fluent'te koşturabilirsiniz.
