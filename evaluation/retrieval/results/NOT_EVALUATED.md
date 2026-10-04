# Candidates that could not be evaluated (2026-09-25)

| model | outcome | cause |
|---|---|---|
| nomic-ai/nomic-embed-text-v1.5 @ e9b67630 | failed at first encode | Its remote modeling code (nomic-ai/nomic-bert-2048 @ 7710840) calls `get_extended_attention_mask`, which transformers 5.17 no longer provides (`AttributeError`). Using it would mean pinning transformers < 5 or patching third-party remote code. |
| mixedbread-ai/mxbai-embed-large-v1 @ b33106f5 | did not finish (killed after ~60 min; retry hit the 25-min cap) | 335M params, fp32 ≈ 1.3 GB of weights. With 1.2–2.3 GB of RAM free on this 11.7 GB machine (Chrome and VS Code open), the process paged and embedded far slower than its size predicts. Its quality was not measured. |

Re-run either model with `python -m evaluation.retrieval.run --models <key>` on a machine with more free memory
(mxbai) or in an environment with transformers < 5 (nomic).
