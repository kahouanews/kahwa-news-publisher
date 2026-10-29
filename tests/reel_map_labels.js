// node tests/reel_map_labels.js <build/style-A dir> <png/jpg path with {t}, or ""> <t> ...
// -> prints JSON {t: [iso3 of the country labels on the reel map]}. Needs KAHWA_CHROME (Chrome / Chrome Headless Shell).
// Used by tests/test_maps.py::test_reel_western_sahara_and_morocco_labels_in_browser and for review frames.
const path = require("path");
const puppeteer = require(path.resolve(__dirname, "../kahwa_publisher/reels/node_modules/puppeteer-core"));
(async () => {
  const [dir, shot, ...times] = process.argv.slice(2);
  const b = await puppeteer.launch({ executablePath: process.env.KAHWA_CHROME, args: ["--no-sandbox", "--allow-file-access-from-files", "--disable-gpu"] });
  const p = await b.newPage();
  await p.setViewport({ width: 1080, height: 1920 });
  await p.goto("file://" + path.resolve(dir) + "/index.html", { waitUntil: "load" });
  await p.evaluate(() => document.fonts.ready);
  const out = {};
  for (const t of times) {
    out[t] = await p.evaluate((t) => { window.KAHWA_TL.seek(+t); return window.KAHWA_MAP_LABELS || []; }, t);
    if (shot) {
      await new Promise((r) => setTimeout(r, 250));
      const jpg = shot.endsWith(".jpg");
      await p.screenshot({ path: shot.replace("{t}", t), type: jpg ? "jpeg" : "png", quality: jpg ? 92 : undefined });
    }
  }
  console.log(JSON.stringify(out));
  await b.close();
})();
