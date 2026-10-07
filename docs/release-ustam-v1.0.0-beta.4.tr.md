# Ustam 1.0.0-beta.4

[English](release-ustam-v1.0.0-beta.4.md) · [Türkçe](release-ustam-v1.0.0-beta.4.tr.md)

Antigravity, aynı yerel Ustam merkezinde Codex, Claude Code ve OpenCode'a katılıyor. Bu ön sürüm projeye özel Antigravity ekip kurulumu, korumalı kurulum/geri alma ve manuel İşler köprüsü ekler. Mevcut Türkçe tanıtım ve kapak korunur.

## Antigravity ekipleri ve modelleri

Kayıtlı projeler için Antigravity seçip hazır ekip, kayıtlı özel ekip veya kendi yardımcı/model ayarlarınızı kullanabilirsiniz. Desteklenen native model katmanları `inherit`, `flash` ve `pro` seçenekleridir; görev başına effort ayarı yoktur. Kaydedilen eşzamanlılık değeri bir, Ustam'ın uyumluluk politikasıdır; native eşzamanlılık sınırının veya salt okunur şefin kanıtı değildir.

Kurulum desteklenen `agy` CLI'ın 1.2.16 veya daha yeni sürümünü ve başarılı yerel help yetenek kontrolünü gerektirir. Eksik, eski, bilinmeyen veya uyumsuz CLI, proje dosyalarına yazılmadan nedenini bildirir. CLI tespiti hesap hakkını veya model çalışmasını doğrulamaz. Eski IDE ayrıca doğrulanmamış olarak gösterilir. Kurulum oturum açmaz, Antigravity kurmaz, global ayarları değiştirmez ve yapay zekâ işi başlatmaz.

## Korumalı proje kurulumu ve geri alma

Önizleme ekibi, mevcut dosya baytlarını, proje dizini kimliğini ve beş dakikalık süreyi bağlar. Kimlik uygula öncesinde ve geri almada yeniden kontrol edilir. Kopyalanmış bir kurulum, başka bir dizine yazma yetkisi vermez.

Kurucu sınırlı bir sağlayıcı ad alanını yönetir; mevcut AGENTS.md/GEMINI.md dosyalarını ve diğer sağlayıcıları korur, sahiplenilmemiş çakışmaları reddeder ve yazmadan önce CLI'ı kontrol eder. Kalıcı önce/sonra yedekleri ve tamamlanan adımların makbuzları ön kontrol, yedekleme, uygulama ve doğrulama aşamalarını bildirir. Geri alma, önceki kurulumu döndürmeden veya ilk kurulumu kaldırmadan önce yedek bütünlüğünü ve mevcut beklenen baytları doğrular.

Symlink yollar, değişmiş yönetilen dosyalar ve eksik kurulumlar reddedilir. Uygulama ve geri alma beklenen baytları karşılaştırır. Eşzamanlı kullanıcı düzenlemeleri korunur ve bildirilir; yedekler inceleme için tutulur. Geri alma ancak başlangıçtaki bütün bayt durumları yeniden gözlenirse tamamlanmış sayılır. Korunan çakışmayla biten hata kurtarma incelemesi gerektirir.

## Manuel İşler köprüsü

Antigravity mevcut sınırlı İşler akışını kullanır: kapsam ve başarı ölçütleri, uygulama öncesinde ayrı kabul testi yazarı, izole Git worktree, dondurulan aday, denetleyicinin gözlediği kontroller, bağımsız salt okunur inceleme, entegrasyon yeniden kontrolü ve açık insan onaylı uygulama. Proje köprüsü sağlayıcıyı, kayıtlı projeyi ve güvenilir çalışma zamanını sabitler. Native ayrıştırıcı iş onaylayamaz, yanıtlayamaz veya uygulayamaz. Kaynak Python çağrısı izole kip (`-I`) gerektirir.

Kurallar ve görev talimatları prompt geleneğidir. Native araç izinlerini, güvenlik sandbox'ını veya salt okunur şefi zorlamaz. CLI çıkış kodu veya yardımcının başarı bildirimi gözlenen kontrollerin ve insan onayının yerine geçmez. Native model çalışması doğrulanmış değildir.

## Desteklenmeyen alanlar

Antigravity için gözetimsiz Jobs kapalıdır. Kullanım boş kayıtlar ve toplamlarla desteklenmiyor olarak bildirilir; bu ölçülmüş sıfır kullanım değildir. Ustam IDE loglarından, keyfi transcript dosyalarından veya global kotadan kullanım çıkarmaz; global statusline toplayıcısı kurmaz. Motor mevcut üç herkese açık depoya dahildir; dördüncü herkese açık depo yoktur.

## İndirmeler ve doğrulama

Yeni yerel paket yalnız macOS arm64 içindir. Windows/Linux yerel indirmeleri önceki beta.2 sürümü olarak kalır. Kaynak arşivleri yayımlanan beta.4 commit'ine bağlıdır. Mac arm64 paketi yerelde derlendi ve kontrol edildi: üç deponun test takımları ve doğrulayıcıları, yerel paketleme/yaşam döngüsü kontrolleri ve dört sağlayıcının manuel İşler köprüsü geçti. Her deponun test takımında yedi test atlandı. Bu, herkese açık indirmenin kabulünü veya native model çalışmasını kanıtlamaz. Antigravity motoru dahili kaynaktaki `80613c6c7c11d2a3398b3499a402f328575f0fe0` commit'ine sabitlenir; sürüm hash listesi indirilebilir dosyaları aynen tanımlar.

Apple Developer ID imzası/notarization ve herkese açık indirmede Gatekeeper kabulü çözülmüş değildir. Gerçek Windows çalışma zamanı QA veya ücretli model çağrısı iddia edilmez. Yerel testler ve işlem makbuzları her ortamın veya kesilen dosya sistemi işleminin başarılı olacağını garanti etmez.
