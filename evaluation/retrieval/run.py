"""Evaluate candidate embedding models with the dense retrieval baseline.

  python -m evaluation.retrieval.run                      all candidates, one subprocess each
  python -m evaluation.retrieval.run --models bge-base e5-base
  python -m evaluation.retrieval.run --report-only        rebuild the report from saved results

Per model: load, embed all retrievable chunks (retrieval_text), embed every benchmark query one at a
time, rank all chunks by cosine similarity, score against queries.jsonl. Writes
evaluation/retrieval/results/<model>.json and evaluation/retrieval/results/report.md.
Each model runs in its own process so memory measurements are not polluted by earlier models.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
TOP_K = 10
THREADS = 8  # physical cores on the evaluation machine; fixed for comparable timings


def load_benchmark() -> tuple[dict, list[dict]]:
    lines = (HERE / "queries.jsonl").read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])["_header"]
    return header, [json.loads(line) for line in lines[1:] if line]


class PeakRSS:
    """Samples this process's resident memory every 10 ms while active."""

    def __init__(self):
        import psutil

        self.proc, self.peak, self._stop = psutil.Process(), 0, threading.Event()

    def __enter__(self):
        self.peak = self.proc.memory_info().rss
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            self.peak = max(self.peak, self.proc.memory_info().rss)
            time.sleep(0.01)

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join()
        self.peak = max(self.peak, self.proc.memory_info().rss)


def weights_size_mb(repo: str, revision: str | None) -> float | None:
    from huggingface_hub import scan_cache_dir

    for r in scan_cache_dir().repos:
        if r.repo_id == repo:
            for rev in r.revisions:
                if revision is None or rev.commit_hash == revision:
                    size = sum(f.size_on_disk for f in rev.files if f.file_name.endswith((".safetensors", ".bin")))
                    return round(size / 2**20, 1)
    return None


def counterpart(chunk_id: str, chunks_by_id: dict, uconn: dict) -> str | None:
    """The same section of the other UConn procedures version (for version-confusion diagnostics)."""
    c = chunks_by_id[chunk_id]
    other = uconn.get(c.doc_id)
    if other is None:
        return None
    same = [x for x in chunks_by_id.values() if x.doc_id == other and x.section_path == c.section_path]
    return same[0].chunk_id if len(same) == 1 else None


def evaluate_one(key: str) -> dict:
    import numpy as np
    import psutil
    import torch

    torch.set_num_threads(THREADS)
    from chunking.pipeline import load_chunks
    from retrieval.dense import DenseIndex, encode, load_model, query_text
    from retrieval.embedders import CANDIDATES

    from .metrics import aggregate, case_metrics

    spec = CANDIDATES[key]
    header, cases = load_benchmark()
    chunks = [c for cs in load_chunks().values() for c in cs]
    by_id = {c.chunk_id: c for c in chunks}
    proc = psutil.Process()
    rss0 = proc.memory_info().rss

    t = time.perf_counter()
    with PeakRSS() as mem_load:
        model = load_model(spec)
    load_s = time.perf_counter() - t
    rss_loaded = proc.memory_info().rss
    encode(model, ["warm-up"])

    with PeakRSS() as mem_index:
        index = DenseIndex.build(spec, chunks, model)
    index.save()

    tok = model.tokenizer
    passages = [spec.passage_prefix + c.retrieval_text for c in chunks]
    lengths = [len(tok(p, add_special_tokens=True)["input_ids"]) for p in passages]
    truncated = [c.chunk_id for c, n in zip(chunks, lengths) if n > model.max_seq_length]
    est_ratio = statistics.mean(n / max(c.retrieval_token_count, 1) for c, n in zip(chunks, lengths))

    q_times, per_case, runs = [], [], []
    for case in cases:
        t = time.perf_counter()
        qv = encode(model, [query_text(case["query"], spec)])[0]
        results = index.search(qv, TOP_K)
        q_times.append(time.perf_counter() - t)
        ranked = [r.chunk_id for r in results]
        m = case_metrics(case, ranked)
        per_case.append(m)
        run = {"id": case["id"], "metrics": m, "top": [
            {"rank": r.rank, "chunk_id": r.chunk_id, "score": round(r.score, 5), "doc_id": r.doc_id,
             "organization": r.organization, "section_path": r.section_path, "pages": [r.page_start, r.page_end]}
            for r in results]}
        if case.get("expected_version"):  # version confusion: where does the other version's same section rank?
            scores = index.matrix @ qv
            order = list(np.lexsort((np.arange(len(scores)), -scores)))
            ids = [index.chunks[i].chunk_id for i in order]
            uconn = {header_doc: other for header_doc, other in [
                ("travel-and-entertainment-procedures-final-ccccf9", "2026-07-01-travel-and-entertainment-procedures-ca903b"),
                ("2026-07-01-travel-and-entertainment-procedures-ca903b", "travel-and-entertainment-procedures-final-ccccf9")]}
            gold = case["units"][0]["sources"][0]["chunk_id"]
            twin = counterpart(gold, by_id, uconn)
            run["version_check"] = {"gold_rank": ids.index(gold) + 1, "other_version_chunk": twin,
                                    "other_version_rank": ids.index(twin) + 1 if twin else None}
        runs.append(run)

    return {
        "model": {
            "key": key, "repo": spec.repo, "revision": index.meta["revision"], "why": spec.why, "notes": spec.notes,
            "dimension": index.meta["dimension"], "max_seq_length": model.max_seq_length,
            "parameters_m": round(sum(p.numel() for p in model.parameters()) / 1e6, 1),
            "weights_mb": weights_size_mb(spec.repo, index.meta["revision"]),
            "tokenizer": f"{type(tok).__name__} (vocab {tok.vocab_size})",
            "query_prefix": spec.query_prefix, "passage_prefix": spec.passage_prefix,
            "trust_remote_code": spec.trust_remote_code,
        },
        "environment": {
            "device": "cpu", "cpu": platform.processor(), "torch_threads": torch.get_num_threads(),
            "python": platform.python_version(), "torch": torch.__version__,
            "sentence_transformers": __import__("sentence_transformers").__version__,
        },
        "resources": {
            "load_seconds": round(load_s, 2),
            "rss_before_load_mb": round(rss0 / 2**20), "rss_after_load_mb": round(rss_loaded / 2**20),
            "peak_rss_load_mb": round(mem_load.peak / 2**20), "peak_rss_indexing_mb": round(mem_index.peak / 2**20),
            "chunks_embedded": len(chunks), "index_seconds": index.meta["build_seconds"],
            "chunks_per_second": round(len(chunks) / index.meta["build_seconds"], 1),
            "query_ms_mean": round(1000 * statistics.mean(q_times), 1),
            "query_ms_p95": round(1000 * sorted(q_times)[int(0.95 * (len(q_times) - 1))], 1),
            "index_bytes": int(index.matrix.nbytes),
        },
        "tokenization": {
            "max_passage_tokens": max(lengths), "median_passage_tokens": int(statistics.median(lengths)),
            "truncated_chunks": len(truncated), "truncated_chunk_ids": truncated,
            "model_tokens_per_estimated_token": round(est_ratio, 3),
        },
        "benchmark": {"version": header["benchmark_version"], "cases": len(cases)},
        "metrics": aggregate(per_case, cases),
        "runs": runs,
    }


def main(argv=None) -> int:
    from retrieval.embedders import CANDIDATES

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="*", default=list(CANDIDATES))
    parser.add_argument("--single", help=argparse.SUPPRESS)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    RESULTS.mkdir(exist_ok=True)

    if args.single:
        result = evaluate_one(args.single)
        (RESULTS / f"{args.single}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        o = result["metrics"]["overall"]
        print(f"{args.single}: R@1 {o['recall@1']:.3f} R@5 {o['recall@5']:.3f} R@10 {o['recall@10']:.3f} MRR {o['mrr@10']:.3f}")
        return 0
    if not args.report_only:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "TOKENIZERS_PARALLELISM": "false"}
        for key in args.models:
            code = subprocess.call([sys.executable, "-m", "evaluation.retrieval.run", "--single", key], env=env)
            if code:
                print(f"{key}: FAILED (exit {code})")
    from .report import write_report

    print(write_report(RESULTS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
