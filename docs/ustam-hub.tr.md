# Ustam yerel hub

Ustam’ı aç → Uygulamaları seç → Projeleri ekle → Orkestra seç → Projeye kur.

Ustam, Codex, Claude Code, OpenCode ve Antigravity için tek yerel tarayıcı hub’ıdır. Kullanacağınız uygulamaları seçin, proje klasörlerini ekleyin; projenin ayar alanında orkestra seçin veya oluşturun. Orkestra kaydetmek yeniden kullanılabilir ayarları kaydeder; sağlayıcı görevi başlatmaz. **Projeye kur** hedef dosyaları kontrol eder ve çakışmasız projelere tek işlemde kurar. Sonuçlar her proje için gösterilir. Çakışmalar ve hatalar görünür kalır; yalnızca doğrulanmış başarılı kurulumlar mevcut oturumda kurulu rozeti alır. Kurulum sohbete mesaj göndermez ve sağlayıcı görevi başlatmaz. Ana kurulum ekranında iş açıklaması veya çalışma planı istenmez.

## İş türüne göre hazır ekipler

Hazır ekipten önce iş türünü seçin. Her önerilen ekip keşif, uygulama, doğrulama ve inceleme rollerini içerir; bunlar şefe ek yardımcılardır.

| İş | Ek uzman | Codex / Claude yardımcı | OpenCode yardımcı |
| --- | --- | --- | --- |
| Hata düzeltme | Hata analisti | 5 | 4 |
| Özellik ekleme | — | 4 | 4 |
| Web | QA operatörü | 5 | 4 |
| Oyun | Araştırmacı, QA operatörü | 6 | 4 |
| Backend | Danışman | 5 | 4 |
| Araştırma | Araştırmacı | 5 | 4 |
| Veri | Araştırmacı | 5 | 4 |
| Güvenlik | Araştırmacı, danışman | 6 | 4 |

Öneriler seçilen sağlayıcının kataloğundaki desteklenen rolleri ve modelleri kullanır. Desteklenmeyen uzman sayıyı azaltabilir; eksik modeller kaydetmeden önce tamamlanmalıdır. Ekibi düzenleyebilirsiniz; iş türünü değiştirmek düzenlemelerinizi sessizce ezmez. Sayılar hazırlanan ayarları anlatır; her yardımcının aynı anda çalışacağını garanti etmez. OpenCode şu anda desteklenen dört yürütme rolünü görevlendirir; hub yardımcıları sırayla çalıştırır.

## Antigravity

Proje kurulumu `inherit`, `flash` ve `pro` model katmanlarını, korumalı kurulum/geri alma makbuzlarını ve manuel İşler köprüsünü destekler. Antigravity Jobs ve ölçülmüş kullanım desteklenmez. Native model çalışması ve salt okunur şef zorlaması doğrulanmış değildir. [Antigravity desteğine bakın](antigravity.tr.md).

## Yerel paket kurulumu

Windows/Linux: beta.2 sürümündeki yerel ZIP’i tamamen çıkarın, **Ustam.exe** veya **Ustam** açın. Çıkarılan dosyaları birlikte tutun. Çalışma zamanı pakete dahildir; yerel paket için ayrıca Python gerekmez. Yerel derlenen Mac uygulaması tek başına taşınabilir.

**Herkese açık Mac indirmesi hazır değil:** yayımlanmış beta.1/beta.2 Mac uygulamalarında ilk açılış sorunu sürüyor. Beta.3 kaynağı ve Mac arm64 paketi paket bütünlüğünü düzeltir; bu, indirilen uygulamanın açılabildiğini kanıtlamaz. Geliştirme Mac’inde yerel kaynak derlemesi açıldı ve kullanıcı sayfayı gördüğünü doğruladı. Bu kopyada karantina özniteliği doğal olarak yoktu; güvenlik korumaları değiştirilmedi. Karantinalı indirme test kopyası engellenmeye devam etti ve macOS Yine de Aç seçeneğini sunmadı. Karantinayı kaldırmayın, Gatekeeper’ı kapatmayın.

Apple Developer ID imzası ve noter onayı mevcut değil. Ad-hoc kod mührü paket bütünlüğünü doğrular; Apple’ın güvenilir dağıtım onayını sağlamaz. Yönetilen bilgisayarlarda ek kısıtlamalar olabilir.

## Mac’te yerel kaynak kurulumu

Bu deponun kaynak ZIP’ini indirip çıkarın. Kaynak derlemesi Python 3.11+ ve sabitlenmiş PyInstaller aracını kurmak için internet gerektirir. Sağlayıcı CLI’leri ayrıca kurulur.

Yönlendirmeli kaynak kurucusu **kabul testi aşamasında**. İndirilmiş kaynağın kullanıcı tarafından açılma yolu henüz doğrulanmadı. Test etmeyi seçerseniz `launchers/Install Ustam.applescript` dosyasını Apple Script Editor’da kaynak olarak açın, kaynağı okuyun ve **Run / Çalıştır** düğmesine kendiniz basın. `scripts`, `launchers`, `ustam` içeren çıkarılmış kaynak klasörünü seçin. Kurucu bilinen Python kurulum yollarını kontrol eder; Python yoksa resmi indirme sayfasını sunar. Ayrı geçici ortamda derler, sabitlenmiş motorları ve Mac’teki bütün yerel kodu doğrular, `~/Applications/Ustam.app` konumuna kurar. Mevcut kopya yalnız Ustam kimliğiyle tanınırsa değiştirilir ve yedeği saklanır. Kurmadan önce çalışan Ustam’ı kapatın. Kayıtlı Ustam verisi ve proje ayarları değiştirilmez. Değiştirme öncesi iptal, önceki kurulumu korur. Derleme günlükleri sorun incelemek için geçici kurucu klasöründe kalır.

İleri düzey manuel kaynak derlemesi için çıkarılmış depo içinde:

```sh
python3 -m venv .venv-build
.venv-build/bin/python -m pip install pyinstaller==6.22.3
.venv-build/bin/python scripts/build_ustam_app.py --skip-engine-build
```

Oluşan `dist/ustam-*/Ustam.app` dosyasını açın. Depoya dahil değişmez motor dosyaları doğrulanır; bu derleme için kardeş depolar gerekmez. Kaynağı doğrudan çalıştırmak için Python 3.11+ ile `python3 -m ustam` kullanın. Her hedef işletim sistemi ve mimari için ayrı derleme gerekir.

## Hesaplar, kayıtlar ve kaldırma

Ayarlar ve önizlemeler çevrimdışı çalışabilir. İş başlatmak seçilen sağlayıcının kurulu CLI’sini, oturumunu ve model erişimini gerektirir; API modelleri sağlayıcı ücreti doğurabilir. Ustam hesap, kimlik bilgisi veya fatura bakiyesi sağlamaz. Geçmiş yerel işleri kaydeder; orkestra animasyonu görseldir.

Tercihler, kayıtlı projeler ve orkestralar Ustam’ın kullanıcı veri klasöründe tutulur. Projeye yalnız açık uygulama işlemiyle ayar yazılır. Projeyi kaldırmak Ustam kaydını kaldırır; klasörünü silmez. Geri yükleme ayrıdır ve yalnız desteklenen yönetilen ayarlara uygulanır; OpenCode geri yüklemesi yoktur. Hub’da proje kurulumunu kaldırma işlemi yoktur. Ustam.app silmek proje ayarlarını kaldırmaz veya kayıtlı Ustam verisini silmez.

Sağlayıcı çalışma sınırları ekip ayarlarından ayrı gösterilir. Her sağlayıcı, tam dosya listesi ve SHA-256 doğrulaması olan paketlenmiş sabit motor kullanır. Yerel `ustam/VERSION`, eski motor sürümlerinden ayrıdır. Arayüz başlatıcısı konsol worker kullanır; dondurulmuş adaptörlerin JSON stdio iletişimi korunur. Windows worker’ları gizlidir. Tarayıcı otomatik açılamazsa Ustam elle açılacak yerel adresi gösterir; sunucu çalışmaya devam eder.

[Eski sağlayıcı konsolu](legacy-console.tr.md), ekran görüntüleri, on görev şablonları ve önce proje seçen başlatıcılar ileri düzey uyumluluk referansıdır. Güncel birleşik hub’dan ayrıdır.

[Beta.4 sürüm notları](release-ustam-v1.0.0-beta.4.tr.md) · [Mac arm64 ZIP](https://github.com/metapak/ustam-codex-orchestrator/releases/download/ustam-v1.0.0-beta.4/ustam-1.0.0-beta.4-macos-arm64.zip). Windows/Linux yerel indirmeleri beta.2 olarak kalır.
