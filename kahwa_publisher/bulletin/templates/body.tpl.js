/* Kahwa News «قهوة اليوم» — newscast BODY composition (HyperFrames), ported from the approved pilot v4 (work/bulletin-pilot-v4).
 * Data-driven: every pilot-specific value (price card, spark labels, grid background map) now comes from window.BUL
 * (kahwa_publisher/bulletin/rundown.py). B.mode "edition" = the «العالم في 30 ثانية» reel body (headlines + outro only).
 * One paused GSAP timeline built from window.BUL. Rules carried from the reel engine: RTL; Arabic animated per WORD only
 * (never per letter, HF bug #5885); no letter-spacing; deterministic; forward motion right -> left.
 * Frame 0 of the body = «أبرز العناوين» title card (never a story). makeMap() is copied verbatim at build time from
 * kahwa_publisher/reels/templates/kahwa.js (remote main b7eb5b1, incl. the Western Sahara fix).
 * Layout: chrome y 276-370 · visual window y 370-800 · text panel y 800-1240 (all critical text above y 1248) ·
 * lower zone y 1270-1900 = non-critical furniture (rundown rail, date/update chip) over the continuing visual.
 */
(function () {
  const B = window.BUL;
  const NO_FOCUS = ["MAR", "DZA", "ARE", "ISR", "PSE", "PSX"];
  const PAIR_TRIGGER = "ESH";
  const PRIORITY_ANCHORS = {
    ESH: [[-13.0, 25.0], [-12.5, 26.0], [-11.0, 26.5], [-14.0, 24.5], [-13.6, 23.6], [-10.2, 26.8], [-14.8, 22.8], [-15.8, 22.0]],
    MAR: [[-7.19, 31.65], [-6.2, 33.0], [-8.0, 30.6], [-9.2, 29.6], [-5.4, 32.2], [-10.0, 28.3], [-6.5, 31.2]],
  };
  const PRIORITY_SPLIT = { ESH: ["الصحراء", "الغربية"] };
  gsap.registerPlugin(CustomEase);
  const E = {
    enter: CustomEase.create("enter", "0.16,1,0.3,1"),
    exit: CustomEase.create("exit", "0.7,0,0.84,0"),
    camera: CustomEase.create("camera", "0.65,0,0.35,1"),
    count: CustomEase.create("count", "0.33,1,0.68,1"),
  };
  const root = document.getElementById("root");
  const tl = gsap.timeline({ paused: true });
  const MAPS = [];
  const SC = {}; B.scenes.forEach((s) => (SC[s.id] = s));

  function el(tag, cls, parent, html, style) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    if (style) Object.assign(e.style, style);
    (parent || root).appendChild(e);
    return e;
  }
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const bidi = (s) => esc(s).replace(/([A-Za-z0-9][A-Za-z0-9 .\-\/:%]*[A-Za-z0-9.%])/g, "<bdi>$1</bdi>");

  const scenesL = el("div", "layer", root); scenesL.id = "scenes";
  const railL = el("div", "layer", root); railL.id = "rail";
  const chrome = el("div", "layer", root); chrome.id = "chrome";

  function rich(container, text) {
    const words = [], kws = [];
    String(text).split(/(\[\[.+?\]\])/).forEach((p) => {
      if (!p) return;
      if (p.startsWith("[[")) {
        const k = el("span", "w kw", container, `<span class="bg"></span>${esc(p.slice(2, -2))}`);
        words.push(k); kws.push(k); container.appendChild(document.createTextNode(" "));
      } else {
        p.split(/\s+/).filter(Boolean).forEach((w) => { words.push(el("span", "w", container, bidi(w))); container.appendChild(document.createTextNode(" ")); });
      }
    });
    return { words, kws };
  }
  function scene(id, z) {
    const s = SC[id];
    const d = el("div", "scene" + (s.t === 0 ? " on0" : ""), scenesL);
    d.dataset.scene = id; if (z) d.style.zIndex = z;
    if (s.t > 0) tl.fromTo(d, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.001, ease: "none", immediateRender: false }, s.t);
    tl.to(d, { autoAlpha: 0, duration: 0.001 }, s.t + s.d);
    return d;
  }
  function wordsIn(words, t) {
    tl.fromTo(words, { autoAlpha: 0, filter: "blur(6px)" },
      { autoAlpha: 1, filter: "blur(0px)", duration: 0.14, ease: "power2.out", stagger: 0.06, immediateRender: true }, t);
    tl.set(words, { filter: "none" }, t + 0.14 + 0.06 * words.length + 0.02);
  }
  function kwWipe(kws, t) {
    kws.forEach((k, i) => {
      const bg = k.querySelector(".bg");
      tl.fromTo(bg, { scaleX: 0 }, { scaleX: 1, duration: 0.18, ease: E.camera, immediateRender: true }, t + i * 0.08);
    });
  }
  function textInto(c, text, t) { const r = rich(c, text); wordsIn(r.words, t); kwWipe(r.kws, t + 0.06 * r.words.length + 0.15); return r; }
  function srcTag(parent, text, t, top) {
    const c = el("div", "src2", parent, `<span>${bidi(text)}</span>`, top ? { top: top + "px" } : null);
    tl.fromTo(c, { x: 40, autoAlpha: 0 }, { x: 0, autoAlpha: 1, duration: 0.3, ease: E.enter, immediateRender: true }, t);
    return c;
  }
  function tab(parent, text, t) {
    const k = el("div", "kicker", parent, esc(text));
    tl.fromTo(k, { clipPath: "inset(0 0 0 100%)" }, { clipPath: "inset(0 0 0 0%)", duration: 0.3, ease: E.enter, immediateRender: true }, t + 0.05);
    return k;
  }
  function wheel(container, display, t, dur) {
    const box = el("span", "num", container);
    const strips = [];
    for (const ch of display) {
      if (!/\d/.test(ch)) { el("span", "sep", box, esc(ch)); continue; }
      const stops = 10 + Number(ch);
      const dg = el("span", "dg", box); const st = el("span", "strip", dg);
      for (let i = 0; i <= stops; i++) el("span", "", st, String(i % 10));
      st.dataset.stops = stops; strips.push(st);
    }
    strips.reverse().forEach((st, i) => tl.fromTo(st, { y: "0em" }, { y: `-${st.dataset.stops}em`, duration: dur || 1.4, ease: E.count, immediateRender: true }, t + i * 0.05));
    return box;
  }

/*__MAKEMAP__*/

  // ------------------------------------------------------------------ visuals
  const AVOID_FIXED = [[40, 780, 1040, 1250], [40, 1262, 1040, 1770], [580, 288, 1030, 372]];
  function mapVis(parent, mp, t, d, opt) {
    opt = opt || {};
    const holder = el("div", "full", parent, null, { opacity: opt.dim || 1 });
    const pills = [];
    if (mp.point && mp.point.label && opt.pills !== false) pills.push({ p: mp.point, e: el("div", "map-label pill dark", parent, esc(mp.point.label)) });
    (mp.extra || []).forEach((x) => { if (opt.pills !== false) pills.push({ p: x, e: el("div", "map-label pill ghost", parent, `<i></i>${esc(x.label)}`), extra: true }); });
    const avoid = (proj) => {
      const b = AVOID_FIXED.slice();
      pills.forEach((q) => { const p = proj([q.p.lon, q.p.lat]); if (p) b.push(q.extra ? [p[0] - 150, p[1] - 90, p[0] + 150, p[1] + 20] : q.p.below ? [p[0] - 220, p[1] + 20, p[0] + 220, p[1] + 110] : [p[0] - 220, p[1] - 150, p[0] + 220, p[1] - 30]); });
      return b;
    };
    const onRender = [(proj) => {
      pills.forEach((q) => {
        const p = proj([q.p.lon, q.p.lat]);
        if (p) { q.e.style.left = p[0] + "px"; q.e.style.top = (q.extra ? p[1] - 76 : q.p.below ? p[1] + 34 : p[1] - 96) + "px"; }
      });
    }];
    const st0 = mp.s0 || mp.st;
    const m = makeMap(holder, { iso3: mp.iso3, point: mp.point, avoid, onRender, labels: opt.labels !== false,
      state: Object.assign({ fill: 1, dot: 1, ring: 0 }, st0) });
    const gl = m.svg.querySelector('circle[fill="url(#hl)"]'); if (gl) gl.setAttribute("opacity", "0.35");   // no red wash
    pills.forEach((q) => gsap.set(q.e, { xPercent: -50 }));
    if (mp.s1) m.to(Object.assign({}, mp.s1), t + 0.05, Math.max(0.8, d - 0.2), E.camera);
    if (mp.point) m.to({ ring: { from: 0, to: Math.max(1, Math.floor(d / 1.5)) } }, t + 0.3, d - 0.4, "none");
    pills.forEach((q, i) => tl.fromTo(q.e, { autoAlpha: 0, y: 14 }, { autoAlpha: 1, y: 0, duration: 0.3, ease: E.enter, immediateRender: true }, t + 0.45 + i * 0.2));
    el("div", "lowfade", parent);
    return m;
  }
  function photoVis(parent, v, t, d, opt) {
    opt = opt || {};
    const bg = el("div", "photo blurfill", parent); const bi = el("img", "", bg); bi.src = v.img;
    const win = el("div", "pwin", parent, null, opt.tall ? { height: "450px" } : null);
    const im = el("img", "", win); im.src = v.img; im.style.objectPosition = v.pos || "50% 50%";
    tl.fromTo(im, { scale: 1.0 }, { scale: 1.07, duration: d, ease: "none", immediateRender: true, transformOrigin: v.pos || "50% 50%" }, t);
    el("div", "archive", parent, esc(v.archive), { right: "72px", top: "392px" });
    el("div", "credit pcred", parent, bidi(v.credit), { top: (opt.tall ? 776 : 726) + "px" });
    el("div", "lowfade", parent);
    return win;
  }
  function inkVis(parent, grid) {
    el("div", "full", parent, null, { background: "var(--ink)" });
    if (grid) el("div", "gridbg", parent, null, { opacity: 0.4 });
    el("div", "glow", parent, null, { top: "900px", opacity: 0.6 });
  }
  function visual(parent, v, t, d, opt) {
    el("div", "full", parent, null, { background: "var(--ink)" });
    if (!v) return inkVis(parent, true);
    if (v.kind === "photo") return photoVis(parent, v, t, d, opt);
    if (v.kind === "map") return mapVis(parent, v.map, t, d, opt);
    return inkVis(parent, true);
  }
  function panel(parent, t, top) {
    const p = el("div", "panel", parent, null, top ? { top: top + "px", height: 1240 - top + "px" } : null);
    tl.fromTo(p, { clipPath: "inset(0 0 0 100%)" }, { clipPath: "inset(0 0 0 0%)", duration: 0.35, ease: E.camera, immediateRender: true }, t);
    return p;
  }

  // ------------------------------------------------------------------ chrome: bug + segment progress
  const bug = el("div", "bug", chrome); el("img", "", bug).src = "logo-kahwa.svg"; el("img", "", bug).src = "logo-band.svg";
  const prog = el("div", "progress", chrome);
  B.segments.forEach((s) => { const seg = el("div", "seg", prog); const i = el("i", "", seg);
    tl.fromTo(i, { scaleX: 0 }, { scaleX: 1, duration: s.d, ease: "none", immediateRender: false }, s.t); });
  gsap.set(prog, { autoAlpha: 0 }); if (SC.b1) tl.to(prog, { autoAlpha: 1, duration: 0.2 }, SC.b1.t); tl.to(prog, { autoAlpha: 0, duration: 0.01 }, SC.end.t);
  tl.to(chrome, { autoAlpha: 0, duration: 0.001 }, SC.end.t);

  // ------------------------------------------------------------------ lower zone: rundown rail (non-critical furniture)
  const rail = el("div", "rundown", railL); rail.setAttribute("data-nc", "1");
  const rh = el("div", "rd-head", rail); el("span", "rd-title", rh, "في هذه النشرة"); el("span", "rd-asof", rh, bidi(B.asof));
  const RROWS = B.segs.map((g, i) => { const r = el("div", "rd-row", rail); el("span", "rd-n", r, String(i + 1).padStart(2, "0")); el("span", "rd-t", r, esc(g.t)); return r; });
  const dateL = el("div", "rd-date", rail, esc(B.date));
  function railState(t, cur, done) {   // cur: highlighted index (-1 none); done: indices before cur are dimmed
    RROWS.forEach((r, i) => {
      const on = i === cur, past = done && i < cur;
      tl.set(r, { backgroundColor: on ? "rgba(220,24,12,0.92)" : "rgba(14,17,25,0.72)", color: on ? "#ffffff" : past ? "#5E6678" : "#C9D0DC" }, t);
    });
  }
  B.scenes.forEach((s) => {
    if (s.type === "headline") { tl.set(railL, { autoAlpha: 1 }, s.t); railState(s.t, s.hlseg, false)   /* v4: derived from the headline's own segment key */; }
    else if (s.seg !== undefined) { tl.set(railL, { autoAlpha: 1 }, s.t); railState(s.t, s.seg, true); }
    else if (s.type === "hltitle") { gsap.set(railL, { autoAlpha: 1 }); railState(0.0001, -1, false); }
    else if (s.type === "end") { tl.set(railL, { autoAlpha: 1 }, s.t); railState(s.t, 99, true); tl.to(railL, { autoAlpha: 0, duration: 0.5, ease: "none" }, s.t + s.d - 0.5); }
  });

  // ------------------------------------------------------------------ red wipe between segments
  function redWipe(t) {
    const bar = el("div", "abs", root, null, { top: "0", width: "220px", height: "1920px", background: "var(--red)", left: "1080px", zIndex: 12 });
    tl.fromTo(bar, { x: 0 }, { x: -1520, duration: 0.34, ease: E.camera, immediateRender: false }, t - 0.17);
  }

  // ------------------------------------------------------------------ scenes
  B.scenes.forEach((s) => {
    const t = s.t, d = s.d;
    if (s.type === "bumper") redWipe(t);
    if (s.id === "end") redWipe(t);
    if (s.type === "hltitle") {
      const sc = scene(s.id);
      inkVis(sc, true);
      const k = el("div", "kicker", sc, "قهوة اليوم");
      const h = el("div", "hl-title", sc); const r = rich(h, s.title);
      gsap.set(r.words, { autoAlpha: 1 });                        // frame 0 of the body: finished title card
      const rule = el("div", "abs", sc, null, { right: "72px", width: "320px", top: "880px", height: "8px", background: "var(--red)", transformOrigin: "100% 50%" });
      tl.fromTo(rule, { scaleX: 0.25 }, { scaleX: 1, duration: 0.6, ease: E.enter, immediateRender: true }, 0.05);
      const sub = el("div", "hl-sub", sc, esc(s.sub));
      tl.fromTo(sub, { autoAlpha: 0.4, y: 10 }, { autoAlpha: 1, y: 0, duration: 0.4, ease: E.enter, immediateRender: true }, 0.1);
      const list = el("div", "hl-pre", sc);
      B.scenes.filter((x) => x.type === "headline").forEach((x, i) => {
        const row = el("div", "hl-pre-row", list, `<b>${x.n}</b>`);
        tl.fromTo(row, { autoAlpha: 0, x: 40 }, { autoAlpha: 1, x: 0, duration: 0.3, ease: E.enter, immediateRender: true }, 0.4 + i * 0.18);
      });
    } else if (s.type === "headline") {
      const sc = scene(s.id);
      if (s.vis.kind === "price") {
        inkVis(sc, true);
        const big = el("div", "hl-price", sc);
        wheel(big, String(s.vis.num), t + 0.1, 1.2); el("span", "punit", big, esc(s.vis.unit || ""));
        el("div", "hl-price-lab", sc, bidi(s.vis.label || ""));
        const sv = el("div", "hl-arrow", sc, s.vis.dir === "up" ? "▲" : "▼");
        tl.fromTo(sv, { autoAlpha: 0, y: -20 }, { autoAlpha: 1, y: 0, duration: 0.4, ease: E.enter, immediateRender: true }, t + 1.0);
        el("div", "lowfade", sc);
      } else if (s.vis.kind === "map") {
        el("div", "full", sc, null, { background: "var(--ink)" });
        mapVis(sc, s.vis.map, t, d);                                   // v4: Yemen headline = map of Yemen / Taiz
      } else if (s.vis.kind === "photo") photoVis(sc, s.vis, t, d, { tall: true });
      else inkVis(sc, true);
      el("div", "kicker", sc, B.mode === "edition" ? "العالم في 30 ثانية" : "أبرز العناوين");
      const p = panel(sc, t + 0.05, 820);
      const n = el("div", "hl-n", p, String(s.n));
      tl.fromTo(n, { scale: 0.6, autoAlpha: 0 }, { scale: 1, autoAlpha: 1, duration: 0.3, ease: E.enter, immediateRender: true }, t + 0.15);
      const c = el("div", "h2 hl-text", p); textInto(c, s.text, t + 0.3);
    } else if (s.type === "bumper") {
      const sc = scene(s.id, 9);
      el("div", "full", sc, null, { background: "var(--ink)" }); el("div", "glow", sc); el("div", "grain", sc);
      const stripe = el("div", "bstripe", sc);
      tl.fromTo(stripe, { x: 400 }, { x: -200, duration: d, ease: "none", immediateRender: true }, t);
      const num = el("div", "bnum", sc, esc(s.num));
      tl.fromTo(num, { autoAlpha: 0, x: 80 }, { autoAlpha: 1, x: 0, duration: 0.4, ease: E.enter, immediateRender: true }, t + 0.1);
      const tg = el("div", "btag", sc, esc(s.tag));
      tl.fromTo(tg, { clipPath: "inset(0 0 0 100%)" }, { clipPath: "inset(0 0 0 0%)", duration: 0.3, ease: E.enter, immediateRender: true }, t + 0.25);
      const ti = el("div", "btitle", sc); const r = rich(ti, s.title); wordsIn(r.words, t + 0.35);
      const lg = el("div", "blogo", sc); const k1 = el("img", "", lg); k1.src = "logo-kahwa.svg"; const k2 = el("img", "", lg); k2.src = "logo-band.svg";
      tl.to([num, ti, tg], { autoAlpha: 0, x: -60, duration: 0.2, ease: E.exit }, t + d - 0.22);
    } else if (s.type === "strap") {
      const sc = scene(s.id);
      visual(sc, s.vis, t, d);
      tab(sc, s.tab, t);
      const p = panel(sc, t + 0.05);
      const c = el("div", "h2 ptext", p); textInto(c, s.text, t + 0.3);
      srcTag(sc, s.src, t + 0.7);
    } else if (s.type === "map") {
      const sc = scene(s.id);
      visual(sc, { kind: "map", map: s.map }, t, d);
      tab(sc, s.tab, t);
      const p = panel(sc, t + 0.1);
      const c = el("div", "h2 ptext", p); textInto(c, s.text, t + 0.35);
      srcTag(sc, s.src, t + 0.75);
    } else if (s.type === "number") {
      const sc = scene(s.id);
      visual(sc, s.vis, t, d);
      tab(sc, s.tab, t);
      const p = panel(sc, t + 0.05, 700);
      const nb = el("div", "numblock2", p);
      const nr = el("div", "numrow", nb); wheel(nr, s.num, t + 0.15, 1.4);
      const lab = el("div", "numlabel", nb); textInto(lab, s.label, t + 0.5);
      const dk = el("div", "numdeck", nb); textInto(dk, s.deck, t + 1.0);
      srcTag(sc, s.src, t + 0.9);
    } else if (s.type === "grid") {
      const sc = scene(s.id);
      el("div", "full", sc, null, { background: "var(--ink)" });
      if (s.bgmap) mapVis(sc, Object.assign({}, s.bgmap, { point: null, extra: [] }), t, d, { dim: 0.32, pills: false, labels: false });
      else inkVis(sc, true);
      tab(sc, s.tab, t);
      const h = el("div", "abs sec-title h2", sc); textInto(h, s.title, t + 0.1);
      s.tiles.forEach((x, i) => {
        const col = i % 2, row = Math.floor(i / 2);
        const tile = el("div", "gtile", sc, null, { top: 520 + row * 270 + "px", right: 72 + col * 480 + "px" });
        const v = el("div", "gv", tile, `<bdi>${esc(x.v)}</bdi>`); el("div", "gl", tile, esc(x.l));
        tl.fromTo(tile, { y: 40, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.4, ease: E.enter, immediateRender: true }, t + 0.5 + i * 0.45);
      });
      srcTag(sc, s.src, t + 1.0);
    } else if (s.type === "timeline") {
      const sc = scene(s.id);
      inkVis(sc, true);
      tab(sc, s.tab, t);
      const h = el("div", "abs sec-title h2", sc); textInto(h, s.title, t + 0.1);
      const line = el("div", "tl-line", sc);
      tl.fromTo(line, { scaleY: 0 }, { scaleY: 1, duration: d * 0.6, ease: "none", immediateRender: true }, t + 0.4);
      const per = (d - 1.2) / s.steps.length;
      s.steps.forEach((x, i) => {
        const row = el("div", "tl-step", sc, null, { top: 520 + i * 190 + "px" });
        const dot = el("div", "tl-dot", row, esc(x.k));
        const tx = el("div", "tl-tx", row);
        const tt = t + 0.5 + i * per;
        tl.fromTo(row, { autoAlpha: 0, x: 50 }, { autoAlpha: 1, x: 0, duration: 0.35, ease: E.enter, immediateRender: true }, tt);
        textInto(tx, x.t, tt + 0.1);
        if (i > 0) tl.to(sc.querySelectorAll(".tl-step")[i - 1], { opacity: 0.6, duration: 0.3 }, tt);
      });
      srcTag(sc, s.src, t + 0.8);
    } else if (s.type === "quote") {
      const sc = scene(s.id);
      visual(sc, s.vis, t, d, { dim: 0.45, pills: false, labels: false });
      tab(sc, s.tab, t);
      const p = panel(sc, t + 0.05, 720);
      el("div", "qmark", p, "”");
      const c = el("div", "h2 qtext", p); textInto(c, s.text, t + 0.3);
      const who = el("div", "qwho", p, `<b>${esc(s.who)}</b> · ${esc(s.role)}`);
      tl.fromTo(who, { autoAlpha: 0, x: 30 }, { autoAlpha: 1, x: 0, duration: 0.3, ease: E.enter, immediateRender: true }, t + 1.4);
      srcTag(sc, s.src, t + 1.0);
    } else if (s.type === "next") {
      const sc = scene(s.id);
      if (s.vis) { visual(sc, s.vis, t, d); el("div", "full", sc, null, { background: "rgba(9,11,16,0.72)" }); }
      else inkVis(sc, true);
      tab(sc, s.tab, t);
      const h = el("div", "abs sec-title h2", sc); textInto(h, s.title, t + 0.1);
      const per = (d - 1.4) / s.rows.length;
      s.rows.forEach((x, i) => {
        const row = el("div", "nx-row", sc, null, { top: 520 + i * 180 + "px" });
        el("div", "nx-i", row, esc(x.i));
        const tx = el("div", "nx-tx", row);
        const tt = t + 0.45 + i * Math.min(per, 2.6);
        tl.fromTo(row, { autoAlpha: 0, x: 50 }, { autoAlpha: 1, x: 0, duration: 0.35, ease: E.enter, immediateRender: true }, tt);
        textInto(tx, x.t, tt + 0.1);
      });
      srcTag(sc, s.src, t + 0.8);
    } else if (s.type === "price") {
      const sc = scene(s.id);
      inkVis(sc, true);
      tab(sc, s.tab, t);
      const card = el("div", "pricecard", sc);
      const lab = el("div", "plab", card); textInto(lab, s.label, t + 0.15);
      const row = el("div", "prow", card); wheel(row, s.num, t + 0.25, 1.5); el("span", "punit", row, esc(s.unit));
      const dl = el("div", "pdelta", card, `<span class="arrow">${s.dir === "up" ? "▲" : "▼"}</span><bdi>${esc(s.delta)}</bdi>`);
      tl.fromTo(dl, { autoAlpha: 0, x: 30 }, { autoAlpha: 1, x: 0, duration: 0.3, ease: E.enter, immediateRender: true }, t + 1.5);
      const asof = el("div", "pasof", card, bidi(s.asof_line || ""));
      tl.fromTo(asof, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.3, immediateRender: true }, t + 1.7);
      const NS = "http://www.w3.org/2000/svg"; const sv = document.createElementNS(NS, "svg"); sv.setAttribute("viewBox", "0 0 936 170"); sv.setAttribute("class", "spark"); sc.appendChild(sv);
      sv.innerHTML = `<line x1="0" y1="150" x2="936" y2="150" stroke="#2A3142" stroke-width="2"/>
        <path id="sp" d="M 860 40 L 76 120" fill="none" stroke="#FF4D40" stroke-width="6" stroke-linecap="round"/>
        <circle cx="860" cy="40" r="10" fill="#F2F4F7"/><circle cx="76" cy="120" r="12" fill="#FF4D40"/>`;
      const spk = s.spark || {}; const pl = el("div", "sparklab r", sc, bidi(spk.right || "")); const pl2 = el("div", "sparklab l", sc, bidi(spk.left || ""));
      const sp = sv.querySelector("#sp"); const L = 790;
      gsap.set(sp, { attr: { "stroke-dasharray": L, "stroke-dashoffset": L } });
      tl.to(sp, { attr: { "stroke-dashoffset": 0 }, duration: 1.0, ease: E.camera }, t + 0.8);
      tl.fromTo([sv, pl, pl2], { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.3, immediateRender: true }, t + 0.7);
      srcTag(sc, s.src, t + 0.9);
    } else if (s.type === "bars") {
      const sc = scene(s.id);
      inkVis(sc, true);
      tab(sc, s.tab, t);
      const h = el("div", "abs sec-title h2", sc); textInto(h, s.title, t + 0.1);
      const mx = Math.max(...s.bars.map((b) => b.v));
      s.bars.forEach((b, i) => {
        const row = el("div", "bar-row2" + (i === s.bars.length - 1 ? " key" : ""), sc, null, { top: 680 + i * 200 + "px" });
        el("div", "bl", row, esc(b.lab));
        const tr = el("div", "track", row); const f = el("div", "fill", tr, null, { width: (b.v / mx * 100).toFixed(1) + "%" });
        const bv = el("div", "bv", tr, `<bdi>${esc(b.disp)}</bdi>`);
        tl.fromTo(f, { scaleX: 0 }, { scaleX: 1, duration: 0.9, ease: E.camera, immediateRender: true }, t + 0.7 + i * 0.5);
        tl.fromTo(bv, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.3, immediateRender: true }, t + 1.3 + i * 0.5);
      });
      if (s.note) el("div", "bar-note", sc, esc(s.note));
      srcTag(sc, s.src, t + 0.9);
    } else if (s.type === "rates") {
      const sc = scene(s.id);
      visual(sc, s.vis, t, d); el("div", "full", sc, null, { background: "rgba(9,11,16,0.55)" });
      tab(sc, s.tab, t);
      const h = el("div", "abs sec-title h2", sc, null, { top: "812px" }); textInto(h, s.title, t + 0.1);
      s.tiles.forEach((x, i) => {
        const tile = el("div", "rtile2", sc, null, { top: "920px", right: 72 + i * 480 + "px" });
        el("div", "rv", tile, `<bdi>${esc(x.v)}</bdi>`); el("div", "rl", tile, esc(x.l)); el("div", "rs", tile, bidi(x.s));
        tl.fromTo(tile, { x: 80, autoAlpha: 0 }, { x: 0, autoAlpha: 1, duration: 0.4, ease: E.enter, immediateRender: true }, t + 0.6 + i * 0.6);
      });
      srcTag(sc, s.src, t + 1.0);
    } else if (s.type === "list") {
      const sc = scene(s.id);
      inkVis(sc, true);
      tab(sc, s.tab, t);
      let tt = t + 0.3;
      s.rows.forEach((row, i) => {
        const rw = el("div", "lrow2", sc, null, { top: 420 + i * 250 + "px" });
        el("div", "li", rw, String(i + 1));
        const tx = el("div", "ltx", rw); const rr = rich(tx, row.t);
        el("div", "ls2", rw, "المصدر: " + esc(row.s));
        tl.fromTo(rw, { x: 60, autoAlpha: 0 }, { x: 0, autoAlpha: 1, duration: 0.3, ease: E.enter, immediateRender: true }, tt);
        wordsIn(rr.words, tt + 0.08); kwWipe(rr.kws, tt + 0.2 + 0.06 * rr.words.length);
        if (i > 0) tl.to(sc.querySelectorAll(".lrow2")[i - 1], { opacity: 0.6, duration: 0.3 }, tt);
        tt += s.row_d[i];
      });
    } else if (s.type === "end") {
      const sc = scene(s.id, 9);
      el("div", "full", sc, null, { background: "var(--ink)" }); el("div", "glow", sc); el("div", "grain", sc);
      const lg = el("div", "logo", sc, null, { top: "430px" }); const k = el("img", "", lg); k.src = "logo-kahwa.svg"; const b = el("img", "", lg); b.src = "logo-band.svg";
      tl.fromTo(k, { clipPath: "inset(0 0 0 100%)" }, { clipPath: "inset(0 0 0 0%)", duration: 0.45, ease: E.enter, immediateRender: true }, t + 0.1);
      tl.fromTo(b, { clipPath: "inset(0 0 0 100%)" }, { clipPath: "inset(0 0 0 0%)", duration: 0.45, ease: E.enter, immediateRender: true }, t + 0.22);
      const l1 = el("div", "endline", sc, esc(s.l1), { top: "700px", fontSize: "60px", fontWeight: 800 });
      tl.fromTo(l1, { y: 24, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.4, ease: E.enter, immediateRender: true }, t + 0.5);
      const rule = el("div", "abs", sc, null, { left: "390px", width: "300px", top: "800px", height: "5px", background: "var(--red)", transformOrigin: "50% 50%" });
      tl.fromTo(rule, { scaleX: 0 }, { scaleX: 1, duration: 0.45, ease: E.enter, immediateRender: true }, t + 0.6);
      const l2 = el("div", "endline", sc, esc(s.l2), { top: "840px", fontSize: "48px", fontWeight: 700 });
      tl.fromTo(l2, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.4, immediateRender: true }, t + 0.9);
      const l3 = el("div", "endline", sc, `<span class="chk" data-nc="1">✓</span> ${esc(s.l3)}`, { top: "940px", fontSize: "38px", fontWeight: 700, color: "#C9D0DC" });
      tl.fromTo(l3, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.4, immediateRender: true }, t + 1.3);
      const cr = el("div", "endline credits", sc, s.credits.split("<br>").map(bidi).join("<br>"), { top: "1060px" });
      tl.fromTo(cr, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.4, immediateRender: true }, t + 1.6);
      tl.to(sc, { autoAlpha: 0, duration: 0.5, ease: "none" }, t + d - 0.5);
    }
  });

  const g = el("div", "grain ov", root, null, { zIndex: 11 });
  tl.fromTo(g, { backgroundPosition: "0px 0px" }, { backgroundPosition: `${37 * 97}px ${53 * 97}px`, duration: B.duration, ease: `steps(${Math.round(B.duration * 15)})`, immediateRender: true }, 0);
  tl.set({}, {}, B.duration);
  MAPS.forEach((f) => f());
  window.KAHWA_TL = tl;
  tl.seek(0);
})();
