# Sorun giderme

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

**`AutoMesh.bat` çift tıklayınca pencere açılıp kapanıyor**
Hata mesajını görmek için komut isteminden çalıştırın:

```bat
cd /d E:\mesh
py -m automesh.guiapp
```

**Arayüz donuyor gibi görünüyor**
Donmaz — mesh üretimi ayrı bir iş parçacığında koşar. Fluent uzun bir adımda
(örneğin hacim ağı) takılıysa günlük akışı durur ama pencere yanıt verir.
İlerlemeyi görmek için "Fluent penceresini göster" kutusunu işaretleyin.

**Durdur'a bastım ama hemen durmadı**
Durdurma bir sonraki kontrol noktasında devreye girer; Fluent'in o anki adımı
(örneğin hacim ağı üretimi) yarıda kesilmez. Böylece Fluent düzgün kapatılır
ve rapor yine yazılır.

## Çalışmayı tekrar üretmek istiyorum

Her çalışma `journal.py` üretir. Bu, aynı adımları tekrarlayan bir PyFluent
scriptidir; Fluent'te elle çalıştırabilir, adım adım inceleyebilir veya
meslektaşınıza gönderebilirsiniz. `--dry-run` ile üretilen journal de geçerlidir:
provayı ANSYS'siz yapıp scripti gerçek Fluent'te koşturabilirsiniz.
