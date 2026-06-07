# ai-jury — Güvenlik Denetim Raporu

**Tarih:** 2026-06-07
**Kapsam:** Tüm `src/ai_jury/` kod tabanı (8.720 satır, 30 modül)
**Yöntem:** Saldırı yüzeyi 4 eksende paralel statik denetim + yüksek-önemli bulguların kaynak kodda manuel doğrulanması
**Tehdit modeli:** Saldırgan diff metnini, diff dosya adlarını, PR/issue gövdesini ve PR yorumlarını kontrol eder. Operatör CLI bayraklarını ve `jury.toml`'u kontrol eder. Araç bu girdileri üçüncü-parti agent CLI'larına ve yerel model sunucularına iletir.

---

## Yönetici Özeti

Kod tabanının güvenlik temeli **güçlü**. En yüksek riskli sınıfların hiçbiri yok: `shell=True`, `os.system`, `eval`/`exec`, `pickle`, `yaml.load`, zip/tar extraction, TLS doğrulama kapatma — hiçbiri bulunmadı. Tüm subprocess çağrıları liste-argv ile yapılıyor; saldırgan diff'i tek bir argv elemanı ya da stdin olarak akıyor, dolayısıyla **diff içeriği üzerinden komut enjeksiyonu mümkün değil**. GitHub erişimi tamamen `gh` CLI'ya delege edilmiş, token süreç listesine/argv'ye sızmıyor. Prompt-injection guard doğru biçimde *advisory* (CI kararını ters çeviremez).

Bununla birlikte iki **yüksek** öncelikli sorun var; her ikisi de tehdit modelinin merkezindeki bir kontrolün vaat ettiği garantiyi tutmuyor:

1. **Sandbox zorunlu değil** — read-only/sandbox bayrakları adapter katmanında değil, config `extra_args`'ında yaşıyor ve runtime denetimi yalnızca uyarı niteliğinde. Yanlış yapılandırılmış bir config, saldırgan diff'i işleyen yazma-yetkili bir reviewer üretebilir.
2. **Redaction boşlukları** — `password=`, `aws_secret_access_key=` ve yaygın sağlayıcı token formatları (Slack, Google, Stripe, JWT, GitHub PAT) maskelenmeden agent prompt'larına ve rapora geçiyor.

| Önem | Adet | Bulgular |
|------|------|----------|
| Critical | 0 | — |
| High | 2 | F-1 (sandbox enforcement), F-2 (redaction key boşluğu) |
| Medium | 3 | F-3 (token format boşluğu), F-4 (SSRF), F-5 (privilege audit by-pass) |
| Low | 5 | F-6…F-10 |
| Info | birkaç | aşağıda |

---

## Yüksek Önem

### F-1 (High) — Sandbox bayrakları garanti değil; `extra_args` defaultları tamamen ezebilir, audit yalnızca uyarı

- **Konum:** `adapters.py:308, 321, 332` (`argv + self.spec.extra_args`); `config.py:514` (`extra_args=list(raw.get("extra_args", []))`); `orchestrator.py:377-384` (audit yalnızca `--strict` altında hard-fail).
- **Açıklama:** Sandbox/read-only bayrakları adapter tarafından enjekte edilmiyor — tamamen config `extra_args` içinde duruyor. Doğrulandı: `ClaudeAdapter.build_argv` = `[command, "-p", prompt] + extra_args`; zorunlu bir `--disallowed-tools` yok. `extra_args = []` ya da `["-s","workspace-write"]` / `["--yolo"]` içeren bir `jury.toml` sandbox'sız bir argv üretir. `audit_privilege` yalnızca uyarı loglar (`orchestrator.py:378`); default'ta (`--strict` kapalı) run devam eder.
- **Senaryo:** Bir repo, claude seat'inin `extra_args`'ında `--disallowed-tools` olmayan bir `jury.toml` ile gelir (ya da operatör yanlış yapılandırır). PR yazarı diff'e prompt-injection payload'u yerleştirir. Artık tool-enabled olan reviewer, CI'da dosya yazma/komut çalıştırabilir. Tek emniyet ağı (privilege uyarısı) sessizce loglanıp geçilir.
- **Düzeltme:** Sandbox'ı config yerine adapter katmanında pazarlık-dışı yap. Her adapter'ın `build_argv`'sinde zorunlu kısıtlama bayraklarını her zaman enjekte et (claude `--disallowed-tools Edit,Write,NotebookEdit,Bash`, codex `-s read-only`, agy `--sandbox`) ve `extra_args`'a karşı dedup et — böylece config yalnızca deny set'e *ekleyebilir*, asla kaldıramaz. Alternatif: `audit_privilege`'ı default hard-fail yap (`--allow-unsandboxed` ile açık opt-in iste).

### F-2 (High) — Redaction anahtar listesi `password` ve kanonik secret değişken adlarını atlıyor

- **Konum:** `redaction.py:31-34`.
- **Açıklama:** `secret_assignment` paterni yalnızca `(api[_-]?key|secret|token)` anahtarlarını tanıyor. Doğrulanmış sızıntılar (count=0, agent'lara ve rapora ham geçer):
  - `password = "..."` — anahtar alternasyonunda yok, maskelenmiyor.
  - `aws_secret_access_key=...` — maskelenmiyor; `secret`'tan sonra zorunlu `(\s*[=:]\s*)` ayracı yerine `_access_key` geldiği için eşleşme olmuyor. En yaygın AWS secret-key değişken adı tamamen kaçıyor.
  - Kapsanmayan diğerleri: `passwd`, `pwd`, `private_key`, `access_key`, `client_secret`, `auth`, DB connection string'leri.
- **Senaryo:** Bir PR diff'inde (ya da `--pr` ile PR gövdesinde) hardcoded `password=` / `aws_secret_access_key=` değeri her üçüncü-parti agent CLI'ya cleartext iletilir ve markdown/JSON/SARIF rapora basılır. Issue #6 secret-redaction kontrolünü bozar.
- **Düzeltme:** Anahtar alternasyonunu genişlet: `(api[_-]?key|secret|token|password|passwd|pwd|access[_-]?key|private[_-]?key|client[_-]?secret|auth)` ve anahtar tarafının sonek sınırında eşleşmesini sağla (keyword'den sonra `[A-Za-z0-9_]*` ile `aws_secret_access_key`'i `secret` üzerinden yakala).

---

## Orta Önem

### F-3 (Medium) — Yaygın sağlayıcı token formatları tespit edilmiyor

- **Konum:** `redaction.py:13-35`.
- **Açıklama:** Doğrulanmış kaçışlar (count=0): Slack (`xox[baprs]-…`), Google API key (`AIza[0-9A-Za-z_-]{35}`), Stripe live/test (`sk_live_…`, `rk_live_…` — alt-tireli form, `sk-` tireli OpenAI formundan farklı), GitHub fine-grained PAT (`github_pat_…` — mevcut `gh[pousr]_` sınıfı bunu kapsamıyor), JWT (`eyJ…`).
- **Düzeltme:** Açık patternler ekle: `xox[baprs]-[0-9A-Za-z-]{10,}`, `AIza[0-9A-Za-z_-]{35}`, `(?:sk|rk|pk)_(?:live|test)_[0-9A-Za-z]{16,}`, `github_pat_[0-9A-Za-z_]{20,}`, `eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+`.

### F-4 (Medium) — Config kaynaklı `endpoint` ile SSRF (scheme/host doğrulaması yok)

- **Konum:** `adapters.py:382-394, 432-434, 465-473, 349-352`; `config.py:515` (`endpoint=raw.get("endpoint")`); `config.py:245-258` (validate yalnızca `model`'i kontrol eder).
- **Açıklama:** Yerel-model `endpoint`'i `jury.toml`'dan (veya `--local-endpoint`) verbatim alınıp doğrudan `urllib.request.urlopen`/`Request`'e geçiyor. Scheme'i `http/https` ile, host'u loopback ile sınırlayan doğrulama yok. `file://`, `ftp://`, `http://169.254.169.254/...` (cloud metadata) kabul edilir. `run` metodu o URL'e **POST** da atar.
- **Senaryo:** Repo'ya işlenmiş kötücül bir `jury.toml`, `endpoint`'i IMDS ya da `file:///etc/passwd`'e ayarlar; local panel seat çalışınca iç/metadata içeriği çekilir ve hata `detail`'i bunu rapora yansıtabilir (bkz. F-8). IMDS'li bir CI runner'da credential exfil.
- **Düzeltme:** `validate_config`'de `vendor == "local"` iken `endpoint`'i `urlsplit` ile parse et, `http`/`https` dışını reddet; loopback-dışı host'u açık opt-in olmadan engelle/uyar. LocalAdapter için yalnızca `HTTPHandler`/`HTTPSHandler` içeren bir `OpenerDirector` kullan (FileHandler/FTPHandler olmadan) — böylece `file://`/`ftp://` doğrulamadan bağımsız imkânsız olur.

### F-5 (Medium) — `privilege._is_sandboxed` tek bir `--sandbox` token'ı ile tüm tehlikeli-bayrak uyarılarını susturuyor (audit yanlış güvence)

- **Konum:** `privilege.py:42-60`, kullanım `:99-100`.
- **Açıklama:** Claude-dışı agent'larda `_is_sandboxed` True dönerse fonksiyon sıfır uyarıyla çıkar — `extra_args`'ın geri kalanı ne kadar tehlikeli olursa olsun. `_is_sandboxed`, argv'de herhangi bir yerdeki çıplak `--sandbox` için True döner ve `--sandbox <-ile-başlayan-herhangi-şey>`'i geçerli sayar (`:56`). Yani `["--sandbox", "--dangerously-skip-permissions", "--yolo"]` tamamen güvenli sayılır. Token değerinin gerçekten kısıtlayıcı bir mod olup olmadığı doğrulanmaz.
- **Düzeltme:** Sandbox token'ının bilinen-kısıtlayıcı bir değer taşımasını şart koş (whitelist: codex `read-only` vb.). `--sandbox --tehlikeli-bayrak` bitişikliğini tatmin edilmiş sandbox sayma; tehlikeli bayrak birlikte varsa yine de bilgilendirici uyarı çıkar.

---

## Düşük Önem

### F-6 (Low) — Güvenilmeyen PATH üzerinden binary çözümleme
`adapters.py:160` (`shutil.which`), `:172/:254/:258` (argv[0] çıplak komut adı). Config-kontrollü `command` herhangi bir string olabilir. PATH'i etkileyebilen ya da erken bir `claude`/`gh` shim bırakabilen bir CI runner'da jüri saldırganın binary'sini çalıştırır. **Düzeltme:** CI'da mutlak-yol çözümlemeyi zorunlu kıl; ayraç içeren göreli `command` değerlerini reddet; `doctor`'da çözülen mutlak yolu göster.

### F-7 (Low) — Timeout'ta process-group kill yok; orphan child riski
`adapters.py:258-270`. `subprocess.run(timeout=...)` yalnızca doğrudan child'a SIGKILL gönderir; node/python wrapper'ların torunları orphan olarak hayatta kalabilir (uzun-ömürlü CI'da kaynak tükenmesi). **Düzeltme:** `start_new_session=True` ile spawn et, `TimeoutExpired`'da `os.killpg` ile tüm grubu öldür (stdlib-only).

### F-8 (Low) — Upstream yanıt gövdesi/`reason` hata metnine yansıtılıyor
`adapters.py:476-485` (`detail = exc.read()...[:300]`), `:493-498`, `:448`. HTTP hatalarında güvenilmeyen yanıtın ilk 300 baytı `AgentResult.error`'a girip raporda render edilir; F-4 ile birlikte SSRF'i gözlemlenebilir kılar. **Düzeltme:** Ham upstream baytlarını kullanıcıya gösterme; yalnızca status/uzunluk logla ya da gömmeden önce `redaction.redact()`'ten geçir.

### F-9 (Low) — Güvenilmeyen yanıtta sınırsız `resp.read()`
`adapters.py:353, 474`. Yanıtlar `json.loads` öncesi bayt tavanı olmadan tam okunuyor; kötücül/hatalı endpoint (F-4 ile erişilebilir) çok-GB gövdeyle süreci OOM edebilir. **Düzeltme:** `resp.read(MAX_BYTES + 1)` ile oku ve aşımı reddet (birkaç MB yeterli).

### F-10 (Low) — Cache bütünlük bağı yok + dizin env-kontrollü
`cache.py:36-42, 197-226`. Cache anahtarı SHA-256 hex digest (path traversal yok), ancak girdiler okunurken MAC/imza ile doğrulanmıyor; cache dizinine yazabilen yerel bir saldırgan bilinen bir diff+config digest'i için sahte "PASS" verdict'i yerleştirebilir (cache poisoning → forged verdict; kod yürütme yok). `JURY_CACHE_DIR`/`XDG_CACHE_HOME` verbatim güveniliyor. **Düzeltme:** Entry'ye kanonik `cache_key` payload'unu (ya da per-user HMAC) göm ve `load()`'ta doğrula; cache dizinini `0o700` ile oluştur, world-writable ise reddet.

---

## Bilgi / Doğrulanmış Güvenli

- **Komut enjeksiyonu yok:** Tüm spawn'lar liste-argv `subprocess.run`; `shell=True`/`os.system`/`os.popen`/`os.exec*` yok. Diff tek argv elemanı (`-p`) ya da stdin (Codex) olarak akar.
- **GitHub credential işleme doğru:** `github.py` token okumuyor/loglamıyor/argv'ye koymuyor — `gh`'a delege. Tüm çağrılar timeout'lu (`_GH_TIMEOUT_S=90`), `--` ayraçlı (flag injection yok).
- **Prompt-injection mimarisi sağlam:** Diff `str.format` ile *değer* olarak interpolate edilir (format-string injection yok), sentinel fence + `_UNTRUSTED_NOTICE` ile sarılır. Guard advisory — CI gate yapısal consensus'tan türer, enjekte "APPROVE" verdict'i çeviremez. (Not: sentinel string'ler sabit/tahmin edilebilir ve untrusted içerikte nötralize edilmiyor — derinlemesine savunma için `injection._PHRASE_PATTERNS`'e literal sentinel eklenebilir.)
- **ReDoS yok:** Saldırgan-beslemeli tüm regex'ler (redaction, injection, PEM DOTALL) 100k–200k karakterlik adversarial girdide <5 ms. `largediff` regex değil `fnmatch` kullanır.
- **Tehlikeli sınıflar yok:** `pickle`, `eval`, `exec`, `marshal`, `yaml.load`, `zipfile`/`tarfile.extractall`, `tempfile`, `chmod`, world-writable mode — hiçbiri `src/ai_jury/`'de yok.
- **`patches.py`** dosya sistemine hiçbir şey uygulamaz; yalnızca doğrulanmış bulgular için markdown render eder (read-only tasarımla tutarlı).
- **`metadata.py`** diff/prompt/secret metnini kasıtlı olarak dışlar.

---

## Önceliklendirilmiş Düzeltme Sırası

1. **F-1** — Sandbox'ı adapter katmanında zorunlu kıl (en yüksek etki; tehdit modelinin çekirdek garantisi).
2. **F-2 / F-3** — Redaction anahtar listesini ve token patternlerini genişlet (secret sızıntısını durdurur).
3. **F-4** — `endpoint` scheme/host doğrulaması + kısıtlı opener (SSRF).
4. **F-5** — `_is_sandboxed` değer doğrulaması (audit yanlış güvencesini kapatır).
5. **F-6…F-10** — Sertleştirme (PATH pinning, process-group kill, response cap, cache integrity).

> Notlar: Bu rapor statik analiz + manuel doğrulamaya dayanır; dinamik exploit doğrulaması yapılmamıştır. F-1 ve F-2 kaynak kodda elle teyit edildi. Her bulgu için `file:line` referansları mevcut koddan alınmıştır.
