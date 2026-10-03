/**
 * Server-render every report component against a real investigation payload.
 *
 * Type checking cannot catch a render-time failure — an undefined access, a bad
 * `.map`, a chart fed a wrong shape. This renders the actual components with a
 * real backend response and asserts that meaningful content reaches the HTML.
 *
 * Pages that need a live backend to render (dashboard, investigate) are
 * checked at the source level instead: the stale-wording and demo-control
 * checks below read those files and assert the copy is present or gone.
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

const FRONTEND_ROOT = fileURLToPath(new URL("..", import.meta.url));
const PROJECT_ROOT = fileURLToPath(new URL("../..", import.meta.url));
const readSource = (relativePath) =>
  readFileSync(join(FRONTEND_ROOT, relativePath), "utf8");

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

// React's server renderer escapes text content, so a string that
// contains an apostrophe or quote appears entity-encoded in the
// HTML. Compare both forms.
const escapeHtml = (text) =>
  String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#x27;");
const rendersText = (markup, text) =>
  markup.includes(text) || markup.includes(escapeHtml(text));

// The report's presentation layer carries localized section titles.
const sections = investigation.report?.sections;

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
check("renders a timeline stage", html.includes("Investigation Timeline"));
check("renders the risk caveat verbatim",
  html.includes("not a probability of fraud or financial loss"));
check("renders the investigation disclaimer",
  rendersText(html, sections?.disclaimer ?? "What this report is, and is not") &&
  rendersText(html, "InvestShield AI is an investigation tool"));
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

// ---- Phase 17: localized section headings --------------------------------
// When the captured payload includes localized section titles, the
// rendered headings must be the localized strings; otherwise the
// English fallback must render.
const SECTION_KEYS = [
  ["risk_assessment", "Risk Assessment"],
  ["claims", "Claims"],
  ["entities", "Entities"],
  ["why_flagged", "Why This Was Flagged"],
  ["evidence", "Evidence"],
  ["timeline", "Investigation Timeline"],
  ["limitations", "Limitations"],
  ["disclaimer", "Disclaimer"],
];

if (sections && typeof sections === "object") {
  for (const [key, fallback] of SECTION_KEYS) {
    const expected = sections[key] ?? fallback;
    check(`renders localized heading: ${key}`, rendersText(html, expected), expected);
  }
} else {
  for (const [, fallback] of SECTION_KEYS) {
    check(`renders fallback heading: ${fallback}`, rendersText(html, fallback));
  }
}

// ---- Phase 17: "Why was this flagged?" -----------------------------------
if (investigation.red_flags.length > 0) {
  check("renders the why-flagged explanation labels",
    html.includes("What was detected") &&
    html.includes("Why the rule triggered") &&
    html.includes("Text that caused it") &&
    html.includes("How it contributed"));
  check("renders the red flag rule reason",
    rendersText(html, investigation.red_flags[0].rule_reason));
}
if (investigation.red_flags.length > 0 &&
    (investigation.risk_assessment?.factors ?? []).some(
      (factor) => factor.origin === "RED_FLAG",
    )) {
  check("renders red flag score contribution",
    html.includes("Score contribution"));
}

// ---- Phase 17: claim / evidence / source graph ---------------------------
const evidenceItemCount = investigation.evidence.reduce(
  (count, group) => count + group.evidence.length,
  0,
);
const hasClaimEntityEdges =
  investigation.entities.length > 0 &&
  investigation.claims.some((claim) => claim.entity_ids.length > 0);
const hasGraph =
  hasClaimEntityEdges || evidenceItemCount > 0;

if (hasGraph) {
  check("renders the evidence graph title",
    html.includes("Claim, evidence and source graph"));
  check("graph renders claim nodes", html.includes('data-node-kind="claim"'));
  check("graph renders the node type vocabulary",
    html.includes("CLAIM") && html.includes("ENTITY") &&
    html.includes("EVIDENCE") && html.includes("SOURCE"));
}
if (hasClaimEntityEdges) {
  check("graph renders entity nodes", html.includes('data-node-kind="entity"'));
  check("graph renders claim→entity edges",
    html.includes('data-edge-kind="claim-entity"'));
}
if (evidenceItemCount > 0) {
  check("graph renders evidence nodes", html.includes('data-node-kind="evidence"'));
  check("graph renders claim→evidence edges",
    html.includes('data-edge-kind="claim-evidence"'));
  check("graph renders evidence→source edges",
    html.includes('data-edge-kind="evidence-source"'));
}
if (!hasGraph) {
  check("graph renders the honest empty state",
    html.includes("No evidence relationships were recorded"));
}

// ---- Phase 17: localized heading rendering (hi / mr) ---------------------
// A render-harness fixture: the same components, with the report's
// presentation layer overridden to Hindi and Marathi. The components always
// read whatever the API returned; this proves the localized titles reach the
// HTML in every supported language, not only English.
const LOCALIZED_SECTIONS = {
  hi: {
    risk_assessment: "जोखिम आकलन",
    claims: "दावे",
    entities: "संस्थाएँ",
    why_flagged: "यह क्यों चिह्नित किया गया",
    evidence: "साक्ष्य",
    timeline: "जांच समयरेखा",
    limitations: "सीमाएँ",
    disclaimer: "अस्वीकरण",
  },
  mr: {
    risk_assessment: "जोखिम मूल्यांकन",
    claims: "दावे",
    entities: "संस्था",
    why_flagged: "हे का चिह्नित केले",
    evidence: "साक्ष्य",
    timeline: "तपासणी कालावधी",
    limitations: "मर्यादा",
    disclaimer: "नकार",
  },
};

for (const [language, localized] of Object.entries(LOCALIZED_SECTIONS)) {
  const localizedInvestigation = {
    ...investigation,
    report: {
      ...(investigation.report ?? {}),
      language,
      sections: { ...(investigation.report?.sections ?? {}), ...localized },
    },
  };
  const localizedHtml = mod.renderReport({
    investigation: localizedInvestigation,
    summary,
    router: StaticRouter,
  });
  for (const [key, value] of Object.entries(localized)) {
    check(`renders ${language} heading: ${key}`, rendersText(localizedHtml, value), value);
  }
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
  ["no graph relationships", "No evidence relationships were recorded"],
];
for (const [label, text] of empties) {
  check(`empty state: ${label}`, emptyHtml.includes(text));
}
// Absence of evidence must never be phrased as a contradiction.
check(
  "empty evidence is not called a contradiction",
  !/no sufficient evidence was found[^]*?is (?:a )?contradiction/i.test(emptyHtml),
);

// ---- Source-level checks for pages that need a live backend -----------
console.log("\n== page sources ==");

const dashboardSource = readSource("src/pages/DashboardPage.tsx");
check("dashboard no longer claims screenshot/PDF analysis do not work",
  !dashboardSource.includes("screenshot and PDF analysis do not"),
  "stale Phase 14 wording is still present");
check("dashboard states that limitations name what could not be checked",
  dashboardSource.includes("Limitations section names every check"));

const investigateSource = readSource("src/pages/InvestigatePage.tsx");
check("investigate page wires the README demo input",
  investigateSource.includes("DEMO_INVESTIGATION_TEXT"));
check("investigate page offers the demo action",
  investigateSource.includes("Try Demo Investigation"));

const demoSource = readSource("src/lib/demo.ts");
const README_DEMO_LINES = [
  "🚨 Exclusive AI Trading Opportunity 🚨",
  "Our SEBI-approved expert team guarantees 35% monthly returns.",
  "Join our private Telegram group today.",
  "Minimum investment ₹25,000.",
  "Pay directly to our account to activate your trading account.",
];
const readmeSource = readFileSync(join(PROJECT_ROOT, "README.md"), "utf8");
for (const line of README_DEMO_LINES) {
  check(`demo module matches README demo line: ${line.slice(0, 32)}…`,
    readmeSource.includes(line) && demoSource.includes(line));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);

// Keep react-dom/server import referenced for the bundler's externals.
void renderToStaticMarkup;
void h;
