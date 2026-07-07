// HTML -> print-ready PDF render service.
//
// POST /render
//   application/json {html, fit_js, width_in, height_in}
//     -> application/json {pdf: <base64>, report: <fit/audit report>}
//   text/html (legacy)  ->  application/pdf
//
// The JSON protocol gives parity with the local Playwright path in
// bookforge/layout/render.py: the client sends its _LAYOUT_FIT_JS source, the
// service runs the same text-fit + layout-audit pass before printing, and the
// returned report feeds the assemble-stage layout gate. The viewport and sheet
// are pinned to trim+bleed at 96 css-px/in exactly like _playwright_pdf, so
// geometry is identical on both paths.
//
// Each page is a fully designed, fixed-size composition (title band + photo +
// sized text), so Chromium prints them 1:1. All chrome (running header, page
// number, bleed) is drawn in the page CSS.

import express from "express";
import puppeteer from "puppeteer";

const app = express();
app.use(express.json({ limit: "200mb" }));
app.use(express.text({ type: "text/*", limit: "200mb" }));

const EXECUTABLE = process.env.PUPPETEER_EXECUTABLE_PATH || undefined;
const CSS_PX_PER_IN = 96;

app.get("/health", (_req, res) => res.json({ ok: true }));

async function renderPdf({ html, fitJs, widthIn, heightIn }) {
  let browser;
  try {
    browser = await puppeteer.launch({
      headless: "new",
      executablePath: EXECUTABLE,
      args: ["--no-sandbox", "--disable-dev-shm-usage"],
    });
    const page = await browser.newPage();
    if (widthIn && heightIn) {
      await page.setViewport({
        width: Math.round(widthIn * CSS_PX_PER_IN),
        height: Math.round(heightIn * CSS_PX_PER_IN),
        deviceScaleFactor: 1,
      });
    }
    await page.emulateMediaType("print");
    // Data URIs only — no network. "load" fires once images decode; "networkidle0"
    // can stall on inline base64 payloads.
    await page.setContent(html, { waitUntil: "load", timeout: 300000 });
    let report = null;
    if (fitJs) {
      await page.evaluate("document.fonts.ready");
      report = await page.evaluate(`(${fitJs})()`);
    }
    // Explicit timeout so a stuck print doesn't hang the request indefinitely
    // (Puppeteer's default 30s is too short for large image-rich books).
    const pdfOpts = { printBackground: true, timeout: 180000 };
    if (widthIn && heightIn) {
      // Pin the sheet to trim+bleed and scale=1 so the sized-in-CSS content maps
      // 1:1 onto the page (no viewport-driven downscale) — matches _playwright_pdf.
      Object.assign(pdfOpts, {
        width: `${widthIn}in`, height: `${heightIn}in`, scale: 1,
        preferCSSPageSize: false,
        margin: { top: "0", bottom: "0", left: "0", right: "0" },
      });
    } else {
      pdfOpts.preferCSSPageSize = true; // legacy behavior
    }
    const pdf = await page.pdf(pdfOpts);
    return { pdf: Buffer.from(pdf), report };
  } finally {
    if (browser) await browser.close();
  }
}

app.post("/render", async (req, res) => {
  try {
    if (req.is("application/json")) {
      const { html, fit_js, width_in, height_in } = req.body || {};
      if (!html) return res.status(400).json({ error: "missing 'html'" });
      const { pdf, report } = await renderPdf({
        html, fitJs: fit_js, widthIn: width_in, heightIn: height_in,
      });
      return res.json({ pdf: pdf.toString("base64"), report });
    }
    // legacy: raw HTML in, raw PDF out, no fit pass
    const { pdf } = await renderPdf({ html: req.body });
    res.set("Content-Type", "application/pdf").send(pdf);
  } catch (err) {
    console.error("render error:", err);
    res.status(500).json({ error: String(err) });
  }
});

const port = process.env.PORT || 4000;
app.listen(port, () => console.log(`render service on :${port}`));
