/**
 * Live integration check for the frontend API client.
 *
 * Bundles `src/services/api-client.ts` with the same esbuild that Vite uses and
 * exercises every exported function against the running backend, including the
 * error paths. This verifies the client's URLs, envelope parsing and error
 * typing against the real API rather than against a mock.
 */

import { build } from "esbuild";
import { writeFileSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";
import { crc32, deflateSync } from "node:zlib";

const OUT = join(mkdtempSync(join(tmpdir(), "is-client-")), "client.mjs");

await build({
  entryPoints: ["src/services/api-client.ts"],
  bundle: true,
  format: "esm",
  platform: "node",
  outfile: OUT,
  logLevel: "error",
  // Resolve the "@/" alias the same way vite.config.ts does. fileURLToPath is
  // required because the project path contains a space, which is percent-encoded
  // in a raw URL pathname.
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

/* A minimal, valid PNG, built with no dependency beyond node:zlib.
 * The image endpoint accepts it and the OCR engine reads it (finding
 * no text in a blank canvas), which is enough to prove the upload
 * reaches the pipeline and that its provenance is returned. */
function pngChunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const typeBuf = Buffer.from(type, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])));
  return Buffer.concat([length, typeBuf, data, crc]);
}

function makePng(width = 1, height = 1) {
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  // IHDR: width, height, bit depth 8, colour type 0 (greyscale),
  // compression 0, filter 0, interlace 0.
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 0;
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;
  // Each scanline is a filter byte (0) followed by `width` pixels.
  const raw = Buffer.alloc(height * (1 + width));
  const idat = deflateSync(raw);
  return Buffer.concat([
    signature,
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", idat),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

console.log(`API base: ${client.API_BASE_URL}\n`);

console.log("== getHealth ==");
const health = await client.getHealth();
check("status is ok", health.status === "ok", health.status);
check("has database probe", typeof health.database?.connected === "boolean");
check("has services map", typeof health.services === "object" && health.services !== null);
check("llm service probe typed", typeof health.services?.llm?.available === "boolean");

console.log("\n== getLimits ==");
const limits = await client.getLimits();
check("supported_input_types is an array", Array.isArray(limits.supported_input_types));
check(
  "reports TEXT as supported",
  Array.isArray(limits.supported_input_types) && limits.supported_input_types.includes("TEXT"),
  JSON.stringify(limits.supported_input_types),
);
check("max_text_length present", typeof limits.max_text_length === "number");
check(
  "reports URL as supported",
  Array.isArray(limits.supported_input_types) && limits.supported_input_types.includes("URL"),
  JSON.stringify(limits.supported_input_types),
);
check("max_url_length present", typeof limits.max_url_length === "number");
check(
  "reports IMAGE as supported",
  Array.isArray(limits.supported_input_types) && limits.supported_input_types.includes("IMAGE"),
  JSON.stringify(limits.supported_input_types),
);
check("max_upload_bytes present", typeof limits.max_upload_bytes === "number");
check("allowed_image_types is an array", Array.isArray(limits.allowed_image_types));
check("ocr_languages is a string", typeof limits.ocr_languages === "string");

console.log("\n== getInvestigations (paging) ==");
const page1 = await client.getInvestigations({ limit: 1, offset: 0 });
check("investigations is an array", Array.isArray(page1.investigations));
check("respects limit=1", page1.investigations.length <= 1, String(page1.investigations.length));
check("total is a number", typeof page1.total === "number", String(page1.total));
check("echoes limit", page1.limit === 1);
check("echoes offset", page1.offset === 0);
if (page1.investigations[0]) {
  const first = page1.investigations[0];
  check("summary has investigation_id", typeof first.investigation_id === "string");
  check("summary status is a known value",
    ["COMPLETED", "PARTIAL", "FAILED"].includes(first.status), first.status);
  check("summary input_type is a known value",
    ["TEXT", "URL", "IMAGE", "PDF"].includes(first.input_type), first.input_type);
  check("summary has created_at", typeof first.created_at === "string");
}

console.log("\n== getInvestigation (existing) ==");
if (page1.investigations[0]) {
  const id = page1.investigations[0].investigation_id;
  const inv = await client.getInvestigation(id);
  check("id round-trips", inv.investigation_id === id);
  check("has claims array", Array.isArray(inv.claims));
  check("has entities array", Array.isArray(inv.entities));
  check("has red_flags array", Array.isArray(inv.red_flags));
  check("has verification_results array", Array.isArray(inv.verification_results));
  check("has evidence array", Array.isArray(inv.evidence));
  check("has timeline array", Array.isArray(inv.timeline));
  check("has limitations array", Array.isArray(inv.limitations));
  check("has warnings array", Array.isArray(inv.warnings));
  check("has errors array", Array.isArray(inv.errors));
  check("risk_assessment is object or null",
    inv.risk_assessment === null || typeof inv.risk_assessment === "object");
  check("completed_at is null on current backend", inv.completed_at === null, String(inv.completed_at));

  if (inv.claims[0]) {
    const claim = inv.claims[0];
    check("claim has text", typeof claim.text === "string");
    check("claim confidence is 0..1",
      claim.confidence >= 0 && claim.confidence <= 1, String(claim.confidence));
    check("claim has evidence_span with offsets",
      typeof claim.evidence_span?.start === "number" &&
      typeof claim.evidence_span?.end === "number");
  }
  if (inv.evidence[0]?.evidence[0]?.source) {
    const src = inv.evidence[0].evidence[0].source;
    check("evidence source has title", typeof src.title === "string");
    check("evidence source has canonical_url", typeof src.canonical_url === "string");
    check("evidence source has source_tier", typeof src.source_tier === "string");
  }
  if (inv.risk_assessment) {
    const r = inv.risk_assessment;
    check("risk has score + level", typeof r.risk_score === "number" && typeof r.risk_level === "string");
    check("risk level is a known value",
      ["LOW", "MEDIUM", "HIGH", "CRITICAL"].includes(r.risk_level), r.risk_level);
    check("risk has thresholds.ceiling", typeof r.thresholds?.ceiling === "number");
    check("risk factors are an array", Array.isArray(r.factors));
  }
} else {
  console.log("  SKIP  no stored investigation available");
}

console.log("\n== error paths ==");

async function expectApiError(label, fn, predicate) {
  try {
    await fn();
    check(label, false, "expected an ApiError but the call succeeded");
  } catch (error) {
    const isApiError = error instanceof client.ApiError;
    check(`${label} -> ApiError`, isApiError, String(error && error.message));
    if (isApiError) {
      check(`${label} -> predicate`, predicate(error), `code=${error.code} status=${error.status}`);
    }
  }
}

await expectApiError(
  "empty text rejected",
  () => client.createTextInvestigation({ text: "   " }),
  (e) => e.isValidation && e.code === "INPUT_EMPTY",
);

await expectApiError(
  "over-length text rejected",
  () => client.createTextInvestigation({ text: "a".repeat(20001) }),
  (e) => e.isValidation && e.detailLines().length > 0,
);

await expectApiError(
  "unknown investigation id",
  () => client.getInvestigation("definitely-not-a-real-id"),
  (e) => e.isNotFound && e.code === "INVESTIGATION_NOT_FOUND",
);

await expectApiError(
  "out-of-range limit rejected",
  () => client.getInvestigations({ limit: 999 }),
  (e) => e.isValidation,
);

await expectApiError(
  "backend unreachable",
  async () => {
    const unreachable = Object.create(client);
    void unreachable;
    // Point a fresh call at a closed port by using a cancelled-free bad host.
    const original = globalThis.fetch;
    globalThis.fetch = () => Promise.reject(new TypeError("Failed to fetch"));
    try {
      await client.getHealth();
    } finally {
      globalThis.fetch = original;
    }
  },
  (e) => e.kind === "network" && e.code === "BACKEND_UNREACHABLE",
);

console.log("\n== url investigation wiring ==");

// These exercise `POST /api/investigations/url` against the real API
// without reaching the internet: the SSRF guard and the scheme check
// both refuse before any socket is opened, so the response is produced
// by the backend's own policy rather than by whatever the network did.

await expectApiError(
  "blocked loopback url rejected",
  () => client.createUrlInvestigation({ url: "http://127.0.0.1/" }),
  (e) => e.status === 422 && e.code === "URL_ADDRESS_BLOCKED",
);

await expectApiError(
  "metadata url rejected",
  () =>
    client.createUrlInvestigation({ url: "https://169.254.169.254/latest/" }),
  (e) => e.status === 422 && e.code === "URL_METADATA_ADDRESS_BLOCKED",
);

await expectApiError(
  "unsupported scheme url rejected",
  () => client.createUrlInvestigation({ url: "ftp://example.com/" }),
  (e) => e.status === 422 && e.code === "URL_SCHEME_UNSUPPORTED",
);

await expectApiError(
  "empty url rejected",
  () => client.createUrlInvestigation({ url: "   " }),
  (e) => e.isValidation,
);

await expectApiError(
  "over-length url rejected",
  () => client.createUrlInvestigation({ url: "https://x/" + "a".repeat(5000) }),
  (e) => e.isValidation,
);

console.log("\n== image investigation wiring ==");

// These exercise `POST /api/investigations/image` against the real
// API. The valid PNG is accepted and its provenance is returned;
// bytes that are not an image and an upload past the byte limit are
// both refused by the backend's own validation rather than by the
// client, which is the part worth checking against the real server.

const pngBytes = makePng(1, 1);
const pngFile = new File([pngBytes], "screenshot.png", { type: "image/png" });

const imageResult = await client.createImageInvestigation(pngFile);
check(
  "image investigation returns IMAGE",
  imageResult.input_type === "IMAGE",
  String(imageResult.input_type),
);
check(
  "image investigation carries image_source",
  imageResult.image_source !== null && typeof imageResult.image_source === "object",
  String(imageResult.image_source),
);
check(
  "image_source reports the filename",
  imageResult.image_source?.filename === "screenshot.png",
  String(imageResult.image_source?.filename),
);
check(
  "image_source reports the declared type",
  imageResult.image_source?.content_type === "image/png",
  String(imageResult.image_source?.content_type),
);
check(
  "image_source reports the byte size",
  imageResult.image_source?.byte_size === pngBytes.length,
  String(imageResult.image_source?.byte_size),
);

// A blank canvas is a valid image the engine reads but finds no
// text in. Where the engine is absent the run degrades to
// OCR_UNAVAILABLE instead of decoding. Either outcome proves the
// upload was processed, so the check accepts both.
const engineRan =
  imageResult.image_source?.format === "PNG" &&
  typeof imageResult.image_source?.width === "number";
const engineAbsent =
  Array.isArray(imageResult.limitations) &&
  imageResult.limitations.includes("OCR_UNAVAILABLE");
check(
  "image was decoded (or OCR is unavailable)",
  engineRan || engineAbsent,
  `format=${String(imageResult.image_source?.format)} limitations=${JSON.stringify(imageResult.limitations)}`,
);

// Bytes that claim to be an image but do not decode are refused.
const notAnImage = new File(
  [Buffer.from("definitely not an image")],
  "shot.png",
  { type: "image/png" },
);
await expectApiError(
  "non-image bytes rejected",
  () => client.createImageInvestigation(notAnImage),
  (e) => e.status === 422 && e.code === "OCR_IMAGE_UNREADABLE",
);

// An upload past the byte limit is refused before it is decoded,
// so this refusal does not depend on the OCR engine being present.
const maxUploadBytes =
  typeof limits.max_upload_bytes === "number" ? limits.max_upload_bytes : 0;
const oversized = new File([Buffer.alloc(maxUploadBytes + 1)], "big.png", {
  type: "image/png",
});
await expectApiError(
  "oversized image rejected",
  () => client.createImageInvestigation(oversized),
  (e) => e.status === 422 && e.code === "OCR_IMAGE_TOO_LARGE",
);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
