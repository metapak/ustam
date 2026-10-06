[English](usage-and-local-eval.md) | [Türkçe](usage-and-local-eval.tr.md)

# Kullanım raporu ve isteğe bağlı yerel değerlendirme

```bash
python3 .codex/tools/usage_report.py
python3 .codex/tools/usage_report.py --json
```

Rapor aracı `~/.codex/sessions` klasörünü salt okunur tarar ve gözlenen `token_usage_record.usage` istek sayaçlarını toplar; eski kümülatif `token_count` sayaçlarında zaman sıralı fark kullanır. İstem veya kaynak içeriklerini yazdırmaz. Model, rol ve görev bilgisi token/oturum metadatasında varsa gösterilir (thread için dosya adı yedek kaynaktır). Bu sayılar yerel gözlemdir; kota yüzdesi, fatura veya maliyet tahmini değildir.

Ustam seçilen projenin kayıtlı oturum geçmişindeki kullanımı toplar; çalışma seçimi bir kayıtlı ana oturuma ve açıkça bağlı yardımcılarına daraltır. Proje filtresi, ilgisiz oturum dosyalarını elemeden önce tüm bağlam ve istek proje metadatasını indeksler; sonradan değişen proje bağlamı ve açık istek geçersiz kılmaları kapsama alınır. Tarama 30 saniyeyle sınırlıdır; bellekteki sınırlı önbellek dosya kimliği, boyutu ve değişiklik zamanı değişince yenilenir. Proje veya oturum klasörlerine önbellek yazılmaz. Kullanım yenilemenin kendi yükleme göstergesi, iptal seçeneği ve toplam 80 saniyelik tarayıcı süre sınırı vardır. Yalnız tamamlanmamış geçmiş indeksinin süre sınırı bir kez otomatik devamı tetikler; diğer hatalar kendiliğinden yeniden denenmez. Gözlenen 24 GB arşiv iki sınırlı denemede yaklaşık 49 saniye gerektirdi; sonraki yenileme yaklaşık 8 saniye sürdü. Hatalarda son başarılı ölçümler korunur ve yeniden denemeye izin verilir.

Ustam’ın dönem filtresinde Tüm zamanlar, Son 7 gün, Son 30 gün ve bitiş günü dahil özel tarih aralığı vardır. Yedi ve otuz gün, tarayıcının saat diliminde bugün dahil takvim günleridir; bitiş sınırı yaz/kış saati değişiklikleri de korunarak sonraki yerel gece yarısıdır. Tarihler önceden normalleştirilmiş hesap kayıtlarını filtreler; eski kümülatif sayaç başlangıçları ve istek kaydı önceliği korunur. Tarih, çalışma, ölçü veya grup değişikliği yüklenmiş raporu kullanır; yeniden kullanım toplamaz. Tarihsiz kayıtlar Tüm zamanlar içinde kalır, sınırlı aralıktan açık kayıt sayısıyla çıkarılır. Tarihli ayrıntısı olmayan kaynaklar için aralık toplamı bilinmiyor olarak gösterilir.

Tüm ekip, Şefler, Yardımcılar ve Sınıflandırılmamış kartları seçili proje, araç, çalışma ve dönemin seçilen ölçüsünü gösterir. Kartlar ayrıntı panelindeki grubu seçer; tekil kayıtlı kimlikler de seçilebilir. Önbellek ayrı gösterilir; Codex ve Claude toplamına ikinci kez eklenmez. OpenCode toplamı kaynağının gözlenen girdi, çıktı, akıl yürütme, önbellek okuma ve yazma bileşenlerini birer kez toplama kuralını korur. Sayılar aynı anda çalışan insanları değil, benzersiz kayıtlı oturum kimliklerini gösterir. Kategori için açık oturum ilişkisi veya tanınan görev metadatası gerekir; ad ve model görev belirlemez. Kategorisi belirlenemeyen kullanım Sınıflandırılmamış içinde kalır.

OpenCode mevcut proje kapsamlı, temizlenmiş dışa aktarım toplayıcısını kullanır; kaynağın son oturum sınırı (listelenen en fazla 12 oturum) korunur. Bunlar tüm hesap geçmişi değil, gözlenen kayıtlardır. Claude mevcut açık metrik/veri yok davranışını korur; Ustam telemetriyi etkinleştirmez veya ajan kimliği uydurmaz.

Yerel değerlendirme kendiliğinden çalışmaz. Örnek dosyayı proje içinde kopyalayıp açık `argv` listesini düzenleyin:

```bash
python3 .codex/tools/local_eval.py .codex/local-eval.json
python3 .codex/tools/ledger.py require-eval --label focused-tests
python3 .codex/tools/ledger.py ready-for-review
```

Araç kabuk kullanmaz, proje kökünde ve süre sınırıyla çalışır, sonucu Git tarafından yok sayılan kısa bir özete yazar. Varsayılan özet komut çıktısı yerine özet değerini saklar. Bu kontrol evrensel kalite ölçümü değildir. Bir görev çalışmasında seçilirse inceleme öncesinde başarılı olması gerekir.
Başarılı sonuç, HEAD ile ilgili izlenen ve yeni çalışma dosyalarının gizlilik koruyan parmak izine bağlanır. Sonraki bir değişiklik sonucu geçersiz kılar; sonuç dosyasının kendisi parmak izinden çıkarılır.

Ledger artık sabit deneme ve olay kimlikleri tutar. `interrupt`, `wait-user` ve `needs-repair` kısa kanıtı korur; `retry --evidence ...` tek sınırlı yeniden denemeye izin verir ve işi kayıtlı sorumlu role geri yollar.


[İstek hesaplama, filtreler ve yerel konsol](local-console.tr.md). `payload.usage` istek bazlıdır; yalnız eski `token_count.info.total_token_usage` kümülatif delta kullanır.

Kullanım, seçilen proje ve araç için doğrulanan yönetilen kurulumdan başlar. Özel başlangıç kaydı proje ve orkestra tercihlerinden ayrı tutulur. Sonraki başarılı orkestra değişiklikleri başlangıcı korur; kurulu olmayan duruma geri dönmek kurulum dönemini bitirir. Eski kurulumlarda sahipliği doğrulanan güncel bildirim kullanılır; önceki bildirim ancak güncel özet değeri doğrudan geri alma kaydıyla eşleşirse dikkate alınır. Kaynak doğrulanamazsa başlangıcın bilinmediği gösterilir ve eski geçmiş ölçülmüş sayılmaz. Kümülatif hesaplamadan sonra tarihi bilinmeyen, kurulum öncesi ve gelecekteki kayıtlar çıkarılır; takvim filtreleri bu sonucu yeniden kullanır. Araç kurulumu başarılı olup özel kayıt yazımı başarısız olursa bu durum açıkça bildirilir; geri alma yapıldığı iddia edilmez.
