// Screenshot one design-system card for gen_brand.py:
//   node render.mjs <page.html> <out.png> <width> <height> <scale> <fonts.css>
// Google Fonts requests are answered empty; fonts.css (built by _brandlib.render_html) serves the
// same faces from the local cache, so the PNG never depends on the network.
// Playwright comes from a local node_modules or the global npm root; set CHROMIUM to a Chromium
// binary if Playwright's own browser is not installed.
import { execSync } from 'node:child_process';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';

const [, , page, out, w, h, scale, fontsCss] = process.argv;
const require = createRequire(import.meta.url);
let pw;
try {
  pw = require('playwright');
} catch {
  pw = require(path.join(execSync('npm root -g').toString().trim(), 'playwright'));
}
const browser = await pw.chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const tab = await browser.newPage({ viewport: { width: +w, height: +h }, deviceScaleFactor: +scale });
await tab.route(/fonts\.(googleapis|gstatic)\.com/, (r) => r.fulfill({ status: 200, contentType: 'text/css', body: '' }));
await tab.goto('file://' + path.resolve(page));
await tab.addStyleTag({ content: fs.readFileSync(fontsCss, 'utf8') });
await tab.evaluate(() => document.fonts.ready);
const missing = await tab.evaluate(() => [...document.fonts].filter((f) => f.status === 'error').map((f) => f.family));
if (missing.length) throw new Error('fonts failed to load: ' + missing.join(', '));
await tab.screenshot({ path: out });
await browser.close();
