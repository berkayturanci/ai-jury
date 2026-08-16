/* ============================================================
   ai-jury — site behavior
   theme · nav · scroll-reveal · hero pipeline · live demo
   ============================================================ */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var q = function (s, r) { return (r || document).querySelector(s); };
  var qa = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- Theme toggle ------------------------------------------------- */
  (function () {
    var btn = $("theme-toggle");
    if (!btn) return;
    function isLight() { return document.documentElement.getAttribute("data-theme") === "light"; }
    function updateAria() {
      var next = isLight() ? "dark" : "light";
      var msg = "Switch to " + next + " theme";
      btn.setAttribute("aria-label", msg);
      btn.setAttribute("title", msg);
    }
    updateAria();
    new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        if (m.attributeName === "data-theme") updateAria();
      });
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    btn.addEventListener("click", function () {
      var next = isLight() ? "dark" : "light";
      document.documentElement.setAttribute("data-theme", next);
      try { if (next === (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")) localStorage.removeItem("theme"); else localStorage.setItem("theme", next); } catch (e) {}
    });
  })();

  /* ---- Nav scrolled + auto-hide on scroll --------------------------- */
  (function () {
    var nav = $("nav");
    if (!nav) return;
    var last = window.scrollY, ticking = false;
    function update() {
      var y = window.scrollY;
      nav.classList.toggle("scrolled", y > 8);
      // hide when scrolling down past the hero; show when scrolling up
      if (y > 240 && y > last + 6) nav.classList.add("nav-hidden");
      else if (y < last - 6 || y <= 240) nav.classList.remove("nav-hidden");
      last = y; ticking = false;
    }
    update();
    window.addEventListener("scroll", function () {
      if (!ticking) { ticking = true; requestAnimationFrame(update); }
    }, { passive: true });
  })();

  /* ---- Mobile hamburger menu ---------------------------------------- */
  (function () {
    var burger = $("nav-burger"), menu = $("nav-mobile");
    if (!burger || !menu) return;
    function setOpen(open) {
      burger.classList.toggle("open", open);
      menu.classList.toggle("open", open);
      burger.setAttribute("aria-expanded", open ? "true" : "false");
      burger.setAttribute("aria-label", open ? "Close menu" : "Open menu");
      burger.setAttribute("title", open ? "Close menu" : "Open menu");
    }
    burger.addEventListener("click", function () { setOpen(!menu.classList.contains("open")); });
    // close after navigating
    qa(".nav-mlink", menu).forEach(function (a) { a.addEventListener("click", function () { setOpen(false); }); });
    // close if resized to desktop
    window.addEventListener("resize", function () { if (window.innerWidth > 680) setOpen(false); });
    // close on Escape
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && menu.classList.contains("open")) {
        setOpen(false);
        burger.focus();
      }
    });
  })();

  /* ---- Scroll reveal (rect-based — robust across embeds) ------------ */
  (function () {
    var items = qa(".reveal");
    if (reduce) { items.forEach(function (el) { el.classList.add("in"); }); return; }
    function check() {
      var vh = window.innerHeight || document.documentElement.clientHeight || 800;
      for (var i = items.length - 1; i >= 0; i--) {
        var r = items[i].getBoundingClientRect();
        if (r.top < vh * 0.92 && r.bottom > 0) { items[i].classList.add("in"); items.splice(i, 1); }
      }
    }
    check();
    requestAnimationFrame(check);
    var ticking = false;
    function onScroll() {
      if (ticking) return; ticking = true;
      requestAnimationFrame(function () { check(); ticking = false; });
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    window.addEventListener("load", function () { check(); requestAnimationFrame(check); });
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(check);
    setTimeout(check, 200);
    setTimeout(check, 700);
    setTimeout(check, 1400);
  })();

  /* ---- Copy install command (CTA band + hero) ----------------------- */
  (function () {
    function wire(btnId, cmdId) {
      var btn = $(btnId), cmd = $(cmdId);
      if (!btn || !cmd) return;
      var labelEl = btn.querySelector("span") || btn;
      var base = labelEl.textContent;
      var baseAria = btn.getAttribute("aria-label");
      var baseTitle = btn.getAttribute("title");
      btn.addEventListener("click", function () {
        var t = cmd.textContent;
        var done = function () {
          btn.classList.add("done"); labelEl.textContent = "copied ✓";
          btn.removeAttribute("aria-label");
          btn.removeAttribute("title");
          setTimeout(function () {
            btn.classList.remove("done"); labelEl.textContent = base;
            if (baseAria !== null) btn.setAttribute("aria-label", baseAria);
            if (baseTitle !== null) btn.setAttribute("title", baseTitle);
          }, 1400);
        };
        if (navigator.clipboard) navigator.clipboard.writeText(t).then(done, done); else done();
      });
    }
    wire("copy-install", "install-cmd");
    wire("hero-copy", "hero-install-cmd");
  })();

  /* ---- Auto copy buttons on code blocks ----------------------------- */
  (function () {
    var COPY = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
    qa("pre.code").forEach(function (pre) {
      if (pre.closest(".codewrap")) return;
      var wrap = document.createElement("div");
      wrap.className = "codewrap";
      pre.parentNode.insertBefore(wrap, pre);
      wrap.appendChild(pre);
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "code-copy";
      btn.setAttribute("aria-label", "Copy code");
      btn.setAttribute("title", "Copy code");
      btn.innerHTML = COPY + "<span>copy</span>";
      wrap.appendChild(btn);
      var baseAria = btn.getAttribute("aria-label");
      var baseTitle = btn.getAttribute("title");
      btn.addEventListener("click", function () {
        var code = pre.querySelector("code") || pre;
        // strip inline comments? no — copy verbatim, trimmed
        var text = code.innerText.replace(/[ \t]+$/gm, "").trim() + "\n";
        var ok = function () {
          btn.classList.add("done");
          btn.querySelector("span").textContent = "copied";
          btn.removeAttribute("aria-label");
          btn.removeAttribute("title");
          setTimeout(function () {
            btn.classList.remove("done"); btn.querySelector("span").textContent = "copy";
            if (baseAria !== null) btn.setAttribute("aria-label", baseAria);
            if (baseTitle !== null) btn.setAttribute("title", baseTitle);
          }, 1400);
        };
        if (navigator.clipboard) navigator.clipboard.writeText(text).then(ok, ok); else ok();
      });
    });
  })();

  /* ---- Version pill (best-effort) ----------------------------------- */
  (function () {
    var el = $("site-version");
    if (!el || !window.fetch) return;
    fetch("https://api.github.com/repos/berkayturanci/ai-jury/releases/latest", { headers: { Accept: "application/vnd.github+json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.tag_name) el.textContent = d.tag_name; })
      .catch(function () {});
  })();

  /* ================================================================
     Hero pipeline — a slow, looping "living" sequence.
     ================================================================ */
  (function () {
    var pipe = $("pipe");
    if (!pipe) return;
    var stages = {};
    qa("[data-stage]", pipe).forEach(function (el) { stages[el.getAttribute("data-stage")] = el; });
    var conns = {};
    qa("[data-conn]", pipe).forEach(function (el) { conns[el.getAttribute("data-conn")] = el; });
    var chips = qa(".rev-chip", pipe);
    var verdict = $("hero-verdict");
    var findings = $("hero-findings");
    var ttl = $("pipe-ttl");
    var debateMark = q("#debate-mark", pipe);
    var inMain = $("stage-in-main");
    var inSub = $("stage-in-sub");
    var synthMain = $("stage-synth-main");
    var tabPr = $("tab-pr");
    var tabIssue = $("tab-issue");

    function clearAll() {
      qa(".lit", pipe).forEach(function (el) { el.classList.remove("lit"); });
      Object.keys(conns).forEach(function (k) { conns[k].classList.remove("flow"); });
    }
    function spark(k) {
      var c = conns[k]; if (!c) return;
      c.classList.remove("flow"); void c.offsetWidth; c.classList.add("flow");
    }
    function lit(stage) { if (stages[stage]) stages[stage].classList.add("lit"); }

    // Two review modes, each cycling a few scenarios with mode-appropriate
    // verdicts (PR: APPROVE / REQUEST CHANGES · issue: READY / NEEDS-INFO /
    // UNCLEAR) and a chair-or-vote synthesis — matching the real CLI.
    var MODES = {
      pr: {
        cmd: "--pr", inMain: "diff / PR", inSub: "redact · chunk",
        scenarios: [
          { n: "123", reviewers: ["claude", "codex", "agy", "qwen"], debate: true,  vote: false, vClass: "approve",   vLabel: "✓ APPROVE" },
          { n: "124", reviewers: ["claude", "codex"],                debate: true,  vote: false, vClass: "changes",   vLabel: "✕ REQUEST CHANGES", findings: "2 blocking" },
          { n: "125", reviewers: ["codex", "agy", "qwen"],           debate: false, vote: true,  vClass: "approve",   vLabel: "✓ APPROVE" }
        ]
      },
      issue: {
        cmd: "--issue", inMain: "issue", inSub: "redact",
        scenarios: [
          { n: "42", reviewers: ["claude", "codex", "agy", "qwen"], debate: true,  vote: false, vClass: "ready",     vLabel: "✓ READY" },
          { n: "43", reviewers: ["claude", "codex"],                debate: true,  vote: false, vClass: "needsinfo", vLabel: "✕ NEEDS-INFO", findings: "missing repro" },
          { n: "44", reviewers: ["codex", "qwen"],                  debate: false, vote: true,  vClass: "unclear",   vLabel: "◐ UNCLEAR", findings: "scope unclear" }
        ]
      }
    };
    var mode = "pr";
    function scenarios() { return MODES[mode].scenarios; }

    function setScenario(s) {
      var m = MODES[mode];
      if (ttl) ttl.textContent = "$ jury " + m.cmd + " " + s.n;
      if (inMain) inMain.textContent = m.inMain;
      if (inSub) inSub.textContent = m.inSub;
      // synthesis: chair synthesis, or a panel vote (the ✓ marks the tally)
      if (synthMain) synthMain.textContent = s.vote ? "vote ✓" : "chair";
      // panel: fade reviewers not on this scenario's jury
      chips.forEach(function (c) { c.classList.toggle("muted", s.reviewers.indexOf(c.getAttribute("data-rev")) === -1); });
      // round 2: shown only when the panel actually debates
      if (stages[2]) stages[2].classList.toggle("skipped", !s.debate);
      if (debateMark) { debateMark.classList.remove("lit"); debateMark.textContent = s.debate ? "debate ⇄" : "skip ✓"; }
      // start in a neutral "pending" state — the outcome isn't known until the flow arrives
      if (verdict) {
        verdict.className = "verdict-badge pending";
        verdict.textContent = "judging…";
      }
      if (stages[5]) stages[5].classList.remove("changed", "needsinfo", "unclear");
      if (findings) { findings.hidden = true; findings.innerHTML = ""; }
    }
    function revealVerdict(s) {
      if (verdict) {
        verdict.className = "verdict-badge " + s.vClass + " pop";
        verdict.textContent = s.vLabel;
      }
      // verdict-stage glow matches the verdict: changes→red, needsinfo→amber, unclear→indigo
      if (stages[5]) {
        stages[5].classList.remove("changed", "needsinfo", "unclear");
        if (s.vClass === "changes") stages[5].classList.add("changed");
        else if (s.vClass === "needsinfo") stages[5].classList.add("needsinfo");
        else if (s.vClass === "unclear") stages[5].classList.add("unclear");
      }
      if (findings) {
        if (s.findings) { findings.hidden = false; findings.innerHTML = '<span class="sev-dot"></span>' + s.findings; }
        else { findings.hidden = true; findings.innerHTML = ""; }
      }
    }

    var timers = [];
    function at(ms, fn) { timers.push(setTimeout(fn, ms)); }
    function stop() { timers.forEach(clearTimeout); timers = []; }
    var idx = 0;

    function applyStatic() {
      stop(); clearAll();
      var s = scenarios()[0];
      setScenario(s); revealVerdict(s);
      Object.keys(stages).forEach(lit);
      chips.forEach(function (c) { if (s.reviewers.indexOf(c.getAttribute("data-rev")) !== -1) c.classList.add("lit"); });
    }

    function play() {
      stop();
      clearAll();
      var s = scenarios()[idx % scenarios().length];
      setScenario(s);

      // motion preset scales the whole sequence (and the spark CSS duration)
      var M = window.__juryMotion || 1;
      var D = Math.round(720 * M);
      document.documentElement.style.setProperty("--spark-dur", (0.72 * M).toFixed(2) + "s");
      function g(ms) { return Math.round(ms * M); }

      var t = g(250);
      // 0 — input
      at(t, function () { lit(0); }); t += g(520);
      // hop: input → reviewers (spark travels, then destination lights on arrival)
      at(t, function () { spark(0); });
      at(t + D, function () { lit(1); });
      t += D + g(240);
      // reviewers light up one by one (only this scenario's panel)
      var active = chips.filter(function (c) { return s.reviewers.indexOf(c.getAttribute("data-rev")) !== -1; });
      active.forEach(function (c, i) { at(t + i * g(200), function () { c.classList.add("lit"); }); });
      t += active.length * g(200) + g(260);
      // hop: reviewers → round 2 (debate) — only when the panel debates
      at(t, function () { spark(1); });
      if (s.debate) { at(t + D, function () { lit(2); if (debateMark) debateMark.classList.add("lit"); }); }
      t += D + g(460);
      // hop: round 2 → verify
      at(t, function () { spark(2); });
      at(t + D, function () { lit(3); });
      t += D + g(320);
      // hop: verify → chair
      at(t, function () { spark(3); });
      at(t + D, function () { lit(4); });
      t += D + g(320);
      // hop: chair → verdict
      at(t, function () { spark(4); });
      at(t + D, function () { lit(5); revealVerdict(s); });
      t += D + g(200);
      // hold on the verdict, then advance to the next scenario
      t += g(2800);
      at(t, function () { idx++; play(); });
    }

    function restart() { idx = 0; if (reduce) applyStatic(); else play(); }

    // PR / Issue tab switching — swap the mode and restart the loop.
    function selectMode(newMode) {
      if (newMode === mode || !MODES[newMode]) return;
      mode = newMode;
      if (tabPr) {
        tabPr.classList.toggle("on", mode === "pr");
        tabPr.setAttribute("aria-selected", mode === "pr" ? "true" : "false");
        tabPr.setAttribute("tabindex", mode === "pr" ? "0" : "-1");
      }
      if (tabIssue) {
        tabIssue.classList.toggle("on", mode === "issue");
        tabIssue.setAttribute("aria-selected", mode === "issue" ? "true" : "false");
        tabIssue.setAttribute("tabindex", mode === "issue" ? "0" : "-1");
      }
      restart();
    }
    function handleTabKeydown(e) {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        var nextMode = mode === "pr" ? "issue" : "pr";
        selectMode(nextMode);
        var nextTab = nextMode === "pr" ? tabPr : tabIssue;
        if (nextTab) nextTab.focus();
      }
    }
    if (tabPr) {
      tabPr.addEventListener("click", function () { selectMode("pr"); });
      tabPr.addEventListener("keydown", handleTabKeydown);
    }
    if (tabIssue) {
      tabIssue.addEventListener("click", function () { selectMode("issue"); });
      tabIssue.addEventListener("keydown", handleTabKeydown);
    }

    if (reduce) {
      applyStatic();
      return;
    }

    // Start when the pipe is in view (rect-based); loop while it stays mounted.
    var started = false;
    function maybeStart() {
      if (started) return;
      var vh = window.innerHeight || document.documentElement.clientHeight || 800;
      var r = pipe.getBoundingClientRect();
      if (r.top < vh * 0.9 && r.bottom > 0) { started = true; play(); }
    }
    maybeStart();
    window.addEventListener("scroll", maybeStart, { passive: true });
    window.addEventListener("load", maybeStart);
    setTimeout(maybeStart, 300);
  })();

  /* ================================================================
     Interactive "Build your jury" demo
     ================================================================ */
  (function () {
    var ctrls = q(".demo-controls");
    if (!ctrls) return;

    var AGENTS = {
      claude:     { vendor: "anthropic", command: "claude" },
      codex:      { vendor: "openai", command: "codex" },
      agy:        { vendor: "google", command: "agy" },
      qwen:       { vendor: "local", model: "qwen2.5-coder:7b", endpoint: "http://localhost:11434/v1" },
      deepseek:   { vendor: "openai-compatible", endpoint: "https://api.deepseek.com/v1", api_key_env: "DEEPSEEK_API_KEY", model: "deepseek-coder" },
      openrouter: { vendor: "openai-compatible", endpoint: "https://openrouter.ai/api/v1", api_key_env: "OPENROUTER_API_KEY", model: "anthropic/claude-3.5-sonnet" },
      groq:       { vendor: "openai-compatible", endpoint: "https://api.groq.com/openai/v1", api_key_env: "GROQ_API_KEY", model: "llama-3.3-70b-versatile" },
      grok:       { vendor: "openai-compatible", endpoint: "https://api.x.ai/v1", api_key_env: "XAI_API_KEY", model: "grok-2-latest" },
      cursor:     { vendor: "cli", command: "cursor-agent", prompt_mode: "arg" },
      aider:      { vendor: "cli", command: "aider", prompt_mode: "stdin" }
    };
    var LABEL = {
      claude: "Claude Code",
      codex: "Codex",
      agy: "Antigravity",
      qwen: "Local",
      deepseek: "DeepSeek",
      openrouter: "OpenRouter",
      groq: "Groq",
      grok: "Grok",
      cursor: "Cursor CLI",
      aider: "Aider CLI"
    };
    var SEV_RANK = { high: 3, medium: 2, low: 1 };
    // Display label for a reviewer: a real loaded run carries its own labels
    // map; the canned demo falls back to the fixed LABEL table, then the name.
    function labelOf(run, a) { return (run.labels && run.labels[a]) || LABEL[a] || a; }
    // Severity used for ranking/vote logic: real-run items carry the raw
    // severity in .sev (e.g. "major") plus a bucketed .sevClass ("high").
    function sevClassOf(f) { return f.sevClass || f.sev; }

    function selectedAgents() {
      var all = ["claude", "codex", "agy", "qwen", "deepseek", "openrouter", "groq", "grok", "cursor", "aider"];
      return all.filter(function (n) {
        var el = $("ag-" + n);
        return el && el.checked;
      });
    }
    function rounds() { return q('input[name="rounds"]:checked').value; }
    function postmode() { return q('input[name="postmode"]:checked').value; }
    function target() { return q('input[name="target"]:checked').value; }
    function verdictMode() {
      // chair or panel vote — works for both PR and issue review (mode-aware vocab)
      return q('input[name="verdict-mode"]:checked').value;
    }

    function render() {
      var ags = selectedAgents();
      var auto = $("auto").checked;
      var verify = $("verify").checked;
      var pm = postmode();
      var progress = $("progress").checked;
      var tgt = target();
      var vm = verdictMode();
      var isIssue = tgt === "issue";

      // Panel vote works for both PR and issue review (#230) — no longer disabled.
      // PR output (post-mode / progress) is PR-only
      var fsPost = $("fs-postmode");
      if (fsPost) {
        fsPost.classList.toggle("disabled", isIssue);
        qa("input", fsPost).forEach(function (el) {
          el.disabled = isIssue;
          if (isIssue) el.setAttribute("title", "PR output options are not applicable for issue review");
          else el.removeAttribute("title");
        });
        if (isIssue) fsPost.setAttribute("title", "PR output options are not applicable for issue review");
        else fsPost.removeAttribute("title");
      }

      // auto-depth owns rounds/verify
      $("verify").disabled = auto;
      if (auto) $("verify").setAttribute("title", "Auto-depth manages verification automatically");
      else $("verify").removeAttribute("title");
      var verifyOpt = $("verify").closest(".opt");
      verifyOpt.classList.toggle("disabled", auto);
      if (auto) verifyOpt.setAttribute("title", "Auto-depth manages verification automatically");
      else verifyOpt.removeAttribute("title");

      // debate (round 2) needs >=2 successful reviewers — lock it for a solo panel
      var soloPanel = ags.length < 2;
      var r2 = q('input[name="rounds"][value="2"]');
      var r1 = q('input[name="rounds"][value="1"]');
      qa('input[name="rounds"]').forEach(function (el) {
        var lockAuto = auto;
        var lockSolo = soloPanel && el.value === "2";
        el.disabled = lockAuto || lockSolo;
        if (lockAuto) el.setAttribute("title", "Auto-depth manages rounds automatically");
        else if (lockSolo) el.setAttribute("title", "Debate requires at least 2 reviewers");
        else el.removeAttribute("title");

        var opt = el.closest(".opt");
        opt.classList.toggle("disabled", lockAuto || lockSolo);
        if (lockAuto) opt.setAttribute("title", "Auto-depth manages rounds automatically");
        else if (lockSolo) opt.setAttribute("title", "Debate requires at least 2 reviewers");
        else opt.removeAttribute("title");
      });
      // if a solo panel has "2 rounds" selected, fall back to 1 round (review only)
      if (soloPanel && !auto && r2 && r2.checked) { r2.checked = false; if (r1) r1.checked = true; }
      // reflect the reason on the debate option
      if (r2) {
        var opt2 = r2.closest(".opt");
        var hint = opt2.querySelector(".opt-hint");
        if (soloPanel && !auto) {
          if (!hint) { hint = document.createElement("span"); hint.className = "tag opt-hint"; opt2.appendChild(hint); }
          hint.textContent = "needs ≥2";
        } else if (hint) { hint.remove(); }
      }

      var r = parseInt(rounds(), 10);

      var note;
      if (ags.length === 0) {
        note = "Pick at least one reviewer.";
      } else if (auto) {
        note = (isIssue ? "issue completeness · " : "") + "auto-depth · rounds & verify decided per diff · panel never trimmed";
      } else {
        var bits = [ags.length + " reviewer" + (ags.length > 1 ? "s" : "")];
        bits.push(r === 2 && ags.length >= 2 ? "debate" : "no debate");
        bits.push(verify ? "verify on" : "verify off");
        if (isIssue) {
          bits.unshift("issue completeness");
          bits.push(vm === "vote" ? "panel vote" : "chair decides");
        } else {
          bits.push(vm === "vote" ? "panel vote" : "chair decides");
          bits.push(pm === "phased" ? "phased comments" : "one comment");
          if (progress) bits.push("live progress");
        }
        note = bits.join(" · ");
      }
      $("flow-note").textContent = note;

      // generate jury.toml
      var lines = ["[jury]", "rounds = " + r, 'chair = "' + (ags[0] || "claude") + '"', "verify = " + (verify ? "true" : "false")];
      if (auto) lines.push("auto_depth = true");
      if ($("opt-tiered") && $("opt-tiered").checked) lines.push('routing = "tiered"');
      if ($("opt-hints") && $("opt-hints").checked) lines.push("hints = true");
      lines.push("");
      ags.forEach(function (n) {
        var a = AGENTS[n];
        lines.push("[[agent]]");
        lines.push('name = "' + n + '"');
        lines.push('vendor = "' + a.vendor + '"');
        if (a.command) lines.push('command = "' + a.command + '"');
        if (a.endpoint) lines.push('endpoint = "' + a.endpoint + '"');
        if (a.api_key_env) lines.push('api_key_env = "' + a.api_key_env + '"');
        if (a.model) lines.push('model = "' + a.model + '"');
        if (a.prompt_mode) lines.push('prompt_mode = "' + a.prompt_mode + '"');
        lines.push("");
      });
      $("toml-out").textContent = lines.join("\n").trim() + "\n";

      var runBtn = $("run-btn");
      if (runBtn) {
        if (ags.length === 0) {
          runBtn.disabled = true;
          runBtn.setAttribute("title", "Pick at least one reviewer to run.");
        } else {
          runBtn.disabled = false;
          runBtn.removeAttribute("title");
        }
      }
    }

    // Any config change resets the run output: collapse it so the user
    // re-runs and sees the new outcome play out from scratch.
    ctrls.addEventListener("change", function () {
      render();
      // Reset the run: stop any in-flight animation timer and collapse the
      // output (incl. the theater) so the next Run replays from scratch with the
      // new panel/depth — no stale seats, phase marks, or decision banner linger.
      if (runTimer) { clearTimeout(runTimer); runTimer = null; }
      var ro = $("run-out");
      if (ro && !ro.hidden) {
        ro.hidden = true;
        $("term-body").innerHTML = "";
        $("gh-comments").innerHTML = "";
      }
      var theater = $("demo-theater");
      if (theater) { theater.hidden = true; theater.innerHTML = ""; }
      var btn = $("run-btn");
      if (btn) {
        var noAgents = selectedAgents().length === 0;
        btn.disabled = noAgents;
        btn.removeAttribute("aria-busy");
        if (noAgents) {
          btn.setAttribute("title", "Pick at least one reviewer to run.");
        } else {
          btn.removeAttribute("title");
        }
        btn.textContent = "▶ Run review (demo)";
      }
    });
    render();

    /* ---- scripted run ---- */
    var FINDINGS = [
      { id: "sqli", sev: "high", file: "db.py:42", title: "Query built with an f-string — SQL injection", by: ["codex", "claude"], evidence: "user_id flows from the request into the SQL string unescaped" },
      { id: "null", sev: "medium", file: "auth.py:88", title: "token can be None before .expires_at", by: ["claude", "qwen"], evidence: "the cache-miss early return leaves token unbound" },
      { id: "nplus1", sev: "low", file: "db.py:51", title: "N+1 query inside the per-user loop", by: ["agy"], evidence: "a separate fetch per row instead of one batched query" },
      { id: "race", sev: "medium", file: "auth.py:12", title: "Possible race on the shared cache dict", by: ["qwen"], evidence: "two requests could write the same key", refuted: true }
    ];
    // issue-completeness scenario: a parallel sample set of gaps in the issue
    var ISSUE_GAPS = [
      { aspect: "reproduction steps", sev: "high", status: "missing", by: ["claude", "codex"], evidence: "no ordered steps that reliably trigger the bug" },
      { aspect: "expected vs actual", sev: "medium", status: "unclear", by: ["claude", "qwen"], evidence: "describes the symptom but not what was expected to happen" },
      { aspect: "environment / version", sev: "medium", status: "missing", by: ["codex"], evidence: "no OS, runtime, or jury version recorded" },
      { aspect: "scope / acceptance criteria", sev: "low", status: "missing", by: ["qwen"], evidence: "no definition of done to verify a fix against" }
    ];
    // Also encodes ' and ` so a future single-quoted/template attribute context
    // stays safe (security-review hardening; every current sink is double-quoted).
    function esc(s) { return String(s).replace(/[&<>"'`]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;" }[c]; }); }
    function bySev(a, b) { return (SEV_RANK[sevClassOf(b)] || 0) - (SEV_RANK[sevClassOf(a)] || 0); }

    // Each reviewer votes from the worst severity among the items they raised; the
    // vocabulary is mode-aware (matches the real CLI, #230):
    //   PR:    any high → REQUEST CHANGES · any → COMMENT  · none → APPROVE
    //   issue: any high → NEEDS-INFO      · any → UNCLEAR  · none → READY
    var VOTE_VOCAB = {
      pr:    { blocking: "REQUEST CHANGES", middling: "COMMENT", clear: "APPROVE" },
      issue: { blocking: "NEEDS-INFO",      middling: "UNCLEAR", clear: "READY" }
    };
    // rank within each vocabulary (strictest first) for majority tie-breaks
    var VOTE_RANK = { "REQUEST CHANGES": 3, "COMMENT": 2, "APPROVE": 1, "NEEDS-INFO": 3, "UNCLEAR": 2, "READY": 1 };
    function agentVote(a, items, mode) {
      var v = VOTE_VOCAB[mode];
      var mine = items.filter(function (f) { return f.by.indexOf(a) !== -1; });
      if (mine.some(function (f) { return sevClassOf(f) === "high"; })) return v.blocking;
      return mine.length ? v.middling : v.clear;
    }
    function tallyVote(ags, items, mode) {
      var v = VOTE_VOCAB[mode], counts = {};
      counts[v.blocking] = 0; counts[v.middling] = 0; counts[v.clear] = 0;
      var votes = {};
      ags.forEach(function (a) { var vv = agentVote(a, items, mode); votes[a] = vv; counts[vv]++; });
      // majority wins; tie → stricter (higher VOTE_RANK)
      var winner = v.clear, best = -1;
      Object.keys(counts).forEach(function (k) {
        if (counts[k] > best || (counts[k] === best && VOTE_RANK[k] > VOTE_RANK[winner])) { best = counts[k]; winner = k; }
      });
      return { verdict: winner, counts: counts, votes: votes, for: counts[winner], total: ags.length };
    }

    function buildRun() {
      return target() === "issue" ? buildIssueRun() : buildPrRun();
    }

    function buildPrRun() {
      var ags = selectedAgents();
      var auto = $("auto").checked;
      var r = parseInt(rounds(), 10);
      var verify = $("verify").checked;
      var pm = postmode();
      var progress = $("progress").checked;
      var vm = verdictMode();
      var debate = auto ? true : (r === 2 && ags.length >= 2);
      var verifyOn = auto ? true : verify;
      var chair = ags[0];
      var resolved = debate || verifyOn;
      var surfaced = FINDINGS.filter(function (f) { return f.by.some(function (a) { return ags.indexOf(a) !== -1; }); });
      var dropped = surfaced.filter(function (f) { return f.refuted && resolved; });
      var finalF = surfaced.filter(function (f) { return !(f.refuted && resolved); });
      var chairVerdict = finalF.some(function (f) { return f.sev === "high"; }) ? "REQUEST CHANGES" : (finalF.length ? "COMMENT" : "APPROVE");
      var tally = vm === "vote" ? tallyVote(ags, finalF, "pr") : null;
      var verdict = tally ? tally.verdict : chairVerdict;
      var verdictNote = tally ? (tally.for + " of " + tally.total + " voted") : null;

      var con = [];
      con.push(["$ jury --pr 123" + (auto ? " --auto" : "") + (vm === "vote" ? " --decision vote" : "") + (pm === "phased" ? " --post --post-mode phased" : " --post") + (progress ? " --post-progress" : ""), "cmd"]);
      con.push(["→ diff: auth.py, db.py  (+58 −12) · security-sensitive path", "dim"]);
      if (auto) con.push(["[auto-depth] risk=high → rounds=2, verify=on", "warn"]);
      con.push(["round 1: " + ags.length + " agent" + (ags.length > 1 ? "s" : "") + " reviewing", "head"]);
      ags.forEach(function (a) {
        var n = surfaced.filter(function (f) { return f.by.indexOf(a) !== -1; }).length;
        con.push(["  • " + LABEL[a] + " → " + n + " finding" + (n === 1 ? "" : "s"), ""]);
      });
      if (debate) {
        con.push(["round 2: cross-examination", "head"]);
        if (dropped.length) con.push(["  • DISPUTE " + dropped[0].file + " — refuted by the panel (false positive)", "bad"]);
        con.push(["  • AGREE on the remaining findings", ""]);
      } else if (r === 1) {
        con.push(["round 2: skipped (1 round) — a false positive may survive", "dim"]);
      }
      if (verifyOn) {
        con.push(["verify: chair '" + LABEL[chair] + "' judging " + surfaced.length + " candidate findings", "head"]);
        finalF.slice().sort(bySev).forEach(function (f) { con.push(["  ✓ " + f.file + " confirmed (" + f.sev + ")", "ok"]); });
        dropped.forEach(function (f) { con.push(["  ✗ " + f.file + " dropped (unsupported)", "bad"]); });
      }
      if (tally) {
        con.push(["vote: tallying " + ags.length + " reviewer" + (ags.length > 1 ? "s" : ""), "head"]);
        ags.forEach(function (a) { con.push(["  • " + LABEL[a] + " → " + tally.votes[a], ""]); });
        con.push(["decision: panel vote → " + verdict + " · " + verdictNote, "head"]);
      } else {
        con.push(["synthesis: chair '" + LABEL[chair] + "' → " + verdict, "head"]);
      }
      con.push(["done in 1m12s", "dim"]);

      return { mode: "pr", ags: ags, debate: debate, verifyOn: verifyOn, pm: pm, progress: progress, vm: vm, chair: chair, surfaced: surfaced, dropped: dropped, finalF: finalF, verdict: verdict, verdictNote: verdictNote, tally: tally, con: con, r: r, auto: auto };
    }

    function buildIssueRun() {
      var ags = selectedAgents();
      var auto = $("auto").checked;
      var r = parseInt(rounds(), 10);
      var verify = $("verify").checked;
      var vm = verdictMode();
      var debate = auto ? true : (r === 2 && ags.length >= 2);
      var verifyOn = auto ? true : verify;
      var chair = ags[0];
      // gaps this panel surfaced
      var gaps = ISSUE_GAPS.filter(function (g) { return g.by.some(function (a) { return ags.indexOf(a) !== -1; }); });
      var blocking = gaps.filter(function (g) { return g.sev === "high"; });
      // READY when no gaps · NEEDS-INFO when a blocking gap exists · UNCLEAR otherwise
      var chairVerdict = blocking.length ? "NEEDS-INFO" : (gaps.length ? "UNCLEAR" : "READY");
      var tally = vm === "vote" ? tallyVote(ags, gaps, "issue") : null;
      var verdict = tally ? tally.verdict : chairVerdict;
      var verdictNote = tally ? (tally.for + " of " + tally.total + " voted") : null;

      var con = [];
      con.push(["$ jury --issue 42" + (auto ? " --auto" : "") + (vm === "vote" ? " --decision vote" : ""), "cmd"]);
      con.push(["→ issue #42: \"Login times out intermittently\"  · checking completeness", "dim"]);
      if (auto) con.push(["[auto-depth] scaling rounds/verify to the issue", "warn"]);
      con.push(["round 1: " + ags.length + " agent" + (ags.length > 1 ? "s" : "") + " checking the issue", "head"]);
      ags.forEach(function (a) {
        var n = gaps.filter(function (g) { return g.by.indexOf(a) !== -1; }).length;
        con.push(["  • " + LABEL[a] + " → " + n + " gap" + (n === 1 ? "" : "s"), ""]);
      });
      if (debate) {
        con.push(["round 2: cross-examination", "head"]);
        con.push(["  • AGREE the issue is " + (blocking.length ? "not yet actionable" : (gaps.length ? "thin but workable" : "complete")), ""]);
      } else if (r === 1) {
        con.push(["round 2: skipped (1 round)", "dim"]);
      }
      if (verifyOn) {
        con.push(["verify: chair '" + LABEL[chair] + "' confirming " + gaps.length + " gap" + (gaps.length === 1 ? "" : "s"), "head"]);
        gaps.slice().sort(bySev).forEach(function (g) { con.push(["  • " + g.aspect + " — " + g.status + " (" + g.sev + ")", g.sev === "high" ? "bad" : ""]); });
      }
      if (tally) {
        con.push(["vote: tallying " + ags.length + " reviewer" + (ags.length > 1 ? "s" : ""), "head"]);
        ags.forEach(function (a) { con.push(["  • " + LABEL[a] + " → " + tally.votes[a], ""]); });
        con.push(["decision: panel vote → " + verdict + " · " + verdictNote, "head"]);
      } else {
        con.push(["synthesis: chair '" + LABEL[chair] + "' → " + verdict, "head"]);
      }
      con.push(["done in 48s", "dim"]);

      return { mode: "issue", ags: ags, debate: debate, verifyOn: verifyOn, vm: vm, chair: chair, gaps: gaps, blocking: blocking, verdict: verdict, verdictNote: verdictNote, tally: tally, con: con, r: r, auto: auto };
    }

    function verdictClass(v) {
      if (v === "REQUEST CHANGES" || v === "NEEDS-INFO") return "v-bad";
      if (v === "COMMENT" || v === "UNCLEAR") return "v-warn";
      return "v-ok"; // APPROVE / READY
    }
    function verdictBadge(run) {
      var note = run.verdictNote ? ' <span class="v-note">· ' + esc(run.verdictNote) + "</span>" : "";
      return '<div class="gh-verdict ' + verdictClass(run.verdict) + '">' + esc(run.verdict) + note + "</div>";
    }
    function findingRow(f) {
      // f.sev/f.title/etc. may come from a user-loaded run file — escape all of
      // them; the CSS class uses the bucketed severity (always our own value).
      return '<li class="gh-finding"><span class="sev sev-' + esc(sevClassOf(f)) + '">' + esc(f.sev) + '</span> <code>' + esc(f.file) + '</code> — ' + esc(f.title) + '<span class="why">why: ' + esc(f.evidence) + "</span></li>";
    }
    function gapRow(g) {
      return '<li class="gh-finding"><span class="sev sev-' + g.sev + '">' + g.sev + '</span> <code>' + esc(g.aspect) + '</code> — ' + esc(g.status) + '<span class="why">why: ' + esc(g.evidence) + "</span></li>";
    }
    function commentCard(title, inner) {
      return '<div class="gh-comment"><div class="gh-head"><span class="gh-bot"><img src="assets/logos/mark-convergence.svg" alt="">AI Jury</span><span class="gh-meta">' + esc(title) + "</span></div><div class=\"gh-body\">" + inner + "</div></div>";
    }
    function renderComments(run) {
      var html = "";
      var panel = run.ags.map(function (a) { return labelOf(run, a); }).join(", ");

      if (run.mode === "issue") {
        var body = verdictBadge(run)
          + (run.gaps.length ? "<ul class='gh-findings'>" + run.gaps.slice().sort(bySev).map(gapRow).join("") + "</ul>"
                             : "<p>Complete — repro, expected/actual, and scope are all present.</p>")
          + '<div class="gh-foot">issue #42 · panel: ' + esc(panel) + " · rounds: " + (run.auto ? "auto" : run.r) + (run.verifyOn ? " · verified" : "") + (run.vm === "vote" ? " · panel vote" : " · chair decides") + "</div>";
        html += commentCard(run.ags.length + "-reviewer completeness check", body);
        $("gh-comments").innerHTML = html;
        return;
      }

      if (run.progress) {
        html += '<div class="gh-comment sticky"><div class="gh-head"><span class="gh-bot"><img src="assets/logos/mark-convergence.svg" alt="">AI Jury</span><span class="gh-meta">live status · edited</span></div><div class="gh-body">✅ synthesis complete — round 1 → ' + (run.debate ? "debate → " : "") + (run.verifyOn ? "verify → " : "") + "verdict</div></div>";
      }
      if (run.pm === "phased") {
        var r1 = "<ul class='gh-list'>" + run.ags.map(function (a) {
          var fs = run.surfaced.filter(function (f) { return f.by.indexOf(a) !== -1; });
          return "<li><b>" + esc(labelOf(run, a)) + "</b>: " + (fs.length ? fs.map(function (f) { return esc(f.file); }).join(", ") : "no findings") + "</li>";
        }).join("") + "</ul>";
        html += commentCard("Round 1 · independent review", r1);
        var dbody = run.debate ? (run.dropped.length ? "Refuted <code>" + esc(run.dropped[0].file) + "</code> as a false positive; agreed on the rest." : "Reviewers cross-examined and agreed on the findings.") : "Skipped (1 round). With debate, the panel cross-examines and filters false positives.";
        html += commentCard("Round 2 · debate", dbody);
        var dec = verdictBadge(run) + (run.finalF.length ? "<ul class='gh-findings'>" + run.finalF.slice().sort(bySev).map(findingRow).join("") + "</ul>" : "<p>No blocking findings.</p>");
        html += commentCard((run.vm === "vote" ? "Panel vote · " : "Decision · ") + panel, dec);
      } else {
        var pbody = verdictBadge(run) + (run.finalF.length ? "<ul class='gh-findings'>" + run.finalF.slice().sort(bySev).map(findingRow).join("") + "</ul>" : "<p>No blocking findings — looks good.</p>") + '<div class="gh-foot">panel: ' + esc(panel) + " · rounds: " + (run.auto ? "auto" : run.r) + (run.verifyOn ? " · verified" : "") + (run.vm === "vote" ? " · panel vote" : "") + "</div>";
        html += commentCard(run.ags.length + "-reviewer jury", pbody);
      }
      $("gh-comments").innerHTML = html;
    }
    function consoleLine(line) {
      var span = document.createElement("span");
      span.className = "cl " + (line[1] || "");
      span.textContent = line[0];
      return span;
    }

    var runTimer = null;
    function setTermTitle(run) {
      var t = $("term-title");
      if (t && run.con.length) t.textContent = run.con[0][0].replace(/^\$\s*/, "");
    }
    // ---- Animated theater preview (issue #365) ----------------------------
    // An in-browser echo of the CLI's --theater: jurors around a table light up
    // per phase, then the decision lands. Illustrative; the real animation is
    // the CLI. Mirrors the chosen panel / depth / decision.
    var TH_PHASES = [["review", "REVIEW"], ["debate", "DEBATE"], ["verify", "VERIFY"], ["decision", "DECISION"]];
    var TH_ORDER = ["review", "debate", "verify", "decision"];

    function theaterBeats(run) {
      var beats = [], spoken = [], what = run.mode === "issue" ? "issue" : "diff";
      run.ags.forEach(function (a) {
        beats.push({ phase: "review", speaker: a, spoken: spoken.slice(), center: labelOf(run, a) + " reviews the " + what + "…" });
        spoken.push(a);
      });
      var all = run.ags.slice();
      if (run.debate) beats.push({ phase: "debate", spoken: all, center: "the panel debates ⇄ (round 2)" });
      if (run.verifyOn) beats.push({ phase: "verify", spoken: all, center: "verifying findings — chair " + labelOf(run, run.chair) });
      var how = run.vm === "vote" ? "panel vote" : "chair: " + labelOf(run, run.chair);
      var note = run.verdictNote ? "  ·  " + run.verdictNote : "";
      beats.push({ phase: "decision", spoken: all, banner: true, center: run.verdict + note + "  ·  " + how });
      return beats;
    }

    function renderTheater(run) {
      var stage = $("demo-theater"), ags = run.ags, mid = Math.ceil(ags.length / 2);
      function seatRow(list) {
        return list.map(function (a) {
          // seat names may come from a user-loaded run file — escape them
          return '<div class="seat" data-seat="' + esc(a) + '"><span class="dot d-' + esc(a) + '"></span><span class="snm">' + esc(labelOf(run, a)) + "</span></div>";
        }).join("");
      }
      var strip = TH_PHASES.filter(function (p) {
        return (p[0] !== "debate" || run.debate) && (p[0] !== "verify" || run.verifyOn);
      }).map(function (p) {
        return '<span class="th-ph" data-ph="' + p[0] + '">' + p[1] + "</span>";
      }).join('<span class="th-sep" aria-hidden="true">·</span>');
      stage.innerHTML =
        '<div class="th-strip">' + strip + "</div>" +
        '<div class="th-table">' +
          '<div class="th-seats th-top">' + seatRow(ags.slice(0, mid)) + "</div>" +
          '<div class="th-center" id="th-center">the jury convenes…</div>' +
          '<div class="th-seats th-bot">' + seatRow(ags.slice(mid)) + "</div>" +
        "</div>";
      stage.hidden = false;
    }

    function applyBeat(run, b) {
      var stage = $("demo-theater");
      qa(".th-ph", stage).forEach(function (el) {
        var ph = el.getAttribute("data-ph");
        el.classList.toggle("on", ph === b.phase);
        el.classList.toggle("done", TH_ORDER.indexOf(ph) < TH_ORDER.indexOf(b.phase));
      });
      qa(".seat", stage).forEach(function (el) {
        var a = el.getAttribute("data-seat");
        el.classList.toggle("speaking", b.speaker === a);
        el.classList.toggle("done", b.spoken.indexOf(a) !== -1);
      });
      var c = $("th-center");
      c.className = "th-center" + (b.banner ? " banner " + verdictClass(run.verdict) : "");
      c.textContent = b.center;
    }

    function playTheater(run, onDone) {
      renderTheater(run);
      var beats = theaterBeats(run);
      if (reduce) { applyBeat(run, beats[beats.length - 1]); onDone(); return; }
      var i = 0;
      (function tick() {
        applyBeat(run, beats[i++]);
        if (i < beats.length) runTimer = setTimeout(tick, 950);
        else { runTimer = null; setTimeout(onDone, 650); }
      })();
    }

    function streamTerminal(run, onDone) {
      var i = 0;
      (function step() {
        if (i < run.con.length) {
          $("term-body").appendChild(consoleLine(run.con[i++]));
          $("term-body").scrollTop = $("term-body").scrollHeight;
          runTimer = setTimeout(step, 180);
        } else { runTimer = null; onDone(); }
      })();
    }

    function runDemo() {
      if (selectedAgents().length === 0) { $("flow-note").textContent = "Pick at least one reviewer to run."; return; }
      var run = buildRun();
      var btn = $("run-btn");
      setTermTitle(run);
      $("run-out").hidden = false;
      $("term-body").innerHTML = "";
      $("gh-comments").innerHTML = "";
      if (runTimer) { clearTimeout(runTimer); runTimer = null; }

      if (reduce) {
        playTheater(run, function () {});
        run.con.forEach(function (l) { $("term-body").appendChild(consoleLine(l)); });
        renderComments(run);
        return;
      }
      btn.disabled = true;
      btn.setAttribute("title", "Review is currently running");
      btn.setAttribute("aria-busy", "true");
      btn.textContent = "Running review...";
      playTheater(run, function () {
        streamTerminal(run, function () {
          renderComments(run);
          // The reviewer selection can change while the demo animation is
          // still playing (the change handler only resets the run output,
          // not this in-flight callback) — re-check it instead of always
          // re-enabling, or the button could pop back on with 0 reviewers
          // selected, contradicting the disabled-when-empty invariant.
          var stillNoAgents = selectedAgents().length === 0;
          btn.disabled = stillNoAgents;
          if (stillNoAgents) btn.setAttribute("title", "Pick at least one reviewer to run.");
          else btn.removeAttribute("title");
          btn.removeAttribute("aria-busy");
          btn.textContent = "▶ Run review (demo)";
        });
      });
    }
    $("run-btn").addEventListener("click", runDemo);

    /* ================================================================
       Load a real run (issue #450)
       Replay a serialized outcome JSON through the same theater. Accepts
       exactly the shapes `jury replay` accepts: the bare outcome_to_dict
       serialization (top-level "reviews" array) or a cache entry wrapping
       it under "outcome". Fully client-side — the file never leaves the
       browser, and every file-sourced string is escaped or textContent'd.
       ================================================================ */
    // Real severities are critical/major/minor/nit/info (findings.py);
    // bucket them onto the site's three sev pills and the vote logic.
    function sevBucket(sev) {
      var s = String(sev || "").toLowerCase();
      if (s === "critical" || s === "major" || s === "high") return "high";
      if (s === "minor" || s === "medium") return "medium";
      return "low"; // nit · info · unknown
    }
    function clip(s, n) { s = String(s === null || s === undefined ? "" : s); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
    // The chair synthesis opens with "## Verdict\n<HEADLINE> — …": pull the
    // decision token out of it (PR and issue vocabularies both accepted).
    function chairHeadline(text) {
      if (typeof text !== "string" || !text) return null;
      var m = /verdict[^a-z0-9]{0,12}(request[ _-]changes|needs[ _-]info|approve|comment|ready|unclear)\b/i.exec(text);
      if (!m) return null;
      var v = m[1].toUpperCase().replace(/[_-]+/g, " ");
      return v === "NEEDS INFO" ? "NEEDS-INFO" : v;
    }

    // parseOutcomeJson(text, name) -> { run } on success, { error } otherwise.
    // Pure: no DOM access, no side effects.
    function parseOutcomeJson(text, name) {
      function isObj(x) { return !!x && typeof x === "object" && !Array.isArray(x); }
      var data;
      try { data = JSON.parse(text); } catch (e) { return { error: "That file isn't valid JSON." }; }
      // cache entry → unwrap the serialized outcome it carries
      if (isObj(data) && isObj(data.outcome) && Array.isArray(data.outcome.reviews)) data = data.outcome;
      if (isObj(data) && !Array.isArray(data.reviews) && data.schema_version && data.metadata) {
        return { error: "That's a jury JSON report — load the serialized outcome that `jury replay` accepts instead." };
      }
      if (!isObj(data) || !Array.isArray(data.reviews) || !data.reviews.length) {
        return { error: "That JSON doesn't look like a serialized jury outcome (no reviews found)." };
      }

      var ags = [], labels = {}, failed = {}, counts = {};
      data.reviews.forEach(function (r) {
        if (!isObj(r) || typeof r.agent !== "string" || !r.agent) return;
        var a = r.agent;
        if (ags.indexOf(a) === -1) { ags.push(a); labels[a] = clip(a, 40); counts[a] = 0; }
        counts[a] += Array.isArray(r.findings) ? r.findings.length : 0;
        if (r.ok === false) failed[a] = true;
      });
      if (!ags.length) return { error: "That outcome has no usable reviewers in its reviews list." };

      function mkItem(sev, file, line, title, evidence, by, status) {
        var loc = clip(file, 120);
        if (loc && line !== null && line !== undefined && line !== "") loc += ":" + clip(line, 8);
        return {
          sev: clip(sev, 12) || "info", sevClass: sevBucket(sev),
          file: loc || "(no file)", title: clip(title, 300), evidence: clip(evidence, 300),
          by: by, status: typeof status === "string" ? status : ""
        };
      }
      // findings: prefer the deduped consensus groups (severity/status/
      // reviewers/representative); fall back to the flat findings list.
      var groups = Array.isArray(data.groups) ? data.groups.filter(isObj) : [];
      var items = groups.map(function (g) {
        var rep = isObj(g.representative) ? g.representative : {};
        var by = (Array.isArray(g.reviewers) ? g.reviewers : []).filter(function (x) { return typeof x === "string"; });
        return mkItem(g.severity, rep.file, rep.line, rep.claim, rep.evidence, by, g.status);
      });
      if (!items.length && Array.isArray(data.findings)) {
        items = data.findings.filter(isObj).map(function (f) {
          return mkItem(f.severity, f.file, f.line, f.claim, f.evidence,
            typeof f.reviewer === "string" && f.reviewer ? [f.reviewer] : [], "");
        });
      }
      // verify results: verified → confirmed · unsupported → dropped ·
      // needs_human_decision / unverified → kept (surfaced to the human)
      var dropped = items.filter(function (it) { return it.status === "unsupported"; });
      var finalF = items.filter(function (it) { return it.status !== "unsupported"; });

      var rounds = parseInt(data.rounds_executed, 10) || 1;
      var debate = rounds >= 2 || (Array.isArray(data.debate) && data.debate.length > 0);
      var verifyOn = isObj(data.verify) || (Array.isArray(data.verdicts) && data.verdicts.length > 0);
      var chair = typeof data.chair === "string" && data.chair ? data.chair : ags[0];
      if (!labels[chair]) labels[chair] = clip(chair, 40);

      // decision — mirror the CLI's two modes: prefer the chair-synthesis
      // headline; without one, re-tally a panel vote from the group severities.
      var okAgs = ags.filter(function (a) { return !failed[a]; });
      var voters = okAgs.length ? okAgs : ags;
      var verdict = chairHeadline(isObj(data.synthesis) ? data.synthesis.output : null);
      var vm = "chair", tally = null, verdictNote = null;
      if (!verdict) {
        tally = tallyVote(voters, finalF, "pr");
        verdict = tally.verdict; vm = "vote";
        verdictNote = tally.for + " of " + tally.total + " voted";
      }

      var run = {
        mode: "pr", real: true, ags: ags, labels: labels, chair: chair,
        debate: debate, verifyOn: verifyOn, pm: "single", progress: false,
        vm: vm, surfaced: items, dropped: dropped, finalF: finalF,
        verdict: verdict, verdictNote: verdictNote, tally: tally,
        r: rounds, auto: false
      };
      run.con = realCon(run, counts, failed, data, name);
      return { run: run };
    }

    // Terminal transcript for a loaded run (all lines land via textContent).
    function realCon(run, counts, failed, data, name) {
      var con = [];
      con.push(["$ jury replay " + clip(name || "run.json", 60), "cmd"]);
      con.push(["→ real run · " + run.ags.length + " reviewer" + (run.ags.length === 1 ? "" : "s") + " · rounds executed: " + run.r + (data.from_cache ? " · from cache" : ""), "dim"]);
      con.push(["round 1: independent review", "head"]);
      run.ags.forEach(function (a) {
        if (failed[a]) con.push(["  • " + labelOf(run, a) + " → failed", "bad"]);
        else con.push(["  • " + labelOf(run, a) + " → " + counts[a] + " finding" + (counts[a] === 1 ? "" : "s"), ""]);
      });
      if (run.debate) con.push(["round 2: cross-examination", "head"]);
      if (run.verifyOn) {
        con.push(["verify: chair '" + labelOf(run, run.chair) + "' judged " + run.surfaced.length + " grouped finding" + (run.surfaced.length === 1 ? "" : "s"), "head"]);
        run.surfaced.slice().sort(bySev).forEach(function (it) {
          if (it.status === "verified") con.push(["  ✓ " + it.file + " confirmed (" + it.sev + ")", "ok"]);
          else if (it.status === "unsupported") con.push(["  ✗ " + it.file + " dropped (unsupported)", "bad"]);
          else if (it.status === "needs_human_decision") con.push(["  ◐ " + it.file + " — needs a human decision", "warn"]);
          else con.push(["  • " + it.file + " (" + it.sev + ")", ""]);
        });
      }
      if (run.tally) {
        con.push(["decision: no chair headline in the artifact — re-tallying a panel vote", "head"]);
        run.ags.forEach(function (a) { if (run.tally.votes[a]) con.push(["  • " + labelOf(run, a) + " → " + run.tally.votes[a], ""]); });
        con.push(["decision: panel vote → " + run.verdict + " · " + run.verdictNote, "head"]);
      } else {
        con.push(["synthesis: chair '" + labelOf(run, run.chair) + "' → " + run.verdict, "head"]);
      }
      if (Array.isArray(data.warnings) && data.warnings.length) {
        con.push(["⚠ " + data.warnings.length + " warning" + (data.warnings.length === 1 ? "" : "s") + " recorded in the run", "warn"]);
      }
      con.push(["replayed in the browser — nothing was executed", "dim"]);
      return con;
    }
    // exposed for manual poking / future harnesses; the site never calls it
    window.__juryDemo = { parseOutcomeJson: parseOutcomeJson };

    /* ---- file input + drag-drop wiring -------------------------------- */
    (function () {
      var input = $("run-file"), zone = $("load-run-zone"), status = $("load-run-status"), backBtn = $("back-to-demo");
      if (!input || !zone || !status) return;
      var MAX = 8 * 1024 * 1024; // client-side cap — a real outcome is a few hundred KB at most
      var realLoaded = false;

      function setStatus(msg, kind) {
        status.textContent = msg;
        status.className = "load-run-status" + (kind ? " " + kind : "");
      }
      function playRealRun(run) {
        if (runTimer) { clearTimeout(runTimer); runTimer = null; }
        setTermTitle(run);
        $("run-out").hidden = false;
        $("term-body").innerHTML = "";
        $("gh-comments").innerHTML = "";
        if (reduce) {
          // reduced motion: jump straight to the final beat + full transcript
          playTheater(run, function () {});
          run.con.forEach(function (l) { $("term-body").appendChild(consoleLine(l)); });
          renderComments(run);
          return;
        }
        playTheater(run, function () {
          streamTerminal(run, function () { renderComments(run); });
        });
      }
      function backToDemo() {
        if (!realLoaded) return;
        realLoaded = false;
        if (runTimer) { clearTimeout(runTimer); runTimer = null; }
        $("run-out").hidden = true;
        $("term-body").innerHTML = "";
        $("gh-comments").innerHTML = "";
        var th = $("demo-theater");
        if (th) { th.hidden = true; th.innerHTML = ""; }
        if (backBtn) backBtn.hidden = true;
        input.value = "";
        setStatus("Back to the demo — pick a panel and hit Run review.", "");
      }
      function handleFile(file) {
        if (!file) return;
        if (file.size > MAX) {
          setStatus("“" + clip(file.name, 60) + "” is too large (" + Math.ceil(file.size / (1024 * 1024)) + " MB — the cap is 8 MB). Sticking with the demo.", "err");
          return;
        }
        var reader = new FileReader();
        reader.onerror = function () { setStatus("Couldn't read “" + clip(file.name, 60) + "”. Sticking with the demo.", "err"); };
        reader.onload = function () {
          var res = parseOutcomeJson(String(reader.result), file.name);
          if (res.error) {
            setStatus(res.error + " Sticking with the demo.", "err");
            return;
          }
          var run = res.run;
          realLoaded = true;
          if (backBtn) backBtn.hidden = false;
          setStatus("Loaded “" + clip(file.name, 60) + "” — " + run.ags.length + " reviewer" + (run.ags.length === 1 ? "" : "s") + ", " +
            run.finalF.length + " finding" + (run.finalF.length === 1 ? "" : "s") + " → " + run.verdict + ".", "ok");
          playRealRun(run);
        };
        reader.readAsText(file);
      }

      input.addEventListener("change", function () {
        handleFile(input.files && input.files[0]);
        input.value = ""; // re-selecting the same file re-fires change
      });
      ["dragenter", "dragover"].forEach(function (ev) {
        zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add("dragover"); });
      });
      ["dragleave", "dragend"].forEach(function (ev) {
        zone.addEventListener(ev, function () { zone.classList.remove("dragover"); });
      });
      zone.addEventListener("drop", function (e) {
        e.preventDefault();
        zone.classList.remove("dragover");
        handleFile(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]);
      });
      if (backBtn) {
        backBtn.addEventListener("click", function () { backToDemo(); input.focus(); });
      }
      // Escape backs out of a loaded run (unless the mobile menu owns Escape)
      document.addEventListener("keydown", function (e) {
        if (e.key !== "Escape" || !realLoaded) return;
        var menu = $("nav-mobile");
        if (menu && menu.classList.contains("open")) return;
        backToDemo();
        input.focus();
      });
      // touching the demo controls or Run review hands the stage back to the demo
      ctrls.addEventListener("change", function () {
        if (!realLoaded) return;
        realLoaded = false;
        if (backBtn) backBtn.hidden = true;
        setStatus("", "");
      });
      $("run-btn").addEventListener("click", function () {
        if (!realLoaded) return;
        realLoaded = false;
        if (backBtn) backBtn.hidden = true;
        setStatus("", "");
      });
    })();
  })();

  /* ---- Integrations & Ecosystem Grid -------------------------------- */
  (function initIntegrations() {
    var grid = $("integration-grid");
    if (!grid) return;

    function esc(s) {
      return String(s).replace(/[&<>"'`]/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;" }[c];
      });
    }

    // Authentic crisp brand SVG definitions
    var SVG_ICONS = {
      claude: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M14.07 3.5l6.43 17h-3.64l-1.39-3.79H8.53L7.14 20.5H3.5l6.43-17h4.14zm-4.38 10.36h4.62L12 7.76l-2.31 6.1z"/></svg>',
      openai: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M22.28 9.87a5.98 5.98 0 0 0-.52-4.91 6.05 6.05 0 0 0-6.51-2.9A6.06 6.06 0 0 0 4.96 4.1a5.98 5.98 0 0 0-3.99 4.16 6.05 6.05 0 0 0 .74 7.12 5.98 5.98 0 0 0 .51 4.91 6.05 6.05 0 0 0 6.51 2.9A6.06 6.06 0 0 0 19.04 19.9a5.98 5.98 0 0 0 3.99-4.16 6.05 6.05 0 0 0-.75-7.12v1.25zM12 15a3 3 0 1 1 0-6 3 3 0 0 1 0 6z"/></svg>',
      google: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2L14.4 8.6L21 11L14.4 13.4L12 20L9.6 13.4L3 11L9.6 8.6L12 2z"/></svg>',
      cursor: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2L4 7v10l8 5 8-5V7l-8-5zm0 2.2l5.8 3.6-5.8 3.6-5.8-3.6L12 4.2zm-6 5.1l5 3.1v6.4l-5-3.1V9.3zm12 6.4l-5 3.1v-6.4l5-3.1v6.4z"/></svg>',
      aider: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a2 2 0 0 1 2 2v1h3a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-1v2a1 1 0 0 1-1.7.7L11.6 18H7a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h3V4a2 2 0 0 1 2-2zm-3 8a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm6 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z"/></svg>',
      xai: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z"/></svg>',
      kimi: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 14.59L8.41 12 13 7.41V10h4v4h-4z"/></svg>',
      devin: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l8.66 5v10L12 22l-8.66-5V7L12 2zm0 2.31L5.34 8.15v7.7L12 19.69l6.66-3.84v-7.7L12 4.31z"/></svg>',
      trae: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2L2 19.5h20L12 2zm0 4.5l6.5 11.5h-13L12 6.5z"/></svg>',
      opencode: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M9.4 16.6L4.8 12l4.6-4.6L8 6l-6 6 6 6 1.4-1.4zm5.2 0l4.6-4.6-4.6-4.6L16 6l6 6-6 6-1.4-1.4z"/></svg>',
      hermes: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2L9 8h6l-3-6zm-6 8l-4 6 6-2-2-4zm12 0l-2 4 6 2-4-6zm-7 2v8l3 2 3-2v-8h-6z"/></svg>',
      deepseek: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12c0 3.5 1.8 6.58 4.54 8.38l-1.04 1.8c-.2.34.05.77.44.77h12.12c.39 0 .64-.43.44-.77l-1.04-1.8C19.2 18.58 21 15.5 21 12c0-5.52-4.48-10-10-10zm-3 12a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm6 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3z"/></svg>',
      ollama: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a4 4 0 0 0-4 4v2H7a3 3 0 0 0-3 3v6a3 3 0 0 0 3 3h10a3 3 0 0 0 3-3v-6a3 3 0 0 0-3-3h-1V6a4 4 0 0 0-4-4zm-2 11a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm4 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3z"/></svg>',
      openrouter: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm1 14.59L8.41 12 13 7.41V10h4v4h-4z"/></svg>',
      groq: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13 2L3 14h8l-2 8 10-12h-8l2-8z"/></svg>',
      together: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm-6 8a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm12 0a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm-6 6a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"/></svg>',
      aws: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2L2 7l10 5 10-5-10-5zm0 8.5L4.5 7 12 3.2 19.5 7 12 10.5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>',
      azure: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13.05 2.5L5.7 15.3l4.6 6.2h8l-5.25-19zm-3.6 13.5l-4.75-2.7L2 17.5l7.45 4v-5.5z"/></svg>',
      vllm: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2L4 7v10l8 5 8-5V7l-8-5zm0 3.5l5.5 3.4-5.5 3.4-5.5-3.4L12 5.5z"/></svg>',
      keel: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2L3 19h18L12 2zm0 4.2l5.4 10.8H6.6L12 6.2zM11 13h2v3h-2v-3z"/></svg>',
      github: '<svg viewBox="0 0 24 24" fill="currentColor"><path fill-rule="evenodd" clip-rule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"/></svg>',
      gitlab: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M22.65 14.39L12 22.13 1.35 14.39a.84.84 0 0 1-.3-.94l1.22-3.78 2.44-7.51A.42.42 0 0 1 5.5 2a.43.43 0 0 1 .41.29l2.45 7.53h7.28l2.45-7.53A.43.43 0 0 1 18.5 2a.42.42 0 0 1 .39.26l2.44 7.51 1.22 3.78a.84.84 0 0 1-.3.94z"/></svg>',
      precommit: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 14.17V17a1 1 0 0 1-2 0v-.83a3.001 3.001 0 0 1 0-5.34V7a1 1 0 0 1 2 0v3.83a3.001 3.001 0 0 1 0 5.34zM12 14a1 1 0 1 0 0-2 1 1 0 0 0 0 2z"/></svg>',
      homebrew: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 3h16v3H4V3zm1 4h14v10a4 4 0 0 1-4 4H9a4 4 0 0 1-4-4V7zm2 3v7a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2v-7H7z"/></svg>',
      curl: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2zM4 18V8h16v10H4zm4-7l4 3-4 3v-6zm6 5h4v1h-4v-1z"/></svg>',
      docker: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13.98 11.08h-2.11V8.97h2.11v2.11zm-2.64 0H9.23V8.97h2.11v2.11zm-2.63 0H6.6V8.97h2.11v2.11zm7.9 0h-2.11V8.97h2.11v2.11zm-2.63-2.63h-2.11V6.34h2.11v2.11zm-2.64 0H9.23V6.34h2.11v2.11zm5.27 0h-2.11V6.34h2.11v2.11zm4.4 2.63c-.4-.26-.88-.42-1.39-.42-.14 0-.27.01-.4.04-.32-1.37-1.34-2.15-2.61-2.15v2.53H1.08c-.05.34-.08.68-.08 1.03 0 4.14 3.36 7.5 7.5 7.5 4.8 0 8.77-3.4 9.63-7.9 1.13.11 2.21-.52 2.59-1.57-.35.43-.88.74-1.42.74-.07 0-.14 0-.2-.01z"/></svg>',
      mcp: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a3 3 0 0 0-3 3v2H7a3 3 0 0 0-3 3v2h2v4H4v2a3 3 0 0 0 3 3h2v2a3 3 0 0 0 6 0v-2h2a3 3 0 0 0 3-3v-2h-2v-4h2v-2a3 3 0 0 0-3-3h-2V5a3 3 0 0 0-3-3zm1 5V5a1 1 0 0 0-2 0v2h2zm-5 4h8a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1zm3 10v-2h2v2a1 1 0 0 1-2 0z"/></svg>',
      bot: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a2 2 0 0 1 2 2c0 .74-.4 1.39-1 1.73V7h4a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-8a3 3 0 0 1 3-3h4V5.73A2.001 2.001 0 0 1 12 2zM9 11a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm6 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z"/></svg>',
      vscode: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M17.5 2.5L7.8 10.7 3.2 7.2 1.5 8.4l4.2 3.6-4.2 3.6 1.7 1.2 4.6-3.5 9.7 8.2 5-2.5V5l-5-2.5zm1.5 4.8v9.4L13.2 12 19 7.3z"/></svg>',
      generic: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="9"/></svg>'
    };

    var INTEGRATIONS = [
      // 1. AI Assistants & CLIs
      {
        id: "claude-code",
        name: "Claude Code",
        vendor: "Anthropic",
        cat: "assistants",
        badge: "Native CLI",
        badgeType: "accent",
        iconKey: "claude",
        color: "var(--c-claude)",
        desc: "Autonomous agent CLI by Anthropic with direct filesystem, bash, and git tools.",
        config: '[[agent]]\nname = "claude"\nvendor = "anthropic"\ncommand = "claude"',
        command: "jury --pr 123 --chair claude"
      },
      {
        id: "codex-cli",
        name: "Codex CLI",
        vendor: "OpenAI",
        cat: "assistants",
        badge: "Native CLI",
        badgeType: "green",
        iconKey: "openai",
        color: "var(--c-codex)",
        desc: "Official OpenAI terminal coding assistant and automated PR reviewer.",
        config: '[[agent]]\nname = "codex"\nvendor = "openai"\ncommand = "codex"',
        command: "jury --pr 123"
      },
      {
        id: "antigravity",
        name: "Google Antigravity",
        vendor: "Google DeepMind",
        cat: "assistants",
        badge: "Native CLI",
        badgeType: "accent",
        iconKey: "google",
        color: "var(--c-agy)",
        desc: "Google DeepMind's autonomous agent framework and review orchestrator.",
        config: '[[agent]]\nname = "agy"\nvendor = "google"\ncommand = "agy"',
        command: "jury --pr 123"
      },
      {
        id: "cursor-cli",
        name: "Cursor CLI",
        vendor: "Anysphere",
        cat: "assistants",
        badge: "Agent CLI",
        badgeType: "",
        iconKey: "cursor",
        color: "var(--c-cursor)",
        desc: "Headless CLI agent from the popular AI-native code editor.",
        config: '[[agent]]\nname = "cursor"\nvendor = "cursor"\ncommand = "agent"',
        command: "jury --pr 123"
      },
      {
        id: "aider",
        name: "Aider CLI",
        vendor: "Paul Gauthier",
        cat: "assistants",
        badge: "Terminal Agent",
        badgeType: "",
        iconKey: "aider",
        color: "var(--c-aider)",
        desc: "Popular terminal pair-programming agent driven in non-interactive review mode.",
        config: '[[agent]]\nname = "aider"\nvendor = "aider"\ncommand = "aider --message"',
        command: "jury --pr 123"
      },
      {
        id: "grok-cli",
        name: "Grok CLI",
        vendor: "xAI",
        cat: "assistants",
        badge: "Agent CLI",
        badgeType: "",
        iconKey: "xai",
        color: "var(--c-grok)",
        desc: "xAI official command-line interface for multi-turn adversarial code review.",
        config: '[[agent]]\nname = "grok"\nvendor = "grok"\ncommand = "grok"',
        command: "jury --pr 123"
      },
      {
        id: "kimi-cli",
        name: "Kimi CLI",
        vendor: "Moonshot AI",
        cat: "assistants",
        badge: "Long Context",
        badgeType: "",
        iconKey: "kimi",
        color: "#10b981",
        desc: "Moonshot AI coding assistant optimized for large codebases and repos.",
        config: '[[agent]]\nname = "kimi"\nvendor = "kimi"\ncommand = "kimi"',
        command: "jury --pr 123"
      },
      {
        id: "devin",
        name: "Devin Agent",
        vendor: "Cognition",
        cat: "assistants",
        badge: "Autonomous",
        badgeType: "",
        iconKey: "devin",
        color: "#6366f1",
        desc: "Headless autonomous software engineer agent integration.",
        config: '[[agent]]\nname = "devin"\nvendor = "devin"\ncommand = "devin run"',
        command: "jury --pr 123"
      },
      {
        id: "trae",
        name: "Trae CLI",
        vendor: "ByteDance",
        cat: "assistants",
        badge: "Agent CLI",
        badgeType: "",
        iconKey: "trae",
        color: "#06b6d4",
        desc: "ByteDance adaptive AI coding partner and terminal reviewer.",
        config: '[[agent]]\nname = "trae"\nvendor = "trae"\ncommand = "trae"',
        command: "jury --pr 123"
      },
      {
        id: "opencode",
        name: "OpenCode",
        vendor: "Open Source",
        cat: "assistants",
        badge: "Open Source",
        badgeType: "green",
        iconKey: "opencode",
        color: "#10b981",
        desc: "Community-driven open source terminal coding agent.",
        config: '[[agent]]\nname = "opencode"\nvendor = "opencode"\ncommand = "opencode review"',
        command: "jury --pr 123"
      },
      {
        id: "hermes",
        name: "Hermes Agent",
        vendor: "Nous Research",
        cat: "assistants",
        badge: "Reasoning",
        badgeType: "",
        iconKey: "hermes",
        color: "#8b5cf6",
        desc: "Autonomous open-weights agent specialized in reasoning and tool orchestration.",
        config: '[[agent]]\nname = "hermes"\nvendor = "local"\nmodel = "hermes-3:8b"',
        command: "jury --pr 123"
      },

      // 2. LLM Backends & APIs
      {
        id: "anthropic-api",
        name: "Anthropic Claude API",
        vendor: "Hosted API",
        cat: "backends",
        badge: "Sonnet 3.7",
        badgeType: "accent",
        iconKey: "claude",
        color: "var(--c-claude)",
        desc: "Direct Claude Sonnet/Opus API access without installing agent CLIs.",
        config: '[[agent]]\nname = "claude-api"\nvendor = "anthropic-api"\napi_key_env = "ANTHROPIC_API_KEY"\nmodel = "claude-3-7-sonnet-20250219"',
        command: "ANTHROPIC_API_KEY=... jury --pr 123"
      },
      {
        id: "openai-api",
        name: "OpenAI GPT-4o / o1",
        vendor: "Hosted API",
        cat: "backends",
        badge: "o1 / GPT-4o",
        badgeType: "green",
        iconKey: "openai",
        color: "var(--c-codex)",
        desc: "Direct OpenAI API integration for o1 reasoning and GPT-4o reviews.",
        config: '[[agent]]\nname = "codex-api"\nvendor = "openai-api"\napi_key_env = "OPENAI_API_KEY"\nmodel = "gpt-4o"',
        command: "OPENAI_API_KEY=... jury --pr 123"
      },
      {
        id: "google-gemini",
        name: "Google Gemini API",
        vendor: "Hosted API",
        cat: "backends",
        badge: "2.5 Pro / Flash",
        badgeType: "accent",
        iconKey: "google",
        color: "var(--c-agy)",
        desc: "Direct Google AI Gemini 2.5 API access with 2M token context window.",
        config: '[[agent]]\nname = "gemini-api"\nvendor = "google-api"\napi_key_env = "GEMINI_API_KEY"\nmodel = "gemini-2.5-pro"',
        command: "GEMINI_API_KEY=... jury --pr 123"
      },
      {
        id: "deepseek",
        name: "DeepSeek API",
        vendor: "DeepSeek",
        cat: "backends",
        badge: "$0.27 / 1M",
        badgeType: "green",
        iconKey: "deepseek",
        color: "var(--c-deepseek)",
        desc: "DeepSeek-V3 and DeepSeek-R1 reasoning models via official API.",
        config: '[[agent]]\nname = "deepseek"\nvendor = "deepseek"\napi_key_env = "DEEPSEEK_API_KEY"\nmodel = "deepseek-chat"',
        command: "DEEPSEEK_API_KEY=... jury --pr 123"
      },
      {
        id: "ollama-local",
        name: "Ollama (local)",
        vendor: "On-Device",
        cat: "backends",
        badge: "$0.00 Free Offline",
        badgeType: "green",
        iconKey: "ollama",
        color: "var(--c-qwen)",
        desc: "Run Qwen 2.5 Coder, Llama 3.3, and DeepSeek locally with 100% data privacy.",
        config: '[[agent]]\nname = "qwen"\nvendor = "local"\nendpoint = "http://localhost:11434/v1/chat/completions"\nmodel = "qwen2.5-coder:7b"',
        command: "jury --preset offline --pr 123"
      },
      {
        id: "openrouter",
        name: "OpenRouter",
        vendor: "Unified Router",
        cat: "backends",
        badge: "200+ Models",
        badgeType: "accent",
        iconKey: "openrouter",
        color: "var(--c-openrouter)",
        desc: "Unified routing gateway giving instant access to over 200 AI models.",
        config: '[[agent]]\nname = "openrouter"\nvendor = "openrouter"\napi_key_env = "OPENROUTER_API_KEY"\nmodel = "anthropic/claude-3.7-sonnet"',
        command: "OPENROUTER_API_KEY=... jury --pr 123"
      },
      {
        id: "groq",
        name: "Groq API",
        vendor: "LPU Inference",
        cat: "backends",
        badge: "300+ tok/s",
        badgeType: "accent",
        iconKey: "groq",
        color: "var(--c-groq)",
        desc: "Ultra high-speed LPU inference engine for near-instant multi-agent debate.",
        config: '[[agent]]\nname = "groq"\nvendor = "groq"\napi_key_env = "GROQ_API_KEY"\nmodel = "llama-3.3-70b-versatile"',
        command: "GROQ_API_KEY=... jury --pr 123"
      },
      {
        id: "xai-grok-api",
        name: "xAI Grok API",
        vendor: "xAI",
        cat: "backends",
        badge: "Grok-2",
        badgeType: "",
        iconKey: "xai",
        color: "var(--c-grok)",
        desc: "Direct REST API access to Grok reasoning and code models.",
        config: '[[agent]]\nname = "grok"\nvendor = "openai-compatible"\nendpoint = "https://api.x.ai/v1/chat/completions"\napi_key_env = "XAI_API_KEY"\nmodel = "grok-2-latest"',
        command: "XAI_API_KEY=... jury --pr 123"
      },
      {
        id: "together",
        name: "Together AI",
        vendor: "Cloud Inference",
        cat: "backends",
        badge: "Open Endpoints",
        badgeType: "",
        iconKey: "together",
        color: "#6366f1",
        desc: "Cloud inference hosting for open-weights Llama 3.3, Qwen, and DeepSeek.",
        config: '[[agent]]\nname = "together"\nvendor = "openai-compatible"\nendpoint = "https://api.together.xyz/v1/chat/completions"\napi_key_env = "TOGETHER_API_KEY"\nmodel = "meta-llama/Llama-3.3-70B-Instruct-Turbo"',
        command: "jury --pr 123"
      },
      {
        id: "aws-bedrock",
        name: "AWS Bedrock",
        vendor: "Amazon AWS",
        cat: "backends",
        badge: "Enterprise",
        badgeType: "",
        iconKey: "aws",
        color: "#f59e0b",
        desc: "Enterprise cloud backend with IAM role authentication and VPC security.",
        config: '[[agent]]\nname = "bedrock-claude"\nvendor = "aws-bedrock"\nmodel = "anthropic.claude-3-7-sonnet-20250219-v1:0"',
        command: "jury --pr 123"
      },
      {
        id: "azure-openai",
        name: "Azure OpenAI",
        vendor: "Microsoft Azure",
        cat: "backends",
        badge: "Enterprise",
        badgeType: "",
        iconKey: "azure",
        color: "#0066ff",
        desc: "Private enterprise GPT-4o deployments with SOC2 & HIPAA compliance.",
        config: '[[agent]]\nname = "azure-gpt4o"\nvendor = "openai-compatible"\nendpoint = "https://my-resource.openai.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2024-02-15-preview"\napi_key_env = "AZURE_OPENAI_KEY"',
        command: "jury --pr 123"
      },
      {
        id: "vllm-local",
        name: "vLLM / LM Studio / llama.cpp",
        vendor: "Self-Hosted",
        cat: "backends",
        badge: "Loopback",
        badgeType: "green",
        iconKey: "vllm",
        color: "#10b981",
        desc: "Compatible with any local OpenAI-compatible HTTP inference server.",
        config: '[[agent]]\nname = "local-vllm"\nvendor = "local"\nendpoint = "http://localhost:8000/v1/chat/completions"\nmodel = "deepseek-ai/DeepSeek-Coder-V2-Lite"',
        command: "jury --pr 123"
      },

      // 3. CI/CD & DevOps
      {
        id: "keel-gov",
        name: "Keel Delivery Governance",
        vendor: "Workflow Runner",
        cat: "cicd",
        badge: "Automated Ship",
        badgeType: "accent",
        iconKey: "keel",
        color: "var(--accent-2)",
        desc: "Multi-agent review quality gate inside Keel autonomous software delivery.",
        config: "# .keel/project.yaml\nreview:\n  engine: ai-jury\n  preset: balanced\n  gating: true",
        command: "keel ship .keel/project.yaml --pr 123"
      },
      {
        id: "github-action",
        name: "GitHub Actions",
        vendor: "CI / CD",
        cat: "cicd",
        badge: "1-Click Composite",
        badgeType: "green",
        iconKey: "github",
        color: "#ffffff",
        desc: "First-party composite action for automated PR review and sticky comments.",
        config: "- uses: berkayturanci/ai-jury@v1\n  with:\n    pr: ${{ github.event.pull_request.number }}\n    post-summary: 'true'\n    fail-on: 'critical,major'",
        command: "gh workflow run jury.yml"
      },
      {
        id: "gitlab-ci",
        name: "GitLab CI / CD",
        vendor: "CI / CD",
        cat: "cicd",
        badge: "Pipeline Stage",
        badgeType: "",
        iconKey: "gitlab",
        color: "#f55036",
        desc: "Simple non-blocking advisory review or strict merge request gate.",
        config: "ai_jury_review:\n  image: python:3.12-slim\n  script:\n    - pip install ai-jury\n    - git diff $CI_MERGE_REQUEST_TARGET_BRANCH_SHA... | jury --diff-file - --ci",
        command: "git push origin feat/branch"
      },
      {
        id: "pre-commit",
        name: "Git Pre-Commit Hooks",
        vendor: "Local Git Hooks",
        cat: "cicd",
        badge: "Native Hook",
        badgeType: "green",
        iconKey: "precommit",
        color: "#10b981",
        desc: "Run consensus verification locally before commits or git pushes.",
        config: "# .pre-commit-config.yaml\nrepos:\n  - repo: https://github.com/berkayturanci/ai-jury\n    rev: v1.13.0\n    hooks:\n      - id: ai-jury\n        stages: [pre-push]",
        command: "git push"
      },
      {
        id: "homebrew",
        name: "Homebrew Tap",
        vendor: "Package Manager",
        cat: "cicd",
        badge: "macOS / Linux",
        badgeType: "accent",
        iconKey: "homebrew",
        color: "#f59e0b",
        desc: "Single-command package manager install with automatic path linking.",
        config: "brew install berkayturanci/ai-jury/ai-jury",
        command: "brew install berkayturanci/ai-jury/ai-jury && jury --version"
      },
      {
        id: "curl-install",
        name: "Standalone curl Installer",
        vendor: "Universal Script",
        cat: "cicd",
        badge: "Zero Deps",
        badgeType: "",
        iconKey: "curl",
        color: "#9aa8c4",
        desc: "Install isolated ai-jury binary with zero dependencies via curl.",
        config: "curl -fsSL https://berkayturanci.github.io/ai-jury/install.sh | sh",
        command: "curl -fsSL https://berkayturanci.github.io/ai-jury/install.sh | sh"
      },
      {
        id: "docker-image",
        name: "Docker & DevContainers",
        vendor: "Container",
        cat: "cicd",
        badge: "OCI Container",
        badgeType: "",
        iconKey: "docker",
        color: "#0066ff",
        desc: "Pre-packaged container with all standard agent CLIs and tools configured.",
        config: "docker run --rm -v $(pwd):/repo ghcr.io/berkayturanci/ai-jury:latest --diff-file -",
        command: "docker run --rm -it ghcr.io/berkayturanci/ai-jury"
      },

      // 4. Plugins & Protocols
      {
        id: "claude-skill",
        name: "Claude Code Skill",
        vendor: "Anthropic Skill",
        cat: "skills",
        badge: "Slash Command",
        badgeType: "accent",
        iconKey: "claude",
        color: "var(--c-claude)",
        desc: "Ask Claude Code to convene the multi-agent jury directly in chat.",
        config: "# .claude/skills/ai-jury/SKILL.md\nConvene the review jury on my current branch.",
        command: "/jury review"
      },
      {
        id: "codex-plugin",
        name: "Codex Plugin",
        vendor: "OpenAI Plugin",
        cat: "skills",
        badge: "Manifest Plugin",
        badgeType: "green",
        iconKey: "openai",
        color: "var(--c-codex)",
        desc: "First-class OpenAI Codex CLI plugin registration.",
        config: '# .codex-plugin/plugin.json\n{\n  "name": "ai-jury",\n  "command": "jury"\n}',
        command: "codex plugins run ai-jury"
      },
      {
        id: "agy-skill",
        name: "Antigravity AGY Skill",
        vendor: "Google Skill",
        cat: "skills",
        badge: "DeepMind Skill",
        badgeType: "accent",
        iconKey: "google",
        color: "var(--c-agy)",
        desc: "Google Antigravity custom skill for deliberative team reviews.",
        config: "# .gemini/skills/ai-jury/SKILL.md\nRun ai-jury consensus against latest diff.",
        command: "agy run ai-jury"
      },
      {
        id: "mcp-protocol",
        name: "Model Context Protocol (MCP)",
        vendor: "Open Standard",
        cat: "skills",
        badge: "MCP Server",
        badgeType: "green",
        iconKey: "mcp",
        color: "#10b981",
        desc: "Expose multi-agent consensus verification as standard MCP tools.",
        config: '# mcp.json\n{\n  "mcpServers": {\n    "ai-jury": {\n      "command": "jury",\n      "args": ["--mcp"]\n    }\n  }\n}',
        command: "jury --help"
      },
      {
        id: "pr-bot",
        name: "GitHub Issue & PR Bot",
        vendor: "Webhook / Bot",
        cat: "skills",
        badge: "Comment Trigger",
        badgeType: "",
        iconKey: "bot",
        color: "#ffffff",
        desc: "Trigger review runs dynamically by typing '/jury review' in any PR comment.",
        config: 'jury comment --text "/jury review --rounds 2" --pr 123',
        command: 'jury comment --text "/jury review" --pr 123'
      },
      {
        id: "vscode-cursor",
        name: "VS Code & Cursor IDE",
        vendor: "IDE Task",
        cat: "skills",
        badge: "Task Runner",
        badgeType: "accent",
        iconKey: "vscode",
        color: "#0066ff",
        desc: "One-click review tasks in VS Code / Cursor command palette.",
        config: '// .vscode/tasks.json\n{\n  "label": "AI Jury Review",\n  "type": "shell",\n  "command": "jury"\n}',
        command: "jury"
      }
    ];

    var activeCat = "all";
    var query = "";

    var pills = qa(".int-pill");
    var searchInput = $("int-search");
    var countBadge = $("int-count-badge");
    var modal = $("int-modal");
    var modalClose = $("int-modal-close");
    var mIcon = $("int-m-icon");
    var mName = $("int-m-name");
    var mSub = $("int-m-sub");
    var mDesc = $("int-m-desc");
    var mConfig = $("int-m-config");
    var mCmd = $("int-m-cmd");
    var copyConfigBtn = $("int-copy-config");
    var copyCmdBtn = $("int-copy-cmd");

    function renderCards() {
      var filtered = INTEGRATIONS.filter(function (it) {
        var matchCat = activeCat === "all" || it.cat === activeCat;
        if (!matchCat) return false;
        if (!query) return true;
        var ql = query.toLowerCase();
        return it.name.toLowerCase().indexOf(ql) !== -1 ||
               it.vendor.toLowerCase().indexOf(ql) !== -1 ||
               it.desc.toLowerCase().indexOf(ql) !== -1 ||
               it.badge.toLowerCase().indexOf(ql) !== -1;
      });

      if (countBadge) {
        countBadge.textContent = "Showing " + filtered.length + " of " + INTEGRATIONS.length + " integrations";
      }

      if (filtered.length === 0) {
        grid.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; padding: 3rem 1rem; color: var(--muted);">' +
          '<p style="font-size: 1.1rem; margin-bottom: 0.5rem;">No integrations found matching "' + esc(query) + '"</p>' +
          '<p style="font-size: 0.85rem; color: var(--faint);">Try searching for Claude, Codex, Gemini, Ollama, Docker, or GitHub Actions.</p>' +
          '</div>';
        return;
      }

      var html = "";
      filtered.forEach(function (it) {
        var badgeClass = "int-badge" + (it.badgeType ? " " + it.badgeType : "");
        var iconSvg = SVG_ICONS[it.iconKey] || SVG_ICONS.generic;
        html += '<div class="int-card" data-id="' + esc(it.id) + '" role="button" tabindex="0" aria-label="' + esc(it.name) + ' integration details">' +
          '<div class="int-icon-wrap" style="color:' + esc(it.color) + '">' + iconSvg + '</div>' +
          '<div class="int-card-body">' +
            '<div class="int-card-header">' +
              '<span class="int-card-name">' + esc(it.name) + '</span>' +
              '<span class="' + badgeClass + '">' + esc(it.badge) + '</span>' +
            '</div>' +
            '<div class="int-card-sub">' + esc(it.vendor) + '</div>' +
          '</div>' +
        '</div>';
      });

      grid.innerHTML = html;

      // Add click listeners to cards
      qa(".int-card", grid).forEach(function (card) {
        function open() {
          var id = card.getAttribute("data-id");
          var item = INTEGRATIONS.filter(function (x) { return x.id === id; })[0];
          if (item) openModal(item);
        }
        card.addEventListener("click", open);
        card.addEventListener("keydown", function (e) {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            open();
          }
        });
      });
    }

    function openModal(item) {
      if (!modal) return;
      if (mIcon) {
        mIcon.innerHTML = SVG_ICONS[item.iconKey] || SVG_ICONS.generic;
        mIcon.style.color = item.color;
      }
      if (mName) mName.textContent = item.name;
      if (mSub) mSub.textContent = item.vendor + " • " + item.badge;
      if (mDesc) mDesc.textContent = item.desc;
      if (mConfig) mConfig.textContent = item.config;
      if (mCmd) mCmd.textContent = item.command;

      modal.classList.add("open");
      modal.setAttribute("aria-hidden", "false");
      if (modalClose) modalClose.focus();
    }

    function closeModal() {
      if (!modal) return;
      modal.classList.remove("open");
      modal.setAttribute("aria-hidden", "true");
    }

    if (modalClose) {
      modalClose.addEventListener("click", closeModal);
    }
    if (modal) {
      modal.addEventListener("click", function (e) {
        if (e.target === modal) closeModal();
      });
    }
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && modal && modal.classList.contains("open")) {
        closeModal();
      }
    });

    function setupCopy(btn, codeEl) {
      if (!btn || !codeEl) return;
      btn.addEventListener("click", function () {
        var txt = codeEl.textContent || "";
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(txt).then(function () {
            var orig = btn.textContent;
            btn.textContent = "Copied! ✓";
            btn.style.color = "var(--green-2)";
            btn.style.borderColor = "var(--green)";
            setTimeout(function () {
              btn.textContent = orig;
              btn.style.color = "";
              btn.style.borderColor = "";
            }, 2000);
          });
        }
      });
    }

    setupCopy(copyConfigBtn, mConfig);
    setupCopy(copyCmdBtn, mCmd);

    pills.forEach(function (pill) {
      pill.addEventListener("click", function () {
        pills.forEach(function (p) { p.classList.remove("active"); });
        pill.classList.add("active");
        activeCat = pill.getAttribute("data-cat") || "all";
        renderCards();
      });
    });

    if (searchInput) {
      searchInput.addEventListener("input", function () {
        query = searchInput.value.trim();
        renderCards();
      });
    }

    renderCards();
  })();
})();
