[English](README.md) | [Türkçe](README.tr.md)

<p align="center">
  <img src="docs/assets/cover-tr.svg" alt="Orkestra şefi ve uzman yardımcıları gösteren resimli sahne" width="100%">
</p>

# Ustam

Codex, Claude Code, OpenCode ve Antigravity için tek yerel merkez.

[![Lisans: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/downloads/)

**Uygulamaları, projeleri, şefi ve uzman ekiplerini tek yerel tarayıcı sayfasından seçin.**

Ekibi kurun, değişiklikleri kontrol edin ve geçmiş kullanımı görün. Şef sizinle konuşur ve işleri dağıtır; verilen işleri uzmanlar yapar. Bu görev ayrımı bir talimat kuralıdır, şefin araçlarını teknik olarak kilitlemez.

> [!NOTE]
> Bu bağımsız bir topluluk projesidir. OpenAI ile bağlantılı değildir ve OpenAI tarafından onaylanmamıştır.

## Güncel Türkçe tanıtım

[![Ustam Türkçe tanıtımını izleyin](docs/assets/ustam-trailer-poster-tr.png)](https://raw.githubusercontent.com/metapak/ustam-codex-orchestrator/main/docs/assets/ustam-trailer-tr-55s.mp4)

[Türkçe videoyu oynatın veya indirin (MP4, 55 saniye)](https://raw.githubusercontent.com/metapak/ustam-codex-orchestrator/main/docs/assets/ustam-trailer-tr-55s.mp4). Türkçe başlıklar, özgün müzik ve ses efektleriyle Ustam tanıtımı; sesli anlatım içermez. Ekranlardaki kullanım rakamları örnek veridir. Videoyu açmak için kapağa veya MP4 bağlantısına tıklayın.

## Antigravity

Antigravity; `inherit`, `flash` ve `pro` model katmanlarıyla proje ekibi kurulumu, korumalı kurulum/geri alma ve manuel İşler köprüsü sunar. Antigravity için gözetimsiz Jobs ve ölçülmüş kullanım desteklenmez; native salt okunur şef zorlaması ve model çalışması doğrulanmış değildir. [Destek ve sınırlar](docs/antigravity.tr.md).

## Dört adımda kurulum

**Güncel sürüm:** [1.0.0-beta.4](https://github.com/metapak/ustam-codex-orchestrator/releases/tag/ustam-v1.0.0-beta.4) · [Mac arm64 ZIP](https://github.com/metapak/ustam-codex-orchestrator/releases/download/ustam-v1.0.0-beta.4/ustam-1.0.0-beta.4-macos-arm64.zip) · [Sürüm notları](docs/release-ustam-v1.0.0-beta.4.tr.md). Mac paketi yerelde doğrulandı; herkese açık indirmede Gatekeeper kısıtları çözülmüş değildir. Aşağıdaki Windows/Linux bağlantıları önceki beta.2 sürümüdür.

1. **Ustam’ı indirin:** [1.0.0-beta.2 sürümünde](https://github.com/metapak/ustam-codex-orchestrator/releases/tag/ustam-v1.0.0-beta.2) Windows veya Linux için yerel uygulama ZIP’ini seçin ve tamamını çıkarın: [Windows](https://github.com/metapak/ustam-codex-orchestrator/releases/download/ustam-v1.0.0-beta.2/ustam-1.0.0-beta.2-windows-x86_64.zip) · [Linux](https://github.com/metapak/ustam-codex-orchestrator/releases/download/ustam-v1.0.0-beta.2/ustam-1.0.0-beta.2-linux-x86_64.zip).
2. **Açın:** Windows’ta **Ustam.exe**, Linux’ta **Ustam** dosyasını açın. Mac’te yerel kaynak derlemesi için **Ustam.app** dosyasını açın. Yerel paket Python içerir. Mac uygulaması tek başına taşınabilir; Windows/Linux’ta çıkarılan dosyaları birlikte tutun.
3. **Uygulamaları seçin:** Codex, Claude Code, OpenCode ve Antigravity arasından kullandıklarınızı seçin. Seçtiğiniz komut satırı araçları kurulu ve giriş yapılmış olmalıdır.
4. **Projeleri ekleyin:** Yerel tarayıcı sayfasında proje klasörlerini ekleyin; değişiklikleri kontrol edip uygulayın.

Yerel paket imzasızdır; Mac Gatekeeper indirmeyi engelleyebilir. Ayrıntılar ve ileri düzey kaynak/CLI kullanımı: [Ustam yerel merkezi](docs/ustam-hub.tr.md). Kaynak ZIP’ini çalıştırmak ayrıca Python 3.11+ gerektirir. Sağlayıcıların hesap ve model erişimini Ustam sağlamaz.

## Projeler ve orkestralar

Bir proje ekleyin, orada kullandığınız sağlayıcıyı seçin; ardından projenin kurulum alanında kayıtlı bir orkestra seçin veya yenisini oluşturun. Kaydetmeden önce modelleri ve görevleri inceleyin. Kayıtlı orkestra yeniden kullanılabilen ayardır; kaydetmek sağlayıcı işi başlatmaz. Projeye uygulanacak değişiklikleri yazmadan önce inceleyin.

Yapılandırma ve önizleme çevrimdışı çalışabilir. Gerçek işi başlatmak seçili sağlayıcının CLI’ını, girişini ve model erişimini gerektirir. Ekip ayarları ile çalışma zamanının gerçek yürütme sınırları ayrıdır; sayfa bu sınırları bildirir. Kullanım/geçmiş kaydedilmiş işleri anlatır; canlı orkestra animasyonu değildir.

## İleri düzey uyumluluk

Önceki sağlayıcıya özel konsol mevcut projeler için korunur. Ekran görüntüleri, on görev hazır ayarı ve eski başlatıcılar [eski konsol başvurusunda](docs/legacy-console.tr.md) ayrı anlatılır. Güncel kurulum için [birleşik uygulama rehberini](docs/ustam-hub.tr.md) izleyin.

## Lisans

[Apache-2.0](LICENSE) · [Atıf](NOTICE). Bu bağımsız topluluk projesi sağlayıcıların geliştiricileri tarafından onaylanmış değildir.
