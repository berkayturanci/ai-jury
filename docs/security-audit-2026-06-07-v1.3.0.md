# ai-jury — Güvenlik Re-Audit Raporu (v1.3.0)

**Tarih:** 2026-06-07 (v1.3.0 release sonrası)
**Kapsam:** Tüm `src/ai_jury/` kod tabanı, `main` @ v1.3.0 (`c20e03d`)
**Bağlam:** İlk denetimin ([docs/security-audit-2026-06-07.md](security-audit-2026-06-07.md)) bulguları #287–#296 olarak düzeltilip merge edildi ve v1.3.0 yayınlandı. Bu re-audit iki şeyi yapar: (a) önceki fix'lerin gerçekten tuttuğunu kaynak kodda doğrular, (b) kalan/yeni sorunları arar.
**Yöntem:** Saldırı yüzeyi 4 eksende paralel statik denetim + redaction/injection regex'lerinde canlı ReDoS timing ölçümü.
**Tehdit modeli:** Saldırgan diff metnini, diff dosya adlarını, PR/issue gövdesini, PR yorumlarını ve (kötü senaryoda) repoya işlenmiş `jury.toml`'u kontrol eder.

---

## Yönetici Özeti

**Önceki tur (#287–#296) tüm fix'leri kaynak kodda doğrulandı ve tutuyor.** Bu re-audit'te **Critical veya High bulgu yok**. Saldırı yüzeyinin temeli sağlam: komut enjeksiyonu yok, `shell=True`/`eval`/`pickle`/`yaml.load`/zip-slip yok, TLS doğrulaması kapatılmıyor, path traversal sink'i yok, cache HMAC tasarımında collision/smuggling açığı yok, redaction ReDoS-safe (lineer).

Kalan iş çoğunlukla **derinlemesine-savunma tamlığı**: en dikkat çekeni, #288'in "read-only garantisi koşulsuz" hedefinin **bilinmeyen-vendor yolunda** delinmesi.

| Önem | Adet | Bulgular |
|------|------|----------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 3 | M-1 (unknown-vendor sandbox bypass), M-2 (sentinel fence escape), M-3 (redaction format boşlukları) |
| Low | 5 | L-1…L-5 |
| Info | birkaç | aşağıda |

---

## CONFIRMED-FIXED — Önceki fix'ler tutuyor

Kaynak kodda doğrulandı:

- **#288** — `privilege.enforce_read_only` her CLI adapter'ın `build_argv`'sinde çağrılıyor (`adapters.py` Claude/Codex/Agy); sandbox/`--disallowed-tools` garanti, `--flag=value` formu da merge ediliyor. Config sandbox'ı **bilinen vendor'larda** strip edemiyor.
- **#292** — `_is_sandboxed` vendor-aware; non-agy vendor'da çıplak `--sandbox` artık yanlış güvence vermiyor.
- **#287** — prompt'lar üç gerçek adapter'da da stdin'den; argv'de prompt metni yok.
- **#291** — `validate_config` non-http(s) şemayı ve (opt-in olmadan) non-loopback host'u hard-error yapıyor; opener'da file/ftp **ve redirect** handler yok → 302→IMDS takip edilmiyor, `file://`/`ftp://` `URLError` fırlatıyor.
- **#293** — F-6 (relative command reddi), F-7 (`_spawn` process-group kill), F-8 (error body redaction), F-9 (16 MiB cap + `errors="replace"`), F-10 (cache key embed) hepsi yerinde.
- **#295** — cache entry'lerde per-user HMAC-SHA256; `_hmac_key` `0o600` + `O_EXCL` (race-safe); doğrulama **fail-closed** (key yoksa load→miss, store→yazma yok); `_dir_is_untrusted` world/group-writable dizini reddediyor. Canonicalization (`_canonical`, `mac` hariç + `sort_keys`) sağlam — collision/field-smuggling yok.
- **#296** — `JURY_REQUIRE_ABSOLUTE_COMMAND` strict mode + doctor resolved-path.
- **#289/#290** — redaction `aws_secret_access_key=`/`password=` + Slack/Google/Stripe/PAT/JWT. **ReDoS bound doğrulandı**: 100k–800k karakterlik adversarial girdide süre kesinlikle lineer (uzunluk iki katına → süre iki katına), catastrophic backtracking yok.

---

## Orta Önem (yeni / residual)

### M-1 (Medium) — Bilinmeyen-vendor agent'ı sandbox enforcement'ı VE privilege audit'i atlıyor
- **Konum:** `privilege.py` `enforce_read_only` (vendor anthropic/openai/google ve name claude/codex/agy/gemini değilse `extra_args` **değişmeden** dönüyor); `config.py` `KNOWN_VENDORS` kontrolü bilinmeyen vendor'ı yalnızca **uyarı** yapıyor (hard-error değil); `adapters.make_adapter` bilinmeyen vendor'ı generic `AgyAdapter`'a yönlendiriyor; `privilege.audit_agent` non-claude için yalnızca bir `_DANGEROUS_FLAGS` token'ı **varsa** uyarıyor.
- **Açıklama:** `vendor="acme"`, `command="claude"`, `extra_args=[]` olan bir agent: enforce_read_only sandbox enjekte etmez, config yalnızca uyarır (çalışmaya devam), ve audit sıfır uyarı üretir (dangerous flag yok) — yani `--strict` bile yakalamaz. #288'in "read-only garantisi koşulsuz" amacı bu yolda tutmuyor.
- **Senaryo:** Yanlış/kötücül `jury.toml` bilinmeyen bir vendor tanımlar; reviewer, attacker-controlled diff'i sandbox'sız işler.
- **Düzeltme:** `enforce_read_only`'da tanınmayan vendor'ı konservatif ele al (çalıştırmayı reddet ya da bir deny-all default enjekte et) **ve/veya** bilinmeyen vendor'ı hard `ConfigError` yap. En azından `audit_agent`, restricting sandbox tanınmayan her non-claude agent için (yalnızca dangerous flag varken değil) uyarsın.

### M-2 (Medium) — Sentinel fence escape: untrusted içerik fence token'ları nötralize edilmeden interpolate ediliyor
- **Konum:** `orchestrator.py` (diff/context/other_reviews/findings `<<<UNTRUSTED_… …>>>` fence'lerine `str.format` ile yerleştiriliyor); `prompts.py` (sentinel şablonları).
- **Açıklama:** Untrusted alanlarda literal `UNTRUSTED_DIFF>>>` (veya diğer sentinel'ler) strip/escape edilmiyor. Kapanış sentinel'ini içeren bir diff fence'i erken kapatıp sonraki satırları üst-seviye prompt metni gibi gösterebilir. (İlk audit'te de Medium olarak işaretlenmişti.)
- **Hafifletme (mevcut):** Injection tarayıcı payload'ı yine de yakalıyor (`fake-system-turn`/`override-instructions`), ve CI gate yapısal consensus'tan türüyor — bu yüzden gate çevrilemez → HIGH değil MEDIUM. Ama birincil derinlemesine-savunma katmanı (fence) bypass edilebilir.
- **Düzeltme:** Interpolasyondan önce her untrusted alanda sentinel token'larını nötralize eden tek bir helper (örn. `<<<UNTRUSTED_` / `UNTRUSTED_…>>>` içine zero-width veya escape). Kapanış sentinel'inin fence içinde escape'siz görünemeyeceğini doğrulayan test ekle.

### M-3 (Medium) — Redaction hâlâ bazı yaygın secret formatlarını kaçırıyor
- **Konum:** `redaction.py` `_PATTERNS`.
- **Kaçanlar:** Azure connection string / `AccountKey=…` / `SharedAccessKey=…`; GCP service-account JSON alanları (`private_key_id`, `client_email`, vb. — PEM gövdesi yakalanıyor ama çevresi değil); **basic-auth URL'leri** `scheme://user:password@host` (örn. `redis://default:S3cr3t...@cache:6379`) hiç yakalanmıyor; bilinen keyword öncesinde olmayan generic yüksek-entropi değerler.
- **Senaryo:** Diff'e eklenen bir basic-auth URL'i ya da Azure connection string'i her dış agent'a maskelenmeden gider.
- **Düzeltme:** Basic-auth URL (`://[^/:@\s]+:[^/@\s]{6,}@`), Azure `AccountKey=`/`SharedAccessKey=`, GCP JSON alan adları için pattern ekle; opsiyonel bounded yüksek-entropi fallback. Her pattern'i anchored/bounded tutarak lineerliği koru.

---

## Düşük Önem

### L-1 — `detect_capabilities` version probe'u timeout'ta process-grubunu öldürmüyor
`adapters.py` `detect_capabilities`, hardened `_spawn` yerine düz `subprocess.run(timeout=...)` kullanıyor — F-7'nin ana run yolu için kapattığı orphan-grandchild sızıntısı probe yolunda (jury doctor / capability detection'dan erişilebilir) hâlâ var. **Düzeltme:** probe'u da `_spawn`'dan (veya `start_new_session`+`killpg`) geçir.

### L-2 — Injection zero-width/base64 kapsamı eksik
`injection.py`: `_ZERO_WIDTH` LRM/RLM (`U+200E/200F`), ALM (`U+061C`), Hangul filler (`U+3164` vb.), görünmez matematik operatörleri (`U+2061–2064`), soft hyphen (`U+00AD`), CGJ'yi kaçırıyor. `_BASE64_RE` yalnızca standart base64'ü tek kesintisiz blokta yakalıyor (URL-safe `-_`, newline-bölünmüş, hex kaçıyor). **Düzeltme:** code point setini ve base64 sınıfını genişlet.

### L-3 — `clear()` `.hmac_key`'i bırakıyor
`cache.py` `clear()` yalnızca `*.json` glob'luyor, MAC key hayatta kalıyor. Tek başına vuln değil (`0o600` per-user secret), ama "tüm cache entry'lerini sil" amacıyla tutarsız; şüpheli-kompromizden sonra cache temizleyen kullanıcı için key rotasyonu olmaz. **Düzeltme:** entry'lerden sonra key'i de unlink et, ya da retention'ı dokümante et.

### L-4 — Cache entry yazımı atomik değil ve symlink takip ediyor
`cache.py` `Path.write_text` (O_EXCL/O_NOFOLLOW yok). Trusted (`0o700`) dizin içinde exploit edilemez (yalnızca sahibi symlink koyabilir, `_dir_is_untrusted` gate'i var). **Opsiyonel sertleştirme:** `<key>.json.tmp` + `O_EXCL` sonra `os.replace`.

### L-5 — Cache okumalarında MAC-öncesi boyut sınırı yok
`cache.py` `json.loads(path.read_text())` MAC doğrulamasından **önce** tüm dosyayı okuyor; writable dizine konmuş çok-GB'lık bir `<digest>.json` MAC reddetmeden önce parse edilir. `_dir_is_untrusted` gate'i cross-user durumu engelliyor; self-DoS gerçek tehdit değil. **Opsiyonel:** okumadan önce `path.stat().st_size > N` kontrolü.

---

## Bilgi / Doğrulanmış Güvenli

- **Tehlikeli sınıflar yok:** repo genelinde `pickle`/`eval`/`exec`/`yaml`/`marshal`/`os.system`/`shell=True`/`tempfile.mktemp`/zip-slip — hiçbiri yok.
- **Path traversal sink'i yok:** cache key SHA-256 digest (filename traversal imkânsız); `largediff` diff-header path'ini yalnızca glob/rapor için kullanır, `open()`/`write()`'a vermez; tüm output path'leri (`-o`, `--metadata-json`, `--patches-out`, `doctor --write`) operatör-kaynaklı, diff'ten türetilmiyor.
- **Credential yüzeyi yok:** `AgentSpec`'te api_key/header alanı yok; LocalAdapter yalnızca `Content-Type` gönderir — argv/log/cache/rapora sızacak kimlik bilgisi yok. GitHub tamamen `gh`'a delege.
- **Tüm network çağrıları timeout'lu;** `gh` 90s, `_open` çağrıları timeout geçiyor.
- **Loopback allowlist** alternatif kodlamaları (`127.1`, octal, `::ffff:127.0.0.1`) **güvenli yönde** (non-loopback → hard-error) ele alıyor; SSRF bypass değil, yalnızca kullanılabilirlik notu (`ipaddress.ip_address(host).is_loopback` ile normalleştirilebilir).
- **Windows:** cache integrity check `nt`'de atlanıyor ve `0o600` Windows'ta zayıf — POSIX-öncelikli araç için kabul edilebilir, ama privacy docstring'inde açıkça not edilmeli (L-3 ile birlikte).

---

## Önceliklendirilmiş Düzeltme Sırası

1. **M-1** — Bilinmeyen-vendor yolunu kapat (enforce_read_only + audit_agent + config hard-error). #288'in garantisini gerçekten koşulsuz yapar.
2. **M-3** — Redaction'a basic-auth URL + Azure/GCP pattern'leri ekle (secret sızıntısını azaltır).
3. **M-2** — Sentinel nötralizasyonu (fence derinlemesine-savunmasını geri kazanır).
4. **L-1** — Version probe'u `_spawn`'dan geçir (F-7'yi probe yolunda tamamlar).
5. **L-2…L-5** — Injection code point/base64 genişletme, `clear()` key rotasyonu, atomik cache yazımı, MAC-öncesi boyut sınırı.

> Notlar: Bu rapor statik analiz + manuel doğrulama + redaction/injection için canlı ReDoS timing ölçümüne dayanır; dinamik exploit doğrulaması yapılmamıştır. Önceki tüm fix'ler (#287–#296) kaynak kodda elle teyit edildi. Her bulgu için `file`/fonksiyon referansları mevcut koddan alınmıştır.
