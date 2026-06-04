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
    }
    burger.addEventListener("click", function () { setOpen(!menu.classList.contains("open")); });
    // close after navigating
    qa(".nav-mlink", menu).forEach(function (a) { a.addEventListener("click", function () { setOpen(false); }); });
    // close if resized to desktop
    window.addEventListener("resize", function () { if (window.innerWidth > 680) setOpen(false); });
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
      btn.addEventListener("click", function () {
        var t = cmd.textContent;
        var done = function () {
          btn.classList.add("done"); labelEl.textContent = "copied ✓";
          setTimeout(function () { btn.classList.remove("done"); labelEl.textContent = base; }, 1400);
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
      btn.innerHTML = COPY + "<span>copy</span>";
      wrap.appendChild(btn);
      btn.addEventListener("click", function () {
        var code = pre.querySelector("code") || pre;
        // strip inline comments? no — copy verbatim, trimmed
        var text = code.innerText.replace(/[ \t]+$/gm, "").trim() + "\n";
        var ok = function () {
          btn.classList.add("done");
          btn.querySelector("span").textContent = "copied";
          setTimeout(function () { btn.classList.remove("done"); btn.querySelector("span").textContent = "copy"; }, 1400);
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

    function clearAll() {
      qa(".lit", pipe).forEach(function (el) { el.classList.remove("lit"); });
      Object.keys(conns).forEach(function (k) { conns[k].classList.remove("flow"); });
    }
    function spark(k) {
      var c = conns[k]; if (!c) return;
      c.classList.remove("flow"); void c.offsetWidth; c.classList.add("flow");
    }
    function lit(stage) { if (stages[stage]) stages[stage].classList.add("lit"); }

    // Looping scenarios — different panels & outcomes; the last runs in issue mode.
    var SCENARIOS = [
      { pr: "123", reviewers: ["claude", "codex", "agy", "qwen"], debate: true,  changes: false },
      { pr: "124", reviewers: ["claude", "codex"],               debate: true,  changes: true, findings: "2 blocking" },
      { pr: "125", reviewers: ["codex", "agy", "qwen"],          debate: false, changes: false }
    ];
    function setScenario(s) {
      if (ttl) ttl.textContent = "$ jury --pr " + s.pr;
      // panel: fade reviewers not on this PR's jury
      chips.forEach(function (c) { c.classList.toggle("muted", s.reviewers.indexOf(c.getAttribute("data-rev")) === -1); });
      // round 2: shown only when the panel actually debates
      if (stages[2]) stages[2].classList.toggle("skipped", !s.debate);
      if (debateMark) { debateMark.classList.remove("lit"); debateMark.textContent = s.debate ? "debate ⇄" : "skip ✓"; }
      // start in a neutral "pending" state — the outcome isn't known until the flow arrives
      if (verdict) {
        verdict.className = "verdict-badge pending";
        verdict.textContent = "judging…";
      }
      if (stages[5]) stages[5].classList.remove("changed");
      if (findings) { findings.hidden = true; findings.innerHTML = ""; }
    }
    function revealVerdict(s) {
      if (verdict) {
        verdict.className = "verdict-badge " + (s.changes ? "changes" : "approve") + " pop";
        verdict.textContent = s.changes ? "✕ REQUEST CHANGES" : "✓ APPROVE";
      }
      if (stages[5]) stages[5].classList.toggle("changed", !!s.changes);
      if (findings) {
        if (s.changes) { findings.hidden = false; findings.innerHTML = '<span class="sev-dot"></span>' + s.findings; }
        else { findings.hidden = true; findings.innerHTML = ""; }
      }
    }

    if (reduce) {
      setScenario(SCENARIOS[0]);
      revealVerdict(SCENARIOS[0]);
      Object.keys(stages).forEach(lit);
      chips.forEach(function (c) { c.classList.add("lit"); });
      return;
    }

    var timers = [];
    function at(ms, fn) { timers.push(setTimeout(fn, ms)); }

    var idx = 0;

    function play() {
      timers.forEach(clearTimeout); timers = [];
      clearAll();
      var s = SCENARIOS[idx % SCENARIOS.length];
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
      // reviewers light up one by one (only this PR's panel)
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
      claude: { vendor: "anthropic", command: "claude" },
      codex:  { vendor: "openai", command: "codex" },
      agy:    { vendor: "google", command: "agy" },
      qwen:   { vendor: "local", model: "qwen2.5-coder:7b", endpoint: "http://localhost:11434/v1" }
    };
    var LABEL = { claude: "Claude Code", codex: "Codex", agy: "Antigravity", qwen: "Local" };
    var SEV_RANK = { high: 3, medium: 2, low: 1 };

    function selectedAgents() {
      return ["claude", "codex", "agy", "qwen"].filter(function (n) { return $("ag-" + n).checked; });
    }
    function rounds() { return q('input[name="rounds"]:checked').value; }
    function postmode() { return q('input[name="postmode"]:checked').value; }
    function target() { return q('input[name="target"]:checked').value; }
    function verdictMode() {
      // issue review always falls back to chair (matches the real CLI)
      if (target() === "issue") return "chair";
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

      // issue review forces chair: disable the vote radio and PR-only output options
      var voteRadio = q('input[name="verdict-mode"][value="vote"]');
      if (voteRadio) {
        voteRadio.disabled = isIssue;
        var optVote = $("opt-vote");
        if (optVote) {
          optVote.classList.toggle("disabled", isIssue);
          var voteHint = optVote.querySelector(".opt-hint");
          if (isIssue) {
            if (!voteHint) { voteHint = document.createElement("span"); voteHint.className = "tag opt-hint"; optVote.appendChild(voteHint); }
            voteHint.textContent = "n/a for issues";
          } else if (voteHint) { voteHint.remove(); }
        }
      }
      // PR output (post-mode / progress) is PR-only
      var fsPost = $("fs-postmode");
      if (fsPost) {
        fsPost.classList.toggle("disabled", isIssue);
        qa("input", fsPost).forEach(function (el) { el.disabled = isIssue; });
      }

      // auto-depth owns rounds/verify
      $("verify").disabled = auto;
      $("verify").closest(".opt").classList.toggle("disabled", auto);

      // debate (round 2) needs >=2 successful reviewers — lock it for a solo panel
      var soloPanel = ags.length < 2;
      var r2 = q('input[name="rounds"][value="2"]');
      var r1 = q('input[name="rounds"][value="1"]');
      qa('input[name="rounds"]').forEach(function (el) {
        var lockAuto = auto;
        var lockSolo = soloPanel && el.value === "2";
        el.disabled = lockAuto || lockSolo;
        el.closest(".opt").classList.toggle("disabled", lockAuto || lockSolo);
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
          bits.push("chair decides (vote n/a)");
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
      lines.push("");
      ags.forEach(function (n) {
        var a = AGENTS[n];
        lines.push("[[agent]]");
        lines.push('name = "' + n + '"');
        lines.push('vendor = "' + a.vendor + '"');
        if (a.command) lines.push('command = "' + a.command + '"');
        if (a.endpoint) lines.push('endpoint = "' + a.endpoint + '"');
        if (a.model) lines.push('model = "' + a.model + '"');
        lines.push("");
      });
      $("toml-out").textContent = lines.join("\n").trim() + "\n";
    }

    // Any config change resets the run output: collapse it so the user
    // re-runs and sees the new outcome play out from scratch.
    ctrls.addEventListener("change", function () {
      render();
      if (runTimer) { clearTimeout(runTimer); runTimer = null; }
      var ro = $("run-out");
      if (ro && !ro.hidden) {
        ro.hidden = true;
        $("term-body").innerHTML = "";
        $("gh-comments").innerHTML = "";
      }
      var btn = $("run-btn");
      if (btn) btn.disabled = false;
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
    function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
    function bySev(a, b) { return SEV_RANK[b.sev] - SEV_RANK[a.sev]; }

    // Each reviewer votes from the worst severity among the findings they raised:
    //   any high → REQUEST CHANGES · any finding → COMMENT · none → APPROVE.
    var VOTE_RANK = { "REQUEST CHANGES": 3, "COMMENT": 2, "APPROVE": 1 };
    function agentVote(a, finalF) {
      var mine = finalF.filter(function (f) { return f.by.indexOf(a) !== -1; });
      if (mine.some(function (f) { return f.sev === "high"; })) return "REQUEST CHANGES";
      return mine.length ? "COMMENT" : "APPROVE";
    }
    function tallyVote(ags, finalF) {
      var counts = { "REQUEST CHANGES": 0, "COMMENT": 0, "APPROVE": 0 };
      var votes = {};
      ags.forEach(function (a) { var v = agentVote(a, finalF); votes[a] = v; counts[v]++; });
      // majority wins; tie → stricter (higher VOTE_RANK)
      var winner = "APPROVE", best = -1;
      Object.keys(counts).forEach(function (v) {
        if (counts[v] > best || (counts[v] === best && VOTE_RANK[v] > VOTE_RANK[winner])) { best = counts[v]; winner = v; }
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
      var tally = vm === "vote" ? tallyVote(ags, finalF) : null;
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
      var debate = auto ? true : (r === 2 && ags.length >= 2);
      var verifyOn = auto ? true : verify;
      var chair = ags[0];
      // gaps this panel surfaced
      var gaps = ISSUE_GAPS.filter(function (g) { return g.by.some(function (a) { return ags.indexOf(a) !== -1; }); });
      var blocking = gaps.filter(function (g) { return g.sev === "high"; });
      // READY when no gaps · NEEDS-INFO when a blocking gap exists · UNCLEAR otherwise
      var verdict = blocking.length ? "NEEDS-INFO" : (gaps.length ? "UNCLEAR" : "READY");

      var con = [];
      con.push(["$ jury --issue 42" + (auto ? " --auto" : ""), "cmd"]);
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
      con.push(["synthesis: chair '" + LABEL[chair] + "' → " + verdict, "head"]);
      con.push(["done in 48s", "dim"]);

      return { mode: "issue", ags: ags, debate: debate, verifyOn: verifyOn, chair: chair, gaps: gaps, blocking: blocking, verdict: verdict, con: con, r: r, auto: auto };
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
      return '<li class="gh-finding"><span class="sev sev-' + f.sev + '">' + f.sev + '</span> <code>' + esc(f.file) + '</code> — ' + esc(f.title) + '<span class="why">why: ' + esc(f.evidence) + "</span></li>";
    }
    function gapRow(g) {
      return '<li class="gh-finding"><span class="sev sev-' + g.sev + '">' + g.sev + '</span> <code>' + esc(g.aspect) + '</code> — ' + esc(g.status) + '<span class="why">why: ' + esc(g.evidence) + "</span></li>";
    }
    function commentCard(title, inner) {
      return '<div class="gh-comment"><div class="gh-head"><span class="gh-bot"><img src="assets/logos/mark-convergence.svg" alt="">AI Jury</span><span class="gh-meta">' + esc(title) + "</span></div><div class=\"gh-body\">" + inner + "</div></div>";
    }
    function renderComments(run) {
      var html = "";
      var panel = run.ags.map(function (a) { return LABEL[a]; }).join(", ");

      if (run.mode === "issue") {
        var body = verdictBadge(run)
          + (run.gaps.length ? "<ul class='gh-findings'>" + run.gaps.slice().sort(bySev).map(gapRow).join("") + "</ul>"
                             : "<p>Complete — repro, expected/actual, and scope are all present.</p>")
          + '<div class="gh-foot">issue #42 · panel: ' + esc(panel) + " · rounds: " + (run.auto ? "auto" : run.r) + (run.verifyOn ? " · verified" : "") + " · chair decides</div>";
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
          return "<li><b>" + esc(LABEL[a]) + "</b>: " + (fs.length ? fs.map(function (f) { return esc(f.file); }).join(", ") : "no findings") + "</li>";
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
        run.con.forEach(function (l) { $("term-body").appendChild(consoleLine(l)); });
        renderComments(run);
        return;
      }
      btn.disabled = true;
      var i = 0;
      (function step() {
        if (i < run.con.length) {
          $("term-body").appendChild(consoleLine(run.con[i++]));
          $("term-body").scrollTop = $("term-body").scrollHeight;
          runTimer = setTimeout(step, 220);
        } else {
          runTimer = null;
          renderComments(run);
          btn.disabled = false;
        }
      })();
    }
    function syncRun() {
      if ($("run-out").hidden) return;
      if (runTimer) { clearTimeout(runTimer); runTimer = null; }
      $("run-btn").disabled = false;
      if (selectedAgents().length === 0) {
        $("term-body").innerHTML = "";
        $("gh-comments").innerHTML = '<p class="muted">Pick at least one reviewer.</p>';
        return;
      }
      var run = buildRun();
      setTermTitle(run);
      $("term-body").innerHTML = "";
      run.con.forEach(function (l) { $("term-body").appendChild(consoleLine(l)); });
      renderComments(run);
    }

    $("run-btn").addEventListener("click", runDemo);
  })();
})();
