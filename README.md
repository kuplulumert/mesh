# AutoMesh

SpaceClaim'de hazırladığınız geometriyi alıp **ANSYS Fluent Meshing**'i sizin yerinize
süren otonom bir agent. Geometriyi ölçer, uygun mesh parametrelerini kendisi seçer,
Fluent'i çalıştırır; hata aldığında ya da kalite düşük çıktığında hatayı teşhis edip
**Improve Surface Mesh**, **auto node move**, prizma katmanı azaltma, fault-tolerant
akışa geçme gibi düzeltmeleri kendi uygular ve tekrar dener.

```
   geometri          otomatik              Fluent Meshing            kalite
  (.scdoc/.stp)  ->  parametre      ->     watertight /        ->   kontrolü
                     seçimi                fault-tolerant            |
                        ^                                            |
                        |            teşhis + düzeltme               v
                        +--------------------------------------  yetersiz mi?
```

İki kullanım biçimi var — masaüstü arayüzü ya da tek komut:

```bat
automesh doctor                  :: ortam kontrolü - önce bunu çalıştırın
automesh gui                     :: pencereyi aç, geometriyi tıklayarak seç
automesh propose manifold.stp    :: ölçümler + seçilebilir mesh kademeleri
automesh run manifold.scdoc      :: komut satırı
```

---

## İçindekiler

1. [Ne yapar, ne yapmaz](#ne-yapar-ne-yapmaz)
2. [Kurulum](#kurulum)
3. [Arayüz](#arayüz)
4. [Hızlı başlangıç](#hızlı-başlangıç)
5. [Agent nasıl karar veriyor](#agent-nasıl-karar-veriyor)
6. [Otonom döngü](#otonom-döngü)
7. [Hata → düzeltme tablosu](#hata--düzeltme-tablosu)
8. [Komut satırı](#komut-satırı)
9. [Konfigürasyon](#konfigürasyon)
10. [Çıktılar](#çıktılar)
11. [Claude danışmanı (opsiyonel)](#claude-danışmanı-opsiyonel)
12. [Mimari](#mimari)
13. [Sınırlar ve bilinen kısıtlar](#sınırlar-ve-bilinen-kısıtlar)

---

## Ne yapar, ne yapmaz

**Yapar**

- SpaceClaim'i headless çalıştırıp geometriyi ölçer (hacim, alan, en küçük özellik,
  eğrilik yarıçapları, ince kesitler) ve gerekirse Fluent'in sevdiği bir formata
  (STEP) dışa aktarır.
- SpaceClaim yoksa STEP dosyasını kendi okur (CAD çekirdeği gerektirmeden),
  STL/OBJ için tessellated analiz yapar.
- Ölçümlerden min/max hücre boyutu, büyüme oranı, eğrilik açısı, proximity,
  hacim doldurma tipi ve sınır tabakası parametrelerini **kendisi türetir**;
  hücre sayısını makinenizin bütçesine sığdırır.
- PyFluent ile Fluent Meshing'i başlatır, watertight veya fault-tolerant akışı
  uçtan uca yürütür.
- Yüzey ve hacim ağı kalitesini `check-quality` çıktısından okur, eşiklerle
  karşılaştırır.
- Hata/uyarı metnini 21 kurallı bir bilgi tabanıyla eşleştirir, **kademeli**
  düzeltme uygular (önce yerinde onarım, sonra parametre değişikliği, en son
  akış/tip değişikliği) ve yeniden dener.
- Her çalışma için denetlenebilir bir rapor, tam transcript ve tekrar
  çalıştırılabilir bir PyFluent journal'ı üretir.

**Yapmaz**

- CFD çözümü kurmaz (sınır koşulu, türbülans modeli, çözücü ayarı yok).
- CAD'i onarmaz; geometrideki gerçek boşlukları SpaceClaim'de siz kapatmalısınız
  (agent bunu tespit edip fault-tolerant akışa geçer ama CAD'i değiştirmez).
- Sizin yerinize mühendislik kararı vermez: ürettiği ağı **raporla birlikte**
  kontrol edin.

---

## Kurulum

Gereksinimler:

| Bileşen | Sürüm | Zorunlu mu |
|---|---|---|
| Python | 3.9+ | evet |
| Tkinter | Python ile gelir | sadece arayüz için |
| ANSYS Fluent (Meshing) | 2022R2+ | gerçek mesh için evet |
| PyFluent (`ansys-fluent-core`) | 0.20+ | gerçek mesh için evet |
| ANSYS SpaceClaim / Discovery | 2021R1+ | sadece `.scdoc` analizi için |
| PyYAML | 6+ | YAML config kullanacaksanız |
| trimesh | 4+ | STL/OBJ analizi için |
| anthropic | 1+ | Claude danışmanı için |

```bash
git clone https://github.com/kuplulumert/mesh.git
cd mesh
python -m pip install -e ".[all]"     # veya: pip install -e .
```

Çekirdek kod **yalnızca standart kütüphaneyi** kullanır; yukarıdakiler opsiyoneldir
ve kurulu olanlar otomatik devreye girer. ANSYS kurulu olmayan bir makinede bile
`--dry-run` ile her şeyi deneyebilirsiniz.

---

## Arayüz

Komut satırı yazmak istemiyorsanız masaüstü penceresini kullanın:

```bat
automesh gui
```

Depo kökündeki **`AutoMesh.bat`** dosyasına çift tıklamak da aynı işi yapar
(varsa `.venv`'i kendisi bulur).

Pencerede:

| Bölüm | Ne yapar |
|---|---|
| **Dosyalar** | Geometriyi Windows'un kendi dosya seçicisiyle seçin; çıktı klasörü ve isteğe bağlı konfigürasyon dosyası da buradan |
| **Mesh ayarları** | Çekirdek sayısı, akış, hacim doldurma, hücre sınırı, deneme sayısı, prizma açık/kapalı, **geometri birimi** ve **gösterim birimi** |
| **Akış bilgisi** | y+, hız, yoğunluk, viskozite, karakteristik uzunluk — doldurursanız ilk katman yüksekliği hesaplanır |
| **Çalıştırma** | Fluent penceresini göster (varsayılan açık), bitince Fluent açık kalsın, prova modu + senaryo, Claude danışmanı, ANSYS sürümü |
| **Üç düğme** | `1. Geometriyi analiz et` → `2. Ölçüm ve öneriler` → `3. Mesh oluştur` |
| **Seçili kademe satırı** | Hangi mesh kademesini seçtiğinizi gösterir; "Seçimi temizle" ile otomatiğe döner |
| **Günlük** | Agent'ın kararları canlı akar: teşhisler sarı, hatalar kırmızı, başarı yeşil |
| **Durdur** | Çalışmayı bir sonraki adımda temizce keser; Fluent düzgün kapatılır ve rapor yine yazılır |
| **Raporu aç / Klasörü aç** | Çalışma bitince etkinleşir |
| **Komutu kopyala** | Aynı işi yapan `automesh run ...` satırını panoya alır — otomasyona geçerken işe yarar |

### Birimler

İki ayrı birim var ve karıştırılmamalı:

| | Ne işe yarar | Varsayılan |
|---|---|---|
| **Geometri birimi** | Fluent'e "bu dosya şu birimde" diye bildirilir | boş = dosyadan tespit |
| **Gösterim birimi** | Ekranda ve raporda uzunlukların yazıldığı birim | **mm** |

Bunlar bilerek ayrıdır: SpaceClaim geometriyi metre cinsinden bildirir, ama
0.00069 m yerine 0.6967 mm okumak istersiniz. Gösterim birimini değiştirmek
Fluent'e gidenlere dokunmaz — dokunsaydı model 1000 kat yanlış ölçeklenirdi.

Komut satırında:

```bat
automesh run parca.scdoc --show-unit mm     :: varsayılan
automesh run parca.scdoc --show-unit in     :: inç
automesh run parca.scdoc --show-unit auto   :: model boyutuna göre
```

### Meshlemeyi Fluent'ten izleme

Arayüzde **"Fluent penceresini göster"** varsayılan olarak açıktır: Fluent
Meshing penceresi açılır, görev ağacı ve grafik penceresi ağ kurulurken
canlı güncellenir.

**"Bitince Fluent açık kalsın"** kutusunu işaretlerseniz çalışma bittikten
sonra Fluent kapanmaz; ağı orada döndürüp inceleyebilirsiniz. Fluent'i sonra
elle kapatmanız gerekir.

```bat
automesh run parca.scdoc --gui          :: pencereyi göster
automesh run parca.scdoc --keep-open    :: göster ve bitince açık bırak
```

> Fluent penceresi açıkken **içinde tıklamayın**. Agent Fluent'i API üzerinden
> sürüyor; aynı anda elle müdahale etmek görev ağacını agent'ın beklediği
> durumdan çıkarır. İzlemek serbest, karışmak değil.

### Ölçüm ve öneri ekranı

`2. Ölçüm ve öneriler` düğmesi Fluent'i hiç açmadan geometriyi ölçer ve bir
seçim penceresi açar:

**Solda ölçülen uzunluklar** — sınır kutusu, köşegen, en küçük özellik, en
kısa kenar, en küçük eğrilik yarıçapı, en ince kesit, özellik aralığı. Her
satırın yanında o ölçümün neye yaradığı yazar ("minimum hücre boyutu bunun
üzerine oturur" gibi).

**Sağda seçilebilir kademeler** — otomatik planın etrafında beş basamak:

| Kademe | Boyut | Ne için |
|---|---|---|
| Hızlı önizleme | otomatiğin 2.0 katı | Topolojiyi ve bölgeleri görmek için; CFD'ye uygun değil |
| Kaba | 1.4 katı | İlk yakınsama denemesi |
| **Dengeli (önerilen)** | 1.0 | Agent'ın geometriden hesapladığı nokta |
| İnce | 0.7 katı | Keskin gradyanlar için; ~3 kat hücre |
| Çok ince | 0.5 katı | Ağ bağımsızlık çalışmasının üst basamağı |

Her satırda min–max hücre boyutu, tahmini hücre sayısı, kaba bellek ihtiyacı
ve **en küçük özelliğin kaç hücreyle çözüldüğü** görünür. Altta seçtiğiniz
kademenin gerekçesi ve varsa uyarıları yazar ("En küçük özellik 2 hücreden az
ile çözülüyor - o detaylar ağda kaybolur", "Tahmini hücre sayısı bütçeyi
aşıyor").

Seçtiğiniz kademe ana pencerede görünür ve `3. Mesh oluştur` onu kullanır.
"Otomatiğe bırak" derseniz agent kendi kararıyla devam eder.

Aynı bilgi komut satırında:

```bat
automesh propose "M:\...\Multicyclone.scdoc"
automesh run "M:\...\Multicyclone.scdoc" --level fine
```

Ayarlar kapanışta saklanır, pencereyi bir daha açtığınızda son kullandığınız
değerlerle gelir.

---

## Ortam kontrolü

Kurulum sonrası ilk komut bu olsun:

```bat
automesh doctor
```

Python sürümünü, AutoMesh'in nereden yüklendiğini, PyFluent'in bulunup
bulunmadığını, Tkinter'ı, ANSYS kurulumlarını (`AWP_ROOT*`) ve SpaceClaim'i
tek ekranda gösterir.

### PyFluent standart olmayan bir klasördeyse

Kurumsal makinelerde PyFluent çoğu zaman `site-packages` yerine ortak bir
klasöre kurulur (`D:\Work\plm` gibi). O zaman her yeni komut isteminde
`set PYTHONPATH=...` yazmak gerekir. Bunu kalıcı yapmak için:

```bat
automesh doctor --add-path D:\Work\plm
```

Bu, Python'un kendi `site-packages` klasörüne bir `.pth` dosyası yazar;
Python her açılışta orayı okur. Ortam değişkeni gerekmez, yalnızca o Python
kurulumunu etkiler ve var olan kayıtlar korunur.

Yazılan dosya yolu `sys.path`'in **başına** ekler - `PYTHONPATH` ile aynı
öncelik. Düz bir yol listesi yazmak yetmez, çünkü `site` modülü onları en
sona ekler ve aynı paketin yarım bir kopyası daha önce geliyorsa kazanır.

---

## Hızlı başlangıç

### 1. Önce kuru çalıştırma (ANSYS gerekmez)

Agent'ın kararlarını, hata teşhisini ve raporu görmek için:

```bash
automesh run parca.stp --dry-run
automesh run parca.stp --dry-run --scenario prism     # prizma katmanı çökmesi senaryosu
automesh run parca.stp --dry-run --scenario dirty     # kirli CAD senaryosu
```

Senaryolar: `clean`, `realistic`, `dirty`, `prism`, `memory`, `stubborn`.

### 2. Sadece planı görmek

```bash
automesh plan parca.stp --y-plus 1 --velocity 12
```

```
Mesh planı
  Akış              : watertight
  Min / max boyut   : 0.696662 mm / 1.39332 mm
  Büyüme oranı      : 1.185
  Boyut fonksiyonu  : Curvature & Proximity
  Eğrilik açısı     : 16.2 derece
  Hacim doldurma    : poly-hexcore
  Sınır tabakası    : 19 katman, last-ratio, büyüme 1.20
  İlk katman        : 0.0452699 mm
  Tahmini hücre     : 216,585
```

### 3. Gerçek çalıştırma

```bash
automesh run manifold.scdoc --cores 8
automesh run manifold.scdoc --cores 16 --y-plus 30 --velocity 27.8 --max-cells 20000000
```

Çalışma bitince `runs/<zaman>-<ad>/report.md` dosyasını açın.

---

## Agent nasıl karar veriyor

Hiçbir parametre sabit değildir; hepsi geometriden türetilir. Önce bir
**karmaşıklık skoru** (0–1) hesaplanır:

| Bileşen | Ağırlık | Anlamı |
|---|---|---|
| Özellik aralığı `köşegen / en küçük özellik` | 0.40 | 1:10 basit, 1:3000 çok detaylı |
| Yüzey sayısı | 0.25 | modelin topolojik yoğunluğu |
| Eğrisel yüzey oranı | 0.20 | düzlem dışı yüzeylerin payı |
| Küçük yüzey oranı | 0.15 | fillet/delik yoğunluğu |

Sonra:

| Parametre | Kural |
|---|---|
| **Maksimum boyut** | `köşegen / D`, `D` karmaşıklıkla 40 → 200 arasında değişir |
| **Minimum boyut** | en küçük özelliğin üzerine ≥3 hücre; `max/500` tabanıyla sınırlı |
| **Büyüme oranı** | 1.20 (basit) → 1.15 (karmaşık) |
| **Eğrilik normal açısı** | 18° (basit) → 12° (karmaşık) |
| **Boşluk başına hücre** | 2 → 3 |
| **Boyut fonksiyonu** | eğrisel yüzey ve/veya dar kesit varlığına göre Curvature / Proximity / ikisi |
| **Akış** | geometri su geçirmezse `watertight`, değilse `fault-tolerant` |
| **Hacim doldurma** | varsayılan `poly-hexcore`; hata halinde `polyhedra` → `tetrahedral` |
| **Prizma katmanı** | 5 (basit) → 12 (karmaşık); ince kesit varsa yığın kalınlığı kesitin %40'ını geçmeyecek şekilde azaltılır |
| **İlk katman yüksekliği** | y+ ve hız verildiyse `Cf = 0.058·Re^-0.2` düz levha korelasyonundan; verilmediyse `smooth-transition`, geçiş oranı 0.272 |
| **Hücre bütçesi** | tahmini hücre sayısı sınırı aşarsa boyutlar iteratif olarak kabalaştırılır |

Sınır kutusu bilinmiyorsa (bilinmeyen CAD formatı), agent içe aktarmadan sonra
Fluent'ten `domain extents` alıp planı **yeniden** hesaplar.

---

## Yüzey gruplarına özel boyut

Global bir hücre boyutu her yere aynı davranır: 2 mm'lik bir deliği çözmek
için mesh'i inceltirseniz düz duvarlar da gereksiz yere incelir ve hücre
sayısı patlar. Agent bunun yerine **her yüzeyin gerektirdiği boyutu ölçer**
ve benzer gereksinimleri olanları tek kontrolde toplar.

### Üç ölçüt

Her yüzey için üç büyüklük ayrı ayrı hesaplanır, **en zorlayıcı olan kazanır**:

| Ölçüt | Nereden gelir | Kural | Varsayılan |
|---|---|---|---|
| `curv` — eğrilik | yüzeyin eğrilik yarıçapı `r` | `2·pi·r / N` | N = 16 hücre/çevre |
| `width` — dar bant | `2·alan / çevre` (fileto, sliver, ince şerit) | `genişlik / N` | N = 3 |
| `gap` — ince kesit | gövdenin `2·Hacim/Alan` değeri | `kesit / N` | N = 3 |

Sonuç, yüzeyin **tipinden değil ölçülen büyüklüğünden** çıkar. 10 mm yarıçaplı
ama 0.1 mm genişliğinde bir fileto şeridi, eğriliği kaba olmasına rağmen
`width` ölçütünden 33 µm hücre alır.

### Gruplama ve isimlendirme

Yüzeyler *gerektirdikleri hücre boyutuna* göre kat-kat bantlara ayrılır ve her
bant bir named selection olur. Ad, doğrudan uygulanacak boyutu ve onu belirleyen
ölçütü söyler:

```
automesh_curv_0p31mm     eğrilik 0.8 mm  -> 0.31 mm hücre
automesh_width_0p03mm    dar bant 0.1 mm -> 0.033 mm hücre
automesh_gap_0p30mm      ince kesit 0.9 mm -> 0.30 mm hücre
```

Aynı boyutu gerektiren farklı tipteki yüzeyler tek kontrolde birleşir —
gereksiz kontrol açmanın maliyeti var, faydası yok.

### Sizin kendi gruplarınız

Dokümanda zaten named selection varsa (`inlet`, `outlet`, `wall-duct` …):

- **Hiçbirine dokunulmaz.** Adı, üyeleri, hiçbiri değişmez.
- İçerdikleri yüzeyler aynı üç ölçütle ölçülür ve o gruba **uygun bir face
  size önerilir**.
- Kontrol bütçesi dolarsa **önce sizin gruplarınız** yerini korur; otomatik
  gruplar elenir.

Raporda hangisinin sizin, hangisinin agent'ın olduğu ayrı sütunda yazar.

### Fluent'e aktarım

Her grup bir `Add Local Sizing` görevine dönüşür (`BOIExecution: Face Size`,
kapsam o grubun etiketi).

> **Neden .scdoc:** STEP named selection **taşımaz**. Gruplar varsa agent
> dışa aktarımı otomatik olarak `.scdoc`'a çevirir; Fluent Meshing onu okuyup
> grupları yüzey etiketi (face label) olarak alır. `export_format: auto`
> varsayılanı bunu kendisi halleder.

Örnek bir çalışmadan:

| Grup | Kaynak | Yüzey | Boyutu belirleyen | Hücre boyutu |
|---|---|---|---|---|
| `inlet` | sizin grubunuz | 2 | eğrilik yarıçapı 1.5 mm | **0.6 mm** |
| `automesh_width_0p03mm` | agent | 5 | dar bant 0.1 mm | **0.033 mm** |
| `automesh_gap_0p30mm` | agent | 7 | ince kesit 0.9 mm | **0.30 mm** |
| `automesh_curv_0p31mm` | agent | 12 | eğrilik yarıçapı 0.8 mm | **0.31 mm** |
| *gruplanmayanlar* | — | — | — | *global 2.95 mm* |

### Frenler

- Global boyuta **yakın** kontroller eklenmez (faydası yok, sadece maliyet).
- Hiçbir kontrol global maksimumun **1/200**'ünden ince olamaz.
- Gövde köşegeninin %3'ünden kaba gereksinimler zaten global boyutla çözülür,
  gruplanmaz.
- Kontrol sayısı varsayılan 8 ile sınırlı.

### Ayarlar

```yaml
local_sizing:
  enabled: true
  cells_per_circle: 16.0      # eğrilik ölçütü: delik çevresinde kaç hücre
  cells_across_width: 3.0     # dar bant ölçütü: bandın enine kaç hücre
  cells_across_gap: 3.0       # ince kesit ölçütü: kesitte kaç hücre
  max_controls: 8
  read_existing_groups: true  # sizin gruplarınızı oku (asla değiştirmez)
  size_existing_groups: true  # onlara da boyut öner
  max_useful_ratio: 0.03      # köşegenin %3'ünden kaba gereksinimler gruplanmaz
  min_size_ratio: 200.0       # en ince kontrol = global max / 200
```

---

## Otonom döngü

İki iç içe döngü var:

```
DIŞ DÖNGÜ  (en fazla autonomy.max_attempts kez)
  plan -> initialize -> import -> surface mesh
                                     |
                       İÇ DÖNGÜ (max_repairs_per_attempt kez)
                         kalite kontrolü -> yetersizse yerinde onarım
                                            (Improve Surface Mesh, TUI merdiveni)
                                     |
          describe geometry -> update boundaries/regions
                     -> boundary layers -> volume mesh
                                     |
                       İÇ DÖNGÜ: Improve Volume Mesh, auto node move
                                     |
                              yeterli mi? -> evet: mesh yazılır
                                          -> hayır: plan değiştirilip dış döngü tekrar
```

Kritik kural: **hiçbir çare iki kez aynı şekilde denenmez.**

- Her teşhis kuralının bir *kademe merdiveni* vardır. `prism_failure` ilk seferde
  katman sayısını 2 azaltır, ikinci seferde ilk katmanı yarıya indirir, üçüncüde
  aspect-ratio yöntemine geçer, dördüncüde prizmaları tamamen kapatır.
- Kalite merdiveninde yerinde onarımlar her **yeni** ağ için sıfırdan denenir
  (ucuzlar ve yeni ağda işe yarayabilirler), ama plan değişiklikleri **tek yönlü**
  ilerler; böylece dış döngü asla aynı parametrelerle tekrar etmez.
- Yerinde onarım kaliteyi iyileştirmediyse agent bunu fark eder ve doğrudan
  parametre değişikliğine atlar.

Kalite eşikleri (varsayılan):

| Metrik | İyi | Kabul | Kritik |
|---|---|---|---|
| Hacim max skewness | < 0.80 | < 0.90 | ≥ 0.98 |
| Minimum orthogonal quality | > 0.20 | > 0.10 | ≤ 0.01 |
| Maksimum aspect ratio | – | < 100 | > 1000 |
| Yüzey max skewness | < 0.60 | < 0.80 | ≥ 0.95 |
| Negatif hacim / sol-elli yüzey | yok | yok | herhangi biri |

---

## Hata → düzeltme tablosu

`automesh rules` komutu tam listeyi verir. Öne çıkanlar:

| Fluent ne diyor | Teşhis | 1. çare | 2. çare | 3. çare |
|---|---|---|---|---|
| `intersecting faces` | Kesişen yüzeyler | Improve Surface Mesh (0.85) | min boyut ×0.5, boşluk/hücre +1 | fault-tolerant akış |
| `free faces`, `not watertight` | Su geçirmez değil | kullanılmayan düğümleri sil + birleştir | fault-tolerant akış | dur, CAD onarımı gerekli |
| `multi-connected faces` | Non-manifold | TUI yüzey onarım merdiveni | fault-tolerant akış | – |
| `prisms collapsed` | Prizma çökmesi | katman −2 | ilk katman ×0.5, katman −1 | aspect-ratio yöntemi → kapat |
| `negative volume`, `left-handed` | Geçersiz hücre | auto node move | büyüme −0.05, katman −1 | doldurma tipini yumuşat |
| `not enough memory` | Bellek | boyut ×1.5 | boyut ×1.5 + katman −2 | boyut ×2 + prizma kapalı |
| `hexcore ... failed` | Hexcore | tampon katman +1 | polyhedra'ya geç | – |
| `no fluid region` | Bölge yok | katı+akışkan tanımı | fault-tolerant akış | – |
| `leak path detected` | Sızıntı | min boyut ×0.5 | fault-tolerant akış | – |
| `license`, `flexlm` | Lisans | **dur** (otomatik çözülemez) | – | – |
| Tanınmayan hata | Genel çare | boyut ×1.3 + katman −1 | (tekrarlarsa kademeler devam eder) | – |

Elinizdeki bir Fluent log'unu agent'a teşhis ettirebilirsiniz:

```bash
automesh diagnose fluent-transcript.trn --stage volume
```

---

## Komut satırı

```
automesh doctor                           # ortam kontrolü (+ --add-path ile yol ekle)
automesh gui                              # masaüstü arayüzü
automesh propose <geometri>               # ölçümler + seçilebilir mesh kademeleri
automesh run <geometri> [seçenekler]      # analiz + planla + meshle + raporla
automesh plan <geometri>                  # analiz + plan (Fluent açılmaz)
automesh analyze <geometri>               # sadece geometri metrikleri
automesh diagnose <log|-> [--stage ...]   # bir Fluent çıktısını teşhis et
automesh rules                            # bilgi tabanını listele
automesh config -o automesh.json          # örnek konfigürasyon üret
```

`run` için sık kullanılan seçenekler:

| Seçenek | Anlamı |
|---|---|
| `--dry-run` / `--scenario X` | ANSYS'siz simülasyon |
| `--cores N` | Fluent çekirdek sayısı |
| `--gui` | Fluent arayüzünü göster (meshlemeyi canlı izle) |
| `--version-ansys 24.2.0` | ANSYS sürümünü sabitle |
| `--workflow watertight\|fault-tolerant` | akışı zorla |
| `--fill poly-hexcore\|polyhedra\|hexcore\|tetrahedral` | hacim doldurma |
| `--level preview\|coarse\|balanced\|fine\|very_fine` | hazır mesh kademesi |
| `--min-size 0.4mm`, `--max-size 3mm` | hücre boyutunu elle ver (birim eki opsiyonel) |
| `--growth 1.12`, `--layers 3` | büyüme oranını / katman sayısını zorla |
| `--max-cells`, `--target-cells` | hücre bütçesi |
| `--attempts N` | yeniden deneme sayısı |
| `--y-plus`, `--velocity`, `--density`, `--viscosity`, `--length` | sınır tabakası için akış bilgisi |
| `--no-boundary-layers` | prizma katmanı kurma |
| `--unit mm` | geometrinin gerçek birimini zorla (Fluent'e bildirilir) |
| `--show-unit mm\|in\|auto` | ekranda/raporda gösterim birimi (varsayılan mm) |
| `--keep-open` | bitince Fluent'i açık bırak |
| `--advisor` | bilinmeyen hatalarda Claude'a danış |
| `--set bölüm.anahtar=değer` | herhangi bir ayarı geçersiz kıl |

`doctor` için: `--add-path <klasör>` kalıcı olarak Python yoluna ekler,
`--remove-path <klasör>` çıkarır.

---

## Konfigürasyon

`config/default.yaml` tüm ayarları yorumlarıyla içerir. Kopyalayıp düzenleyin:

```bash
automesh run parca.stp --config benim-ayarim.yaml
```

Çalışma dizininde `automesh.yaml` / `automesh.json` varsa otomatik okunur.
Hazır örnekler: `config/examples/external-aero.yaml` (dış akış, y+ 30),
`config/examples/dirty-cad.yaml` (boşluklu CAD).

Tek seferlik değişiklik için config dosyasına hiç dokunmanız gerekmez:

```bash
automesh run parca.stp --set autonomy.max_attempts=10 --set quality.max_skewness_accept=0.93
```

---

## Çıktılar

Her çalışma `runs/<zaman>-<ad>/` altında:

| Dosya | İçerik |
|---|---|
| `report.md` | insan için rapor: geometri, seçilen parametreler, kalite tablosu, **deneme deneme ne oldu** |
| `result.json` | aynı bilgi makine okunur biçimde |
| `analysis.json` | ham geometri metrikleri |
| `plan-attempt-N.json` | her denemenin tam planı |
| `transcript.log` | Fluent'in söylediği her şey |
| `journal.py` | aynı işlemleri tekrar eden PyFluent scripti |
| `automesh.log` | agent günlüğü |
| `mesh/<ad>.msh.h5` | üretilen mesh |

`journal.py` özellikle işe yarar: `--dry-run` ile provayı yapıp, üretilen journal'ı
gerçek Fluent'te elle çalıştırabilir ya da başkasına verebilirsiniz.

---

## Claude danışmanı (opsiyonel)

Kural tabanı 21 bilinen hata örüntüsünü kapsıyor. Bunların dışında bir hata
gelirse agent varsayılan olarak genel çareye (kabalaştırma) düşer. `--advisor`
ile bu durumda Claude'a danışabilir:

```bash
export ANTHROPIC_API_KEY=sk-...
automesh run parca.stp --advisor
```

Güvenlik açısından önemli iki nokta koda gömülüdür:

- Model **yalnızca** tanımlı eylem sözlüğünden seçim yapabilir
  (`reduce_layers`, `improve_surface_mesh`, `switch_to_fault_tolerant`, …);
  listede olmayan hiçbir şey çalıştırılmaz.
- Danışman hiçbir zaman zorunlu değildir: paket yoksa, anahtar yoksa ya da
  çağrı başarısız olursa agent kural tabanıyla devam eder.

---

## Mimari

```
src/automesh/
  models.py          Ortak veri yapıları (her uzunluk metre cinsinden)
  config.py          Ayarlar + YAML/JSON yükleyici
  units.py           Birim dönüşümü (Fluent'e plan birimiyle gönderilir)
  geometry/
    base.py          Backend seçimi ve türetilmiş metriklerin normalizasyonu
    spaceclaim.py    SpaceClaim.exe'yi headless süren backend
    scripts/spaceclaim_analyze.py   SpaceClaim içinde koşan IronPython betiği
    step.py          Bağımlılıksız STEP okuyucu (bbox, yarıçap, yüzey sayıları)
    discrete.py      STL/OBJ (trimesh varsa onu, yoksa dahili okuyucuyu kullanır)
    fallback.py      Son çare + Fluent'in sınır kutusundan metrik üretimi
  planning/
    sizing.py        Geometri metrikleri -> MeshPlan (tüm heuristikler burada)
    local_sizing.py  Yüzey gruplarından face size kontrolleri
    proposals.py     Ölçüm tablosu + seçilebilir mesh kademeleri ve gerekçeleri
    adjust.py        Plan üzerinde yapılan tekil düzenlemeler
  fluent/
    driver.py        Soyut sürücü + transcript + çıktı sınıflandırma
    pyfluent_driver.py  Gerçek Fluent (sürüm toleranslı)
    mock_driver.py   Fluent simülatörü (dry-run ve testler)
    tui.py           TUI komut kataloğu (sürüm farkları tek dosyada)
    workflows.py     MeshPlan -> workflow görev argümanları
  quality/evaluate.py   check-quality çıktısını ayrıştır ve yargıla
  diagnostics/
    actions.py       İzin verilen eylem sözlüğü
    knowledge_base.py 21 kural, her biri kademeli çare merdiveniyle
    repair.py        Kalite odaklı onarım merdivenleri
    advisor.py       Opsiyonel Claude danışmanı
  orchestrator.py    Otonom döngü
  reporting.py       Markdown + JSON rapor
  doctor.py          Ortam teşhisi ve kalıcı yol ekleme
  cli.py             Komut satırı
  guiapp/
    state.py         Arayüz ayarları, doğrulama, Config'e çevrim (Tk'sız)
    runner.py        İşi arka planda koşturan thread + günlük kuyruğu (Tk'sız)
    app.py           Tkinter ana penceresi (ince katman)
    proposals_dialog.py  Ölçüm ve kademe seçme penceresi
```

Arayüzün mantığı bilerek Tkinter'dan ayrı tutuldu: `state.py` ve `runner.py`
pencere açmadan test edilebiliyor, `app.py` yalnızca widget yerleşimi.

Testler: `python -m pytest` (176 test, ANSYS ve ekran gerektirmez).

---

## Sınırlar ve bilinen kısıtlar

- **Yüzey gruplama yalnızca SpaceClaim backend'iyle çalışır.** STEP/STL
  okuyucular yüzey tipi, çevre ve yarıçap bilgisini bu ayrıntıda vermez; o
  dosyalarla global boyutlandırma devrede kalır.
- **`gap` ölçütü gövde geneli bir tahmindir** (`2·Hacim/Alan`), yüzey yüzey
  gerçek bir yakınlık (proximity) hesabı değildir. Gerçek yüzey-yüzey mesafesi
  SpaceClaim API'sinde pahalı bir sorgu; bu yüzden kaba ama ucuz olan
  kullanıldı. Fluent'in kendi proximity boyut fonksiyonu zaten devrede.
- **SpaceClaim backend'i Windows'a ve lisansa bağlıdır.** Yoksa STEP/STL
  backend'leri devreye girer; bunlar hacim ve alanı bilmez, bu yüzden hücre
  sayısı tahmini sınır kutusuna dayanır (rapor bunu açıkça yazar).
- **PyFluent API'si sürümler arasında değişir.** Sürücü bildiğimiz yazımları
  sırayla dener; tanınmayan TUI komutları hata değil "desteklenmiyor" olarak
  kaydedilir. Sizin sürümünüzde bir komut başka yerdeyse `fluent/tui.py`
  düzenlenecek tek dosyadır.
- **Fault-tolerant akış** görevleri varsayılan ayarlarıyla çalıştırılır; leakage
  eşiği ve capping seçimleri gibi ileri ayarlar henüz plan tarafından
  yönetilmiyor.
- **Named selection oluşturma SpaceClaim API'sine dayanır** ve sürümler arası
  farklılık gösterebilir. Betik birkaç yazımı sırayla dener; başarısız olursa
  grup raporda `oluşturulamadı` olarak işaretlenir ve o grup için kontrol
  üretilmez - çalışma durmaz, global boyutla devam eder.
- Hücre sayısı tahmini yaklaşık iki kat hata payına sahiptir; bütçe bunu hesaba
  katarak kabalaştırır. Öneri ekranındaki **bellek tahmini de kabadır**
  (~1.2 GB / milyon hücre) - doldurma tipine, prizma sayısına ve Fluent
  sürümüne göre değişir.
- Üretilen mesh **her zaman** raporla birlikte kontrol edilmelidir: agent kalite
  eşiklerini tutturur, mühendislik yeterliliğini değil.
