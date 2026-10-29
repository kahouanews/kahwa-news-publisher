/* Kahwa News reel engine v2 (HyperFrames composition). One paused GSAP timeline from window.REEL.
 * Picture = plate clips (real footage/photos, rendered by build.py) + GL Transitions between them (glx.js).
 * Over the picture: scrims, text scenes, chrome. Rules: RTL; Arabic animated per WORD or LINE only
 * (never per letter); no letter-spacing; deterministic; forward motion = right -> left; frame 0 = finished poster.
 */
(function () {
  const R = window.REEL, S = R.story, P = R.plan, STYLE = R.style, M = R.media, BOX = R.box;
  gsap.registerPlugin(CustomEase);
  const E = {
    enter: CustomEase.create("enter", "0.16,1,0.3,1"),
    exit: CustomEase.create("exit", "0.7,0,0.84,0"),
    camera: CustomEase.create("camera", "0.65,0,0.35,1"),
    ambient: CustomEase.create("ambient", "0.37,0,0.63,1"),
    count: CustomEase.create("count", "0.33,1,0.68,1"),
  };
  const root = document.getElementById("root");
  const tl = gsap.timeline({ paused: true });
  const SC = {}; P.scenes.forEach((s) => (SC[s.id] = s));
  const MAPS = [];
  const center = (n) => gsap.set(n, { xPercent: -50, yPercent: -50 });

  // ---------------------------------------------------------------- helpers
  function el(tag, cls, parent, html, style) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    if (style) Object.assign(e.style, style);
    (parent || root).appendChild(e);
    return e;
  }
  const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const bidi = (s) => esc(s).replace(/([A-Za-z][A-Za-z0-9 .\-\/]*[A-Za-z0-9.])/g, "<bdi>$1</bdi>");

  const overlay = el("div", "layer", root); overlay.id = "overlay";
  const scenesL = el("div", "layer", root); scenesL.id = "scenes";
  const chrome = el("div", "layer", root); chrome.id = "chrome";

  /* Rich text: "word [[key phrase]] word" -> word spans; key phrase = red block (white text). */
  function rich(container, text) {
    const words = [], kws = [];
    String(text).split(/(\[\[.+?\]\])/).forEach((p) => {
      if (!p) return;
      if (p.startsWith("[[")) {
        const k = el("span", "w kw", container, `<span class="bg"></span>${esc(p.slice(2, -2))}`);
        words.push(k); kws.push(k); container.appendChild(document.createTextNode(" "));
      } else {
        p.split(/\s+/).filter(Boolean).forEach((w) => { words.push(el("span", "w", container, esc(w))); container.appendChild(document.createTextNode(" ")); });
      }
    });
    return { words, kws };
  }

  // a scene = a DOM group visible over [t, t+d)
  function scene(id, parent) {
    const s = SC[id];
    const d = el("div", "scene" + (s.t === 0 ? " on0" : ""), parent || scenesL);
    d.dataset.scene = id;
    if (s.t > 0) tl.fromTo(d, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.001, ease: "none", immediateRender: false }, s.t);
    tl.to(d, { autoAlpha: 0, duration: 0.001 }, s.t + s.d);
    return d;
  }
  function windowed(node, t0, t1) {
    if (t0 > 0) { gsap.set(node, { autoAlpha: 0 }); tl.to(node, { autoAlpha: 1, duration: 0.001 }, t0); }
    tl.to(node, { autoAlpha: 0, duration: 0.001 }, t1);
    return node;
  }

  /* Kinetic word captions (HyperFrames motion.md): per word blur 6px -> 0 + opacity, 0.13 s, 0.067 s apart, no translate. */
  function wordsIn(words, t, opt) {
    opt = opt || {};
    if (t <= 0.0001) return;   // frame 0 poster: already landed
    tl.fromTo(words, { autoAlpha: 0, filter: "blur(6px)" },
      { autoAlpha: 1, filter: "blur(0px)", duration: opt.dur || 0.13, ease: "power2.out", stagger: opt.stagger || 0.067, immediateRender: true }, t);
    tl.set(words, { filter: "none" }, t + (opt.dur || 0.13) + (opt.stagger || 0.067) * words.length + 0.02);
  }
  function blockOut(node, t) {
    tl.to(node, { autoAlpha: 0, filter: "blur(8px)", duration: 0.2, ease: E.exit }, t);
  }
  // keyword highlight wipe. On the hook scene (frame 0 = poster/thumbnail) the highlight must already be complete on
  // frame 0: a half-drawn or missing red bar on the poster looked like a glitch (fixed 2026-10-29).
  function kwWipe(kws, t, poster) {
    kws.forEach((k, i) => {
      const bg = k.querySelector(".bg");
      if (poster) { tl.set(bg, { scaleX: 1 }, 0); return; }
      tl.fromTo(bg, { scaleX: 0 }, { scaleX: 1, duration: 0.16, ease: E.camera, immediateRender: true }, t + i * 0.08);
    });
  }
  // fixed named series (topic lanes): A = «فهم الخبر», B = «العالم في 30 ثانية»
  function seriesName(style) { return S.series_ar || (style === "B" ? "العالم في 30 ثانية" : "فهم الخبر"); }

  function srcTag(parent, text, t, tEnd) {
    const c = el("div", "src", parent, `<b>المصدر: </b><span>${bidi(text)}</span>`);
    tl.fromTo(c, { x: -40, autoAlpha: 0 }, { x: 0, autoAlpha: 1, duration: 0.3, ease: E.enter, immediateRender: true }, t);
    if (tEnd) tl.to(c, { autoAlpha: 0, duration: 0.18, ease: E.exit }, tEnd - 0.18);
    return c;
  }

  // ---------------------------------------------------------------- plates: credits, archive labels, frame lines
  function plateDress(opt) {
    opt = opt || {};
    const ign = (n) => (n.setAttribute("data-layout-ignore", ""), n);
    P.plates.forEach((p) => {
      const m = M[p.media];
      const g = el("div", "layer", overlay);
      windowed(g, p.t, p.t + p.d);
      if (p.mode === "framed") {
        el("div", "box-line", g, null, { left: BOX[0] + "px", top: BOX[1] + "px", width: BOX[2] + "px", height: BOX[3] + "px" });
        ign(el("div", "credit inbox", g, bidi(m.credit), { left: BOX[0] + 12 + "px", top: BOX[1] + BOX[3] - 52 + "px" }));
        if (m.archive) el("div", "archive", g, esc(m.label), { right: 1080 - BOX[0] - BOX[2] + 12 + "px", top: BOX[1] + 12 + "px" });
      } else {
        ign(el("div", "credit", g, bidi(m.credit), { top: (opt.creditTop || 378) + "px" }));
        if (m.archive) el("div", "archive", g, esc(m.label), { right: "72px", top: (opt.archiveTop || 378) + "px" });
      }
    });
  }
  function scrims(parent, top) {
    el("div", "vignette", parent); el("div", "scrim-b", parent);
    if (top !== false) el("div", "scrim-t", parent);
  }

  // ---------------------------------------------------------------- brand
  function bug(parent) {
    const b = el("div", "bug", parent);
    el("img", "", b).src = "logo-kahwa.svg"; el("img", "", b).src = "logo-band.svg";
    return b;
  }
  function kicker(parent, text, dot) { return el("div", "kicker", parent, (dot ? '<span class="dot"></span>' : "") + esc(text)); }
  function progress(segs, parent) {
    const p = el("div", "progress", parent);
    segs.forEach((s) => {
      const seg = el("div", "seg", p); const i = el("i", "", seg);
      tl.fromTo(i, { scaleX: 0 }, { scaleX: 1, duration: s.d, ease: "none", immediateRender: false }, s.t);
    });
    return p;
  }
  function grain() {
    const g = el("div", "grain ov", root, null, { zIndex: 7 });
    tl.fromTo(g, { backgroundPosition: "0px 0px" }, { backgroundPosition: `${37 * 97}px ${53 * 97}px`, duration: P.duration, ease: `steps(${Math.round(P.duration * 15)})`, immediateRender: true }, 0);
  }
  function redWipe(t) {
    const bar = el("div", "abs", root, null, { top: "0", width: "180px", height: "1920px", background: "var(--red)", left: "1080px", zIndex: 8 });
    bar.setAttribute("data-layout-allow-overflow", "");
    tl.fromTo(bar, { x: 0 }, { x: -1440, duration: 0.3, ease: E.camera, immediateRender: false }, t - 0.14);
  }

  /* Number wheel (adapted from the HyperFrames catalog `number-wheel` component). */
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
    strips.reverse().forEach((st, i) => tl.fromTo(st, { y: "0em" }, { y: `-${st.dataset.stops}em`, duration: dur || 1.5, ease: E.count, immediateRender: true }, t + i * 0.06));
    return box;
  }
  function countText(node, to, t, dur, fmt) {
    const o = { v: 0 }; fmt = fmt || ((v) => Math.round(v).toLocaleString("en-US"));
    node.textContent = fmt(0);
    tl.fromTo(o, { v: 0 }, { v: to, duration: dur || 1.4, ease: E.count, immediateRender: false, onUpdate: () => (node.textContent = fmt(o.v)) }, t);
  }

  // ---------------------------------------------------------------- map (D3 orthographic, label-free, merged land)
  function makeMap(parent, opt) {
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg"); svg.setAttribute("class", "map"); svg.setAttribute("viewBox", "0 0 1080 1920");
    parent.appendChild(svg);
    const defs = document.createElementNS(NS, "defs");
    defs.innerHTML = `<radialGradient id="oc" cx="50%" cy="45%" r="60%"><stop offset="0%" stop-color="#121a2b"/><stop offset="100%" stop-color="#090B10"/></radialGradient>
      <radialGradient id="hl" cx="50%" cy="50%" r="50%"><stop offset="0%" stop-color="rgba(220,24,12,0.35)"/><stop offset="100%" stop-color="rgba(220,24,12,0)"/></radialGradient>`;
    svg.appendChild(defs);
    const mk = (tag, attrs) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); svg.appendChild(e); return e; };
    const sphere = mk("path", { fill: "url(#oc)", stroke: "#2A3142", "stroke-width": 2 });
    const grat = mk("path", { fill: "none", stroke: "rgba(42,49,66,0.55)", "stroke-width": 1 });
    const land = mk("path", { fill: "#1C2232", stroke: "#1C2232", "stroke-width": 0.8, "stroke-linejoin": "round" });
    const sec = mk("path", { fill: "rgba(220,24,12,0)", stroke: "rgba(255,77,64,0)", "stroke-width": 3 });
    const glow = mk("circle", { fill: "url(#hl)", r: 0 });
    const tgt = mk("path", { fill: "#DC180C", "fill-opacity": 0.0, stroke: "#FF4D40", "stroke-width": 3, "stroke-opacity": 0 });
    const ring = mk("circle", { fill: "none", stroke: "#FF4D40", "stroke-width": 4, r: 0, opacity: 0 });
    const dot = mk("circle", { fill: "#fff", stroke: "#DC180C", "stroke-width": 5, r: 0 });
    const feats = WORLD.features;
    const target = feats.find((f) => f.properties.a === opt.iso3);
    const secondary = opt.secIso3 ? feats.find((f) => f.properties.a === opt.secIso3) : null;
    const others = { type: "FeatureCollection", features: feats.filter((f) => f !== target) };
    const graticule = d3.geoGraticule10();
    const proj = d3.geoOrthographic().clipAngle(90).precision(0.4);
    const path = d3.geoPath(proj);
    const st = Object.assign({ lon: 60, lat: 15, scale: 470, cx: 540, cy: 860, fill: 0, ring: 0, dot: 0, sec: 0, tfo: 0.88 }, opt.state || {});
    const pt = opt.point;
    function render() {
      proj.rotate([-st.lon, -st.lat]).scale(st.scale).translate([st.cx, st.cy]);
      sphere.setAttribute("d", path({ type: "Sphere" }));
      grat.setAttribute("d", path(graticule) || "");
      land.setAttribute("d", path(others) || "");
      tgt.setAttribute("d", target ? (path(target) || "") : "");
      tgt.setAttribute("fill-opacity", st.tfo * st.fill); tgt.setAttribute("stroke-opacity", st.fill);
      if (secondary) { sec.setAttribute("d", path(secondary) || ""); sec.setAttribute("fill", `rgba(220,24,12,${0.75 * st.sec})`); sec.setAttribute("stroke", `rgba(255,77,64,${st.sec})`); }
      if (pt) {
        const p = proj([pt.lon, pt.lat]) || [-999, -999];
        dot.setAttribute("cx", p[0]); dot.setAttribute("cy", p[1]); dot.setAttribute("r", 11 * st.dot);
        ring.setAttribute("cx", p[0]); ring.setAttribute("cy", p[1]);
        const rr = st.ring % 1; ring.setAttribute("r", 14 + 110 * rr); ring.setAttribute("opacity", st.ring > 0 ? (1 - rr) * 0.9 : 0);
        glow.setAttribute("cx", p[0]); glow.setAttribute("cy", p[1]); glow.setAttribute("r", 260 * st.fill);
      }
      (opt.onRender || []).forEach((f) => f(proj, st));
    }
    render();
    function to(vals, t, d, ease) {
      const from = {}; for (const k in vals) from[k] = vals[k].from !== undefined ? vals[k].from : st[k];
      const end = {}; for (const k in vals) end[k] = vals[k].to !== undefined ? vals[k].to : vals[k];
      for (const k in end) st[k] = end[k];
      tl.fromTo(st, Object.assign({}, from), Object.assign({ duration: d, ease: ease || E.camera, immediateRender: false, onUpdate: render }, end), t);
    }
    const init = Object.assign({}, st);
    MAPS.push(() => { Object.assign(st, init); render(); });
    return { svg, st, render, to, init };
  }

  // ---------------------------------------------------------------- shared scenes
  function endCard(id, opt) {
    opt = opt || {};
    const s = SC[id], t = s.t;
    const d = scene(id, root); d.style.zIndex = 9;
    el("div", "full", d, null, { background: "var(--ink)" });
    el("div", "glow", d); el("div", "grain", d);
    const lg = el("div", "logo", d);
    const k = el("img", "", lg); k.src = "logo-kahwa.svg"; const b = el("img", "", lg); b.src = "logo-band.svg";
    tl.fromTo(k, { clipPath: "inset(0 0 0 100%)" }, { clipPath: "inset(0 0 0 0%)", duration: 0.45, ease: E.enter, immediateRender: true }, t + 0.1);
    tl.fromTo(b, { clipPath: "inset(0 0 0 100%)" }, { clipPath: "inset(0 0 0 0%)", duration: 0.45, ease: E.enter, immediateRender: true }, t + 0.22);
    const tag = el("div", "endline", d, esc(S.tagline), { top: "760px", fontSize: "44px", fontWeight: 700, color: "var(--muted)" });
    tl.fromTo(tag, { y: 24, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.4, ease: E.enter, immediateRender: true }, t + 0.5);
    const rule = el("div", "abs", d, null, { left: "340px", width: "400px", top: "850px", height: "3px", background: "var(--red)", transformOrigin: "100% 50%" });
    tl.fromTo(rule, { scaleX: 0 }, { scaleX: 1, duration: 0.45, ease: E.enter, immediateRender: true }, t + 0.55);
    const srcs = el("div", "endline", d, `<div style="font-size:30px;color:var(--muted);font-weight:700;margin-bottom:18px">المصادر</div>`, { top: "890px" });
    const chips = (opt.sources || S.sources).map((x) => el("span", "chip", srcs, bidi(x)));
    tl.fromTo(chips, { scale: 0.9, autoAlpha: 0 }, { scale: 1, autoAlpha: 1, duration: 0.25, ease: E.enter, stagger: 0.1, immediateRender: true }, t + 0.7);
    const used = opt.credits || [];
    if (used.length) {
      const cr = el("div", "endline", d, used.map((c) => bidi(c)).join("<br>"), { top: "1036px", fontSize: "23px", lineHeight: "1.5", color: "var(--muted)" });
      cr.setAttribute("data-layout-ignore", "");
      tl.fromTo(cr, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.4, ease: E.enter, immediateRender: true }, t + 0.9);
    }
    // (v2: no "AI-assisted" line at all - user hard rule; story.ai_line is rejected by the build gate)
    return d;
  }
  function creditsFor(ids) {
    const seen = new Set(), out = [];
    ids.forEach((id) => {
      const c = M[id].credit.replace(/^(صورة|صور|صورة ليلية|لقطات[^:]*|صورة أرشيفية[^:]*):\s*/, "");
      if (!seen.has(c)) { seen.add(c); out.push(c); }
    });
    return out;
  }
  const usedMedia = () => [...new Set(P.plates.map((p) => p.media))];
  function endCredits() {
    // compact, deduplicated credit lines: "<author> · <licence>" (sources: Wikimedia Commons / NASA)
    const ids = usedMedia(), seen = new Set(), lines = [];
    ids.forEach((i) => { const c = M[i].short || M[i].credit; if (!seen.has(c)) { seen.add(c); lines.push(c); } });
    if (lines.length > 4) return [lines.slice(0, 4).join(" · ") + " …"];
    return lines.length ? ["صور ولقطات: " + lines.join(" · ")] : [];
  }

  function capBlock(parent, text, t, opt) {
    opt = opt || {};
    const tb = el("div", "tblock", parent, null, opt.style || {});
    let tab = null;
    if (opt.tab) { tab = el("div", "tab", tb, opt.tab); el("br", "", tb); }
    const c = el("div", opt.cls || "h2", tb); const r = rich(c, text);
    if (tab) tl.fromTo(tab, { clipPath: "inset(0 0 0 100%)" }, { clipPath: "inset(0 0 0 0%)", duration: 0.3, ease: E.enter, immediateRender: true }, t);
    wordsIn(r.words, t + 0.08); kwWipe(r.kws, t + 0.08 + 0.067 * r.words.length + 0.1);
    return { tb, c, r, tab };
  }

  // ================================================================= STYLE A — "شرح AJ+"
  function styleA() {
    plateDress(); scrims(overlay);
    // hook (frame 0 poster)
    {
      const d = scene("hook");
      const tb = el("div", "tblock", d);
      const h = el("div", "h1", tb); const r = rich(h, S.headline);
      el("div", "deck", tb, esc(S.deck));
      kwWipe(r.kws, 0.17, true);
      kicker(d, seriesName("A"));
      blockOut(tb, SC.hook.d - 0.42);
    }
    // sat: real satellite loop (framed)
    {
      const s = SC.sat, d = scene("sat");
      const cb = capBlock(d, S.visual_caption || S.cimss_caption || S.deck, s.t + 0.42, { tab: esc(S.visual_tab || S.topic_ar || ""), cls: "h2 cap2" });
      blockOut(cb.tb, s.t + s.d - 0.22);
    }
    // map
    {
      const s = SC.map, t = s.t, d = scene("map");
      el("div", "full", d, null, { background: "var(--ink)" });
      const loc = S.location;
      const lab = el("div", "map-label pill", d, esc(loc.label_ar));
      const sea = el("div", "map-label sea-label", d, esc(loc.sea_ar || ""));
      if (!loc.sea_ar || !loc.sea_point) sea.style.display = "none";
      const tf = WORLD.features.find((f) => f.properties.a === loc.country_iso3);
      const cen = loc.center || (tf ? d3.geoCentroid(tf) : [loc.point.lon, loc.point.lat]);
      let zs = 1000;
      if (tf) { const b = d3.geoBounds(tf); let dl = b[1][0] - b[0][0]; if (dl < 0) dl += 360;
        const ext = Math.max(dl * Math.cos(cen[1] * Math.PI / 180), b[1][1] - b[0][1], 2.5);
        zs = Math.max(480, Math.min(4200, 560 / (ext * Math.PI / 180))); }
      const city = el("div", "map-label pill dark", d, esc(loc.point.label_ar));
      const m = makeMap(d, {
        iso3: loc.country_iso3, point: loc.point, state: { lon: 30, lat: 18, scale: 430, cx: 540, cy: 760 },
        onRender: [(proj) => {
          const a = proj(cen) || [-900, 0]; lab.style.left = a[0] + "px"; lab.style.top = a[1] - 30 + "px";
          const sp = proj(loc.sea_point || [0, 0]) || [-500, 0]; sea.style.left = sp[0] + "px"; sea.style.top = sp[1] + "px";
          const cp = proj([loc.point.lon, loc.point.lat]) || [-500, 0]; city.style.left = cp[0] - 150 + "px"; city.style.top = cp[1] + 8 + "px";
        }],
      });
      d.insertBefore(m.svg, lab); [lab, sea, city].forEach(center);
      m.to({ lon: cen[0], lat: cen[1] }, t + 0.1, 1.6, E.camera);
      m.to({ scale: zs, cy: 600 }, t + 1.3, 1.4, E.camera);
      m.to({ fill: 1 }, t + 1.5, 0.5, E.enter);
      m.to({ dot: 1 }, t + 2.4, 0.3, E.enter);
      m.to({ ring: 3 }, t + 2.4, 2.4, "none");
      tl.fromTo(lab, { autoAlpha: 0, x: 40 }, { autoAlpha: 1, x: 0, duration: 0.35, ease: E.enter, immediateRender: true }, t + 1.8);
      tl.fromTo(city, { autoAlpha: 0, x: 40 }, { autoAlpha: 1, x: 0, duration: 0.35, ease: E.enter, immediateRender: true }, t + 2.6);
      tl.fromTo(sea, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.5, ease: E.enter, immediateRender: true }, t + 2.2);
      el("div", "scrim-b", d, null, { opacity: 0.9 });
      // first the category line, then the lower third (name tab + role panel + source)
      const cb = capBlock(d, S.where_line || S.category_line, t + 0.3, { tab: "أين؟", cls: "h3" });
      blockOut(cb.tb, t + 2.45);
      const lt = el("div", "lt", d);
      const nm = el("div", "nm", lt, esc(S.lower_third.name_ar)); el("br", "", lt);
      const rl = el("div", "rl", lt, esc(S.lower_third.role_ar));
      const ls = el("div", "ls", lt, bidi("المصدر: " + S.lower_third.source));
      tl.fromTo(nm, { clipPath: "inset(0 0 0 100%)" }, { clipPath: "inset(0 0 0 0%)", duration: 0.3, ease: E.enter, immediateRender: true }, t + 2.75);
      tl.fromTo(rl, { clipPath: "inset(0 0 0 100%)" }, { clipPath: "inset(0 0 0 0%)", duration: 0.3, ease: E.enter, immediateRender: true }, t + 2.85);
      tl.fromTo(ls, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.3, immediateRender: true }, t + 3.0);
      tl.to(lt, { autoAlpha: 0, x: -30, duration: 0.2, ease: E.exit }, t + s.d - 0.25);
    }
    // facts: caption carries across the mid-scene hard cut (picture changes, words don't)
    S.facts.forEach((f, i) => {
      const id = "fact" + i, s = SC[id], t = s.t, d = scene(id);
      const cb = capBlock(d, f.text, t + 0.35, { tab: `<bdi>${i + 1}/${S.facts.length}</bdi>`, cls: "h2 cap2" });
      srcTag(d, f.source, t + 0.9);
      blockOut(cb.tb, t + s.d - 0.4);
    });
    // number
    {
      const s = SC.number, t = s.t, d = scene("number"), N = S.big_number;
      el("div", "full", d, null, { background: "var(--ink)" }); el("div", "gridbg", d, null, { opacity: 0.5 }); el("div", "glow", d, null, { top: "820px" });
      const wrap = el("div", "abs", d, null, { left: "72px", right: "72px", top: "560px", textAlign: "center" });
      const pre = el("div", "h3", wrap, esc(N.prefix_ar), { color: "var(--muted)", marginBottom: "10px" });
      const nb = el("div", "", wrap); const w = wheel(nb, N.display, t + 0.3, 1.6);
      const lab = el("div", "h2", wrap, null, { marginTop: "26px" }); const r = rich(lab, N.label_ar);
      const sub = el("div", "deck", wrap, esc(N.sub_ar));
      wordsIn([pre], t + 0.05);
      tl.to(w, { color: "#FF4D40", duration: 0.2 }, t + 2.4);
      wordsIn(r.words, t + 2.45); wordsIn([sub], t + 2.9);
      srcTag(d, N.source, t + 2.5);
    }
    // next (two plates: night -> sat27, LinearBlur between)
    {
      const s = SC.next, t = s.t, d = scene("next");
      const tb = el("div", "tblock", d);
      const tab = el("div", "tab", tb, esc(S.next.title_ar)); el("br", "", tb);
      tl.fromTo(tab, { clipPath: "inset(0 0 0 100%)" }, { clipPath: "inset(0 0 0 0%)", duration: 0.35, ease: E.enter, immediateRender: true }, t + 0.1);
      S.next.lines.forEach((ln, i) => {
        const row = el("div", "h2 cap2", tb, null, { marginTop: i ? "22px" : "0", paddingRight: "26px", borderRight: "8px solid var(--red)" });
        const r = rich(row, ln);
        const t0 = t + 0.35 + i * 3.25;
        tl.fromTo(row, { clipPath: "inset(0 0 0 100%)" }, { clipPath: "inset(0 0 0 0%)", duration: 0.45, ease: E.enter, immediateRender: true }, t0);
        kwWipe(r.kws, t0 + 0.45);
        srcTag(d, S.next.sources[i], t0 + 0.4, i === 0 ? t + 3.25 : null);
      });
    }
    endCard("end", { credits: endCredits() });
    P.wipes.forEach(redWipe);
  }

  // ================================================================= STYLE B — "عاجل"
  function styleB() {
    plateDress({ creditTop: 470, archiveTop: 470 });
    const d = scene("hook");
    el("div", "full", d, null, { background: "linear-gradient(180deg, rgba(9,11,16,0.30) 0%, rgba(9,11,16,0.50) 25%, rgba(9,11,16,0.50) 44%, rgba(9,11,16,0.22) 62%, rgba(9,11,16,0.75) 100%)" });
    d.appendChild(overlay);   // credits above the darkening
    overlay.style.zIndex = 1;
    const stripes = [0, 1, 2].map((i) => el("div", "stripe", d, null, { left: 1560 + i * 300 + "px", opacity: 0.9 - i * 0.25 }));
    stripes.forEach((x) => x.setAttribute("data-layout-allow-overflow", ""));
    tl.fromTo(stripes, { x: 0 }, { x: -3200, duration: 1.0, ease: E.camera, stagger: 0.06, immediateRender: false }, 0.05);
    const band = el("div", "bk-band", d);
    const big = el("div", "big", band, `<span class="dot"></span><span>عاجل</span>`);
    const meta = el("div", "meta", band, `<b class="series">${esc(seriesName("B"))}</b><span>${esc(S.date_ar)}</span>`);
    meta.style.left = "300px";
    const bx = el("div", "abs", band, null, { left: 0, top: 0, width: "268px", height: "150px", background: "var(--ink)" });
    const bb = el("div", "abs", bx, null, { left: "46px", top: "38px", width: "182px", height: "70px" });
    el("img", "", bb, null, { position: "absolute", left: 0, top: 0, width: "100%", height: "100%" }).src = "logo-kahwa.svg";
    el("img", "", bb, null, { position: "absolute", left: 0, top: 0, width: "100%", height: "100%" }).src = "logo-band.svg";
    const dot = big.querySelector(".dot");
    tl.to(dot, { opacity: 0.35, duration: 0.5, ease: "sine.inOut", yoyo: true, repeat: Math.floor(SC.end.t / 0.5) - 1 }, 0.5);
    const shine = el("div", "abs", band, null, { top: 0, left: "1080px", width: "160px", height: "150px", background: "linear-gradient(90deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.35) 50%, rgba(255,255,255,0) 100%)", transform: "skewX(-14deg)" });
    shine.setAttribute("data-layout-allow-overflow", "");
    tl.fromTo(shine, { x: 0 }, { x: -1400, duration: 0.7, ease: E.camera, immediateRender: false }, 0.35);
    const tb = el("div", "tblock", d); tb.style.bottom = "auto"; tb.style.top = "560px";
    const h = el("div", "h1", tb, null, { textShadow: "0 4px 30px rgba(0,0,0,0.6)" }); const r = rich(h, S.headline_breaking);
    const bdeck = el("div", "deck", tb, esc(S.deck_breaking || [(S.location.region_ar && !String(S.deck || "").includes(S.location.region_ar)) ? S.location.region_ar : "", S.deck].filter(Boolean).join(" · ")), { color: "#F2F4F7" });
    tl.to(bdeck, { autoAlpha: 0, filter: "blur(6px)", duration: 0.2, ease: E.exit }, SC.line0.t - 0.45);
    kwWipe(r.kws, 0.17, true);
    tl.to(tb, { scale: 0.72, transformOrigin: "100% 0%", duration: 0.5, ease: E.camera }, SC.line0.t - 0.3);
    S.breaking_lines.forEach((ln, i) => {
      const s = SC["line" + i], t = s.t;
      const c = el("div", "card", d, null, { top: "790px", paddingBottom: "88px" });
      const tx = el("div", "h2", c, null, { fontSize: "68px", lineHeight: "1.36" }); const rr = rich(tx, ln.text);
      tl.fromTo(c, { autoAlpha: 0, x: 80 }, { autoAlpha: 1, x: 0, duration: 0.35, ease: E.enter, immediateRender: true }, t + 0.12);
      wordsIn(rr.words, t + 0.2); kwWipe(rr.kws, t + 0.25 + 0.067 * rr.words.length);
      tl.to(c, { autoAlpha: 0, x: -80, duration: 0.22, ease: E.exit }, t + s.d - 0.3);
      const sc = srcTag(c, ln.source, t + 0.5);
      Object.assign(sc.style, { left: "40px", top: "auto", bottom: "22px", height: "46px", lineHeight: "46px", fontSize: "28px", background: "rgba(9,11,16,0.6)" });
    });
    const tk = el("div", "ticker", d);
    tk.setAttribute("data-layout-allow-overlap", ""); tk.setAttribute("data-layout-allow-overflow", "");
    const lab = el("div", "lab", tk, "آخر الأخبار"); lab.style.fontSize = "30px";
    const cr = el("div", "crawl", tk);
    cr.innerHTML = S.ticker.concat(S.ticker).map((x) => `<span>${esc(x)}</span><span class="sq"></span>`).join("");
    const T0 = 2.6, T1 = SC.end.t;
    tl.fromTo(tk, { y: 90, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.35, ease: E.enter, immediateRender: true }, T0);
    tl.fromTo(cr, { x: 0 }, { x: 150 * (T1 - T0), duration: T1 - T0, ease: "none", immediateRender: true }, T0);
    endCard("end", { credits: endCredits() });
  }

  // ================================================================= STYLE C — "خريطة وأرقام"
  function styleC() {
    const loc = S.location;
    const base = el("div", "full", scenesL); base.style.background = "var(--ink)";
    el("div", "gridbg", base, null, { opacity: 0.35 });
    const labs = {};
    let glyph = null;
    const L = (k, cls, txt) => (labs[k] = el("div", "map-label " + cls, base, esc(txt)));
    const m = makeMap(base, {
      iso3: loc.country_iso3, point: loc.point, secIso3: loc.secondary.iso3,
      state: { lon: loc.center[0] - 6, lat: 2, scale: 430, cx: 540, cy: 1150, fill: 1 },
      onRender: [(proj, st) => {
        const pos = (k, ll, dx, dy) => { const p = proj(ll); if (!labs[k]) return; if (p) { labs[k].style.left = p[0] + (dx || 0) + "px"; labs[k].style.top = p[1] + (dy || 0) + "px"; } else labs[k].style.left = "-900px"; };
        pos("ind", loc.center, 0, -20); pos("sea", [89.6, 16.8]); pos("city", [loc.point.lon, loc.point.lat], -150, 64); pos("npl", loc.secondary.center, 0, -86);
        if (glyph) { const g = proj([st.gx, st.gy]); if (g) { glyph.style.left = g[0] + "px"; glyph.style.top = g[1] + "px"; } }
      }],
    });
    L("ind", "pill", loc.label_ar); L("sea", "sea-label", loc.sea_ar); L("city", "pill dark", loc.point.label_ar); L("npl", "pill", loc.secondary.label_ar);
    glyph = el("div", "map-label", base, `<svg width="120" height="120" viewBox="-60 -60 120 120"><g fill="none" stroke="#F2F4F7" stroke-width="7" stroke-linecap="round"><path d="M0,-38 A38,38 0 0 1 38,0"/><path d="M0,38 A38,38 0 0 1 -38,0"/><path d="M-22,-48 C-60,-20 -40,10 -14,0"/><path d="M22,48 C60,20 40,-10 14,0"/></g><circle r="9" fill="#FF4D40"/></svg>`);
    center(glyph); Object.values(labs).forEach(center);
    m.st.gx = 88.5; m.st.gy = 13.0; m.init.gx = 88.5; m.init.gy = 13.0; m.render();
    base.insertBefore(m.svg, base.children[1]);
    Object.values(labs).forEach((x) => gsap.set(x, { autoAlpha: 0 })); gsap.set(glyph, { autoAlpha: 0 });
    tl.to(glyph, { rotation: -720, duration: 6, ease: "none" }, 3.2);
    // hook
    const hk = scene("hook");
    const tt = el("div", "abs", hk, null, { right: "72px", left: "72px", top: "396px", textAlign: "right" });
    const h = el("div", "h1b", tt); const r = rich(h, S.headline_numbers); kwWipe(r.kws, 0.17, true);
    el("div", "deck", tt, esc(S.date_ar));
    kicker(hk, "خريطة وأرقام");
    blockOut(tt, SC.hook.d - 0.3);
    m.to({ lon: loc.center[0] - 2, lat: 4 }, 0, SC.hook.d, E.ambient);
    const mscrim = el("div", "scrim-b", base, null, { opacity: 0 });
    tl.to(mscrim, { opacity: 1, duration: 0.6 }, SC.zoom.t + 1.0);
    gsap.set(labs.ind, { autoAlpha: 1 });
    // zoom to landfall
    const z = SC.zoom, zt = z.t, zd = scene("zoom");
    m.to({ lon: 84.0, lat: 16.2, scale: 2300, cy: 640, tfo: 0.34 }, zt, 1.8, E.camera);
    tl.to(labs.ind, { autoAlpha: 0, duration: 0.3 }, zt + 0.2);
    tl.fromTo(labs.sea, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.4, immediateRender: false }, zt + 1.6);
    tl.fromTo(glyph, { autoAlpha: 0, scale: 0.6 }, { autoAlpha: 1, scale: 1, duration: 0.4, ease: E.enter, immediateRender: false }, zt + 1.7);
    m.to({ gx: { from: 88.5, to: loc.point.lon }, gy: { from: 13.0, to: loc.point.lat } }, zt + 2.0, 2.2, E.camera);
    m.to({ dot: 1 }, zt + 4.1, 0.3, E.enter); m.to({ ring: { from: 0, to: 2 } }, zt + 4.1, 1.8, "none");
    tl.fromTo(labs.city, { autoAlpha: 0, x: 40 }, { autoAlpha: 1, x: 0, duration: 0.35, ease: E.enter, immediateRender: false }, zt + 4.2);
    const zcb = capBlock(zd, "عبر إلى اليابسة قرب [[نرسابور]] في أندرا براديش", zt + 0.4, { cls: "h2" });
    srcTag(zd, "الأرصاد الهندية عبر ذا هندو", zt + 1.2);
    blockOut(zcb.tb, z.t + z.d - 0.25);
    // satellite imagery (two dated plates, GL DirectionalWipe between)
    const sa = SC.sat, sd = scene("sat");
    tl.to(base, { autoAlpha: 0, duration: 0.001 }, sa.t);
    tl.to(base, { autoAlpha: 1, duration: 0.001 }, sa.t + sa.d);
    plateDress(); scrims(sd);
    S.sat_captions.forEach((c, i) => {
      const t0 = sa.t + (i ? 2.0 + 0.35 : 0.3);
      const cb = capBlock(sd, c.text, t0, { tab: esc(c.tab), cls: "h2" });
      blockOut(cb.tb, sa.t + (i ? sa.d - 0.25 : 2.0 - 0.3));
    });
    // counter over dimmed map
    const c = SC.count, ct = c.t, cd = scene("count");
    tl.to(m.svg, { opacity: 0.28, duration: 0.4, ease: E.enter }, ct);
    tl.to([glyph, labs.city, labs.sea], { opacity: 0, duration: 0.3 }, ct);
    const N = S.big_number;
    const cw = el("div", "abs", cd, null, { left: "72px", right: "72px", top: "560px", textAlign: "center" });
    const pre = el("div", "h3", cw, esc(N.prefix_ar), { color: "var(--muted)" });
    const nb = el("div", "", cw); const w = wheel(nb, N.display, ct + 0.3, 1.6);
    const cl = el("div", "h2", cw, esc(N.label_ar), { marginTop: "20px" }); const sub = el("div", "deck", cw, esc(N.sub_ar));
    wordsIn([pre], ct + 0.05); wordsIn([cl, sub], ct + 2.0, { stagger: 0.12 });
    tl.to(w, { color: "#FF4D40", duration: 0.2 }, ct + 2.4);
    srcTag(cd, N.source, ct + 2.1);
    // tiles
    const ti = SC.tiles, tt0 = ti.t, td = scene("tiles");
    tl.to(m.svg, { opacity: 0.12, duration: 0.3 }, tt0);
    const hdr = el("div", "abs", td, null, { right: "72px", top: "396px" }); el("div", "tab", hdr, "أرقام أخرى");
    tl.fromTo(hdr, { clipPath: "inset(0 0 0 100%)" }, { clipPath: "inset(0 0 0 0%)", duration: 0.4, ease: E.enter, immediateRender: true }, tt0 + 0.05);
    S.tiles.forEach((x, i) => {
      const tile = el("div", "tile", td, null, { top: 500 + i * 272 + "px" });
      const row = el("div", "trow", tile);
      const v = el("span", "tv", row, "0");
      el("span", "tu", row, esc(x.unit_ar));
      el("div", "tl", tile, esc(x.label_ar)); el("div", "ts", tile, bidi("المصدر: " + x.source));
      tl.fromTo(tile, { x: 90, autoAlpha: 0 }, { x: 0, autoAlpha: 1, duration: 0.35, ease: E.enter, immediateRender: true }, tt0 + 0.2 + i * 0.18);
      countText(v, x.value, tt0 + 0.35 + i * 0.18, 1.3, (vv) => Math.round(vv).toLocaleString("en-US") + (x.suffix || ""));
    });
    // bars
    const b = SC.bars, bt = b.t, bd = scene("bars");
    const bh = el("div", "abs", bd, null, { right: "72px", left: "72px", top: "420px", textAlign: "right" });
    const bht = el("div", "h2", bh, esc(S.bars.title_ar));
    wordsIn([bht], bt + 0.05);
    S.bars.items.forEach((it, i) => {
      const row = el("div", "bar-row" + (it.key ? " key" : ""), bd, null, { top: 600 + i * 200 + "px" });
      const blab = el("div", "bl", row, esc(it.label_ar));
      const tr = el("div", "track", row); const fl = el("div", "fill", tr);
      const frac = it.value / S.bars.max; fl.style.width = frac * 100 + "%";
      const bv = el("div", "bv", row, "0"); bv.style.left = (1 - frac) * 936 + 8 + "px";
      tl.fromTo(fl, { scaleX: 0 }, { scaleX: 1, duration: 0.7, ease: E.enter, immediateRender: true }, bt + 0.4 + i * 0.25);
      tl.fromTo(blab, { autoAlpha: 0, x: 40 }, { autoAlpha: 1, x: 0, duration: 0.3, ease: E.enter, immediateRender: true }, bt + 0.3 + i * 0.25);
      countText(bv, it.value, bt + 0.4 + i * 0.25, 0.9, (vv) => String(Math.round(vv)));
    });
    srcTag(bd, S.bars.source, bt + 1.5);
    // Nepal
    const n = SC.nepal, nt = n.t, nd = scene("nepal");
    tl.to([m.svg], { opacity: 1, duration: 0.4 }, nt);
    m.to({ dot: 0, ring: 0 }, nt, 0.2, "none");
    m.to({ lon: 84.3, lat: 25.6, scale: 2600, cy: 640 }, nt, 1.4, E.camera);
    m.to({ sec: 1 }, nt + 1.1, 0.5, E.enter);
    tl.fromTo(labs.npl, { autoAlpha: 0, y: 20 }, { autoAlpha: 1, y: 0, duration: 0.35, ease: E.enter, immediateRender: false }, nt + 1.4);
    const ntb = el("div", "tblock", nd); const nc = el("div", "h2", ntb); const nr = rich(nc, S.secondary_fact.text);
    const nsub = el("div", "deck", ntb, esc(S.secondary_fact.sub_ar));
    wordsIn(nr.words, nt + 0.5); kwWipe(nr.kws, nt + 0.6 + 0.067 * nr.words.length); wordsIn([nsub], nt + 1.4);
    srcTag(nd, S.secondary_fact.source, nt + 1.2);
    tl.to(base, { autoAlpha: 0, duration: 0.001 }, SC.end.t);
    P.wipes.forEach(redWipe);
    endCard("end", { credits: ["خريطة: Natural Earth (ملكية عامة)"].concat(endCredits()) });
  }

  // ================================================================= STYLE D — "صورة حية"
  function styleD() {
    plateDress(); scrims(overlay);
    S.captions.forEach((cp, i) => {
      const id = "cap" + i, s = SC[id], t = s.t, d = scene(id);
      let cstart = t + 0.42;
      if (i === 0) {
        const hs = el("div", "abs", d, null, { left: 0, top: 0, width: "1080px", height: "1150px", background: "linear-gradient(180deg, rgba(9,11,16,0.55) 0%, rgba(9,11,16,0.78) 32%, rgba(9,11,16,0.62) 58%, rgba(9,11,16,0) 100%)" });
        tl.to(hs, { autoAlpha: 0, duration: 0.5, ease: E.exit }, s.hookOut);
        const tb = el("div", "tblock", d, null, { bottom: "auto", top: "440px", textShadow: "0 4px 28px rgba(0,0,0,0.65)" });
        el("div", "abs", tb, null, { right: "0", top: "-34px", width: "120px", height: "8px", background: "var(--red)" });
        const h = el("div", "h1", tb); const r = rich(h, S.headline);
        kwWipe(r.kws, 0.17);
        el("div", "deck", tb, esc(S.deck), { color: "#F2F4F7" });
        blockOut(tb, s.hookOut);
        cstart = s.hookOut + 0.3;
      }
      const cap = el("div", "cap", d);
      const words = cp.text.split(/\s+/).filter(Boolean).map((w) => {
        const sp = el("span", "w", cap, `<span class="bg"></span>${esc(w)}`); cap.appendChild(document.createTextNode(" "));
        return sp;
      });
      const keys = new Set(cp.key || []);
      let tt = cstart;
      words.forEach((w) => {
        const txt = w.textContent; const dur = Math.min(0.5, Math.max(0.24, 0.14 + 0.032 * txt.length));
        const bg = w.querySelector(".bg");
        tl.fromTo(w, { autoAlpha: 0, filter: "blur(6px)" }, { autoAlpha: 1, filter: "blur(0px)", duration: 0.13, ease: "power2.out", immediateRender: true }, tt);
        tl.set(w, { filter: "none" }, tt + 0.15);
        tl.fromTo(bg, { scaleX: 0 }, { scaleX: 1, duration: 0.12, ease: E.camera, immediateRender: true }, tt);
        if (!keys.has(txt)) tl.to(bg, { scaleX: 0, transformOrigin: "0% 50%", duration: 0.12, ease: E.exit }, tt + dur);
        tt += dur;
      });
      if (S.caption_sources && S.caption_sources[i]) srcTag(d, S.caption_sources[i], Math.min(tt, t + s.d - 1.2), t + s.d - 0.15);
      blockOut(cap, t + s.d - 0.32);
    });
    kicker(document.querySelector('[data-scene="cap0"]'), "صورة حية");
    endCard("end", { credits: endCredits() });
  }

  // ---------------------------------------------------------------- graphic background (no-footage fallback)
  function graphicGround() {
    const g = document.getElementById("ground");
    el("div", "gridbg", g, null, { opacity: 0.45 }); el("div", "glow", g, null, { top: "760px", opacity: 0.8 });
    const loc = S.location || {};
    if (!loc.country_iso3 || !window.WORLD) return;
    const tf = WORLD.features.find((f) => f.properties.a === loc.country_iso3);
    const cen = loc.center || (tf ? d3.geoCentroid(tf) : (loc.point ? [loc.point.lon, loc.point.lat] : [20, 20]));
    const gl = el("div", "full", g, null, { opacity: 0.32 });
    const m = makeMap(gl, { iso3: loc.country_iso3, point: loc.point, state: { lon: cen[0] + 25, lat: cen[1] * 0.6, scale: 620, cx: 540, cy: 980, fill: 0.6, dot: 0.8 } });
    m.to({ lon: cen[0] - 25 }, 0, P.duration, "none");
  }
  if (P.plates.length < (STYLE === "A" ? 10 : 5)) graphicGround();

  // ---------------------------------------------------------------- build
  ({ A: styleA, B: styleB, C: styleC, D: styleD })[STYLE]();
  if (STYLE !== "B") bug(chrome);
  progress(P.segments, chrome);
  tl.to(chrome, { autoAlpha: 0, duration: 0.001 }, SC.end.t);
  grain();
  MAPS.forEach((f) => f());
  // GL transitions: drive the compositor from the timeline too (preview + any runtime without hf-seek)
  const glDriver = { t: 0 };
  tl.fromTo(glDriver, { t: 0 }, { t: P.duration, duration: P.duration, ease: "none", immediateRender: false,
    onUpdate: () => window.KAHWA_GL && window.KAHWA_GL.draw(tl.time()) }, 0);

  window.KAHWA_TL = tl;
  tl.seek(0);
})();
