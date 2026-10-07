# Ustam 1.0.0-beta.5

[English](release-ustam-v1.0.0-beta.5.md) · [Türkçe](release-ustam-v1.0.0-beta.5.tr.md)

Ustam'ın artık bakımı sürdürülen tek deposu var: [metapak/ustam](https://github.com/metapak/ustam). Codex, Claude Code, OpenCode ve Antigravity tek merkezi, güncel indirmeyi ve sürüm sayfasını paylaşır. Mevcut Codex deposu geçmişi, issue'ları, yıldızları ve önceki sürümleri korumak için yeniden adlandırılır. Önceki Claude/OpenCode depoları geçmişlerini geçiş açıklaması ve arşiv durumuyla korur.

## Değişiklikler

- Dört motor bu depodaki yalnız kaynak içeren 144 çalışma zamanı dosyasından paketlenir; özgün lisanslar, atıflar ve kaynak kökeni korunur. Bütün motorlar aynı değişmez ana kaynak anlık görüntüsüne sabitlenir. Depodaki dosyaları derlemek kardeş checkout veya GitHub'dan harici motor indirmesi gerektirmez.
- Projede araç seçimi dört sağlayıcıyı sunar, global Codex'e özel tercih ayrı kalır. Hazır, özel ve kayıtlı orkestralar Proje → Araç → Ekip → Kur akışını izler.
- Claude model/effort seçenekleri her gerçek modelin desteklediği değerlere uyar. Desteklemeyen Haiku ve çözümlenemeyen alias modellerde effort denetimi gizlenir ve değer boş kalır. Şef max effort sunmaz; yardımcılar yalnız model destekliyorsa sunar. Geçersiz eski birleşimler kaydetme/önizleme öncesi açık düzeltme ister.
- Son onaylanan araç seçici ve model/effort düzeltmeleri dahildir. Mevcut Türkçe tanıtım, kapak ve bütün önceki sürümler korunur.

## Kurulum ve destek

Yeni yerel paket yalnız macOS arm64 içindir. Yerel çalışma zamanını ve sabit motorları içerir; sağlayıcı CLI'ları, hesapları ve model erişimi ayrı gerekliliklerdir. Windows/Linux beta.2 indirmeleri eski üç araçlı sürümün geçmiş paketleridir; Antigravity veya yeni özellikleri içermezler.

Mac Developer ID imzası/notarization ve herkese açık indirmede Gatekeeper kabulü çözülmüş değildir. Bu sürüm için Windows çalışma zamanı kabulü veya ücretli model çalışması iddia edilmez. Tam kaynak/yerel paket doğrulaması ve ek hash'leri son yayın adayıyla eşleşmelidir; yerel doğrulama belgelenen ortamlarla sınırlıdır.

Antigravity manuel İşler köprüsünü ve korumalı proje kurulumunu/geri almayı korur. `agy` 1.2.16+ ve yerel help kontrolü gerektirir, inherit/flash/pro model katmanlarını destekler; ölçülmüş kullanım veya gözetimsiz Jobs yoktur. Native salt okunur şef zorlaması ve hesap/model çalışması doğrulanmış değildir. Depo adının değişmesi mevcut yerel durumu veya proje yapılandırmasını kendiliğinden taşımaz ya da üzerine yazmaz.

## Depo geçişi

Önceki Codex adresi yeniden adlandırılan depoya yönlenir; bu eski adı yeniden oluşturmayın. Arşivlenen Claude/OpenCode README bağlantıları Ustam'a getirir. Önceki sürüm ekleri erişilebilir kalır. Beta.5 ön sürümdür: daha eski kararlı sürüme açılabilen genel latest-release adresi yerine açık sürüm/indirme bağlantısını kullanın. [Geçiş ayrıntıları](repository-migration.tr.md).
