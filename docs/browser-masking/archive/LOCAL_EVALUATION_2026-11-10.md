# Local browser masking evaluation — 2026-11-10

All cases below are the repository's synthetic fixtures. Chrome 157 ran on an Apple M3 WebGPU adapter. The Client chat path did not use this model.

## Fixed v5 baseline

The complete 80 development + 40 synthetic holdout run used model revision `881c276b17a748fc5b82f694226df4952ed6607c` and prompt version `zh-span-extraction-v5-no-examples-sentence150`.

| Split | Valid output | Precision | Recall | P95 for 200–500 characters |
| --- | ---: | ---: | ---: | ---: |
| Development | 22/80 | 96.77% | 27.27% | 22.82 s |
| Synthetic holdout | 19/40 | 95.65% | 40.00% | 18.82 s |
| All | 41/120 | 96.30% | 31.52% | 21.86 s |

The run had 64 `invalid_entity_span` cases and 15 `incomplete_output` cases, with no browser page errors or network-policy violations. Among the 64 invalid spans, 50 contained an empty string and 14 contained text absent from the source. All 15 incomplete outputs occurred in the contact category on an identical 21-character, unannotated second chunk: generation repeated one non-source token until the 512-token limit. Strict source-span validation correctly rejected these outputs.

## Local-only trials

Both trials reused the cached model and blocked remote requests (`BROWSER_LOCAL_CACHE_ONLY=true`). Each reported zero fetched model bytes, zero remote-request violations, and zero page errors.

1. **Packed short sentences within 150 characters:** The run stopped after case 28 of the development split when a case reached the 30-second model timeout. For the same first 28 cases, valid outputs and recall matched v5 (10/28 and 35.71%), while 200–500-character P95 increased from 8.52 s to 30.00 s. This change was reverted.
2. **Require nonempty, bounded spans through prompt and JSON schema:** The three-case basic readiness check failed because the no-entity control returned a non-source span. Removing the schema change while retaining only the prompt instruction produced the same failure. Both changes were reverted.

The retained test-harness option `BROWSER_LOCAL_CACHE_ONLY=true` rejects all remote requests, making future cached-model trials auditable without external model downloads.

## Decision

No tested change improved readiness. The current lab extraction and prompt remain at v5. The browser model remains outside Client chat: the fixed synthetic benchmark is far below the required 100% valid output, 95% precision and recall per type and split, and 5-second P95. An independent, annotated holdout is also still missing. Further model or architecture work needs a new blinded holdout before any enablement decision.
