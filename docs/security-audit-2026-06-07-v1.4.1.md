# ai-jury — Güvenlik Re-Audit Raporu (v1.4.1)

**Tarih:** 2026-06-07 (v1.4.1 release sonrası)
**Kapsam:** Tüm `src/ai_jury/` kod tabanı, `main` @ v1.4.1
**Bağlam:** Önceki üç denetimin bulguları #287–#310 olarak düzeltildi ve v1.3.0 / v1.4.0 / v1.4.1'de yayınlandı. Bu dördüncü tur: (a) tüm fix'lerin v1.4.1 kaynağında tuttuğunu doğrular, (b) kalan/yeni sorunları arar.
**Yöntem:** Saldırı yüzeyi 4 eksende paralel statik denetim + canlı stress/ReDoS timing ölçümü + alternatif loopback-kodlama fuzz'u. İki yeni Medium kaynak kodda ampirik teyit edildi.

---

## Yönetici Özeti

**#287–#310 fix'lerinin tamamı v1.4.1 kaynağında doğrulandı ve tutuyor.** #309/#310 stress-test'i geçti: alternatif loopback kodlamaları (`127.1`, octal, decimal, `::ffff:127.0.0.1`, `0.0.0.0`, `localhost.attacker.com`) hepsi **fail-closed**; unknown-vendor için her vendor/name kombinasyonu sandbox'lı.

Ancak bu turda **iki yeni Medium** çıktı — ikisi de **DoS/dayanıklılık** sınıfında (gizli bypass değil), ve ilginç biçimde biri #309'un düzelttiği hata sınıfının **başka bir call-site'ı**:

| Önem | Adet | Bulgular |
|------|------|----------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 2 | M-1 (injection.scan O(N²) DoS), M-2 (`_endpoint_issues` malformed-URL crash) |
| Low | 7 | L-1…L-7 (çoğu önceki turdan, henüz ele alınmadı) |
| Info | birkaç | aşağıda |

---

## CONFIRMED-FIXED — Önceki fix'ler tutuyor (v1.4.1)

- **#310** — `enforce_read_only` unknown-vendor için `--sandbox` enjekte ediyor (fail-closed); `vendor=="local"` fast-path **ilk** sırada. Her vendor/name kombinasyonu için subprocess adapter'ı sandbox bayrağı alıyor.
- **#309** — `list_local_models` `_endpoint_issues`'u try/except **içinde** çağırıyor; tüm init/list yolları bu seam'den geçiyor; malformed URL `[]` döndürüyor (crash değil). Alternatif loopback kodlamaları fail-closed (allowlist, denylist değil).
- **#287/#288/#292** — prompt stdin'den; adapter enforcement `--flag=value` dahil; `_is_sandboxed` vendor-aware.
- **#291** — non-http(s)/non-loopback hard-error; opener'da file/ftp/redirect handler yok.
- **#293** — F-6, F-7 (`_spawn` + `detect_capabilities` group-kill), F-8, F-9, F-10.
- **#295** — HMAC fail-closed, `0o600`+`O_EXCL`, `_dir_is_untrusted`, collision/smuggling-safe canonicalization, cache-key path traversal yok.
- **#296** — strict absolute-command + doctor resolved-path.
- **#301** — `neutralize_sentinels` iki-geçişli; tüm untrusted slot'larda; stress-test'te hiçbir fence forge edilemiyor.
- **#302** — basic-auth (boş username dahil), Azure `AccountKey`, GCP `private_key_id`.
- **#303** — L-2 (zero-width code-point set + URL-safe base64), L-3, L-4, L-5.

Ayrıca: `pickle`/`eval`/`exec`/`yaml`/`zipfile`/`tempfile.mktemp`/`os.system`/`shell=True` yok; path-traversal sink'i yok; TLS doğrulaması (urllib default) açık (`CERT_NONE` yok); redaction/secret-assignment regex'leri lineer (ReDoS yok).

---

## Orta Önem (yeni)

### M-1 (Medium) — `injection.scan` zero-width yolunda O(N²) — CPU-exhaustion DoS
- **Konum:** `injection.py` `scan` (her eşleşen char için bir `InjectionHit`) + `_line_of(text, index)` → `text.count("\n", 0, index)` (O(index)). N hit × O(N) = **quadratic**.
- **Doğrulandı (ampirik):** 50k zero-width char → 445ms; 100k → 1712ms; 200k → **6648ms** (uzunluk iki katına → süre ~dört katına; net quadratic). Regex'in kendisi hızlı (~19ms/200k); maliyet per-hit line-number hesabında.
- **Senaryo:** Tamamı zero-width char olan bir diff (ya da `--pr` ile PR gövdesi). `scan_inputs` her chunk'ın tam diff'inde **fan-out'tan önce** çalışır. Default 200k cap'te tek zero-width chunk ~6.6s; `max_bytes` artırıldığında / çok-chunk'lı diff'te quadratic katlanır → review host'unda CPU tüketimi. Aynı O(N²) şekli yüksek-hit'li herhangi bir pattern için geçerli; zero-width en kolay kitlesel üretilen.
- **Düzeltme:** Satır numaralarını tek geçişte hesapla (newline offset'lerini bir kez precompute et ya da sıralı match pozisyonları arasında artımlı bir cursor tut) → `scan` O(N). Opsiyonel: kind başına hit'i sınırla (ilk K + sayı) — bulgu zaten advisory.

### M-2 (Medium) — `_endpoint_issues` malformed URL'de ham `ValueError` ile çöküyor (config-validation crash)
- **Konum:** `config.py` `_endpoint_issues`'taki `urlsplit(endpoint)`; `validate_config` → `load_config(validate=True)` üzerinden erişilebilir.
- **Doğrulandı (ampirik):** `endpoint = "http://[::1"` → `urlsplit` `ValueError("Invalid IPv6 URL")` fırlatıyor; `validate_config` **ConfigError yerine ham ValueError ile çöküyor** (stack trace).
- **Açıklama:** #309 fix'i yalnızca `list_local_models` seam'ine uygulandı, `_endpoint_issues`'un kendisine değil. Aynı hata sınıfı config-validation yolunda açık.
- **Senaryo:** Attacker-controlled `jury.toml`'da `endpoint = "http://[::1"` → herhangi bir `jury config`/`--validate` yüklemesi temiz validation hatası yerine stack trace ile çöker (config-validation DoS / kötü failure modu — tam da tehdit modelinin hedef aldığı saldırgan-kontrollü yüzeyde). Malformed URL ayrıca SSRF allowlist'ine hiç ulaşmaz.
- **Düzeltme:** `_endpoint_issues`'ta `urlsplit`'i try/except'e al ve hatayı hard error'a çevir:
  ```python
  try:
      parsed = urlsplit(endpoint)
  except ValueError:
      errors.append(f"agent '{label}' endpoint '{endpoint}' is not a valid URL.")
      return errors, warnings
  ```
  Bu hem `validate_config`'i düzeltir hem de `list_local_models`'ın geniş `except`'ine olan bağımlılığı kaldırır.

---

## Düşük Önem (çoğu önceki turdan, v1.4.1'de henüz ele alınmadı)

- **L-1 — `prior_txt` debate eklentisi nötralize/fence edilmeden interpolate ediliyor** (`orchestrator.py`, çok-turlu debate). Re-confirmed, açık. **Düzeltme:** fence'le + `neutralize_sentinels`'ten geçir.
- **L-2 — Redaction hâlâ kaçıran formatlar:** SendGrid `SG.<id>.<secret>`, Twilio SID `AC[0-9a-f]{32}`, PyPI `pypi-…`, Slack webhook URL'leri, npm `npm_…`, bare 40-hex. **Düzeltme:** `secret_assignment`'tan önce anchored pattern'ler.
- **L-3 — `clear()` glob'u paylaşılan dizinde ilgisiz dosyaları siler** (`cache.py`). **Düzeltme:** yalnızca 64-hex stem'li dosyaları reap et / `entries/` alt-dizini.
- **L-4 — Atomik write symlink takip ediyor / same-PID concurrency-safe değil** (`cache.py`). **Düzeltme:** `tempfile.mkstemp(dir=self.dir)`.
- **L-5 — `tomllib.load` boyut sınırı yok** (`config.py`, `policy.py`). **Düzeltme:** okumadan önce stat-cap (cache'teki `_MAX_CACHE_BYTES` gibi).
- **L-6 — `_is_sandboxed` audit'i `=`-form sandbox'ı görmüyor** (`privilege.py`) → `--sandbox=read-only` config'inde `--strict` altında **false-positive** hard-fail. Güvenli yönde (under-warn değil over-warn) ama doğruluk hatası. **Düzeltme:** `_is_sandboxed`'a `=`-form parsing ekle (`_ensure_value_sandbox` ile tutarlı).
- **L-7 — init endpoint stdout'ta redakte edilmiyor** (`cli.py:592,594,608`). `http://user:pass@127.0.0.1` loopback gate'i geçer ve cleartext yansır. **Düzeltme:** `redaction.redact(endpoint)[0]`'dan geçir (doctor.py gibi).

---

## Bilgi / Doğrulanmış Güvenli

- **`compare_diff` `base`/`head` hex re-validation yok** (`github.py`) — pratikte `_MARKER_RE` `[0-9a-fA-F]{7,40}` ve gh head SHA ile sınırlı (`--` ayraçlı, shell yok). Gelecekteki bir caller doğrulanmamış değer geçerse `compare_diff` sınırında `^[0-9a-fA-F]{7,40}$` doğrulaması defense-in-depth olur. Info.
- **Bare-name PATH resolution default** — `JURY_REQUIRE_ABSOLUTE_COMMAND` opt-in; doctor resolved-path ile teyit edilebilir. Kabul edilmiş tasarım. Info.

---

## Önceliklendirilmiş Düzeltme Sırası

1. **M-1** — `injection.scan` line-number hesabını O(N)'e indir (DoS). Tek-geçiş newline offset.
2. **M-2** — `_endpoint_issues`'taki `urlsplit`'i sarmala → malformed URL'de ConfigError (config-validation crash).
3. **L-1** — `prior_txt` nötralizasyonu.
4. **L-6 / L-7** — `_is_sandboxed` `=`-form; init endpoint redaction.
5. **L-2…L-5** — redaction format'ları; `clear()` glob; atomik write `mkstemp`; `tomllib` size cap.

> Notlar: Bu rapor statik analiz + manuel doğrulama + canlı stress/ReDoS timing + loopback-fuzz'a dayanır; dinamik exploit doğrulaması yapılmamıştır. M-1 ve M-2 kaynak kodda ampirik teyit edildi. Önceki tüm fix'ler (#287–#310) v1.4.1 kaynağında elle teyit edildi.
