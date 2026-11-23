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
const modelMarkers = ["maskingValidationReport", "masking-tiny/", "albert-tiny-chinese-ner"];
const leakedAssets = productionAssets.filter(name => {
  if (!name.endsWith(".js")) return false;
  const source = readFileSync(resolve(production, "assets", name), "utf8");
  return modelMarkers.some(marker => source.includes(marker));
});

if (existsSync(resolve(production, "masking-validation.html")) || existsSync(resolve(production, "masking-tiny")) || productionAssets.some(name => /^ort-wasm-/.test(name)) || productionAssets.some(name => validationAsset.test(name)) || leakedAssets.length) {
  throw new Error("Production Admin bundle includes the masking validation page or worker");
}
if (!productionOnly) {
  const previewAssets = assetNames(preview);
  if (!existsSync(resolve(preview, "masking-validation.html")) || !previewAssets.some(name => /^runtime\.worker-.*\.js$/.test(name)) || !previewAssets.some(name => /^ort-wasm-.*\.wasm$/.test(name))) {
    throw new Error("Preview Admin bundle is missing the masking validation page or worker");
  }
  // The GPL-3.0 Tiny NER weights may only be hosted when bundling was requested explicitly.
  if (process.env.MASKING_TINY_BUNDLE !== "1" && existsSync(resolve(preview, "masking-tiny"))) {
    throw new Error("Preview Admin bundle includes Tiny NER weights without MASKING_TINY_BUNDLE=1");
  }
}

console.log(productionOnly ? "Admin production masking bundle boundary passed" : "Admin production/preview masking bundle boundary passed");
