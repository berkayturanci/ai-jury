# ai-jury — Güvenlik Re-Audit Raporu (v1.5.0)

**Tarih:** 2026-06-07 (v1.5.0 release sonrası)
**Kapsam:** Tüm `src/ai_jury/` kod tabanı, `main` @ v1.5.0
**Bağlam:** Önceki dört denetimin bulguları #287–#316 olarak düzeltildi ve v1.3.0 → v1.5.0'da yayınlandı. Bu beşinci tur: (a) tüm fix'lerin v1.5.0 kaynağında tuttuğunu doğrular, (b) kalan/yeni sorunları arar.
**Yöntem:** Saldırı yüzeyi 4 eksende paralel statik denetim + canlı stress/ReDoS timing + alternatif loopback fuzz. İki ana bulgu kaynak kodda ampirik teyit edildi.

---

## Yönetici Özeti

**#287–#316 fix'lerinin tamamı v1.5.0 kaynağında doğrulandı ve tutuyor.** Filesystem/cache ekseni tamamen temiz (yeni bulgu yok); subprocess/sandbox ekseni yalnızca iki Info. Bu turda da **Critical/High yok**.

Bununla birlikte iki gerçek bulgu çıktı; en önemlisi **#316/L-1 fix'imin eksik kapsamı** — bir untrusted-içerik slot'unu nötralize ettim ama yapısal olarak aynı sınıftaki ikinci bir addendum'ı kaçırdım:

| Önem | Adet | Bulgular |
|------|------|----------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 1 | M-1 (synthesis verdicts slot fence'siz/neutralize'siz) |
| Low | 3 | L-1 (init endpoint redaction kısa/bare-token userinfo'yu kaçırıyor), L-2 (classification `vulnerab`/`exploit` keyword stem'leri eşleşmiyor), L-3 (nested redaction — kozmetik) |
| Info | birkaç | aşağıda |

---

## CONFIRMED-FIXED — Önceki fix'ler tutuyor (v1.5.0)

- **#314** — `injection.scan` O(N) (newline offset bir kez + bisect + kind başına cap); satır numaraları doğru (index 0, newline-sonrası, multi-line teyit edildi); cap gerçek bir injection'ı gizlemiyor (farklı kind'lar bağımsız cap'leniyor); 100k–500k lineer (68→342ms).
- **#315** — malformed endpoint (`http://[::1`, `http://[fe80::1%25eth0]`, `://x`, `''`, …) hepsi temiz `ConfigError`, crash yok.
- **#316** — L-1 prior_txt fence+neutralize (✓ ama bkz. M-1); L-2 SendGrid/PyPI/npm/Slack-webhook (false-positive/ReDoS yok); L-3 `clear()` 64-hex (ilgisiz dosya silmiyor); L-4 `mkstemp` (O_EXCL, symlink-follow yok, unlink-on-error); L-5 TOML 4 MiB cap + non-UTF8 temiz hata; L-6 `_is_sandboxed` `=`-form (enforcement ile divergence yok); L-7 init endpoint redaction (✓ ama bkz. L-1).
- **#287–#313** — prompt stdin'den; adapter enforcement; unknown-vendor fail-closed (`vendor=="local"` ilk); `_spawn` group-kill (probe dahil); opener file/ftp/redirect handler yok; HMAC fail-closed; TLS doğrulaması açık (`CERT_NONE` yok). İki-geçişli `neutralize_sentinels` tüm review/debate/verify/synthesis slot'larında.
- **Tehlikeli sınıflar yok:** `pickle`/`eval`/`exec`/`yaml`/zip-slip/`mktemp`/`shell=True` — hiçbiri yok; path-traversal sink'i yok; cache-key path traversal imkânsız (SHA-256 digest).

---

## Orta Önem

### M-1 (Medium) — Synthesis prompt'una eklenen "VERIFICATION VERDICTS" untrusted içerik fence'siz ve neutralize'siz
- **Konum:** `orchestrator.py:802-803` — `prompt += f"\n\n=== VERIFICATION VERDICTS ===\n{_format_verdicts(verdicts)}\n"`; renderer `_format_verdicts` (`:688-697`) `v.claim`/`v.reasoning`'i ham basıyor.
- **Doğrulandı:** Kaynak kodda satır 803 teyit edildi. Diğer her untrusted slot (diff/context/reviews/debate/findings/prior_txt) `neutralize_sentinels` + `<<<UNTRUSTED_…>>>` fence alıyor; bu addendum **ikisini de almıyor**.
- **Açıklama:** `verdict.claim`/`reasoning`, VERIFY prompt'una göre aday bulguyu (untrusted diff metnini) alıntılar; `parse_verdicts` yalnızca `.strip()` uygular. #316/L-1'de `prior_txt`'i fence+neutralize ettim ama **yapısal olarak aynı sınıftaki** bu verdicts addendum'ını kaçırdım.
- **Senaryo:** Attacker diff'i, bir reviewer'ın bulgu claim'ine echo ettiği bir metin taşır → verifier onu `verdict.claim`'e kopyalar → synthesis prompt'una herhangi bir UNTRUSTED fence DIŞINDA iner, burada forged `UNTRUSTED_REVIEW>>>` ile önceki fence'i kırabilir ya da sahte `SYSTEM:` direktifi enjekte edebilir. CI gate consensus-türevli olduğundan verdict çevrilemez, ama insana gösterilen synthesis verdict METNİ manipüle edilebilir.
- **Düzeltme:** `prior_txt` desenini aynala — fence'le + neutralize et:
  ```python
  prompt += (
      "\n\n=== VERIFICATION VERDICTS (may quote UNTRUSTED text) ===\n"
      "<<<UNTRUSTED_FINDINGS\n"
      + prompts.neutralize_sentinels(_format_verdicts(verdicts))
      + "\nUNTRUSTED_FINDINGS>>>\n"
  )
  ```

---

## Düşük Önem

### L-1 (Low/Medium) — init endpoint redaction kısa/bare-token userinfo'yu kaçırıyor (#316/L-7 residual)
- **Konum:** `redaction.py` `basic_auth` pattern `(://[^/:@\s]*:)([^@\s]{6,})(@)`, `cli.py:591,596,598,612` üzerinden.
- **Açıklama:** Pattern parolanın **≥6 char** ve **`user:pass` colon formu** olmasını şart koşuyor. İki sızıntı sınıfı geçiyor: kısa parolalar (`http://user:pass@host`, 4-char) ve colon'suz bare-token userinfo (`http://apitoken12345@host:11434/v1`). `jury init --list-models --local-endpoint http://token@internal/v1` CI'da bunu cleartext stdout'a basar. L-7'nin amacı ("userinfo'yu redakte et") tam karşılanmıyor.
- **Düzeltme:** `redact()`'e güvenme; gösterimden önce userinfo'yu yapısal olarak strip et (`urlsplit` ile netloc'u `hostname[:port]`'a indir veya `userinfo@`'yı `[REDACTED]@` yap). `cli.py` ve `doctor.py`'de tek helper. Defense-in-depth için `basic_auth`'a colon'suz `(://[^/:@\s]+)(@)` arm ekle ve min-uzunluğu düşür.

### L-2 (Low) — classification `vulnerab`/`exploit` keyword stem'leri hiç eşleşmiyor
- **Konum:** `classification.py:69,71` (keyword listesi) + `:77-79` (`\b…\b` derleme).
- **Doğrulandı (ampirik):** `is_security_finding(Finding(claim="this is a vulnerability"))` → **False**; `"exploitable bug"` → False; `"exploited"` → False. (`injection`/`auth` gibi tam kelimeler çalışıyor.)
- **Açıklama:** `vulnerab`/`exploit` stem'leri `\bvulnerab\b` olarak derleniyor; trailing `\b`, `vulnerability` içinde (`b`'den sonra `i` geliyor) başarısız. Stem'ler yalnızca çıplak "vulnerab"/"exploit" string'iyle eşleşir, ki doğal metinde geçmez.
- **Senaryo:** Tek güvenlik sinyali "vulnerability"/"exploitable" olan bir bulgu (severity major/minor, başka keyword yok, critical değil) non-security sınıflanır: `security_sensitive=False`, "possible security issue" etiketi ve `needs_human_attention` eskalasyonu bastırılır.
- **Düzeltme:** Prefix stem'ler için trailing `\b` yerine `\w*` kullan: `r"\bvulnerab\w*"` / `r"\bexploit\w*"` — ya da tam kelimeleri listeye ekle.

### L-3 (Low) — nested redaction (kozmetik/telemetri)
`redaction.py` — `https://user:AKIA…@host` için `aws_access_key` önce redakte eder, sonra `basic_auth` `[REDACTED:aws_access_key]`'i tekrar redakte eder → `[REDACTED:basic_auth]`, `count=2`. Sızıntı değil (secret yine gitti), ama bilgilendirici kind kaybolur ve `redaction_count` şişer. **Düzeltme:** value char-class'larını mevcut `[REDACTED:…]` token'ını dışlayacak şekilde ayarla, ya da URL-userinfo strip'ini (L-1 fix) generic pattern'lerden önce çalıştır.

---

## Bilgi / Doğrulanmış Güvenli

- **TOCTOU `available()`→`_spawn`** — `build_argv`/`_spawn` bare `spec.command` geçiyor, kernel exec'te PATH'i yeniden çözüyor. Privilege-geçişi yok (aynı uid/PATH); `JURY_REQUIRE_ABSOLUTE_COMMAND` tamamen kapatıyor. Opsiyonel: strict modda absolute path'i Popen'a geçir. Info.
- **`gh --repo <value>` `--` ayraçsız** (`github.py`) — `repo` operatör-kaynaklı (diff değil), argv list-form (shell yok); diff tehdit modelinden erişilemez. Info.
- **Hâlâ kaçan secret formatları** (kasıtlı): GitLab `glpat-`, HuggingFace `hf_`, Google OAuth refresh `1//`, Azure SAS `sig=`. (Anthropic `sk-ant-` zaten `sk-` kuralıyla yakalanıyor.) İsteğe bağlı eklenebilir.
- **`redaction.scan` ReDoS yok:** secret_assignment/base64/pem 400k'da ~2s (yüksek sabit, `{0,40}` bound ile lineer); diff'ler `largediff` ile boyut-cap'li.

---

## Önceliklendirilmiş Düzeltme Sırası

1. **M-1** — Synthesis verdicts addendum'ını fence'le + neutralize et (L-1 fix'inin eksik kapsamını tamamlar).
2. **L-1** — init/doctor endpoint gösteriminde userinfo'yu yapısal strip et (kısa/bare-token sızıntısı).
3. **L-2** — classification `vulnerab`/`exploit` stem'lerini `\w*` ile düzelt.
4. **L-3** — nested redaction (kozmetik).

> Notlar: Bu rapor statik analiz + manuel doğrulama + canlı stress/ReDoS timing + loopback fuzz'a dayanır; dinamik exploit doğrulaması yapılmamıştır. M-1 ve L-2 kaynak kodda ampirik teyit edildi. Önceki tüm fix'ler (#287–#316) v1.5.0 kaynağında elle teyit edildi.
