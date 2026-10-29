/* Kahwa News opener — one paused GSAP timeline, seeked frame-by-frame by HyperFrames.
   Arabic is animated per WORD only (HyperFrames #5885). Western digits in fixed-width cells.
   Params arrive in window.OPENER (see opener.py): concept, T, S (sting start), notes[], beat, title, date_ar,
   time_hhmm, time_hms, tz_label, tagline[], handoff, pings[]. */
(function () {
  const O = window.OPENER;
  const root = document.getElementById("stage");
  const tl = gsap.timeline({ paused: true });
  window.OPENER_TL = tl;
  const ENTER = CustomEase.create("enter", "0.16,1,0.3,1");
  const EXIT = CustomEase.create("exit", "0.7,0,0.84,0");
  const CAM = CustomEase.create("cam", "0.65,0,0.35,1");
  const N = O.notes.map((x) => O.S + x);          // the 5 sting note times (absolute)
  const LAND = N[4], B = O.beat;

  function el(tag, cls, parent, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html !== undefined) e.innerHTML = html;
    (parent || root).appendChild(e);
    return e;
  }
  function words(parent, list, cls) {
    return list.map((w, i) => el("span", "w" + (cls && cls[i] ? " " + cls[i] : ""), parent, w));
  }
  function digits(parent, s) {          // "15:47" -> fixed cells
    parent.innerHTML = "";
    for (const ch of s) el("span", ch === ":" ? "c" : "d", parent, ch);
  }

  // ---------------- shared lockup (built for both concepts) ----------------
  function lockup(layer) {
    const L = {};
    L.logo = el("div", "logo", layer);
    L.bandWrap = el("div", "lyr band", L.logo);
    L.bandGlow = el("img", "bandglow", L.logo); L.bandGlow.src = "logo-band.svg";
    L.band = el("img", "", L.bandWrap); L.band.src = "logo-band.svg";
    L.wmWrap = el("div", "lyr wm", L.logo);
    L.wm = el("img", "", L.wmWrap); L.wm.src = "logo-kahwa.svg";
    L.sweep = el("div", "sweep", L.logo);
    L.title = el("div", "title", layer);
    L.tw = words(L.title, O.title);
    L.row = el("div", "daterow", layer);
    L.time = el("div", "chip time", L.row);
    L.tdig = el("span", "digits", L.time); digits(L.tdig, O.time_hhmm);
    L.tz = el("span", "tz", L.time, O.tz_label);
    L.date = el("div", "chip date", L.row, O.date_ar);
    L.hand = el("div", "handoff", layer);
    L.tab = el("div", "tab", L.hand, O.handoff);
    L.rule = el("div", "rule", L.hand);
    return L;
  }
  function lockupIn(L, opt) {
    // initial states
    gsap.set(L.bandWrap, { clipPath: "inset(0 0 0 100%)" });
    gsap.set(L.bandGlow, { opacity: 0 });
    gsap.set(L.wm, { yPercent: 62 });
    gsap.set(L.tw, { opacity: 0, y: 70, filter: "blur(14px)" });
    gsap.set([L.time, L.date], { clipPath: "inset(0 0 0 100%)" });
    gsap.set(L.tab, { clipPath: "inset(0 0 0 100%)" });
    gsap.set(L.rule, { scaleX: 0 });
    if (!opt.noBand) tl.to(L.bandWrap, { clipPath: "inset(0 0 0 0%)", duration: 0.30, ease: ENTER }, N[0]);
    tl.fromTo(L.bandGlow, { opacity: 0.0 }, { opacity: 0.9, duration: 0.12, ease: "none", immediateRender: false }, N[0]);
    tl.to(L.bandGlow, { opacity: 0.0, duration: 0.6, ease: "power2.out" }, N[0] + 0.12);
    tl.to(L.wm, { yPercent: 0, duration: 0.42, ease: ENTER }, N[1]);
    tl.to(L.tw[0], { opacity: 1, y: 0, filter: "blur(0px)", duration: 0.42, ease: ENTER }, N[2]);
    if (L.tw[1]) tl.to(L.tw[1], { opacity: 1, y: 0, filter: "blur(0px)", duration: 0.42, ease: ENTER }, N[3]);
    for (let i = 2; i < L.tw.length; i++) tl.to(L.tw[i], { opacity: 1, y: 0, filter: "blur(0px)", duration: 0.4, ease: ENTER }, N[3] + 0.1 * (i - 1));
    // landing: light sweep + settle + glow pulse
    tl.fromTo(L.logo, { scale: 1.035 }, { scale: 1, duration: 0.7, ease: ENTER, immediateRender: false }, LAND);
    tl.set(L.sweep, { opacity: 1 }, LAND);
    tl.fromTo(L.sweep, { backgroundPosition: "100% 0" }, { backgroundPosition: "0% 0", duration: 0.75, ease: "power2.inOut", immediateRender: false }, LAND);
    tl.set(L.sweep, { opacity: 0 }, LAND + 0.76);
    tl.fromTo(L.bandGlow, { opacity: 0.85 }, { opacity: 0, duration: 0.9, ease: "power2.out", immediateRender: false }, LAND);
    // info row on the half-beats after the landing
    if (!opt.noTime) tl.to(L.time, { clipPath: "inset(0 0 0 0%)", duration: 0.34, ease: ENTER }, LAND + B * 0.5);
    tl.to(L.date, { clipPath: "inset(0 0 0 0%)", duration: 0.38, ease: ENTER }, LAND + B * 1.0);
    tl.to(L.tab, { clipPath: "inset(0 0 0 0%)", duration: 0.34, ease: ENTER }, LAND + B * 1.5);
    tl.to(L.rule, { scaleX: 1, duration: 0.6, ease: ENTER }, LAND + B * 1.5);
  }
  function sizeRule(L) {}   // rule width is pure CSS (flex) so font loading can never break it
  // live clock text helper: driven by timeline time so seeking is deterministic
  function hms(base, t) {
    const [h, m, s] = base.split(":").map(Number);
    let x = h * 3600 + m * 60 + s + Math.floor(t);
    x = ((x % 86400) + 86400) % 86400;
    const p = (n) => String(n).padStart(2, "0");
    return p(Math.floor(x / 3600)) + ":" + p(Math.floor(x / 60) % 60) + ":" + p(x % 60);
  }

  // =====================================================================
  if (O.concept === "A") {
    // HUD + brackets
    const br = ["tr", "tl", "br", "bl"].map((k) => el("div", "bracket " + k));
    const hudL = el("div", "hud l"); const hudLd = el("span", "digits", hudL);
    const hudR = el("div", "hud r", undefined, '<span class="dot"></span>' + O.hud_right);
    gsap.set(br, { opacity: 0, scale: 1.25 });
    tl.to(br, { opacity: 1, scale: 1, duration: 0.5, ease: ENTER, stagger: 0.06 }, 0.0);
    gsap.set([hudL, hudR], { opacity: 0, y: -16 });
    tl.to([hudR, hudL], { opacity: 1, y: 0, duration: 0.45, ease: ENTER, stagger: 0.12 }, B * 1);
    tl.to({}, { duration: O.T, onUpdate: function () { digits(hudLd, hms(O.time_hms, tl.time()) + " " ); } }, 0);
    // tagline per word on beats 4,5,6 (bar 2)
    const tag = el("div", "tagline");
    const tws = words(tag, O.tagline, O.tagline.map((w, i) => (i === O.tagline.length - 1 ? "accent" : "")));
    gsap.set(tws, { opacity: 0, y: 60, filter: "blur(16px)" });
    tws.forEach((w, i) => tl.to(w, { opacity: 1, y: 0, filter: "blur(0px)", duration: 0.45, ease: ENTER }, B * (4 + i)));
    tl.to(tws, { opacity: 0, y: -40, filter: "blur(18px)", duration: 0.5, ease: EXIT, stagger: 0.05 }, B * 9.0);
    // pre-line draws right->left into the band position, then hands over to the logo band
    const pre = el("div", "preline");
    gsap.set(pre, { scaleX: 0 });
    tl.to(pre, { scaleX: 1, duration: B * 1.6, ease: CAM }, O.S - B * 2);
    tl.to(pre, { opacity: 0, duration: 0.12, ease: "none" }, N[0] + 0.05);
    // flash on the sting hit
    const fl = el("div", "flash");
    gsap.set(fl, { opacity: 0 });
    tl.to(fl, { opacity: 0.55, duration: 0.06, ease: "none" }, N[0]);
    tl.to(fl, { opacity: 0, duration: 0.5, ease: "power2.out" }, N[0] + 0.06);
    // HUD and brackets leave as the logo takes the frame (hold frame stays clean)
    tl.to([hudL, hudR], { opacity: 0, duration: 0.35, ease: EXIT }, N[0]);
    tl.to(br, { opacity: 0, duration: 0.35, ease: EXIT }, N[0]);
    const L = lockup(root);
    lockupIn(L, {});
    sizeRule(L);
    el("div", "vig"); el("div", "grain");
  }

  // =====================================================================
  if (O.concept === "B") {
    el("div", "bgB");
    const beam = el("div", "beam");
    // grid: 7 verticals (column lines) + 3 horizontals, drawn on the ticks of beat 1
    const xs = [72, 228, 384, 540, 696, 852, 1008];
    const gv = xs.map((x) => { const e = el("div", "gridv"); e.style.left = (x - 0.75) + "px"; return e; });
    const gh = [300, 940, 1240].map((y) => { const e = el("div", "gridh"); e.style.top = y + "px"; return e; });
    gsap.set(gv, { scaleY: 0 }); gsap.set(gh, { scaleX: 0 });
    gv.slice().reverse().forEach((e, i) => tl.to(e, { scaleY: 1, duration: 0.55, ease: ENTER }, i * (B / 4)));
    gh.forEach((e, i) => tl.to(e, { scaleX: 1, duration: 0.7, ease: ENTER }, B * 1 + i * (B / 4)));
    // dot world on a tilted plane
    const persp = el("div", "persp");
    const plane = el("div", "plane", persp);
    const rev = el("div", "reveal", plane);
    const im = el("img", "", rev); im.src = "dots.png";
    const scw = el("div", "scanwrap", plane); el("div", "scan", scw);
    const pings = O.pings.map((p) => {
      const g = el("div", "ping", plane); g.style.left = (p.x * 2400) + "px"; g.style.top = (p.y * 880) + "px";
      const r = el("div", "ring", g); const c = el("div", "core", g);
      gsap.set([r, c], { scale: 0, opacity: 0 });
      return { g, r, c };
    });
    gsap.set(plane, { rotateX: 56, scale: 0.92, y: 30 });
    tl.to(plane, { rotateX: 64, scale: 1.0, y: 0, duration: O.S, ease: CAM }, 0);
    // RTL reveal (east -> west) by clip-path; a red scan window rides the reveal edge, twice
    gsap.set(rev, { clipPath: "inset(0 0 0 58%)" });
    tl.to(rev, { clipPath: "inset(0 0 0 0%)", duration: B * 4, ease: "power1.inOut" }, 0);
    gsap.set(scw, { clipPath: "inset(0 0% 0 100%)" });
    tl.fromTo(scw, { clipPath: "inset(0 0% 0 96%)" }, { clipPath: "inset(0 96% 0 0%)", duration: B * 4, ease: "power1.inOut", immediateRender: false }, 0);
    tl.fromTo(scw, { clipPath: "inset(0 0% 0 96%)" }, { clipPath: "inset(0 96% 0 0%)", duration: B * 4, ease: "power1.inOut", immediateRender: false }, B * 5);
    tl.set(scw, { clipPath: "inset(0 100% 0 0%)" }, B * 9);
    tl.to(beam, { opacity: 1, duration: B * 4, ease: "power1.out" }, 0);
    // pings on beats 4..(S-1) — one per beat, RTL order (east first)
    const order = pings.slice().sort((a, b) => parseFloat(b.g.style.left) - parseFloat(a.g.style.left));
    const nb = Math.max(0, Math.round(O.S / B) - 4);
    for (let i = 0; i < Math.min(nb * 2, order.length); i++) {
      const p = order[i], t0 = B * (4 + Math.floor(i / 2)) + (i % 2) * B * 0.5;
      tl.to(p.c, { scale: 1, opacity: 1, duration: 0.18, ease: "back.out(3)" }, t0);
      tl.fromTo(p.r, { scale: 0.2, opacity: 1 }, { scale: 2.6, opacity: 0, duration: 0.9, ease: "power2.out", immediateRender: false }, t0);
      tl.to(p.c, { opacity: 0.55, scale: 0.7, duration: 0.5, ease: "power2.out" }, t0 + 0.4);
    }
    // the clock: counts 00:00 -> time (white), locks on bar 2 downbeat with a red pulse, locks on bar 2 downbeat with a red pulse
    const lab = el("div", "clocklab", undefined, '<span class="dot"></span>' + O.clock_label);
    const clk = el("div", "clock"); const cd = el("span", "digits", clk);
    const [hh, mm] = O.time_hhmm.split(":").map(Number);
    const target = hh * 60 + mm;
    const ctr = { v: 0 };
    tl.to(ctr, { v: target, duration: B * 4, ease: "expo.out",
      onUpdate: () => { const v = Math.round(ctr.v); digits(cd, String(Math.floor(v / 60)).padStart(2, "0") + ":" + String(v % 60).padStart(2, "0")); } }, 0);
    digits(cd, "00:00");
    gsap.set(lab, { opacity: 0, y: -14 });
    tl.to(lab, { opacity: 1, y: 0, duration: 0.4, ease: ENTER }, B * 0.5);
    tl.to(clk, { color: "#FF3B2E", scale: 1.05, duration: 0.05, ease: "none" }, B * 4);
    tl.to(clk, { color: "#F2F4F7", scale: 1, duration: 0.7, ease: "power2.out" }, B * 4 + 0.05);
    // the sting: clock flies into the time chip; red bar slams across then becomes the logo band
    const L = lockup(root);
    const red = el("div", "redbar");
    gsap.set(red, { scaleX: 0 });
    tl.to(lab, { opacity: 0, duration: 0.25, ease: EXIT }, N[0] - 0.1);
    tl.to(red, { scaleX: 1, duration: 0.22, ease: ENTER }, N[0]);
    // shrink to the band's exact box (x 234..844), then hand over to the real logo band
    tl.to(red, { left: 234, width: 610, scaleX: 1, duration: 0.30, ease: CAM }, N[0] + 0.18);
    tl.set(L.bandWrap, { clipPath: "inset(0 0 0 0%)" }, N[0] + 0.48);
    tl.to(red, { opacity: 0, duration: 0.08, ease: "none" }, N[0] + 0.48);
    // clock -> time chip (FLIP-style: shrink and move to the chip's place; chip wipes in under it)
    tl.to(clk, { y: 838 + 35 - (470 + 150), scale: 42 / 250, x: () => chipCenterX() - 540, duration: 0.5, ease: CAM }, N[0]);
    tl.to(clk, { opacity: 0, duration: 0.1, ease: "none" }, N[0] + 0.46);
    tl.to(gv.concat(gh), { opacity: 0.35, duration: 0.8, ease: "power2.out" }, N[0]);
    tl.to(plane, { rotateX: 70, scale: 1.0, y: 190, opacity: 0.8, duration: 1.4, ease: CAM }, N[2]);
    tl.to(beam, { opacity: 0.6, duration: 1.0, ease: "power2.out" }, LAND);
    lockupIn(L, { noBand: true, noTime: true });
    tl.set(L.time, { clipPath: "inset(0 0 0 0%)" }, N[0] + 0.46);
    sizeRule(L);
    function chipCenterX() {
      const r = L.tdig.getBoundingClientRect();
      return r.left + r.width / 2;
    }
    el("div", "vig"); el("div", "grain");
  }
  // make sure the timeline spans the full duration (static hold at the end)
  tl.set({}, {}, O.T);
})();
