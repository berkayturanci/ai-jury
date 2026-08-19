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

    // 100% Official Brand Vector SVGs (Simple Icons / Official Brand Kits)
    var SVG_ICONS = {
      claude: '<svg role="img" viewBox="0 0 24 24" fill="#D97757"><path d="M17.3041 3.541h-3.6718l6.696 16.918H24Zm-10.6082 0L0 20.459h3.7442l1.3693-3.5527h7.0052l1.3693 3.5528h3.7442L10.5363 3.5409Zm-.3712 10.2232 2.2914-5.9456 2.2914 5.9456Z"/></svg>',
      openai: '<svg role="img" viewBox="0 0 24 24" fill="#10A37F"><path d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z"/></svg>',
      google: '<svg role="img" viewBox="0 0 24 24" fill="#4285F4"><path d="M11.04 19.32Q12 21.51 12 24q0-2.49.93-4.68.96-2.19 2.58-3.81t3.81-2.55Q21.51 12 24 12q-2.49 0-4.68-.93a12.3 12.3 0 0 1-3.81-2.58 12.3 12.3 0 0 1-2.58-3.81Q12 2.49 12 0q0 2.49-.96 4.68-.93 2.19-2.55 3.81a12.3 12.3 0 0 1-3.81 2.58Q2.49 12 0 12q2.49 0 4.68.96 2.19.93 3.81 2.55t2.55 3.81"/></svg>',
      cursor: '<svg role="img" viewBox="0 0 24 24" fill="currentColor"><path d="M11.503.131 1.891 5.678a.84.84 0 0 0-.42.726v11.188c0 .3.162.575.42.724l9.609 5.55a1 1 0 0 0 .998 0l9.61-5.55a.84.84 0 0 0 .42-.724V6.404a.84.84 0 0 0-.42-.726L12.497.131a1.01 1.01 0 0 0-.996 0M2.657 6.338h18.55c.263 0 .43.287.297.515L12.23 22.918c-.062.107-.229.064-.229-.06V12.335a.59.59 0 0 0-.295-.51l-9.11-5.257c-.109-.063-.064-.23.061-.23"/></svg>',
      aider: '<svg role="img" viewBox="0 0 24 24" fill="#10B981"><path d="M12 2a2 2 0 0 1 2 2v1h3a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-1v2a1 1 0 0 1-1.7.7L11.6 18H7a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h3V4a2 2 0 0 1 2-2zm-3 8a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm6 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z"/></svg>',
      xai: '<svg role="img" viewBox="0 0 24 24" fill="currentColor"><path d="M18.901 1.153h3.68l-8.04 9.19L24 22.846h-7.406l-5.8-7.584-6.638 7.584H.474l8.6-9.83L0 1.154h7.594l5.243 6.932ZM17.61 20.644h2.039L6.486 3.24H4.298Z"/></svg>',
      kimi: '<svg role="img" viewBox="0 0 24 24" fill="#00D084"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 14.59L8.41 12 13 7.41V10h4v4h-4z"/></svg>',
      devin: '<svg role="img" viewBox="0 0 24 24" fill="#6366F1"><path d="M12 2l8.66 5v10L12 22l-8.66-5V7L12 2zm0 2.31L5.34 8.15v7.7L12 19.69l6.66-3.84v-7.7L12 4.31z"/></svg>',
      trae: '<svg role="img" viewBox="0 0 24 24" fill="#00D2FF"><path d="M12 2L2 19.5h20L12 2zm0 4.5l6.5 11.5h-13L12 6.5z"/></svg>',
      opencode: '<svg role="img" viewBox="0 0 24 24" fill="#10B981"><path d="M9.4 16.6L4.8 12l4.6-4.6L8 6l-6 6 6 6 1.4-1.4zm5.2 0l4.6-4.6-4.6-4.6L16 6l6 6-6 6-1.4-1.4z"/></svg>',
      hermes: '<svg role="img" viewBox="0 0 24 24" fill="#A855F7"><path d="M12 2L9 8h6l-3-6zm-6 8l-4 6 6-2-2-4zm12 0l-2 4 6 2-4-6zm-7 2v8l3 2 3-2v-8h-6z"/></svg>',
      deepseek: '<svg role="img" viewBox="0 0 24 24" fill="#4D6BFE"><path d="M23.748 4.651c-.254-.124-.364.113-.512.233-.051.04-.094.09-.137.137-.372.397-.806.657-1.373.626-.829-.046-1.537.214-2.163.848-.133-.782-.575-1.248-1.247-1.548-.352-.155-.708-.311-.955-.65-.172-.24-.219-.509-.305-.774-.055-.16-.11-.323-.293-.35-.2-.031-.278.136-.356.276-.313.572-.434 1.202-.422 1.84.027 1.436.633 2.58 1.838 3.393.137.094.172.187.129.323-.082.28-.18.553-.266.833-.055.179-.137.218-.328.14a5.5 5.5 0 0 1-1.737-1.179c-.857-.828-1.631-1.743-2.597-2.46a12 12 0 0 0-.689-.47c-.985-.957.13-1.743.387-1.836.27-.098.094-.433-.778-.428-.872.003-1.67.295-2.687.685a3 3 0 0 1-.465.136 9.6 9.6 0 0 0-2.883-.101c-1.885.21-3.39 1.1-4.497 2.622C.082 8.776-.231 10.854.152 13.02c.403 2.284 1.568 4.175 3.36 5.653 1.857 1.533 3.997 2.284 6.438 2.14 1.482-.085 3.132-.284 4.994-1.86.47.234.962.328 1.78.398.629.058 1.235-.031 1.705-.129.735-.155.684-.836.418-.961-2.155-1.004-1.682-.595-2.112-.926 1.095-1.295 2.768-3.598 3.284-6.733.05-.346.115-.834.108-1.114-.004-.171.035-.238.23-.257a4.2 4.2 0 0 0 1.545-.475c1.397-.763 1.96-2.016 2.093-3.517.02-.23-.004-.467-.247-.588M11.58 18.168c-2.088-1.642-3.101-2.183-3.52-2.16-.39.024-.32.472-.234.763.09.288.207.487.371.74.114.167.192.416-.113.603-.673.416-1.842-.14-1.897-.168-1.361-.801-2.5-1.86-3.301-3.306-.775-1.393-1.225-2.888-1.299-4.482-.02-.385.094-.522.477-.592a4.7 4.7 0 0 1 1.53-.038c2.131.311 3.946 1.264 5.467 2.774.868.86 1.525 1.887 2.202 2.89.72 1.066 1.494 2.082 2.48 2.915.348.291.626.513.892.677-.802.09-2.14.109-3.055-.615zm1.001-6.44a.306.306 0 0 1 .415-.287.3.3 0 0 1 .113.074.3.3 0 0 1 .086.214c0 .17-.136.307-.308.307a.303.303 0 0 1-.306-.307m3.11 1.596c-.2.081-.4.151-.591.16a1.25 1.25 0 0 1-.798-.254c-.274-.23-.47-.358-.551-.758a1.7 1.7 0 0 1 .015-.588c.07-.327-.007-.537-.238-.727-.188-.156-.426-.199-.689-.199a.6.6 0 0 1-.254-.078.253.253 0 0 1-.114-.358 1 1 0 0 1 .192-.21c.356-.202.767-.136 1.146.016.352.144.618.408 1.001.782.392.451.462.576.685.915.176.264.336.536.446.848.066.194-.02.353-.25.45"/></svg>',
      ollama: '<svg role="img" viewBox="0 0 24 24" fill="currentColor"><path d="M16.361 10.26a.894.894 0 0 0-.558.47l-.072.148.001.207c0 .193.004.217.059.353.076.193.152.312.291.448.24.238.51.3.872.205a.86.86 0 0 0 .517-.436.752.752 0 0 0 .08-.498c-.064-.453-.33-.782-.724-.897a1.06 1.06 0 0 0-.466 0zm-9.203.005c-.305.096-.533.32-.65.639a1.187 1.187 0 0 0-.06.52c.057.309.31.59.598.667.362.095.632.033.872-.205.14-.136.215-.255.291-.448.055-.136.059-.16.059-.353l.001-.207-.072-.148a.894.894 0 0 0-.565-.472 1.02 1.02 0 0 0-.474.007Zm4.184 2c-.131.071-.223.25-.195.383.031.143.157.288.353.407.105.063.112.072.117.136.004.038-.01.146-.029.243-.02.094-.036.194-.036.222.002.074.07.195.143.253.064.052.076.054.255.059.164.005.198.001.264-.03.169-.082.212-.234.15-.525-.052-.243-.042-.28.087-.355.137-.08.281-.219.324-.314a.365.365 0 0 0-.175-.48.394.394 0 0 0-.181-.033c-.126 0-.207.03-.355.124l-.085.053-.053-.032c-.219-.13-.259-.145-.391-.143a.396.396 0 0 0-.193.032zm.39-2.195c-.373.036-.475.05-.654.086-.291.06-.68.195-.951.328-.94.46-1.589 1.226-1.787 2.114-.04.176-.045.234-.045.53 0 .294.005.357.043.524.264 1.16 1.332 2.017 2.714 2.173.3.033 1.596.033 1.896 0 1.11-.125 2.064-.727 2.493-1.571.114-.226.169-.372.22-.602.039-.167.044-.23.044-.523 0-.297-.005-.355-.045-.531-.288-1.29-1.539-2.304-3.072-2.497a6.873 6.873 0 0 0-.855-.031zm.645.937a3.283 3.283 0 0 1 1.44.514c.223.148.537.458.671.662.166.251.26.508.303.82.02.143.01.251-.043.482-.08.345-.332.705-.672.957a3.115 3.115 0 0 1-.689.348c-.382.122-.632.144-1.525.138-.582-.006-.686-.01-.853-.042-.57-.107-1.022-.334-1.35-.68-.264-.28-.385-.535-.45-.946-.03-.192.025-.509.137-.776.136-.326.488-.73.836-.963.403-.269.934-.46 1.422-.512.187-.02.586-.02.773-.002zm-5.503-11a1.653 1.653 0 0 0-.683.298C5.617.74 5.173 1.666 4.985 2.819c-.07.436-.119 1.04-.119 1.503 0 .544.064 1.24.155 1.721.02.107.031.202.023.208a8.12 8.12 0 0 1-.187.152 5.324 5.324 0 0 0-.949 1.02 5.49 5.49 0 0 0-.94 2.339 6.625 6.625 0 0 0-.023 1.357c.091.78.325 1.438.727 2.04l.13.195-.037.064c-.269.452-.498 1.105-.605 1.732-.084.496-.095.629-.095 1.294 0 .67.009.803.088 1.266.095.555.288 1.143.503 1.534.071.128.243.393.264.407.007.003-.014.067-.046.141a7.405 7.405 0 0 0-.548 1.873c-.062.417-.071.552-.071.991 0 .56.031.832.148 1.279L3.42 24h1.478l-.05-.091c-.297-.552-.325-1.575-.068-2.597.117-.472.25-.819.498-1.296l.148-.29v-.177c0-.165-.003-.184-.057-.293a.915.915 0 0 0-.194-.25 1.74 1.74 0 0 1-.385-.543c-.424-.92-.506-2.286-.208-3.451.124-.486.329-.918.544-1.154a.787.787 0 0 0 .223-.531c0-.195-.07-.355-.224-.522a3.136 3.136 0 0 1-.817-1.729c-.14-.96.114-2.005.69-2.834.563-.814 1.353-1.336 2.237-1.475.199-.033.57-.028.776.01.226.04.367.028.512-.041.179-.085.268-.19.374-.431.093-.215.165-.333.36-.576.234-.29.46-.489.822-.729.413-.27.884-.467 1.352-.561.17-.035.25-.04.569-.04.319 0 .398.005.569.04a4.07 4.07 0 0 1 1.914.997c.117.109.398.457.488.602.034.057.095.177.132.267.105.241.195.346.374.43.14.068.286.082.503.045.343-.058.607-.053.943.016 1.144.23 2.14 1.173 2.581 2.437.385 1.108.276 2.267-.296 3.153-.097.15-.193.27-.333.419-.301.322-.301.722-.001 1.053.493.539.801 1.866.708 3.036-.062.772-.26 1.463-.533 1.854a2.096 2.096 0 0 1-.224.258.916.916 0 0 0-.194.25c-.054.109-.057.128-.057.293v.178l.148.29c.248.476.38.823.498 1.295.253 1.008.231 2.01-.059 2.581a.845.845 0 0 0-.044.098c0 .006.329.009.732.009h.73l.02-.074.036-.134c.019-.076.057-.3.088-.516.029-.217.029-1.016 0-1.258-.11-.875-.295-1.57-.597-2.226-.032-.074-.053-.138-.046-.141.008-.005.057-.074.108-.152.376-.569.607-1.284.724-2.228.031-.26.031-1.378 0-1.628-.083-.645-.182-1.082-.348-1.525a6.083 6.083 0 0 0-.329-.7l-.038-.064.131-.194c.402-.604.636-1.262.727-2.04a6.625 6.625 0 0 0-.024-1.358 5.512 5.512 0 0 0-.939-2.339 5.325 5.325 0 0 0-.95-1.02 8.097 8.097 0 0 1-.186-.152.692.692 0 0 1 .023-.208c.208-1.087.201-2.443-.017-3.503-.19-.924-.535-1.658-.98-2.082-.354-.338-.716-.482-1.15-.455-.996.059-1.8 1.205-2.116 3.01a6.805 6.805 0 0 0-.097.726c0 .036-.007.066-.015.066a.96.96 0 0 1-.149-.078A4.857 4.857 0 0 0 12 3.03c-.832 0-1.687.243-2.456.698a.958.958 0 0 1-.148.078c-.008 0-.015-.03-.015-.066a6.71 6.71 0 0 0-.097-.725C8.997 1.392 8.337.319 7.46.048a2.096 2.096 0 0 0-.585-.041Zm.293 1.402c.248.197.523.759.682 1.388.03.113.06.244.069.292.007.047.026.152.041.233.067.365.098.76.102 1.24l.002.475-.12.175-.118.178h-.278c-.324 0-.646.041-.954.124l-.238.06c-.033.007-.038-.003-.057-.144a8.438 8.438 0 0 1 .016-2.323c.124-.788.413-1.501.696-1.711.067-.05.079-.049.157.013zm9.825-.012c.17.126.358.46.498.888.28.854.36 2.028.212 3.145-.019.14-.024.151-.057.144l-.238-.06a3.693 3.693 0 0 0-.954-.124h-.278l-.119-.178-.119-.175.002-.474c.004-.669.066-1.19.214-1.772.157-.623.434-1.185.68-1.382.078-.062.09-.063.159-.012z"/></svg>',
      openrouter: '<svg role="img" viewBox="0 0 24 24" fill="#6366F1"><path d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm1 14.59L8.41 12 13 7.41V10h4v4h-4z"/></svg>',
      groq: '<svg role="img" viewBox="0 0 24 24" fill="#F55036"><path d="M13 2L3 14h8l-2 8 10-12h-8l2-8z"/></svg>',
      together: '<svg role="img" viewBox="0 0 24 24" fill="#3B82F6"><path d="M12 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm-6 8a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm12 0a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm-6 6a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"/></svg>',
      aws: '<svg role="img" viewBox="0 0 24 24" fill="#FF9900"><path d="M6.763 10.036c0 .296.032.535.088.71.064.176.144.368.256.576.04.063.056.127.056.183 0 .08-.048.16-.152.24l-.503.335a.383.383 0 0 1-.208.072c-.08 0-.16-.04-.239-.112a2.47 2.47 0 0 1-.287-.375 6.18 6.18 0 0 1-.248-.471c-.622.734-1.405 1.101-2.347 1.101-.67 0-1.205-.191-1.596-.574-.391-.384-.59-.894-.59-1.533 0-.678.239-1.23.726-1.644.487-.415 1.133-.623 1.955-.623.272 0 .551.024.846.064.296.04.6.104.918.176v-.583c0-.607-.127-1.03-.375-1.277-.255-.248-.686-.367-1.3-.367-.28 0-.568.031-.863.103-.295.072-.583.16-.862.272a2.287 2.287 0 0 1-.28.104.488.488 0 0 1-.127.023c-.112 0-.168-.08-.168-.247v-.391c0-.128.016-.224.056-.28a.597.597 0 0 1 .224-.167c.279-.144.614-.264 1.005-.36a4.84 4.84 0 0 1 1.246-.151c.95 0 1.644.216 2.091.647.439.43.662 1.085.662 1.963v2.586zm-3.24 1.214c.263 0 .534-.048.822-.144.287-.096.543-.271.758-.51.128-.152.224-.32.272-.512.047-.191.08-.423.08-.694v-.335a6.66 6.66 0 0 0-.735-.136 6.02 6.02 0 0 0-.75-.048c-.535 0-.926.104-1.19.32-.263.215-.39.518-.39.917 0 .375.095.655.295.846.191.2.47.296.838.296zm6.41.862c-.144 0-.24-.024-.304-.08-.064-.048-.12-.16-.168-.311L7.586 5.55a1.398 1.398 0 0 1-.072-.32c0-.128.064-.2.191-.2h.783c.151 0 .255.025.31.08.065.048.113.16.16.312l1.342 5.284 1.245-5.284c.04-.16.088-.264.151-.312a.549.549 0 0 1 .32-.08h.638c.152 0 .256.025.32.08.063.048.12.16.151.312l1.261 5.348 1.381-5.348c.048-.16.104-.264.16-.312a.52.52 0 0 1 .311-.08h.743c.127 0 .2.065.2.2 0 .04-.009.08-.017.128a1.137 1.137 0 0 1-.056.2l-1.923 6.17c-.048.16-.104.263-.168.311a.51.51 0 0 1-.303.08h-.687c-.151 0-.255-.024-.32-.08-.063-.056-.119-.16-.15-.32l-1.238-5.148-1.23 5.14c-.04.16-.087.264-.15.32-.065.056-.177.08-.32.08zm10.256.215c-.415 0-.83-.048-1.229-.143-.399-.096-.71-.2-.918-.32-.128-.071-.215-.151-.247-.223a.563.563 0 0 1-.048-.224v-.407c0-.167.064-.247.183-.247.048 0 .096.008.144.024.048.016.12.048.2.08.271.12.566.215.878.279.319.064.63.096.95.096.502 0 .894-.088 1.165-.264a.86.86 0 0 0 .415-.758.777.777 0 0 0-.215-.559c-.144-.151-.416-.287-.807-.415l-1.157-.36c-.583-.183-1.014-.454-1.277-.813a1.902 1.902 0 0 1-.4-1.158c0-.335.073-.63.216-.886.144-.255.335-.479.575-.654.24-.184.51-.32.83-.415.32-.096.655-.136 1.006-.136.175 0 .359.008.535.032.183.024.35.056.518.088.16.04.312.08.455.127.144.048.256.096.336.144a.69.69 0 0 1 .24.2.43.43 0 0 1 .071.263v.375c0 .168-.064.256-.184.256a.83.83 0 0 1-.303-.096 3.652 3.652 0 0 0-1.532-.311c-.455 0-.815.071-1.062.223-.248.152-.375.383-.375.71 0 .224.08.416.24.567.159.152.454.304.877.44l1.134.358c.574.184.99.44 1.237.767.247.327.367.702.367 1.117 0 .343-.072.655-.207.926-.144.272-.336.511-.583.703-.248.2-.543.343-.886.447-.36.111-.734.167-1.142.167zM21.698 16.207c-2.626 1.94-6.442 2.969-9.722 2.969-4.598 0-8.74-1.7-11.87-4.526-.247-.223-.024-.527.272-.351 3.384 1.963 7.559 3.153 11.877 3.153 2.914 0 6.114-.607 9.06-1.852.439-.2.814.287.383.607zM22.792 14.961c-.336-.43-2.22-.207-3.074-.103-.255.032-.295-.192-.063-.36 1.5-1.053 3.967-.75 4.254-.399.287.36-.08 2.826-1.485 4.007-.215.184-.423.088-.327-.151.32-.79 1.03-2.57.695-2.994z"/></svg>',
      azure: '<svg role="img" viewBox="0 0 24 24" fill="#0089D6"><path d="M13.05 2.5L5.7 15.3l4.6 6.2h8l-5.25-19zm-3.6 13.5l-4.75-2.7L2 17.5l7.45 4v-5.5z"/></svg>',
      vllm: '<svg role="img" viewBox="0 0 24 24" fill="#10B981"><path d="m23.6 0-8.721 4.59L9.829 24h7.41zM9.83 24V5.142H.4Z"/></svg>',
      keel: '<svg role="img" viewBox="0 0 24 24" fill="#6366F1"><path d="M12 2L3 19h18L12 2zm0 4.2l5.4 10.8H6.6L12 6.2zM11 13h2v3h-2v-3z"/></svg>',
      github: '<svg role="img" viewBox="0 0 24 24" fill="currentColor"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>',
      gitlab: '<svg role="img" viewBox="0 0 24 24" fill="#FC6D26"><path d="m23.6004 9.5927-.0337-.0862L20.3.9814a.851.851 0 0 0-.3362-.405.8748.8748 0 0 0-.9997.0539.8748.8748 0 0 0-.29.4399l-2.2055 6.748H7.5375l-2.2057-6.748a.8573.8573 0 0 0-.29-.4412.8748.8748 0 0 0-.9997-.0537.8585.8585 0 0 0-.3362.4049L.4332 9.5015l-.0325.0862a6.0657 6.0657 0 0 0 2.0119 7.0105l.0113.0087.03.0213 4.976 3.7264 2.462 1.8633 1.4995 1.1321a1.0085 1.0085 0 0 0 1.2197 0l1.4995-1.1321 2.4619-1.8633 5.006-3.7489.0125-.01a6.0682 6.0682 0 0 0 2.0094-7.003z"/></svg>',
      precommit: '<svg role="img" viewBox="0 0 24 24" fill="#FBB040"><path d="M12 0c-.563 0-1.127.215-1.557.645L.645 10.443c-.86.86-.86 2.254 0 3.114l9.798 9.798c.86.86 2.254.86 3.114 0l9.798-9.798c.86-.86.86-2.254 0-3.114L13.557.645A2.195 2.195 0 0012 0zm0 1.74c.493 0 .987.186 1.361.56L21.7 10.64c.75.75.75 1.973 0 2.722L13.361 21.7c-.374.375-.868.56-1.361.56s-.987-.185-1.361-.56L2.3 13.361a1.93 1.93 0 010-2.722L10.639 2.3c.374-.375.868-.56 1.361-.56zm0 .62c-.333 0-.664.127-.92.382L2.742 11.08a1.295 1.295 0 000 1.84l8.338 8.338a1.296 1.296 0 001.84 0l8.338-8.338a1.295 1.295 0 000-1.84L12.92 2.742A1.296 1.296 0 0012 2.36zM9.207 7.624h3.959c.55 0 1.015.079 1.4.238.385.16.7.371.942.633.241.262.417.561.527.897a3.34 3.34 0 010 2.084c-.11.34-.286.64-.527.902a2.628 2.628 0 01-.942.633c-.385.16-.85.238-1.4.238h-2.043v3.156H9.207zm1.916 1.484v2.657h1.514c.222 0 .434-.016.64-.05.207-.032.39-.097.547-.193.158-.095.284-.23.38-.404.094-.174.142-.401.142-.682 0-.28-.048-.507-.143-.681a1.053 1.053 0 00-.379-.404 1.513 1.513 0 00-.547-.194 4.056 4.056 0 00-.64-.049z"/></svg>',
      homebrew: '<svg role="img" viewBox="0 0 24 24" fill="#FBB040"><path d="M7.938 0a.214.214 0 0 0-.206.156c-.316 1.104.179 2.15.838 2.935.153.181.313.347.476.501a2.039 2.039 0 0 0-.665.02c-1.184.233-2.193.985-2.74 2.532a3.893 3.893 0 0 0-.2 1.466 1.565 1.565 0 0 0-1.156 1.504 1.59 1.59 0 0 0 1.227 1.541l.026 12.046c0 .195.1.377.264.482a.214.214 0 0 0 .008.005c.537.31 2.047.812 5.21.812 3.238 0 4.7-.678 5.181-1.04a.214.214 0 0 0 .008-.007.571.571 0 0 0 .206-.439c.002-.344.002-1.136.002-1.604a.143.143 0 0 1 .147-.144c.397.006.869.006 1.318.005a1.826 1.826 0 0 0 1.832-1.825v-5.804a1.826 1.826 0 0 0-1.825-1.826H16.56a.14.14 0 0 1-.143-.144V10.6h.007v-.001a1.573 1.573 0 0 0 1.356-1.556c0-.816-.627-1.489-1.424-1.563-.025-1.438-.437-2.126-.736-2.58a.214.214 0 0 0-.005-.007c-.364-.51-1.193-1.282-2.275-1.316-.503-.016-.842.124-1.125.254-.217.1-.42.177-.67.22.002-1.286.945-1.981.945-1.981a.214.214 0 0 0 .05-.298s-.087-.122-.21-.26c-.121-.136-.269-.294-.47-.378a.214.214 0 0 0-.079-.017.214.214 0 0 0-.145.055 4.308 4.308 0 0 0-.875 1.101 3.42 3.42 0 0 0-.133.273 3.497 3.497 0 0 0-.381-.846C9.794.978 9.063.436 8.017.016A.214.214 0 0 0 7.939 0zm.156.524c.85.378 1.43.83 1.79 1.403.274.438.426.962.484 1.584a3.07 3.07 0 0 0-.012.462 6.897 6.897 0 0 1-.168-.052 5.487 5.487 0 0 1-1.29-1.106c-.551-.657-.935-1.46-.804-2.291zM11.8 1.618c.07.054.141.101.212.18.034.039.032.04.058.073-.332.308-1.07 1.144-.952 2.453a.214.214 0 0 0 .222.195c.469-.017.782-.172 1.056-.299.273-.126.508-.228.931-.214.875.027 1.639.715 1.939 1.134.295.449.65 1 .663 2.36a1.66 1.66 0 0 0-.41.142 1.938 1.938 0 0 0-1.77-1.16 1.94 1.94 0 0 0-1.87 1.448 1.783 1.783 0 0 0-1.356-.64c-.484 0-.91.205-1.233.517a1.873 1.873 0 0 0-1.85-1.625c-.649 0-1.218.335-1.552.84a3.1 3.1 0 0 1 .157-.735c.51-1.437 1.355-2.045 2.42-2.254.367-.073.664-.011.99.095.325.106.671.262 1.094.342a.214.214 0 0 0 .252-.245c-.112-.67.073-1.266.336-1.744a3.71 3.71 0 0 1 .663-.863zM7.44 6.611a1.442 1.442 0 0 1 1.363 1.925.214.214 0 0 0 .168.283h.005a.214.214 0 0 0 .238-.146 1.373 1.373 0 0 1 2.613-.01.214.214 0 0 0 .417-.09 1.509 1.509 0 0 1 1.504-1.664c.678 0 1.249.445 1.442 1.056a.214.214 0 0 0 .259.143l.15-.04a.214.214 0 0 0 .051-.02 1.139 1.139 0 0 1 1.702.995 1.14 1.14 0 0 1-.985 1.131.214.214 0 0 0-.001 0 2.215 2.215 0 0 0-.485.126 10.65 10.65 0 0 1-1.176.365.214.214 0 0 0-.162.186 1.276 1.276 0 0 1-.146.478 2.07 2.07 0 0 0-.239 1.111l.001.151a.438.438 0 0 1-.16.36.665.665 0 0 1-.43.14.586.586 0 0 1-.588-.59.803.803 0 0 0-.38-.681.214.214 0 0 0-.002-.002c-.24-.145-.43-.37-.532-.636a.214.214 0 0 0-.207-.138 19.469 19.469 0 0 1-5.37-.6l-.003-.002a9.007 9.007 0 0 0-.838-.194h.003a1.16 1.16 0 0 1-.937-1.134c0-.619.488-1.118 1.101-1.14a.214.214 0 0 0 .204-.176 1.443 1.443 0 0 1 1.42-1.187zm8.549 4.106v.455c0 .314.259.573.572.573h1.329a1.397 1.397 0 0 1 1.397 1.397v5.804a1.396 1.396 0 0 1-1.402 1.396.214.214 0 0 0-.002 0c-.448.002-.918 0-1.31-.005a.573.573 0 0 0-.584.573c0 .468 0 1.262-.002 1.603a.214.214 0 0 0 0 .001c0 .042-.019.08-.05.107-.346.26-1.75.95-4.915.95-3.107 0-4.587-.52-4.99-.752a.143.143 0 0 1-.065-.118l-.025-11.955c.145.033.288.07.431.11a.214.214 0 0 0 .003 0c.115.031.246.064.383.097v10.37c0 .129.069.247.18.31.453.217 1.767.732 4.071.732 2.32 0 3.595-.626 4.022-.884a.357.357 0 0 0 .164-.3l.001-10.21c.267-.075.531-.158.792-.254zm-7.99.894a.493.493 0 0 1 .494.493v8.578a.493.493 0 0 1-.493.493.493.493 0 0 1-.494-.493v-8.578A.493.493 0 0 1 8 11.611zm8.652 1.14a.663.663 0 0 0-.662.662v5.208a.663.663 0 0 0 .662.662h1.14a.663.663 0 0 0 .662-.662v-5.209a.663.663 0 0 0-.662-.662zm0 .428h1.14a.233.233 0 0 1 .233.233v5.21a.233.233 0 0 1-.233.232h-1.14a.233.233 0 0 1-.233-.233v-5.209a.233.233 0 0 1 .233-.233z"/></svg>',
      curl: '<svg role="img" viewBox="0 0 24 24" fill="#9AA8C4"><path d="M.803 14.8169c0-.5342.433-.9665.9665-.9665.5335 0 .9665.4323.9665.9665 0 .5335-.433.9657-.9665.9657-.5335 0-.9666-.4322-.9666-.9657m2.736 0c0-.1963-.0532-.376-.1119-.5525-.2344-.7024-.876-1.2169-1.6575-1.2169-.1249 0-.2344.0465-.3524.0708C.6149 13.2865 0 13.9646 0 14.817c0 .9764.7923 1.7694 1.7695 1.7694.9772 0 1.7694-.793 1.7694-1.7694m-1.7694-7.149c.5335 0 .9665.433.9665.9665 0 .5335-.433.9665-.9665.9665-.5343 0-.9666-.433-.9666-.9665 0-.5335.4323-.9665.9666-.9665m0 2.7359c.9772 0 1.7694-.7923 1.7694-1.7694 0-.1956-.0532-.376-.1119-.5525-.2344-.7024-.8767-1.2169-1.6575-1.2169-.1249 0-.2344.0465-.3524.0716C.6149 7.104 0 7.782 0 8.6344c0 .9771.7923 1.7694 1.7695 1.7694m13.221-5.694c-.5342 0-.9665-.433-.9665-.9664a.966.966 0 01.9666-.9665c.5335 0 .9658.4322.9658.9665 0 .5334-.4323.9664-.9658.9664m-9.6 16.5133c-.5335 0-.9666-.433-.9666-.9665 0-.5342.433-.9665.9666-.9665a.966.966 0 01.9665.9665c0 .5335-.4323.9665-.9665.9665m9.6-19.2491c-.978 0-1.7695.7922-1.7695 1.7694 0 .2085.0525.4025.1187.5882L5.039 18.5581c-.803.1681-1.4179.8462-1.4179 1.6985 0 .9772.7923 1.7694 1.7695 1.7694.9772 0 1.7694-.7922 1.7694-1.7694 0-.1963-.0525-.3759-.111-.5525l8.3427-14.2728c.7778-.1865 1.3683-.8531 1.3683-1.688 0-.977-.793-1.7693-1.7694-1.7693m7.24 2.7359c-.5343 0-.9666-.433-.9666-.9665a.966.966 0 01.9665-.9665c.5335 0 .9666.4322.9666.9665 0 .5334-.433.9665-.9666.9665M12.6313 21.223c-.5343 0-.9665-.433-.9665-.9665a.966.966 0 01.9665-.9665c.5335 0 .9658.4323.9658.9665 0 .5335-.4323.9665-.9658.9665M22.2305 1.974c-.9772 0-1.7694.7922-1.7694 1.7694 0 .2085.0525.4025.1187.5882l-8.3009 14.2265c-.8021.1681-1.417.8462-1.417 1.6985 0 .9772.7922 1.7694 1.7694 1.7694.9764 0 1.7687-.7922 1.7687-1.7694 0-.1963-.0525-.3759-.1111-.5525l8.3427-14.2728C23.4094 5.2448 24 4.5782 24 3.7433c0-.977-.7923-1.7693-1.7695-1.7693"/></svg>',
      docker: '<svg role="img" viewBox="0 0 24 24" fill="#2496ED"><path d="M13.983 11.078h2.119a.186.186 0 00.186-.185V9.006a.186.186 0 00-.186-.186h-2.119a.185.185 0 00-.185.185v1.888c0 .102.083.185.185.185m-2.954-5.43h2.118a.186.186 0 00.186-.186V3.574a.186.186 0 00-.186-.185h-2.118a.185.185 0 00-.185.185v1.888c0 .102.082.185.185.185m0 2.716h2.118a.187.187 0 00.186-.186V6.29a.186.186 0 00-.186-.185h-2.118a.185.185 0 00-.185.185v1.887c0 .102.082.185.185.186m-2.93 0h2.12a.186.186 0 00.184-.186V6.29a.185.185 0 00-.185-.185H8.1a.185.185 0 00-.185.185v1.887c0 .102.083.185.185.186m-2.964 0h2.119a.186.186 0 00.185-.186V6.29a.185.185 0 00-.185-.185H5.136a.186.186 0 00-.186.185v1.887c0 .102.084.185.186.186m5.893 2.715h2.118a.186.186 0 00.186-.185V9.006a.186.186 0 00-.186-.186h-2.118a.185.185 0 00-.185.185v1.888c0 .102.082.185.185.185m-2.93 0h2.12a.185.185 0 00.184-.185V9.006a.185.185 0 00-.184-.186h-2.12a.185.185 0 00-.184.185v1.888c0 .102.083.185.185.185m-2.964 0h2.119a.185.185 0 00.185-.185V9.006a.185.185 0 00-.184-.186h-2.12a.186.186 0 00-.186.186v1.887c0 .102.084.185.186.185m-2.92 0h2.12a.185.185 0 00.184-.185V9.006a.185.185 0 00-.184-.186h-2.12a.185.185 0 00-.184.185v1.888c0 .102.082.185.185.185M23.763 9.89c-.065-.051-.672-.51-1.954-.51-.338.001-.676.03-1.01.087-.248-1.7-1.653-2.53-1.716-2.566l-.344-.199-.226.327c-.284.438-.49.922-.612 1.43-.23.97-.09 1.882.403 2.661-.595.332-1.55.413-1.744.42H.751a.751.751 0 00-.75.748 11.376 11.376 0 00.692 4.062c.545 1.428 1.355 2.48 2.41 3.124 1.18.723 3.1 1.137 5.275 1.137.983.003 1.963-.086 2.93-.266a12.248 12.248 0 003.823-1.389c.98-.567 1.86-1.288 2.61-2.136 1.252-1.418 1.998-2.997 2.553-4.4h.221c1.372 0 2.215-.549 2.68-1.009.309-.293.55-.65.707-1.046l.098-.288Z"/></svg>',
      mcp: '<svg role="img" viewBox="0 0 24 24" fill="#10B981"><path d="M12 2a3 3 0 0 0-3 3v2H7a3 3 0 0 0-3 3v2h2v4H4v2a3 3 0 0 0 3 3h2v2a3 3 0 0 0 6 0v-2h2a3 3 0 0 0 3-3v-2h-2v-4h2v-2a3 3 0 0 0-3-3h-2V5a3 3 0 0 0-3-3zm1 5V5a1 1 0 0 0-2 0v2h2zm-5 4h8a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1zm3 10v-2h2v2a1 1 0 0 1-2 0z"/></svg>',
      bot: '<svg role="img" viewBox="0 0 24 24" fill="#9AA8C4"><path d="M12 2a2 2 0 0 1 2 2c0 .74-.4 1.39-1 1.73V7h4a3 3 0 0 1 3 3v8a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-8a3 3 0 0 1 3-3h4V5.73A2.001 2.001 0 0 1 12 2zM9 11a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3zm6 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z"/></svg>',
      vscode: '<svg role="img" viewBox="0 0 24 24" fill="#007ACC"><path d="M23.15 2.587L18.21.21a1.494 1.494 0 0 0-1.705.29l-9.46 8.63-4.12-3.128a.999.999 0 0 0-1.276.057L.327 7.261A1 1 0 0 0 .326 8.74L3.899 12 .326 15.26a1 1 0 0 0 .001 1.479L1.65 17.94a.999.999 0 0 0 1.276.057l4.12-3.128 9.46 8.63a1.492 1.492 0 0 0 1.704.29l4.942-2.377A1.5 1.5 0 0 0 24 20.06V3.939a1.5 1.5 0 0 0-.85-1.352zm-5.146 14.861L10.826 12l7.178-5.448v10.896z"/></svg>',
      generic: '<svg role="img" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="9"/></svg>'
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
        logo: "logos/claude.svg",
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
        logo: "logos/openai.svg",
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
        logo: "logos/google-antigravity.png",
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
        logo: "logos/cursor.svg",
        color: "var(--c-cursor)",
        desc: "Headless CLI agent from the popular AI-native code editor.",
        config: '[[agent]]\nname = "cursor"\nvendor = "generic_cli"\ncommand = "agent"',
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
        logo: "logos/aider.svg",
        color: "var(--c-aider)",
        desc: "Popular terminal pair-programming agent driven in non-interactive review mode.",
        config: '[[agent]]\nname = "aider"\nvendor = "generic_cli"\ncommand = "aider --message"',
        command: "jury --pr 123"
      },
      {
        id: "generic-cli",
        name: "Custom Agent CLI",
        vendor: "Universal CLI Adapter",
        cat: "assistants",
        badge: "Universal Adapter",
        badgeType: "green",
        iconKey: "bot",
        logo: "logos/opencode.svg",
        color: "#10b981",
        desc: "Wrap any arbitrary coding CLI (Goose, OpenHands, Devin) as an autonomous juror.",
        config: '[[agent]]\nname = "my-agent"\nvendor = "generic_cli"\ncommand = "my-tool review"',
        command: "jury --pr 123"
      },

      // 2. Hosted LLM APIs
      {
        id: "anthropic-api",
        name: "Anthropic Claude API",
        vendor: "Anthropic",
        cat: "backends",
        badge: "Sonnet 3.7",
        badgeType: "accent",
        iconKey: "claude",
        logo: "logos/anthropic.svg",
        color: "var(--c-claude)",
        desc: "Direct Claude Sonnet/Opus API access without installing agent CLIs.",
        config: '[[agent]]\nname = "claude-api"\nvendor = "anthropic-api"\napi_key_env = "ANTHROPIC_API_KEY"\nmodel = "claude-3-7-sonnet-20250219"',
        command: "ANTHROPIC_API_KEY=... jury --pr 123"
      },
      {
        id: "openai-api",
        name: "OpenAI GPT-4o / o1",
        vendor: "OpenAI",
        cat: "backends",
        badge: "o1 / GPT-4o",
        badgeType: "green",
        iconKey: "openai",
        logo: "logos/openai.svg",
        color: "var(--c-codex)",
        desc: "Direct OpenAI API integration for o1 reasoning and GPT-4o reviews.",
        config: '[[agent]]\nname = "codex-api"\nvendor = "openai-api"\napi_key_env = "OPENAI_API_KEY"\nmodel = "gpt-4o"',
        command: "OPENAI_API_KEY=... jury --pr 123"
      },
      {
        id: "google-gemini",
        name: "Google Gemini API",
        vendor: "Google AI",
        cat: "backends",
        badge: "2.5 Pro / Flash",
        badgeType: "accent",
        iconKey: "google",
        logo: "logos/googlegemini.svg",
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
        logo: "logos/deepseek.svg",
        color: "var(--c-deepseek)",
        desc: "DeepSeek-V3 and DeepSeek-R1 reasoning models via official API.",
        config: '[[agent]]\nname = "deepseek"\nvendor = "generic_openai_api"\nendpoint = "https://api.deepseek.com/v1/chat/completions"\napi_key_env = "DEEPSEEK_API_KEY"\nmodel = "deepseek-chat"',
        command: "DEEPSEEK_API_KEY=... jury --pr 123"
      },
      {
        id: "xai-grok-api",
        name: "xAI Grok API",
        vendor: "xAI",
        cat: "backends",
        badge: "Grok-2",
        badgeType: "",
        iconKey: "xai",
        logo: "logos/xai.svg",
        color: "var(--c-grok)",
        desc: "Direct REST API access to Grok reasoning and code models.",
        config: '[[agent]]\nname = "grok"\nvendor = "generic_openai_api"\nendpoint = "https://api.x.ai/v1/chat/completions"\napi_key_env = "XAI_API_KEY"\nmodel = "grok-2-latest"',
        command: "XAI_API_KEY=... jury --pr 123"
      },
      {
        id: "groq",
        name: "Groq API",
        vendor: "Groq",
        cat: "backends",
        badge: "300+ tok/s",
        badgeType: "accent",
        iconKey: "groq",
        logo: "logos/groq.svg",
        color: "var(--c-groq)",
        desc: "Ultra high-speed LPU inference engine for near-instant multi-agent debate.",
        config: '[[agent]]\nname = "groq"\nvendor = "generic_openai_api"\nendpoint = "https://api.groq.com/openai/v1/chat/completions"\napi_key_env = "GROQ_API_KEY"\nmodel = "llama-3.3-70b-versatile"',
        command: "GROQ_API_KEY=... jury --pr 123"
      },
      {
        id: "openrouter",
        name: "OpenRouter",
        vendor: "OpenRouter",
        cat: "backends",
        badge: "200+ Models",
        badgeType: "accent",
        iconKey: "openrouter",
        logo: "logos/openrouter.svg",
        color: "var(--c-openrouter)",
        desc: "Unified routing gateway giving instant access to over 200 AI models.",
        config: '[[agent]]\nname = "openrouter"\nvendor = "generic_openai_api"\nendpoint = "https://openrouter.ai/api/v1/chat/completions"\napi_key_env = "OPENROUTER_API_KEY"\nmodel = "anthropic/claude-3.7-sonnet"',
        command: "OPENROUTER_API_KEY=... jury --pr 123"
      },
      {
        id: "together",
        name: "Together AI",
        vendor: "Together AI",
        cat: "backends",
        badge: "Open Weights",
        badgeType: "",
        iconKey: "together",
        logo: "logos/together.svg",
        color: "#6366f1",
        desc: "Cloud inference hosting for open-weights Llama 3.3, Qwen, and DeepSeek.",
        config: '[[agent]]\nname = "together"\nvendor = "generic_openai_api"\nendpoint = "https://api.together.xyz/v1/chat/completions"\napi_key_env = "TOGETHER_API_KEY"\nmodel = "meta-llama/Llama-3.3-70B-Instruct-Turbo"',
        command: "jury --pr 123"
      },

      // 3. Local & Offline Engines
      {
        id: "ollama-local",
        name: "Ollama Local Engine",
        vendor: "Ollama",
        cat: "local",
        badge: "$0.00 Free Offline",
        badgeType: "green",
        iconKey: "ollama",
        logo: "logos/ollama.svg",
        color: "var(--c-qwen)",
        desc: "Run Qwen 2.5 Coder, Llama 3.3, and DeepSeek locally with 100% data privacy.",
        config: '[[agent]]\nname = "qwen"\nvendor = "local"\nendpoint = "http://localhost:11434/v1/chat/completions"\nmodel = "qwen2.5-coder:7b"',
        command: "jury --preset offline --pr 123"
      },
      {
        id: "vllm-local",
        name: "vLLM / LM Studio / llama.cpp",
        vendor: "Self-Hosted",
        cat: "local",
        badge: "Local Loopback",
        badgeType: "green",
        iconKey: "vllm",
        logo: "logos/vllm.svg",
        color: "#10b981",
        desc: "Compatible with any local OpenAI-compatible HTTP inference server.",
        config: '[[agent]]\nname = "local-vllm"\nvendor = "local"\nendpoint = "http://localhost:8000/v1/chat/completions"\nmodel = "deepseek-ai/DeepSeek-Coder-V2-Lite"',
        command: "jury --pr 123"
      },

      // 4. CI/CD & Developer Tools
      {
        id: "github-action",
        name: "GitHub Actions",
        vendor: "GitHub",
        cat: "cicd",
        badge: "First-Party Action",
        badgeType: "green",
        iconKey: "github",
        logo: "logos/githubactions.svg",
        color: "#ffffff",
        desc: "Official composite GitHub Action for automated PR reviews and sticky comments.",
        config: "- uses: berkayturanci/ai-jury@v1\n  with:\n    pr: ${{ github.event.pull_request.number }}\n    post-summary: 'true'\n    fail-on: 'critical,major'",
        command: "gh workflow run jury.yml"
      },
      {
        id: "pre-commit",
        name: "Git Pre-Commit Hooks",
        vendor: "Git Hooks",
        cat: "cicd",
        badge: "Native Hook",
        badgeType: "green",
        iconKey: "precommit",
        logo: "logos/precommit.svg",
        color: "#10b981",
        desc: "Run consensus verification locally before commits or git pushes.",
        config: "# .pre-commit-config.yaml\nrepos:\n  - repo: https://github.com/berkayturanci/ai-jury\n    rev: v1.14.4\n    hooks:\n      - id: ai-jury\n        stages: [pre-push]",
        command: "git push"
      },
      {
        id: "claude-plugin",
        name: "Claude Code Plugin & Skill",
        vendor: "Anthropic Skill",
        cat: "cicd",
        badge: "Slash Command",
        badgeType: "accent",
        iconKey: "claude",
        logo: "logos/claude.svg",
        color: "var(--c-claude)",
        desc: "First-class Claude Code plugin and skill for direct chat reviews.",
        config: "# .claude-plugin/plugin.json\n{\n  \"name\": \"ai-jury\",\n  \"description\": \"Multi-agent review jury\"\n}",
        command: "/jury review"
      },
      {
        id: "codex-plugin",
        name: "Codex Plugin",
        vendor: "OpenAI Plugin",
        cat: "cicd",
        badge: "Manifest Plugin",
        badgeType: "green",
        iconKey: "openai",
        logo: "logos/openai.svg",
        color: "var(--c-codex)",
        desc: "First-class OpenAI Codex CLI plugin registration.",
        config: '# .codex-plugin/plugin.json\n{\n  "name": "ai-jury",\n  "command": "jury"\n}',
        command: "codex plugins run ai-jury"
      },
      {
        id: "homebrew",
        name: "Homebrew Tap",
        vendor: "Package Manager",
        cat: "cicd",
        badge: "macOS / Linux",
        badgeType: "accent",
        iconKey: "homebrew",
        logo: "logos/homebrew.svg",
        color: "#f59e0b",
        desc: "Single-command package manager install with automatic path linking.",
        config: "brew install berkayturanci/ai-jury/ai-jury",
        command: "brew install berkayturanci/ai-jury/ai-jury && jury --version"
      },
      {
        id: "curl-install",
        name: "PyPI & Standalone Installer",
        vendor: "pip / uv / curl",
        cat: "cicd",
        badge: "Zero Deps",
        badgeType: "green",
        iconKey: "curl",
        logo: "logos/pypi.svg",
        color: "#9aa8c4",
        desc: "Install isolated ai-jury binary with zero dependencies via pip, uv, or curl.",
        config: "pip install ai-jury\n# or\ncurl -fsSL https://ai-jury.dev/install.sh | sh",
        command: "pipx install ai-jury && jury --doctor"
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
          '<p style="font-size: 0.85rem; color: var(--faint);">Try searching for Claude, Codex, Gemini, Ollama, or GitHub Actions.</p>' +
          '</div>';
        return;
      }

      var html = "";
      filtered.forEach(function (it) {
        var badgeClass = "int-badge" + (it.badgeType ? " " + it.badgeType : "");
        var iconHtml = it.logo
          ? '<img src="' + esc(it.logo) + '" alt="" width="26" height="26" loading="lazy" />'
          : (SVG_ICONS[it.iconKey] || SVG_ICONS.generic);
        html += '<div class="int-card" data-id="' + esc(it.id) + '" role="button" tabindex="0" aria-label="' + esc(it.name) + ' integration details">' +
          '<div class="int-icon-wrap" style="color:' + esc(it.color) + '">' + iconHtml + '</div>' +
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
        if (item.logo) {
          mIcon.innerHTML = '<img src="' + esc(item.logo) + '" alt="" width="34" height="34" style="object-fit:contain;display:block;" />';
        } else {
          mIcon.innerHTML = SVG_ICONS[item.iconKey] || SVG_ICONS.generic;
          mIcon.style.color = item.color;
        }
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
        // aria-pressed carries the state the .active class only shows visually,
        // so a screen-reader user can tell which filter is on (issue #550). It
        // has to be cleared on every pill, not just set on the new one, or the
        // previous filter keeps announcing itself as pressed.
        pills.forEach(function (p) {
          p.classList.remove("active");
          p.setAttribute("aria-pressed", "false");
        });
        pill.classList.add("active");
        pill.setAttribute("aria-pressed", "true");
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
