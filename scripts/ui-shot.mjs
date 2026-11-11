// Usage: node scripts/ui-shot.mjs <url> <out-prefix> [count] [intervalMs]
// Takes screenshots of the kiosk UI at 1280×720 and prints console errors + reported fps.
import { chromium } from "playwright";

const [url, prefix, count = "1", interval = "1000"] = process.argv.slice(2);
const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
page.on("console", (m) => m.type() === "error" && console.log("console.error:", m.text()));
page.on("pageerror", (e) => console.log("pageerror:", e.message));
await page.goto(url, { waitUntil: "load" });
await page.waitForTimeout(1500);
for (let i = 0; i < Number(count); i++) {
  await page.screenshot({ path: `${prefix}-${i}.png` });
  await page.waitForTimeout(Number(interval));
}
await browser.close();
