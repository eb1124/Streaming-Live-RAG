"""BM25-only vs dense-only (arctic-m) vs hybrid (RRF) on the frozen benchmark.

  python -m evaluation.retrieval.compare

Uses queries.jsonl and metrics.py unchanged. Dense reuses the saved arctic-m index (data/index/arctic-m,
rebuilt if stale) and must reproduce the rankings stored in results/arctic-m.json exactly (checked).
Writes results/methods/<method>.json and results/comparison.md.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from .metrics import KS, aggregate, case_metrics
from .run import RESULTS, THREADS, load_benchmark

METHODS_DIR = RESULTS / "methods"
DENSE_KEY = "arctic-m"
TOP_K = 10
TYPES = ["direct_fact", "paraphrase", "conditional", "exception", "numeric_threshold", "terminology",
         "clause_specific", "cross_section", "version_sensitive"]


def run_methods() -> dict[str, dict]:
    import torch

    torch.set_num_threads(THREADS)
    from chunking.pipeline import load_chunks
    from retrieval.bm25 import BM25Index
    from retrieval.dense import DenseIndex, encode, load_model, query_text
    from retrieval.embedders import CANDIDATES
    from retrieval.hybrid import FUSION_DEPTH, RRF_K, HybridRetriever

    header, cases = load_benchmark()
    chunks = [c for cs in load_chunks().values() for c in cs]
    spec = CANDIDATES[DENSE_KEY]
    model = load_model(spec)
    try:
        dense_index = DenseIndex.load(DENSE_KEY, chunks)
    except (RuntimeError, FileNotFoundError):
        dense_index = DenseIndex.build(spec, chunks, model)
        dense_index.save()
    t = time.perf_counter()
    bm25 = BM25Index(dense_index.chunks)
    bm25_build = time.perf_counter() - t
    hybrid = HybridRetriever(dense_index, model, bm25)

    def dense_search(q):
        return dense_index.search(encode(model, [query_text(q, spec)])[0], TOP_K)

    methods = {
        "bm25": (lambda q: bm25.search(q, TOP_K),
                 {"analyzer": "lowercase, thousands joined, stopwords, snowball english", "k1": bm25.k1, "b": bm25.b,
                  "field": "retrieval_text", "vocabulary": len(bm25.idf), "build_ms": round(1000 * bm25_build, 1)}),
        "dense-arctic-m": (dense_search, {"model": spec.repo, "revision": spec.revision, "field": "retrieval_text"}),
        "hybrid-rrf": (lambda q: hybrid.retrieve(q, TOP_K),
                       {"dense": DENSE_KEY, "lexical": "bm25", "rrf_k": RRF_K, "fusion_depth": FUSION_DEPTH}),
    }
    out = {}
    for name, (search, config) in methods.items():
        per_case, runs, times = [], [], []
        for case in cases:
            t = time.perf_counter()
            results = search(case["query"])
            times.append(time.perf_counter() - t)
            m = case_metrics(case, [r.chunk_id for r in results])
            per_case.append(m)
            run = {"id": case["id"], "metrics": m, "top": [
                {"rank": r.rank, "chunk_id": r.chunk_id, "score": round(r.score, 6), "doc_id": r.doc_id,
                 "section_path": r.section_path, "pages": [r.page_start, r.page_end]} for r in results]}
            if name == "hybrid-rrf":  # component ranks of the gold chunks, for diagnosing fusion
                fused = {hybrid.chunks[f.index].chunk_id: f.ranks for f in hybrid.fuse(case["query"])}
                run["gold_component_ranks"] = {s["chunk_id"]: fused.get(s["chunk_id"], {})
                                               for u in case["units"] for s in u["sources"]}
            runs.append(run)
        times.sort()
        out[name] = {"method": name, "config": config, "benchmark": header["benchmark_version"],
                     "query_ms_mean": round(1000 * sum(times) / len(times), 2),
                     "query_ms_p95": round(1000 * times[int(0.95 * (len(times) - 1))], 2),
                     "metrics": aggregate(per_case, cases), "runs": runs}
    return out


def check_dense_reproduces(results: dict) -> str:
    saved = RESULTS / f"{DENSE_KEY}.json"
    if not saved.exists():
        return "no saved dense results to compare"
    old = {r["id"]: [t["chunk_id"] for t in r["top"]] for r in json.loads(saved.read_text(encoding="utf-8"))["runs"]}
    new = {r["id"]: [t["chunk_id"] for t in r["top"]] for r in results["dense-arctic-m"]["runs"]}
    diff = [k for k in new if old.get(k) != new[k]]
    return "identical top-10 rankings for all cases" if not diff else f"DIFFERS for {diff}"


def write_comparison(results: dict, determinism: str) -> str:
    _, cases = load_benchmark()
    names = list(results)
    out = ["# BM25 vs dense vs hybrid (RRF)", "",
           f"Benchmark {results['bm25']['benchmark']}, {len(cases)} cases, top-10. Dense = {DENSE_KEY}. "
           f"Hybrid = RRF(k={results['hybrid-rrf']['config']['rrf_k']}) over the top "
           f"{results['hybrid-rrf']['config']['fusion_depth']} of each. Determinism check (dense vs saved run): {determinism}.", ""]
    out += ["## Overall", "", "| method | " + " | ".join(f"R@{k}" for k in KS) + " | MRR@10 | AllUnits@10 | query ms (mean/p95) |",
            "|---|" + "---|" * (len(KS) + 3)]
    for n in names:
        o = results[n]["metrics"]["overall"]
        out.append(f"| {n} | " + " | ".join(f"{o[f'recall@{k}']:.3f}" for k in KS)
                   + f" | {o['mrr@10']:.3f} | {o['all_units@10']:.3f} | {results[n]['query_ms_mean']} / {results[n]['query_ms_p95']} |")
    out.append("")
    for metric, label in (("recall@1", "Recall@1"), ("recall@5", "Recall@5"), ("mrr@10", "MRR@10")):
        out += [f"## By question type: {label}", "", "| type | n | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names)]
        for t in TYPES:
            row = results["bm25"]["metrics"]["by_type"].get(t)
            if row:
                out.append(f"| {t} | {row['n']} | " + " | ".join(f"{results[n]['metrics']['by_type'][t][metric]:.2f}" for n in names) + " |")
        out.append("")
    out += ["## Per case: rank of the first gold chunk (— = not in top 10)", "",
            "| case | type | " + " | ".join(names) + " | hybrid: gold ranks in (dense, bm25) |", "|---|---|" + "---|" * (len(names) + 1)]
    by_run = {n: {r["id"]: r for r in results[n]["runs"]} for n in names}
    for c in cases:
        ranks = []
        for n in names:
            m = by_run[n][c["id"]]["metrics"]
            r = m["first_gold_rank"]
            cell = str(r) if r else "—"
            if m["recall@10"] not in (0.0, 1.0):
                cell += " (◐)"
            ranks.append(cell)
        comp = by_run["hybrid-rrf"][c["id"]]["gold_component_ranks"]
        best = min(comp.values(), key=lambda d: min(d.values()) if d else 999, default={})
        comp_cell = f"({best.get('dense', '>100')}, {best.get('bm25', '>100')})" if best else "(>100, >100)"
        if any(x != "1" for x in ranks):
            out.append(f"| {c['id']} | {c['question_type']} | " + " | ".join(ranks) + f" | {comp_cell} |")
    out += ["", "Cases where every method ranked a gold chunk first are omitted. ◐ = only some evidence units in the top 10.", ""]
    out += ["## Misses (not every evidence unit in the top 10)", "", "| case | type | query | " + " | ".join(names) + " |",
            "|---|---|---|" + "---|" * len(names)]
    for c in cases:
        marks = []
        for n in names:
            rec = by_run[n][c["id"]]["metrics"]["recall@10"]
            marks.append("✓" if rec == 1 else ("◐" if rec > 0 else "✗"))
        if "✓" != marks[0] or len(set(marks)) > 1:
            out.append(f"| {c['id']} | {c['question_type']} | {c['query'][:70]} | " + " | ".join(marks) + " |")
    report = "\n".join(out) + "\n"
    (RESULTS / "comparison.md").write_text(report, encoding="utf-8")
    return report


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    results = run_methods()
    METHODS_DIR.mkdir(parents=True, exist_ok=True)
    for name, r in results.items():
        (METHODS_DIR / f"{name}.json").write_text(json.dumps(r, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(write_comparison(results, check_dense_reproduces(results)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
