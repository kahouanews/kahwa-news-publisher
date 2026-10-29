/* Kahwa v2 GL compositor: GL Transitions 2.0 shaders (MIT, gl-transitions/gl-transitions) between plate clips.
 * The canvas sits above the plate <video>s and under every overlay (scrims, text, chrome), so text keeps animating
 * live while the picture transitions. Textures are the exact plate frames at the window edges (saved by build.py).
 * Deterministic: the frame drawn is a pure function of timeline time (hf-seek + a GSAP onUpdate for preview).
 */
(function () {
  const R = window.REEL, TR = (R.plan.transitions || []);
  const canvas = document.getElementById("glx");
  window.__hf = window.__hf || {};
  window.__hf.buildReady = window.__hf.buildReady || {};
  const api = { draw: function () {}, ready: Promise.resolve() };
  window.KAHWA_GL = api;
  if (!TR.length || !canvas) return;
  const gl = canvas.getContext("webgl", { preserveDrawingBuffer: true, premultipliedAlpha: false, antialias: false });
  if (!gl) { console.warn("[glx] WebGL unavailable: transitions become hard cuts"); return; }

  const VS = "attribute vec2 _p; varying vec2 _uv; void main(){ gl_Position = vec4(_p,0.0,1.0); _uv = _p*0.5+0.5; }";
  const FS = (src, mirror) => `precision highp float;
varying vec2 _uv;
uniform sampler2D from, to;
uniform float progress, ratio;
vec4 getFromColor(vec2 uv){ return texture2D(from, ${mirror ? "vec2(1.0-uv.x, uv.y)" : "uv"}); }
vec4 getToColor(vec2 uv){ return texture2D(to, ${mirror ? "vec2(1.0-uv.x, uv.y)" : "uv"}); }
${src}
void main(){ gl_FragColor = transition(${mirror ? "vec2(1.0-_uv.x, _uv.y)" : "_uv"}); }`;

  function compile(type, src) {
    const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const progs = {};
  function program(tr) {
    const key = tr.shader + (tr.mirror ? ":m" : "");
    if (progs[key]) return progs[key];
    const p = gl.createProgram();
    gl.attachShader(p, compile(gl.VERTEX_SHADER, VS));
    gl.attachShader(p, compile(gl.FRAGMENT_SHADER, FS(R.shaders[tr.shader].glsl, tr.mirror)));
    gl.bindAttribLocation(p, 0, "_p"); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    return (progs[key] = p);
  }
  const imgs = {}; document.querySelectorAll("#gltex img").forEach((im) => (imgs[im.dataset.key] = im));
  const tex = {};
  function upload(src) {
    if (tex[src]) return tex[src];
    if (!imgs[src] || !imgs[src].naturalWidth) { console.warn("[glx] texture not loaded", src); return null; }
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, imgs[src]);
    return (tex[src] = t);
  }
  const ready = Promise.all(Object.values(imgs).map((im) => (im.complete && im.naturalWidth ? Promise.resolve() : im.decode().catch(() => null))))
    .then(() => { TR.forEach((tr) => { program(tr); upload(tr.tex_from); upload(tr.tex_to); }); });
  window.__hf.buildReady.glx = ready;
  api.ready = ready;

  // camera ease (0.65,0,0.35,1) ~ power2.inOut
  const ease = (x) => (x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2);
  let shown = false;
  function draw(t) {
    const tr = TR.find((x) => t >= x.t0 && t < x.t1);
    if (!tr || !tex[tr.tex_from] || !tex[tr.tex_to]) {
      if (shown || canvas.style.opacity !== "0") { canvas.style.opacity = "0"; shown = false; }
      return;
    }
    const p = program(tr);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.useProgram(p);
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, tex[tr.tex_from]); gl.uniform1i(gl.getUniformLocation(p, "from"), 0);
    gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, tex[tr.tex_to]); gl.uniform1i(gl.getUniformLocation(p, "to"), 1);
    gl.uniform1f(gl.getUniformLocation(p, "progress"), ease((t - tr.t0) / (tr.t1 - tr.t0)));
    gl.uniform1f(gl.getUniformLocation(p, "ratio"), canvas.width / canvas.height);
    const defs = R.shaders[tr.shader].params;
    for (const name in defs) {   // always set every parameter (unset uniform = 0 breaks most transitions)
      const v = [].concat(tr.params[name] !== undefined ? tr.params[name] : defs[name].default).map(Number);
      const loc = gl.getUniformLocation(p, name);
      if (defs[name].type === "int" || defs[name].type === "bool") gl.uniform1i(loc, v[0]);
      else gl["uniform" + v.length + "fv"](loc, v);
    }
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    canvas.style.opacity = "1"; shown = true;
  }
  api.draw = draw;
  window.addEventListener("hf-seek", (e) => {
    const t = e.detail.time;
    if (e.detail.waitUntil) e.detail.waitUntil(ready.then(() => draw(t)));
    else ready.then(() => draw(t));
  });
})();
