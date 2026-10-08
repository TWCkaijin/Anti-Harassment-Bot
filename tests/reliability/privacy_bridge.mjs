/** JSONL bridge to the browser's exact local review and approval implementation. */
import { registerHooks } from "node:module";
import { createInterface } from "node:readline";

const privacyUrl = new URL("../../frontend/src/services/clientPrivacy.ts", import.meta.url);
const allowedImports = new Map([
  ["./caseFacts", new URL("../../frontend/src/services/caseFacts.ts", import.meta.url).href],
  ["./imageUpload", new URL("../../frontend/src/services/imageUpload.ts", import.meta.url).href],
]);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL === privacyUrl.href && allowedImports.has(specifier)) {
      return { url: allowedImports.get(specifier), shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

let privacy;
try {
  privacy = await import(privacyUrl.href);
} catch {
  process.stderr.write("privacy_bridge_initialization_failed\n");
  process.exit(1);
}

const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
  try {
    const input = JSON.parse(line);
    const draft = privacy.createPrivacyDraft(input.request, input.hidden_terms ?? []);
    const approved = privacy.approvePrivacyDraft(draft);
    const body = privacy.reviewedRequestBody(approved);
    process.stdout.write(`${JSON.stringify({
      approved_body: body,
      findings: draft.findings.map(({ type, count }) => ({ type, count })),
    })}\n`);
  } catch {
    // Never echo raw source text, signed tokens, or exception messages.
    process.stdout.write('{"error_code":"privacy_review_failed"}\n');
  }
}
