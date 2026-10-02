/**
 * End-to-end check of the TEXT investigation flow the UI performs.
 *
 * Creates a real investigation through the same client function the
 * Investigate page calls, then reads it back through the function the result
 * page calls, and asserts the response carries everything the report renders.
 * Skipped unless `RUN_LIVE=1`, because it costs real LLM and search calls.
 */

import { build } from "esbuild";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

if (process.env.RUN_LIVE !== "1") {
  console.log("SKIP: set RUN_LIVE=1 to execute the live text investigation flow.");
  process.exit(0);
}

const OUT = join(mkdtempSync(join(tmpdir(), "is-e2e-")), "client.mjs");

await build({
  entryPoints: ["src/services/api-client.ts"],
  bundle: true,
  format: "esm",
  platform: "node",
  outfile: OUT,
  logLevel: "error",
  alias: { "@": fileURLToPath(new URL("../src", import.meta.url)) },
  define: { "import.meta.env.VITE_API_BASE_URL": "undefined" },
});

const client = await import(pathToFileURL(OUT).href);

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

const TEXT =
  "Guaranteed 40% returns in 30 days. Limited slots available. Send 50000 INR today to activate your account. I am a SEBI registered adviser RA123456. Join my Telegram group for profit screenshots.";

console.log("Submitting a real TEXT investigation (this can take a few minutes)...\n");
const started = Date.now();
const created = await client.createTextInvestigation({ text: TEXT, language: "en" });
console.log(`Created in ${((Date.now() - started) / 1000).toFixed(1)}s\n`);

check("response has an investigation_id", typeof created.investigation_id === "string", created.investigation_id);
check("status is a known value",
  ["COMPLETED", "PARTIAL", "FAILED"].includes(created.status), created.status);
check("input_type is TEXT", created.input_type === "TEXT", created.input_type);
check("language round-trips", created.language === "en", created.language);
check("claims were extracted", created.claims.length > 0, String(created.claims.length));
check("entities were extracted", created.entities.length > 0, String(created.entities.length));
check("red flags were detected", created.red_flags.length > 0, String(created.red_flags.length));
check("timeline has events", created.timeline.length > 0, String(created.timeline.length));
check("risk assessment present", created.risk_assessment !== null);
check("timeline is in pipeline order",
  created.timeline.map((e) => e.stage).join(",").startsWith("input"),
  created.timeline.map((e) => e.stage).join(","));

const id = created.investigation_id;

console.log("\nReading the investigation back through getInvestigation()...");
const fetched = await client.getInvestigation(id);
check("id round-trips through GET", fetched.investigation_id === id);
check("claim count is stable across POST and GET",
  fetched.claims.length === created.claims.length,
  `${created.claims.length} -> ${fetched.claims.length}`);
check("red flag count is stable",
  fetched.red_flags.length === created.red_flags.length);

console.log("\nIt appears in the history list...");
const list = await client.getInvestigations({ limit: 20, offset: 0 });
check("list contains the new investigation",
  list.investigations.some((i) => i.investigation_id === id));
const summary = list.investigations.find((i) => i.investigation_id === id);
if (summary) {
  check("summary status matches detail", summary.status === created.status,
    `${summary.status} vs ${created.status}`);
  check("summary claim_count matches", summary.claim_count === created.claims.length,
    `${summary.claim_count} vs ${created.claims.length}`);
  check("summary red_flag_count matches", summary.red_flag_count === created.red_flags.length);
}

console.log(`\nINVESTIGATION_ID=${id}`);
console.log(`${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
