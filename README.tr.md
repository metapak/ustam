[English](README.md) | [Türkçe](README.tr.md)

<p align="center">
  <img src="docs/assets/cover-tr.svg" alt="Orkestra şefi ve uzman yardımcıları gösteren resimli sahne" width="100%">
</p>

# Ustam

Codex, Claude Code, OpenCode ve Antigravity için tek yerel merkez.

Bu, Ustam’ın bakımı sürdürülen tek deposudur. Güncel indirmeler ve sürümler buradadır; önceki sağlayıcıya özel depolar bu merkeze yönlendirir. [Depo geçişi](docs/repository-migration.tr.md).

[![Lisans: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/downloads/)

**Uygulamaları, projeleri, şefi ve uzman ekiplerini tek yerel tarayıcı sayfasından seçin.**

Ekibi kurun, değişiklikleri kontrol edin ve geçmiş kullanımı görün. Şef sizinle konuşur ve işleri dağıtır; verilen işleri uzmanlar yapar. Bu görev ayrımı bir talimat kuralıdır, şefin araçlarını teknik olarak kilitlemez.

> [!NOTE]
> Bu bağımsız bir topluluk projesidir. OpenAI ile bağlantılı değildir ve OpenAI tarafından onaylanmamıştır.

## Güncel Türkçe tanıtım

[![Ustam Türkçe tanıtımını izleyin](docs/assets/ustam-trailer-poster-tr.png)](https://raw.githubusercontent.com/metapak/ustam/main/docs/assets/ustam-trailer-tr-55s.mp4)

[Türkçe videoyu oynatın veya indirin (MP4, 55 saniye)](https://raw.githubusercontent.com/metapak/ustam/main/docs/assets/ustam-trailer-tr-55s.mp4). Türkçe başlıklar, özgün müzik ve ses efektleriyle Ustam tanıtımı; sesli anlatım içermez. Ekranlardaki kullanım rakamları örnek veridir. Videoyu açmak için kapağa veya MP4 bağlantısına tıklayın.

## Antigravity

Antigravity; `inherit`, `flash` ve `pro` model katmanlarıyla proje ekibi kurulumu, korumalı kurulum/geri alma ve manuel İşler köprüsü sunar. Antigravity için gözetimsiz Jobs ve ölçülmüş kullanım desteklenmez; native salt okunur şef zorlaması ve model çalışması doğrulanmış değildir. [Destek ve sınırlar](docs/antigravity.tr.md).

## İndirin ve başlayın

**[Mac arm64 için Ustam'ı indirin](https://github.com/metapak/ustam/releases/download/ustam-v1.0.0-beta.5/ustam-1.0.0-beta.5-macos-arm64.zip)** · [1.0.0-beta.5 sürümü ve hash listesi](https://github.com/metapak/ustam/releases/tag/ustam-v1.0.0-beta.5) · [Sürüm notları](docs/release-ustam-v1.0.0-beta.5.tr.md).

Mac paketi Python içerir ve yerelde kontrol edildi. Apple Developer ID imzası veya notarization yoktur; herkese açık indirmede Gatekeeper kabulü çözülmüş değildir. [Mac kurulum notları](docs/ustam-macos-local-install.tr.md).

Ustam'ı açıp **Proje → Araç → Ekip → Kur** adımlarını izleyin:

1. **Proje:** Yapılandırmak istediğiniz proje klasörünü ekleyin.
2. **Araç:** O proje için Codex, Claude Code, OpenCode veya Antigravity seçin.
3. **Ekip:** Hazır veya kayıtlı orkestrayı seçin; isterseniz şefi, yardımcıları ve desteklenen model seçeneklerini ayarlayın.
4. **Kur:** Değişiklikleri önizleyip projeye kurulumu onaylayın. Kurulum yapay zekâ işi başlatmaz.

Seçilen sağlayıcının CLI'ını ayrıca kurun; gerçek sağlayıcı işi için oturum ve model erişimi gerekir. Kaynak kullanımı Python 3.11+ gerektirir. [Önceki Windows/Linux beta.2 paketleri](https://github.com/metapak/ustam/releases/tag/ustam-v1.0.0-beta.2), Antigravity ve yeni özellikler olmadan önceki üç araçlı sürümü kapsar. Bunlar eski indirmelerdir, beta.5 derlemeleri değildir.

## Araç desteği

| Araç | Proje ekipleri ve modeller | Geri alma | Kaydedilmiş kullanım |
|---|---|---|---|
| Codex | Kurulu CLI ve erişilebilir model kataloğu | Yönetilen yapılandırma | Yerel kaydedilmiş kullanım |
| Claude Code | Kurulu CLI; modele özel effort seçenekleri | Yönetilen yapılandırma | Yerel kaydedilmiş kullanım |
| OpenCode | Kurulu CLI ve yapılandırılmış sağlayıcılar/modeller | Yok | Desteklenen yerel kayıtlar |
| Antigravity | `agy` 1.2.16+ ve yerel help kontrolü; `inherit`, `flash`, `pro` | Doğrulanan yönetilen dosyalar ve yedekler | Desteklenmiyor |

Manuel İşler köprüsü dört araçta da bulunur. Antigravity için gözetimsiz Jobs kapalıdır; hesap hakkı, native model çalışması ve native salt okunur şef zorlaması doğrulanmış değildir. Kullanılabilir modeller ve araçlar kurulu CLI'a ve hesaba bağlıdır. [Antigravity ayrıntıları](docs/antigravity.tr.md) · [İşler protokolü](docs/ustam-work-protocol.tr.md).

## Projeler ve orkestralar

Bir proje ekleyin, orada kullandığınız sağlayıcıyı seçin; ardından projenin kurulum alanında kayıtlı bir orkestra seçin veya yenisini oluşturun. Kaydetmeden önce modelleri ve görevleri inceleyin. Kayıtlı orkestra yeniden kullanılabilen ayardır; kaydetmek sağlayıcı işi başlatmaz. Projeye uygulanacak değişiklikleri yazmadan önce inceleyin.

Yapılandırma ve önizleme çevrimdışı çalışabilir. Gerçek işi başlatmak seçili sağlayıcının CLI’ını, girişini ve model erişimini gerektirir. Ekip ayarları ile çalışma zamanının gerçek yürütme sınırları ayrıdır; sayfa bu sınırları bildirir. Kullanım/geçmiş kaydedilmiş işleri anlatır; canlı orkestra animasyonu değildir.

## İleri düzey uyumluluk

Önceki sağlayıcıya özel konsol mevcut projeler için korunur. Ekran görüntüleri, on görev hazır ayarı ve eski başlatıcılar [eski konsol başvurusunda](docs/legacy-console.tr.md) ayrı anlatılır. Güncel kurulum için [birleşik uygulama rehberini](docs/ustam-hub.tr.md) izleyin.

## Lisans

[Apache-2.0](LICENSE) · [Atıf](NOTICE). Bu bağımsız topluluk projesi sağlayıcıların geliştiricileri tarafından onaylanmış değildir.
