"""Render docs/REPORT.md (Markdown + Mermaid + images) to docs/REPORT.pdf with headless Chrome.

Needs: playwright (Python) and a local Google Chrome, plus internet access for the
marked and mermaid scripts from jsDelivr.
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
SRC = DOCS / "REPORT.md"
OUT = DOCS / "REPORT.pdf"
HTML = DOCS / "_report_print.html"

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Route Intelligence Agent — Report</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
  @page { size: A4; margin: 16mm 15mm 18mm; }
  :root { --ink:#1f1f1e; --ink2:#5f5e5a; --line:#e6e5e0; --accent:#6d28d9; --soft:#f5f3ff; }
  body { font-family: Inter, system-ui, sans-serif; color: var(--ink); font-size: 10.5pt; line-height: 1.55; margin: 0; }
  .cover { border-radius: 14px; padding: 22px 26px; margin-bottom: 18px; color: #fff;
           background: linear-gradient(120deg, #3b0764, #312e81 45%, #0f172a); }
  .cover .eyebrow { font-size: 8.5pt; letter-spacing: .14em; text-transform: uppercase; color: #c4b5fd; font-weight: 700; }
  .cover h1 { margin: 6px 0 6px; font-size: 24pt; line-height: 1.1; border: 0; color: #fff; }
  .cover p { margin: 0; color: #cbd5e1; font-size: 10.5pt; }
  .cover .chips { margin-top: 12px; display: flex; gap: 8px; flex-wrap: wrap; }
  .cover .chips span { font-size: 8.5pt; padding: 4px 9px; border-radius: 999px; background: rgba(255,255,255,.1);
                       border: 1px solid rgba(255,255,255,.2); }
  h1 { font-size: 20pt; margin: 0 0 8px; }
  h2 { font-size: 14pt; margin: 22px 0 8px; padding-bottom: 4px; border-bottom: 2px solid var(--line);
       color: #2e1065; break-after: avoid; }
  h3 { font-size: 11.5pt; margin: 16px 0 6px; color: #3b0764; break-after: avoid; }
  p, li { orphans: 3; widows: 3; }
  a { color: var(--accent); }
  code { font-family: "JetBrains Mono", Consolas, monospace; font-size: 8.8pt; background: #f4f4f2;
         padding: 1px 4px; border-radius: 4px; }
  pre { background: #f7f7f5; border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px;
        overflow: hidden; white-space: pre-wrap; break-inside: avoid; }
  pre code { background: none; padding: 0; font-size: 8.4pt; }
  table { border-collapse: collapse; width: 100%; margin: 10px 0 14px; font-size: 9pt; }
  tr { break-inside: avoid; }
  th { background: var(--soft); color: #2e1065; text-align: left; font-weight: 700; }
  th, td { border: 1px solid var(--line); padding: 5px 8px; vertical-align: top; }
  tr:nth-child(even) td { background: #fbfbfa; }
  blockquote { margin: 10px 0; padding: 8px 14px; border-left: 4px solid #8b5cf6; background: var(--soft);
               border-radius: 0 8px 8px 0; color: #312e81; break-inside: avoid; }
  img { max-width: 100%; display: block; margin: 8px auto 4px; border-radius: 8px;
        border: 1px solid var(--line); break-inside: avoid; }
  p:has(> img) { break-inside: avoid; }
  em { color: var(--ink2); }
  .mermaid { display: flex; justify-content: center; margin: 10px 0 14px; break-inside: avoid; }
  .mermaid svg { max-width: 100%; max-height: 120mm; height: auto; }
  .keep { break-inside: avoid; }
  hr { border: 0; border-top: 1px solid var(--line); }
</style></head>
<body>
<div class="cover">
  <div class="eyebrow">Latentsig review assignment · Problem 1: Route Intelligence</div>
  <h1>Route Intelligence Agent</h1>
  <p>Disruption-aware rerouting and ETA recalculation: live weather, traffic incidents and customs
     delays, a LightGBM delay-risk model on DataCo data, and dispatcher-approved reroutes.</p>
  <div class="chips"><span>Kaggle DataCo</span><span>LightGBM</span><span>Agentic workflow · 10 tools</span>
     <span>OSRM + Open-Meteo</span><span>FastAPI + Next.js</span><span>35 tests</span></div>
</div>
<div id="content"></div>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"></script>
<script>
  const md = __MARKDOWN__;
  // The cover replaces the document's own H1 and its intro line.
  const body = md.replace(/^# .*\\n+(.*\\n)?/, "");
  document.getElementById("content").innerHTML = marked.parse(body);
  document.querySelectorAll("pre code.language-mermaid").forEach((code) => {
    const div = document.createElement("div");
    div.className = "mermaid";
    div.textContent = code.textContent;
    code.parentElement.replaceWith(div);
  });
  // Keep each subsection (h3 + its text + screenshot) on one page where it fits.
  document.querySelectorAll("#content h3").forEach((h) => {
    const box = document.createElement("section");
    box.className = "keep";
    h.before(box);
    let node = h;
    while (node && !(node !== h && /^H[123]$/.test(node.tagName))) {
      const next = node.nextElementSibling;
      box.appendChild(node);
      node = next;
    }
  });
  mermaid.initialize({ startOnLoad: false, theme: "neutral", securityLevel: "loose",
                       flowchart: { htmlLabels: true }, sequence: { useMaxWidth: true } });
  mermaid.run({ querySelector: ".mermaid" })
    .then(() => { document.body.dataset.ready = "1"; })
    .catch((e) => { document.body.dataset.ready = "error: " + (e.message || e.str || e); });
</script>
</body></html>"""


def main() -> None:
    html = TEMPLATE.replace("__MARKDOWN__", json.dumps(SRC.read_text(encoding="utf-8")))
    HTML.write_text(html, encoding="utf-8")  # lives in docs/ so relative image paths resolve
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page()
            page.goto(HTML.as_uri(), wait_until="networkidle")
            page.wait_for_function("document.body.dataset.ready", timeout=60000)
            status = page.evaluate("document.body.dataset.ready")
            if status != "1":
                raise RuntimeError(f"Mermaid diagram failed to render: {status}")
            page.evaluate("Promise.all([...document.images].map(i => i.complete ? 1 : new Promise(r => i.onload = i.onerror = r)))")
            page.pdf(path=str(OUT), format="A4", print_background=True, display_header_footer=True,
                     header_template="<span></span>",
                     footer_template='<div style="font-size:8px;color:#8a8984;width:100%;padding:0 15mm;'
                                     'display:flex;justify-content:space-between;font-family:Inter,sans-serif">'
                                     '<span>Route Intelligence Agent — Report</span>'
                                     '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>',
                     margin={"top": "16mm", "bottom": "18mm", "left": "15mm", "right": "15mm"})
            browser.close()
    finally:
        HTML.unlink(missing_ok=True)
    print(f"saved {OUT}")


if __name__ == "__main__":
    main()
