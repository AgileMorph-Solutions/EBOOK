// HTML -> print-ready PDF render service.
// POST /render  body: text/html  ->  application/pdf
//
// Each page is a fully designed, fixed-size composition (title band + photo +
// sized text), so Chromium prints them 1:1 via preferCSSPageSize. All chrome
// (running header, page number, bleed) is drawn in the page CSS.

import express from "express";
import puppeteer from "puppeteer";

const app = express();
app.use(express.text({ type: "*/*", limit: "200mb" }));

const EXECUTABLE = process.env.PUPPETEER_EXECUTABLE_PATH || undefined;

app.get("/health", (_req, res) => res.json({ ok: true }));

app.post("/render", async (req, res) => {
  let browser;
  try {
    browser = await puppeteer.launch({
      headless: "new",
      executablePath: EXECUTABLE,
      args: ["--no-sandbox", "--disable-dev-shm-usage", "--font-render-hinting=none"],
    });
    const page = await browser.newPage();
    await page.setContent(req.body, { waitUntil: "networkidle0", timeout: 120000 });
    const pdf = await page.pdf({ printBackground: true, preferCSSPageSize: true });
    res.set("Content-Type", "application/pdf").send(Buffer.from(pdf));
  } catch (err) {
    console.error("render error:", err);
    res.status(500).json({ error: String(err) });
  } finally {
    if (browser) await browser.close();
  }
});

const port = process.env.PORT || 4000;
app.listen(port, () => console.log(`render service on :${port}`));
