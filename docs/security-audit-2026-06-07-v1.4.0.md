# ai-jury — Güvenlik Re-Audit Raporu (v1.4.0)

**Tarih:** 2026-06-07 (v1.4.0 release sonrası)
**Kapsam:** Tüm `src/ai_jury/` kod tabanı, `main` @ v1.4.0
**Bağlam:** İki önceki denetimin ([security-audit-2026-06-07.md](security-audit-2026-06-07.md), [security-audit-2026-06-07-v1.3.0.md](security-audit-2026-06-07-v1.3.0.md)) bulguları #287–#303 olarak düzeltildi ve v1.3.0 / v1.4.0'da yayınlandı. Bu üçüncü tur: (a) tüm fix'lerin v1.4.0 kaynağında tuttuğunu doğrular, (b) kalan/yeni sorunları arar.
**Yöntem:** Saldırı yüzeyi 4 eksende paralel statik denetim + sentinel-nötralizasyon ve redaction/injection regex'lerinde canlı stress/ReDoS timing ölçümü. İki Medium bulgu kaynak kodda ampirik teyit edildi.

---

## Yönetici Özeti

**#287–#303 fix'lerinin tamamı v1.4.0 kaynağında doğrulandı ve tutuyor.** Bu turda da **Critical veya High yok**. Sentinel nötralizasyonu (#301) tüm istenen kaçış vektörlerine (combined `<<<UNTRUSTED_X>>>`, whitespace/newline closer, nested, casing, forged breakout) karşı dayanıklı; redaction/injection regex'leri lineer (ReDoS yok); cache HMAC tasarımı fail-closed ve collision/smuggling-safe.

Bununla birlikte **iki gerçek Medium residual** ortaya çıktı — ikisi de "warn ama enforce etme" / "bir yolu gate'le ama diğerini atla" deseninin örnekleri:

| Önem | Adet | Bulgular |
|------|------|----------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 2 | M-1 (unknown-vendor fail-open sandbox), M-2 (`jury init --local-endpoint` SSRF bypass) |
| Low | 6 | L-1…L-6 |
| Info | birkaç | aşağıda |

---

## CONFIRMED-FIXED — Önceki fix'ler tutuyor (v1.4.0)

Kaynak kodda doğrulandı:

- **#287/#288/#292** — prompt stdin'den (argv yok); `enforce_read_only` her CLI adapter'da, `--flag=value` dahil; `_is_sandboxed` vendor-aware.
- **#291** — non-http(s) ve (opt-in olmadan) non-loopback host hard-error; opener'da file/ftp **ve redirect** handler yok (302→IMDS takip edilmiyor). Tek network seam `_open`; bypass yok.
- **#293** — F-6 (relative command), F-7 (`_spawn` process-group kill), F-8 (error body redaction), F-9 (16 MiB cap + `errors="replace"`), F-10 (cache key embed) yerinde.
- **#295** — cache HMAC-SHA256, `_hmac_key` `0o600`+`O_EXCL`, fail-closed (key yoksa load→miss/store→yazma yok), `_dir_is_untrusted` gate. `_canonical` (mac hariç + recursive sort_keys) collision/field-smuggling-safe.
- **#296** — `JURY_REQUIRE_ABSOLUTE_COMMAND` strict + doctor resolved-path.
- **#300** — `audit_agent` artık sandbox'sız her non-claude agent için uyarıyor (local hariç).
- **#301** — `neutralize_sentinels` **iki-geçişli** (lookahead opener + capturing closer, `\s*` toleranslı); **her** untrusted slot (review/debate/verify/synthesis: diff/context/other_reviews/reviews/debate/findings) kapsanıyor. Stress-test'te tüm kaçış vektörleri kırıldı.
- **#302** — redaction basic-auth (boş username dahil), Azure `AccountKey=`, GCP `"private_key_id"`. ReDoS yok: 100k–500k adversarial girdide lineer (~0.44s→2.19s).
- **#303** — L-1 (probe `_spawn`'dan), L-2 (zero-width code-point set + URL-safe base64), L-3 (clear key rotasyonu + tmp reap), L-4 (pid-tagged atomik write), L-5 (bounded read, TOCTOU yok).

Ayrıca temiz: `shell=True`/`os.system`/`eval`/`pickle`/`yaml`/zip-slip yok; path-traversal sink'i yok; `gh` çağrıları `--` ayraçlı + timeout'lu; credential argv/log/cache'e sızmıyor; TLS doğrulaması (urllib default) açık.

---

## Orta Önem (yeni / residual)

### M-1 (Medium) — Bilinmeyen-vendor agent default modda gerçekten SANDBOX'SIZ çalışıyor (fail-open)
- **Konum:** `adapters.py` `make_adapter` (bilinmeyen vendor → generic `AgyAdapter`); `privilege.py` `enforce_read_only` (claude/openai/google dışındaki vendor için `extra_args`'ı **değişmeden** döndürüyor — `--sandbox` enjekte etmiyor); `config.py` `KNOWN_VENDORS` (bilinmeyen vendor yalnızca uyarı); `orchestrator.py` (audit yalnızca `--strict` altında hard-fail).
- **Doğrulandı (ampirik):** `enforce_read_only('mycli','x',[])` → `[]`.
- **Açıklama:** #300, audit'in artık uyarı ÜRETMESİNİ sağladı (audit blind-spot'u kapattı), ama **run-seviyesindeki boşluk açık**: bilinmeyen vendor için hiçbir sandbox bayrağı eklenmiyor. `vendor="mycli"`, `extra_args=[]` olan bir agent default (non-strict) modda attacker-controlled diff'i sandbox'sız işler; config yalnızca uyarır, audit yalnızca uyarır.
- **Senaryo:** Operatör yanlış/bilinmeyen bir vendor adı yazar (typo ya da listede olmayan gerçek bir CLI). Diff'teki prompt-injection, alttaki CLI'ın honor ettiği bir tool/write/shell talimatıysa sandbox'sız çalışır; advisory uyarı normal çıktıda kolayca gözden kaçar.
- **Düzeltme:** Fail-closed yap. Tercih sırası: (a) bilinmeyen vendor için `extra_args`'ta açık bir sandbox bayrağı yoksa adapter kurmayı **reddet** (uyarı değil hard error); (b) generic fallback zaten agy semantiği varsaydığından `enforce_read_only`'nin son branch'i `["--sandbox"]` enjekte etsin; (c) unknown-vendor/unsandboxed durumunu `--strict`'ten bağımsız hard error yap. (a) en güvenlisi — generic fallback'in agy `--print`/`--sandbox` sözdizimini keyfi bir binary için varsayması zaten sağlam değil.

### M-2 (Medium) — `jury init --local-endpoint` SSRF doğrulamasını (`_endpoint_issues`) atlıyor
- **Konum:** `cli.py:587-606` (`endpoint = ns.local_endpoint` → doğrudan `list_local_models(endpoint)` → `_open(url)`, **`_endpoint_issues` çağrısı yok**); aynı durum `_init_interactive` (`cli.py:387`) ve `_init_wizard` (`cli.py:529`)'da.
- **Doğrulandı:** init/list-models yolu `_endpoint_issues` çağırmıyor (config yolu — `config.py:334-338` — doğru gate'li).
- **Açıklama:** `_endpoint_issues` loopback-only / `JURY_ALLOW_REMOTE_ENDPOINT` gate'ini uygulayan katman. `jury init --local-endpoint http://169.254.169.254/latest/meta-data --list-models` çağrısı bu gate'i hiç çağırmadan seçilen herhangi bir iç/metadata host'a **doğrudan GET** yapar. Opener `file://`/`ftp://`'yi ve redirect'i hâlâ engelliyor (defense-in-depth tutuyor), ama doğrudan-GET SSRF primitive'i açık; yanıt model listesi olarak parse edilip `data[].id` kullanıcıya yansıtılabilir (iç-host erişilebilirliğini teyit eder).
- **Düzeltme:** `_init_cmd`'de `list_local_models`'ten önce `ns.local_endpoint`'i `_endpoint_issues(endpoint, "init")`'ten geçir (non-loopback/non-http(s) reddet ya da `JURY_ALLOW_REMOTE_ENDPOINT` iste), config doğrulamasını aynala. Alternatif: gate'i `_open`/`list_local_models` seviyesine taşı ki hiçbir caller atlayamasın.

---

## Düşük Önem

### L-1 — `init --local-endpoint` stdout'ta redakte edilmeden yansıtılıyor
`cli.py:592,594,608` ham `endpoint`'i basıyor (URL'de `user:pass@` gömülüyse plaintext stdout/CI log'a düşer). `doctor.py` endpoint'leri `_redact_value`'dan geçiriyor; init yolu geçirmiyor. **Düzeltme:** init çıktısını da `redaction.redact()`/`_redact_value`'dan geçir.

### L-2 — TLS context açık (explicit) değil
`adapters.py:427` `HTTPSHandler()` `context=` olmadan → stdlib default (doğrulama AÇIK; vuln değil). Residual: güvenlik-kritik bir opener için explicit `ssl.create_default_context()` + `CERT_NONE` asla kullanılmadığını assert eden bir test daha sağlam. **Düzeltme:** `HTTPSHandler(context=ssl.create_default_context())` + regression test.

### L-3 — `prior_txt` debate eklentisi nötralize/fence edilmeden interpolate ediliyor
`orchestrator.py:254-256, 276-281`. Adaptif çok-turlu debate'te önceki turun çıktısı `=== PRIOR DEBATE ===` başlığıyla ham ekleniyor — `neutralize_sentinels()` ve `<<<UNTRUSTED_REVIEW` fence'i YOK (round-1 `other_reviews` ikisini de alıyor). Bir agent forged `UNTRUSTED_REVIEW>>>`'i echo ederse round-2'de un-neutralized iner. Contingent (agent'ın marker'ı echo etmesi gerek) + gate çevrilemez → Low. **Düzeltme:** `prior_txt`'i fence'le + `neutralize_sentinels`'ten geçir.

### L-4 — Yaygın bazı secret formatları hâlâ redaction'ı atlıyor
`redaction.py:13-77`. Bare (assignment bağlamı olmayan) haldeyken kaçanlar: SendGrid `SG.<id>.<secret>`, Twilio SID `(AC|SK|US)[0-9a-f]{32}`, PyPI `pypi-…`, Slack webhook URL'leri `hooks.slack.com/services/…`, bare 32/40/64-hex API secret'ları. **Düzeltme:** bu formatlar için `secret_assignment`'tan önce anchored/bounded pattern ekle.

### L-5 — `clear()`'daki `*.tmp`/`*.json` glob'u ilgisiz dosyaları silebilir
`cache.py:369,375`. `JURY_CACHE_DIR`/`XDG_CACHE_HOME` paylaşılan/özel-olmayan bir dizine işaret ederse `jury cache clear` o dizindeki cache'in oluşturmadığı `*.tmp`/`*.json` dosyalarını da siler. **Düzeltme:** yalnızca cache'in kendi adlandırma desenini (`*.json.*.tmp`) reap et ve/veya cache'in oluşturmadığı dizinde çalışmayı reddet (sentinel marker dosyası).

### L-6 — Atomik-write temp adı same-PID concurrency / symlink
`cache.py:353` `f"{path.name}.{os.getpid()}.tmp"` ayrı süreçleri ayırır ama aynı süreçte iki thread'i veya recycled-PID'i ayırmaz; ayrıca `write_text` önceden var olan bir symlink'i takip eder (0700 dir gate başka kullanıcıyı engeller). Impact: MAC + `replace()` atomikliği nedeniyle poisoning değil, kayıp/bozuk yazım. **Düzeltme:** pid-tag yerine `tempfile.mkstemp(dir=self.dir)` (unique ad + `O_EXCL|O_CREAT`, symlink-follow yok).

---

## Bilgi / Doğrulanmış Güvenli

- **`tomllib.load` boyut sınırı yok** (`config.py:725-726`, `policy.py:88-89`) — config/policy dosyaları lokal/kullanıcı-sahipli (diff tehdit modelinde attacker-controlled değil); cache ile simetri için opsiyonel boyut kontrolü. Info.
- **`default_cache_dir()` env'i doğrulamadan onurlandırıyor** — integrity yine MAC+dir gate ile korunuyor (poisoning'e karşı fail-closed); yalnızca L-5'in destructive `clear()` ön-koşulu. Info.
- **`detect_capabilities` probe'u sandbox'sız** binary'yi çalıştırır — prompt/attacker içeriği yok, kısa timeout, kendi process-grubu; #296 + doctor resolved-path ile hafifletilmiş. Info.
- **github.py** `compare_diff` SHA'ları `_MARKER_RE` `[0-9a-fA-F]{7,40}` ile sınırlı + `--` ayraçlı → injection yok.
- **classification.py** word-boundary-safe; untrusted interpolation yok; trust-inversion yok.

---

## Önceliklendirilmiş Düzeltme Sırası

1. **M-1** — Unknown-vendor sandbox boşluğunu fail-closed yap (adapter kurmayı reddet veya `--sandbox` enjekte et). Secure-by-default duruşunu gerçekten koşulsuz kılar.
2. **M-2** — `jury init --local-endpoint`'i `_endpoint_issues`'tan geçir (ya da gate'i `_open`/`list_local_models`'e taşı). SSRF gate'inin tek atlanabilen yolunu kapatır.
3. **L-1…L-3** — init endpoint redaction, explicit TLS context+test, `prior_txt` nötralizasyonu.
4. **L-4** — redaction'a SendGrid/Twilio/PyPI/Slack-webhook/bare-hex pattern'leri.
5. **L-5/L-6** — `clear()` reap'ini cache-owned adlara daralt; atomik write'ı `tempfile.mkstemp`'e geçir.

> Notlar: Bu rapor statik analiz + manuel doğrulama + sentinel/redaction/injection için canlı stress/ReDoS timing ölçümüne dayanır; dinamik exploit doğrulaması yapılmamıştır. M-1 ve M-2 kaynak kodda ampirik teyit edildi. Önceki tüm fix'ler (#287–#303) v1.4.0 kaynağında elle teyit edildi.
