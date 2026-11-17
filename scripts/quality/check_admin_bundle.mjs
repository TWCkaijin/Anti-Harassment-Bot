import { existsSync, readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const production = resolve(root, "admin/frontend/dist");
const preview = resolve(root, "admin/frontend/dist-preview");
const productionOnly = process.argv.slice(2).includes("--production-only");
if (process.argv.slice(2).some(arg => arg !== "--production-only")) {
  throw new Error("Unknown check_admin_bundle argument");
}

function assetNames(directory) {
  const assets = resolve(directory, "assets");
  if (!existsSync(resolve(directory, "index.html")) || !existsSync(assets)) {
    throw new Error(`Missing Admin build output: ${directory}`);
  }
  return readdirSync(assets);
}

const productionAssets = assetNames(production);
const validationAsset = /^(?:ValidationPage|masking-validation|runtime\.worker)-.*\.js$/;
const modelMarkers = ["Qwen3-0.6B-PII-v1", "maskingValidationReport", "mlc-chat-config.json"];
const leakedAssets = productionAssets.filter(name => {
  if (!name.endsWith(".js")) return false;
  const source = readFileSync(resolve(production, "assets", name), "utf8");
  return modelMarkers.some(marker => source.includes(marker));
});

if (existsSync(resolve(production, "masking-validation.html")) || productionAssets.some(name => validationAsset.test(name)) || leakedAssets.length) {
  throw new Error("Production Admin bundle includes the masking validation page or worker");
}
if (!productionOnly) {
  const previewAssets = assetNames(preview);
  if (!existsSync(resolve(preview, "masking-validation.html")) || !previewAssets.some(name => /^runtime\.worker-.*\.js$/.test(name))) {
    throw new Error("Preview Admin bundle is missing the masking validation page or worker");
  }
}

console.log(productionOnly ? "Admin production masking bundle boundary passed" : "Admin production/preview masking bundle boundary passed");
