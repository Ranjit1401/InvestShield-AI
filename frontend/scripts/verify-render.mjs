/**
 * Server-render every report component against a real investigation payload.
 *
 * Type checking cannot catch a render-time failure — an undefined access, a bad
 * `.map`, a chart fed a wrong shape. This renders the actual components with a
 * real backend response and asserts that meaningful content reaches the HTML.
 *
 * Usage: node scripts/verify-render.mjs <path-to-investigation.json> <id> <summary.json>
 */

import { build } from "esbuild";
import { mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server.js";

const [investigationPath, investigationId, summaryPath] = process.argv.slice(2);
if (!investigationPath || !investigationId) {
  console.error("usage: node verify-render.mjs <investigation.json> <id> [summary.json]");
  process.exit(2);
}

// PowerShell writes a UTF-8 BOM, which JSON.parse rejects.
const readJson = (path) =>
  JSON.parse(readFileSync(path, "utf8").replace(/^﻿/, ""));

const investigation = readJson(investigationPath);
const summary = summaryPath ? readJson(summaryPath) : null;

// Emit inside the project so the `external` react packages still resolve from
// ./node_modules; a temp directory has no node_modules to resolve against.
const OUT = join(fileURLToPath(new URL("../node_modules/.tmp", import.meta.url)), "render-check.mjs");
mkdirSync(dirname(OUT), { recursive: true });

await build({
  entryPoints: ["scripts/render-entry.tsx"],
  bundle: true,
  format: "esm",
  platform: "node",
  outfile: OUT,
  logLevel: "error",
  jsx: "automatic",
  alias: { "@": fileURLToPath(new URL("../src", import.meta.url)) },
  define: { "import.meta.env.VITE_API_BASE_URL": "undefined" },
  loader: { ".css": "empty" },
  external: ["react", "react-dom", "react-dom/server", "react-router-dom"],
  // lucide-react ships CommonJS, which cannot be required from ESM output.
  // Provide Node's real `require` so those modules load.
  banner: {
    js: [
      "import { createRequire as __cr } from 'node:module';",
      "import { fileURLToPath as __ftp } from 'node:url';",
      "import { dirname as __dn } from 'node:path';",
      "const require = __cr(import.meta.url);",
    ].join("\n"),
  },
});

const mod = await import(pathToFileURL(OUT).href);

let pass = 0;
let fail = 0;
function check(label, condition, detail = "") {
  if (condition) {
    pass += 1;
    console.log(`  PASS  ${label}`);
  } else {
    fail += 1;
    console.log(`  FAIL  ${label}${detail ? ` -> ${detail}` : ""}`);
  }
}

const html = mod.renderReport({ investigation, summary, router: StaticRouter });

console.log(`Rendered ${html.length} characters of HTML.\n`);

// The report must be substantive, not a shell.
check("renders the investigation id", html.includes(investigation.investigation_id));
check("renders a claim text",
  investigation.claims.length === 0 || html.includes(investigation.claims[0].text));
check("renders an entity name",
  investigation.entities.length === 0 || html.includes(investigation.entities[0].name));
check("renders a red flag name",
  investigation.red_flags.length === 0 || html.includes(investigation.red_flags[0].name));
check("renders the risk score",
  investigation.risk_assessment === null ||
  html.includes(String(investigation.risk_assessment.risk_score)));
check("renders a timeline stage", html.includes("Investigation timeline"));
check("renders the risk caveat verbatim",
  html.includes("not a probability of fraud or financial loss"));
check("renders the investigation disclaimer",
  html.includes("What this report is, and is not") &&
  html.includes("a recommendation"));
check("does not render raw JSON braces as content",
  !html.includes("undefined"), "the string 'undefined' appeared in the output");

if (investigation.evidence.length > 0 && investigation.evidence[0].evidence.length > 0) {
  const source = investigation.evidence[0].evidence[0].source;
  check("renders an evidence source title", html.includes(source.title));
  check("renders evidence relationship label", html.includes("Mentions the claim"));
  check("external links are rel=noopener", html.includes('rel="noopener noreferrer"'));
  check("external links set referrerPolicy", html.includes('referrerPolicy="no-referrer"')
    || html.includes('referrerpolicy="no-referrer"'));
  check("no javascript: URLs are linked",
    !/href="javascript:/i.test(html));
}

if (investigation.warnings.length > 0) {
  check("renders limitation messages",
    html.includes(investigation.warnings[0].message));
}
if (investigation.evidence.length === 0) {
  check("renders the no-evidence empty state",
    html.includes("No sufficient evidence"));
}

if (summary) {
  const listHtml = mod.renderList({ investigation: summary, router: StaticRouter });
  check("list renders the summary id", listHtml.includes(summary.investigation_id));
  check("list links to the result route",
    listHtml.includes(`/investigation/${encodeURIComponent(summary.investigation_id)}`));
}

console.log(`\n${pass} passed, ${fail} failed`);

// ---- Empty states ----------------------------------------------------
console.log("\n== empty states ==");
const emptyHtml = mod.renderEmptyReport({ router: StaticRouter });
console.log(`Rendered ${emptyHtml.length} characters of empty-state HTML.`);

const empties = [
  ["no evidence", "No sufficient evidence was found"],
  ["no claims", "No claims were extracted"],
  ["no entities", "No entities were identified"],
  ["no red flags", "No red-flag patterns were detected"],
  ["no risk assessment", "No risk assessment was produced"],
  ["recorded failure", "Investigation failed"],
  ["missing timestamp", "Not recorded"],
  ["timeline unreported", "The pipeline did not report this stage"],
];
for (const [label, text] of empties) {
  check(`empty state: ${label}`, emptyHtml.includes(text));
}
// Absence of evidence must never be phrased as a contradiction.
check(
  "empty evidence is not called a contradiction",
  !/no sufficient evidence was found[^]*?is (?:a )?contradiction/i.test(emptyHtml),
);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);

// Keep react-dom/server import referenced for the bundler's externals.
void renderToStaticMarkup;
void h;
