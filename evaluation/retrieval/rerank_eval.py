"""Dense-only vs hybrid (RRF) vs hybrid + cross-encoder reranking on the frozen benchmark.

  python -m evaluation.retrieval.rerank_eval

queries.jsonl, metrics.py, BM25, dense and RRF are used unchanged. Dense and hybrid rankings are checked
against results/methods/{dense-arctic-m,hybrid-rrf}.json (from compare.py). Writes
results/methods/hybrid-rrf-rerank.json and results/rerank.md.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time

from .compare import METHODS_DIR, TYPES
from .metrics import KS, aggregate, case_metrics
from .run import RESULTS, THREADS, PeakRSS, load_benchmark

TOP_K = 10


def gold_ids(case: dict) -> set[str]:
    return {s["chunk_id"] for u in case["units"] for s in u["sources"]}


def run() -> dict:
    import psutil
    import torch

    torch.set_num_threads(THREADS)
    from chunking.pipeline import load_chunks
    from retrieval.bm25 import BM25Index
    from retrieval.dense import DenseIndex, encode, load_model, query_text
    from retrieval.embedders import CANDIDATES
    from retrieval.hybrid import FUSION_DEPTH, RRF_K, HybridRetriever
    from retrieval.rerank import (BATCH_SIZE, CANDIDATE_POOL, MAX_LENGTH, RERANKER_REPO, RERANKER_REVISION,
                                  RerankedHybridRetriever, load_reranker)

    proc = psutil.Process()
    _, cases = load_benchmark()
    chunks = [c for cs in load_chunks().values() for c in cs]
    spec = CANDIDATES["arctic-m"]
    model = load_model(spec)
    dense = DenseIndex.load("arctic-m", chunks)
    hybrid = HybridRetriever(dense, model, BM25Index(dense.chunks))

    rss_before = proc.memory_info().rss
    t = time.perf_counter()
    with PeakRSS() as mem_load:
        reranker = load_reranker()
    load_s = time.perf_counter() - t
    rss_after = proc.memory_info().rss
    reranker.predict([("warm-up", "warm-up")], show_progress_bar=False)
    rr = RerankedHybridRetriever(hybrid, reranker)
    ids = [c.chunk_id for c in hybrid.chunks]
    tok = reranker.tokenizer

    per = {"dense-arctic-m": [], "hybrid-rrf": [], "hybrid-rrf-rerank": []}
    runs = {k: [] for k in per}
    timing = {"dense_ms": [], "hybrid_ms": [], "candidates_ms": [], "rerank_ms": []}
    movement, pair_lengths = [], []
    with PeakRSS() as mem_rerank:
        for case in cases:
            q = case["query"]
            t = time.perf_counter()
            dense_top = [r.chunk_id for r in dense.search(encode(model, [query_text(q, spec)])[0], TOP_K)]
            timing["dense_ms"].append(time.perf_counter() - t)
            t = time.perf_counter()
            fused = hybrid.fuse(q)
            timing["hybrid_ms"].append(time.perf_counter() - t)
            hybrid_top = [ids[f.index] for f in fused[:TOP_K]]
            t = time.perf_counter()
            pool = rr.candidates(q)
            timing["candidates_ms"].append(time.perf_counter() - t)
            t = time.perf_counter()
            reranked = rr.rerank(q, pool)
            timing["rerank_ms"].append(time.perf_counter() - t)
            rerank_top = [ids[r.index] for r in reranked[:TOP_K]]
            pair_lengths += [len(tok(q, hybrid.chunks[i].retrieval_text)["input_ids"]) for i in pool]

            for name, ranked in (("dense-arctic-m", dense_top), ("hybrid-rrf", hybrid_top), ("hybrid-rrf-rerank", rerank_top)):
                m = case_metrics(case, ranked)
                per[name].append(m)
                entry = {"id": case["id"], "metrics": m, "top": ranked}
                if name == "hybrid-rrf-rerank":
                    entry["scores"] = [round(r.score, 5) for r in reranked[:TOP_K]]
                    entry["hybrid_ranks"] = [r.hybrid_rank for r in reranked[:TOP_K]]
                runs[name].append(entry)

            gold = gold_ids(case)
            before = {ids[i]: r for r, i in enumerate(pool, 1)}
            after = {ids[r.index]: pos for pos, r in enumerate(reranked, 1)}
            movement.append({
                "id": case["id"],
                "gold_in_pool": sorted((cid, before[cid], after[cid]) for cid in gold if cid in before),
                "gold_outside_pool": sorted(cid for cid in gold if cid not in before),
                "first_before": min((before[c] for c in gold if c in before), default=None),
                "first_after": min((after[c] for c in gold if c in after), default=None),
            })

    def ms(xs):
        return {"mean": round(1000 * statistics.mean(xs), 1), "p95": round(1000 * sorted(xs)[int(0.95 * (len(xs) - 1))], 1)}

    return {
        "config": {"reranker": RERANKER_REPO, "revision": RERANKER_REVISION, "candidate_pool": CANDIDATE_POOL,
                   "max_length": MAX_LENGTH, "batch_size": BATCH_SIZE, "passage_field": "retrieval_text",
                   "tie_break": "hybrid rank", "hybrid": {"dense": "arctic-m", "rrf_k": RRF_K, "fusion_depth": FUSION_DEPTH},
                   "parameters_m": round(sum(p.numel() for p in reranker.model.parameters()) / 1e6, 1),
                   "tokenizer": f"{type(tok).__name__} (vocab {tok.vocab_size})", "torch_threads": torch.get_num_threads()},
        "resources": {"reranker_load_s": round(load_s, 2), "rss_before_reranker_mb": round(rss_before / 2**20),
                      "rss_after_reranker_mb": round(rss_after / 2**20), "reranker_rss_delta_mb": round((rss_after - rss_before) / 2**20),
                      "peak_rss_load_mb": round(mem_load.peak / 2**20), "peak_rss_eval_mb": round(mem_rerank.peak / 2**20),
                      "pairs_scored": len(pair_lengths), "max_pair_tokens": max(pair_lengths),
                      "median_pair_tokens": int(statistics.median(pair_lengths)),
                      "truncated_pairs": sum(1 for n in pair_lengths if n > MAX_LENGTH),
                      **{k: ms(v) for k, v in timing.items()}},
        "metrics": {k: aggregate(v, cases) for k, v in per.items()},
        "runs": runs,
        "movement": movement,
    }


def consistency(result: dict) -> list[str]:
    notes = []
    for name in ("dense-arctic-m", "hybrid-rrf"):
        path = METHODS_DIR / f"{name}.json"
        if not path.exists():
            notes.append(f"{name}: no saved run to compare")
            continue
        saved = {r["id"]: [t["chunk_id"] for t in r["top"]] for r in json.loads(path.read_text(encoding="utf-8"))["runs"]}
        now = {r["id"]: r["top"] for r in result["runs"][name]}
        diff = [k for k in now if saved.get(k) != now[k]]
        notes.append(f"{name}: {'identical to saved rankings' if not diff else f'DIFFERS for {diff}'}")
    return notes


def write_report(result: dict, notes: list[str]) -> str:
    _, cases = load_benchmark()
    names = ["dense-arctic-m", "hybrid-rrf", "hybrid-rrf-rerank"]
    cfg, res, mv = result["config"], result["resources"], {m["id"]: m for m in result["movement"]}
    out = ["# Cross-encoder reranking", "",
           f"Reranker {cfg['reranker']} @ {cfg['revision'][:10]} ({cfg['parameters_m']}M params, max {cfg['max_length']} tokens), "
           f"candidate pool = top {cfg['candidate_pool']} of hybrid RRF (arctic-m + BM25, k={cfg['hybrid']['rrf_k']}). "
           f"Benchmark: {len(cases)} cases, top-10. Consistency: " + "; ".join(notes) + ".", ""]
    out += ["## Overall", "", "| method | " + " | ".join(f"R@{k}" for k in KS) + " | MRR@10 | AllUnits@10 |", "|---|" + "---|" * (len(KS) + 2)]
    for n in names:
        o = result["metrics"][n]["overall"]
        out.append(f"| {n} | " + " | ".join(f"{o[f'recall@{k}']:.3f}" for k in KS) + f" | {o['mrr@10']:.3f} | {o['all_units@10']:.3f} |")
    out.append("")
    for metric, label in (("recall@1", "Recall@1"), ("recall@3", "Recall@3"), ("mrr@10", "MRR@10")):
        out += [f"## By question type: {label}", "", "| type | n | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names)]
        for t in TYPES:
            row = result["metrics"]["hybrid-rrf"]["by_type"].get(t)
            if row:
                out.append(f"| {t} | {row['n']} | " + " | ".join(f"{result['metrics'][n]['by_type'][t][metric]:.2f}" for n in names) + " |")
        out.append("")

    ups = [m for m in result["movement"] if m["first_before"] and m["first_after"] and m["first_after"] < m["first_before"]]
    downs = [m for m in result["movement"] if m["first_before"] and m["first_after"] and m["first_after"] > m["first_before"]]
    same = [m for m in result["movement"] if m["first_before"] and m["first_before"] == m["first_after"]]
    absent = [m for m in result["movement"] if not m["first_before"]]
    chunk_moves = [(a, b) for m in result["movement"] for _, a, b in m["gold_in_pool"]]
    out += ["## Movement of gold evidence (within the candidate pool)", "",
            f"* Case level (first gold chunk): moved up in {len(ups)}, down in {len(downs)}, unchanged in {len(same)}; "
            f"no gold chunk in the pool for {len(absent)} ({', '.join(m['id'] for m in absent) or 'none'}).",
            f"* Chunk level (every gold chunk in a pool, {len(chunk_moves)} total): up {sum(1 for a, b in chunk_moves if b < a)}, "
            f"down {sum(1 for a, b in chunk_moves if b > a)}, unchanged {sum(1 for a, b in chunk_moves if b == a)}.", ""]
    hyb = {r["id"]: r["metrics"] for r in result["runs"]["hybrid-rrf"]}
    rer = {r["id"]: r["metrics"] for r in result["runs"]["hybrid-rrf-rerank"]}
    pushed = [c for c in cases if hyb[c["id"]]["recall@10"] > rer[c["id"]]["recall@10"]]
    corrected = [c for c in cases if (hyb[c["id"]]["first_gold_rank"] or 99) > 3 and (rer[c["id"]]["first_gold_rank"] or 99) <= 3]
    worsened = [c for c in cases if (hyb[c["id"]]["first_gold_rank"] or 99) <= 3 < (rer[c["id"]]["first_gold_rank"] or 99)]
    out += ["### Hybrid had the evidence in the top 10, reranking pushed (some of) it out", ""]
    out += [f"* {c['id']} ({c['question_type']}): hybrid rank {hyb[c['id']]['first_gold_rank']}, reranked "
            f"{rer[c['id']]['first_gold_rank'] or '>10'}; pool ranks (before→after) {mv[c['id']]['gold_in_pool']}" for c in pushed] or ["* none"]
    out += ["", "### Reranking corrected a poor hybrid ranking (hybrid first gold > 3 → reranked ≤ 3)", ""]
    out += [f"* {c['id']} ({c['question_type']}): {hyb[c['id']]['first_gold_rank'] or '>10'} → {rer[c['id']]['first_gold_rank']}" for c in corrected] or ["* none"]
    out += ["", "### Reranking demoted a good hybrid ranking (hybrid first gold ≤ 3 → reranked > 3)", ""]
    out += [f"* {c['id']} ({c['question_type']}): {hyb[c['id']]['first_gold_rank']} → {rer[c['id']]['first_gold_rank'] or '>10'}" for c in worsened] or ["* none"]
    out += ["", "## Per case: rank of the first gold chunk (cases where any method is not at rank 1)", "",
            "| case | type | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names)]
    by = {n: {r["id"]: r["metrics"] for r in result["runs"][n]} for n in names}
    for c in cases:
        cells = []
        for n in names:
            m = by[n][c["id"]]
            cell = str(m["first_gold_rank"] or "—") + (" ◐" if 0 < m["recall@10"] < 1 else "")
            cells.append(cell)
        if any(x != "1" for x in cells):
            out.append(f"| {c['id']} | {c['question_type']} | " + " | ".join(cells) + " |")
    out += ["", "— = no gold chunk in the top 10; ◐ = only some evidence units in the top 10.", "",
            "## Misses (not every evidence unit in the top 10)", "", "| case | type | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names)]
    for c in cases:
        marks = ["✓" if by[n][c["id"]]["recall@10"] == 1 else ("◐" if by[n][c["id"]]["recall@10"] > 0 else "✗") for n in names]
        if any(m != "✓" for m in marks):
            out.append(f"| {c['id']} | {c['question_type']} | " + " | ".join(marks) + " |")
    out += ["", "## Runtime and memory (CPU)", "",
            f"* Reranker load: {res['reranker_load_s']} s; RSS {res['rss_before_reranker_mb']} → {res['rss_after_reranker_mb']} MB "
            f"(+{res['reranker_rss_delta_mb']} MB, with arctic-m already loaded); peak RSS during evaluation {res['peak_rss_eval_mb']} MB.",
            f"* Per query (mean / p95 ms): dense {res['dense_ms']['mean']} / {res['dense_ms']['p95']}; hybrid {res['hybrid_ms']['mean']} / {res['hybrid_ms']['p95']}; "
            f"candidate pool {res['candidates_ms']['mean']} / {res['candidates_ms']['p95']}; cross-encoder on {cfg['candidate_pool']} pairs "
            f"{res['rerank_ms']['mean']} / {res['rerank_ms']['p95']}.",
            f"* Pairs scored: {res['pairs_scored']}; tokens per pair: median {res['median_pair_tokens']}, max {res['max_pair_tokens']}; "
            f"truncated (> {cfg['max_length']}): {res['truncated_pairs']}.", ""]
    report = "\n".join(out) + "\n"
    (RESULTS / "rerank.md").write_text(report, encoding="utf-8")
    return report


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    result = run()
    notes = consistency(result)
    METHODS_DIR.mkdir(parents=True, exist_ok=True)
    (METHODS_DIR / "hybrid-rrf-rerank.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(write_report(result, notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
