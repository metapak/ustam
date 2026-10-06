# Ustam 1.0.0-beta.3

[English](release-ustam-v1.0.0-beta.3.md) · [Türkçe](release-ustam-v1.0.0-beta.3.tr.md)

Bu ön sürüm ortak yerel merkezi ve üç sağlayıcı dağıtımını günceller. Mac arm64 paketi doğrulanan güncel arayüzü ve sabitlenmiş sağlayıcı motorlarını içerir. 55 saniyelik Türkçe tanıtım README üzerinden ve sürüm eklerinden açılabilir.

## İş protokolü

- Uygulamadan önce kapsamı, dosya sahipliğini, kabul ölçütlerini ve izinli komutları kaydeden sınırlı bir iş sözleşmesi oluşturulur.
- Ayrı kabul testi yazarı iyi ve kötü örnekler sağlar. Yerel denetleyici zayıf test paketlerini reddeder ve uygulama başlamadan her ölçütün kapsanmasını ister.
- Hazırlık, staged/unstaged ve yok sayılmayan untracked bağlamı koruyarak ayrı bir Git worktree oluşturur. Adayı dondurma; içeriği, dosya kiplerini, index/HEAD’i, sözleşmeyi ve test paketini bağlar. Eşzamanlı sahiplik çakışmaları reddedilir.
- Sınırlı sorular ilgili işler için tek yanıtı paylaşabilir. Sözleşme değişirse eski yanıt ve onaylar geçersiz olur. Maddi kapsam değişiklikleri sözleşme sürümüne bağlı insan onayı ister.
- Doğrulama ve entegrasyon kontrolleri gözlenen yerel komutları ve belirli sonuç veren dosya kontrollerini dondurulan adaya ve güncel proje bağlamına bağlar. Yardımcıların bildirdiği sonuçlar doğrulanmış sayılmaz. Kanıta bağlı sonuç kartları; aday, test veya çalışma ortamı değiştiğinde geçersiz olur.
- Değişiklikleri uygulamak İşler ekranında açık onay ister. Sürümlü kayıtlar, işlem kimlikleri, iptal/devam ve saklanan adaylar kurtarmayı destekler; kesilen işlemler kendiliğinden yeniden yürütülmez.

Bu, sağlayıcının kurulu iş protokolü aracıyla çağrılan manuel yerel köprüdür. Otomatik sağlayıcı hook’u veya ücretli denetleyici çağrısı yoktur. Yerleşik kontroller `contains` ve `json_equals` destekler; tarayıcı davranışı, performans ve araştırmanın anlamsal niteliği kabul kontrolü olarak desteklenmez. Görev adları bildirilen kökendir; imzalı kimlik kanıtı değildir. Worktree güvenlik sandbox’ı değildir.

## Veri koruma ve sağlayıcı düzeltmeleri

Sahiplik denetimi eşdeğer kanonik yolları ve çakışan etkin yazımları reddeder. Uygulama/geri alma dosya kimliğini kontrol ederek eşzamanlı dış değişiklikleri korur; güvenli geri alma mümkün değilse kurtarma verisi saklanır. Claude geri yükleme önceki kurulum manifestinin kimliğini aynen korur. OpenCode görev/araç izinleri ve paketlenen sağlayıcı talimatları sınırlı iş akışıyla hizalanır. Yapılandırılmış Codex model yönlendirmesi kullanılır. Motorlar commit ve tam dosya hash’leriyle sabitlenir; hesap/model erişimi koşulludur.

## Kullanım ve arayüz

Proje seçimi; hazır, özel ve kayıtlı ekiplerle yardımcı/model ayarlarını aynı kurulum alanında birleştirir. Codex kullanım taraması sınırlandırıldı; olay sırası düzenlendi, ekleme anlık görüntüleri ve değişmeyen dosyaları yeniden kullanma ile önbellek geliştirildi. Önbellek uç durumları ve muhasebe bağımlılıkları kapsandı. Proje seçimi, bekleyen durumlar, dil yönetimi, kullanım yükleme ve İşler aktarımı iyileştirildi. Arayüzdeki metin simgeleri yerine mevcut metin rengini kullanan 14 erişilebilir SVG ikon eklendi. Türkçe tanıtım özgün müzik ve ses efektleri içerir, sesli anlatım içermez; kullanım rakamları örnek veridir.

## Mac paketleme ve açılış

Paket bütünlüğü kontrolleri yerel ikili dosyaları/framework’leri ve zorunlu arayüz modüllerini kapsar. Kaynak kurucusu tanınan mevcut uygulamayı yedekler, proje ayarlarını ve durumu korur. Sahip olunan derleme süreçlerinin temizlenmesi, kesilen derlemelerin bildirilmesi, yeniden açılış ve başlangıç/tarayıcı bildirimleri iyileştirildi. Script Editor üzerinden kaynak kurulumu hâlâ kullanıcının gerçek Run kabul testini bekliyor.

## İndirmeler ve sınırlar

Yeni yerel paket **macOS arm64** içindir. Windows/Linux beta.2 indirmeleri önceki sürümdür; burada beta.3 Windows/Linux ikili paketi yayımlanmıyor. GitHub kaynak arşivleri bu sürümün güncel kaynağını içerir.

Mac paketi bir geliştirme Mac’inde yerelde derlendi, kuruldu ve açıldı; arayüz, yaşam döngüsü, paketleme ve koruma kontrolleri yapıldı. Bu, karantinaya alınmış herkese açık indirmenin açılacağını kanıtlamaz. Apple Developer ID imzası/notarization yoktur; Gatekeeper kısıtları çözülmüş değildir. Bu sürüm için Windows çalışma zamanı ve ücretli model çağrıları test edilmedi. Protokol kurtarılabilirdir; dosya sistemleri arasında atomik işlem değildir. Kesilen uygulama kısmi kullanıcı onaylı değişiklik bırakabilir ve inceleme gerektirir. Hatasız veya her ortamda güvenilir kurulum iddiası yoktur.
