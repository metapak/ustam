# Mac’te yerel kaynak kurulumu

Bu yol, Apple Developer hesabı olmadan Ustam’ı Mac’inizde derler. Karantinayı kaldırmaz, Gatekeeper’ı kapatmaz ve indirilen uygulamaya Apple güven onayı sağlamaz. Script Editor açılış yolu için gerçek kullanıcı Run kabul testi hâlâ bekleniyor.

1. Deponun kaynak ZIP’ini indirip çıkarın. `scripts`, `launchers` ve `ustam` klasörlerini birlikte tutun.
2. `launchers/Install Ustam.applescript` dosyasını Apple Script Editor’da **kaynak** olarak açın. Kaynağı okuyun, ardından **Run / Çalıştır** düğmesine kendiniz basın. Bu yönlendirmeli yol için Terminal komutu gerekmez.
3. Çıkarılmış kaynak klasörünü seçin. Python 3.11+ yoksa resmi Python indirme bağlantısını seçin, kurun ve kaynağı tekrar çalıştırın. Sabitlenmiş derleme aracını kurmak için internet gerekir.
4. Kurucu ayrı derleme ortamını oluştururken, Ustam’ı derlerken, her yerel ikili/framework dosyasını doğrularken ve çıkarılmış ZIP’i kontrol ederken bekleyin. İlerleme mesajı aşamaya göre değişir. Her derleme aşaması bir saatle sınırlıdır; Stop, kurucuya ait derlemeyi iptal eder. Yardımcı beklenmedik biçimde durursa sonraki durum sorgusu hata ve günlük yolunu gösterir, kimliği doğrulanan derleme grubunu durdurur. Eski ilerleme mesajı sonsuza kadar gösterilmez.
5. Doğrulamadan sonra kurucu **Ustam.app** dosyasını `~/Applications` içine yerleştirir. Tanınan mevcut Ustam kopyası değiştirilmeden önce yedeklenir; ilgisiz uygulamanın üzerine yazılmaz. Güncellemeden önce çalışan Ustam’ı kapatın. Kayıtlı Ustam verisi ve sağlayıcı/proje ayarları korunur. Sağlayıcı görevi başlatılmaz.
6. **Ustam aç** seçin. Mac’te hub hazır olana kadar kısa “Ustam hazırlanıyor” bildirimi görünür. Tarayıcı otomatik açılır. Açılamazsa bildirim yerel adresi ve **Tarayıcıda aç** düğmesini gösterir. Başlangıç başarısız olursa sessizce kaybolmak yerine hata bildirilir.

Uygulamayı kaldırmak, kayıtlı projeleri veya ayarlarını kaldırmaz. Bir Mac’te yerel derlemenin çalışması, karantinalı herkese açık indirmelerin açılacağını kanıtlamaz. macOS açılışı engeller ve onay yolu sunmazsa durup sonucu bildirin; korumaları aşmayın.
