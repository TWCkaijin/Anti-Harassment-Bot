# Third-party notices

This project is licensed under Apache-2.0 (see [LICENSE](LICENSE)). The components below are
distributed under their own terms.

## Dependency license summary

Checked 2026-11-24 from the installed lock files (`pnpm-lock.yaml`, `uv.lock`). Re-check after
dependency updates with `pnpm licenses list --prod` and the package metadata in `.venv`.

| Ecosystem | Licenses |
| --- | --- |
| Frontend runtime (`pnpm licenses list --prod`, 197 packages) | MIT 125, Apache-2.0 53, BSD-3-Clause 10, ISC 8, 0BSD 1 |
| Python (runtime and dev, 80 packages) | Apache-2.0, MIT, BSD (2/3-clause), PSF-2.0; `certifi` is MPL-2.0 and `tqdm` is MPL-2.0 AND MIT |

No dependency is under a strong copyleft license. MPL-2.0 is file-level copyleft: the two
packages are used unmodified, so only their notices need to be kept if they are redistributed.
Deployed Functions install Python packages from the locked requirements at build time; the
repository does not vendor them.

## External services and data

| Item | Terms |
| --- | --- |
| OpenRouter and the models it routes to | Each provider's terms of service and usage policy |
| Embedding model `intfloat/multilingual-e5-large` (called through OpenRouter) | MIT (model card) |
| Firebase / Google Cloud | Google Cloud terms |
| Law texts and judgments in the knowledge base (`data/`, `resource/legal/`, not in Git) | Taiwan government open data terms of each source; see `data/README.md` |

## CKIP ALBERT Tiny Chinese NER

| Item | Value |
| --- | --- |
| Model | [`ckiplab/albert-tiny-chinese-ner`](https://huggingface.co/ckiplab/albert-tiny-chinese-ner) |
| Pinned revision | `bcb519856ca93a666b1e48a9daef3f88c9b572a0` |
| Author | Mu Yang at [CKIP](https://ckip.iis.sinica.edu.tw), Academia Sinica ([ckip-transformers](https://github.com/ckiplab/ckip-transformers)) |
| License | GPL-3.0 (model card `license: gpl-3.0`, checked 2026-11-24) |
| Used by | Admin "本機遮罩驗證" page only (`admin/frontend/src/masking-lab/`). Not used by the Client chat. |

**The weights are not in this repository.** `admin/frontend/scripts/prepare_tiny_ner.py` downloads
the pinned revision from Hugging Face, checks its SHA-256, and converts it to ONNX in the local,
Git-ignored folder `admin/frontend/.masking-tiny/`. The ONNX file is a converted form of the
original weights. That script documents the conversion.

**Distribution.** Production Admin builds never include the model. Preview builds include it only
when `MASKING_TINY_BUNDLE=1` is set at build time (see
[docs/browser-masking/README.md](docs/browser-masking/README.md)). Anyone who hosts or otherwise
distributes the model files must comply with GPL-3.0: ship the
[GPL-3.0 license text](https://www.gnu.org/licenses/gpl-3.0.html) and the author attribution above,
state that the ONNX file is a modified (converted) form, and make the original weights and the
conversion script available to recipients.

Before adopting this model in any user-facing feature, the project owner should confirm that
GPL-3.0 is acceptable or switch to a permissively licensed model.
