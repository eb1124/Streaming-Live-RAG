"""Markdown report from evaluation/retrieval/results/*.json."""

from __future__ import annotations

import json
from pathlib import Path

from .run import load_benchmark

TYPES = ["direct_fact", "paraphrase", "conditional", "exception", "numeric_threshold", "terminology",
         "clause_specific", "cross_section", "version_sensitive"]


def _load(results_dir: Path) -> list[dict]:
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(results_dir.glob("*.json"))]
    return sorted(rows, key=lambda r: -r["metrics"]["overall"]["mrr@10"])


def write_report(results_dir: Path) -> str:
    models = _load(results_dir)
    _, cases = load_benchmark()
    by_case = {c["id"]: c for c in cases}
    out = ["# Dense retrieval: embedding model evaluation", ""]
    env = models[0]["environment"]
    out += [f"Benchmark: {models[0]['benchmark']['version']}, {len(cases)} cases. Device: {env['device']} "
            f"({env['cpu']}), {env['torch_threads']} threads, torch {env['torch']}, "
            f"sentence-transformers {env['sentence_transformers']}. Top-k = 10, exact cosine search.", ""]

    out += ["## Models", "", "| model | repo @ revision | params (M) | weights (MB) | dim | max tokens | tokenizer | prefixes |",
            "|---|---|---|---|---|---|---|---|"]
    for m in models:
        md = m["model"]
        pre = f"q:`{md['query_prefix'].strip()}` p:`{md['passage_prefix'].strip()}`" if (md["query_prefix"] or md["passage_prefix"]) else "none"
        out.append(f"| {md['key']} | {md['repo']} @ {str(md['revision'])[:10]} | {md['parameters_m']} | {md['weights_mb']} | "
                   f"{md['dimension']} | {md['max_seq_length']} | {md['tokenizer']} | {pre} |")
    out.append("")

    out += ["## Retrieval quality (all cases)", "", "| model | R@1 | R@3 | R@5 | R@10 | MRR@10 | AllUnits@10 |", "|---|---|---|---|---|---|---|"]
    for m in models:
        o = m["metrics"]["overall"]
        out.append(f"| {m['model']['key']} | {o['recall@1']:.3f} | {o['recall@3']:.3f} | {o['recall@5']:.3f} | "
                   f"{o['recall@10']:.3f} | {o['mrr@10']:.3f} | {o['all_units@10']:.3f} |")
    out.append("")

    out += ["## By question type (Recall@5 / MRR@10)", "", "| type | n | " + " | ".join(m["model"]["key"] for m in models) + " |",
            "|---|---|" + "---|" * len(models)]
    for t in TYPES + sorted({k for m in models for k in m["metrics"]["by_type"] if k.startswith("+")}):
        row = models[0]["metrics"]["by_type"].get(t)
        if not row:
            continue
        cells = []
        for m in models:
            s = m["metrics"]["by_type"][t]
            cells.append(f"{s['recall@5']:.2f} / {s['mrr@10']:.2f}")
        out.append(f"| {t} | {row['n']} | " + " | ".join(cells) + " |")
    out.append("")

    out += ["## Resources (CPU)", "", "| model | load s | index 466 chunks s | chunks/s | query ms (mean / p95) | RSS after load MB | peak RSS indexing MB | index KB |",
            "|---|---|---|---|---|---|---|---|"]
    for m in models:
        r = m["resources"]
        out.append(f"| {m['model']['key']} | {r['load_seconds']} | {r['index_seconds']} | {r['chunks_per_second']} | "
                   f"{r['query_ms_mean']} / {r['query_ms_p95']} | {r['rss_after_load_mb']} | {r['peak_rss_indexing_mb']} | {r['index_bytes'] // 1024} |")
    out.append("")

    out += ["## Tokenization", "", "| model | max passage tokens | median | truncated chunks | model tokens per estimated token |", "|---|---|---|---|---|"]
    for m in models:
        t = m["tokenization"]
        out.append(f"| {m['model']['key']} | {t['max_passage_tokens']} | {t['median_passage_tokens']} | {t['truncated_chunks']} | {t['model_tokens_per_estimated_token']} |")
    out.append("")

    out += ["## Version-sensitive cases (UConn procedures)", "",
            "Rank of the expected version's chunk vs the same section of the other version.", "",
            "| case | expected | " + " | ".join(m["model"]["key"] for m in models) + " |", "|---|---|" + "---|" * len(models)]
    for cid in [c["id"] for c in cases if c.get("expected_version")]:
        cells = []
        for m in models:
            v = next(r for r in m["runs"] if r["id"] == cid).get("version_check", {})
            cells.append(f"{v.get('gold_rank')} vs {v.get('other_version_rank') or '—'}")
        out.append(f"| {cid} | {by_case[cid]['expected_version']} | " + " | ".join(cells) + " |")
    out.append("")

    out += ["## Misses", "", "Cases where a model did not retrieve every evidence unit within the top 10 "
            "(✗ = no unit found, ◐ = some units found).", "", "| case | type | " + " | ".join(m["model"]["key"] for m in models) + " |",
            "|---|---|" + "---|" * len(models)]
    for c in cases:
        marks, any_miss = [], False
        for m in models:
            run = next(r for r in m["runs"] if r["id"] == c["id"])
            rec = run["metrics"]["recall@10"]
            marks.append("✓" if rec == 1 else ("◐" if rec > 0 else "✗"))
            any_miss |= rec < 1
        if any_miss:
            out.append(f"| {c['id']} | {c['question_type']} | " + " | ".join(marks) + " |")
    out.append("")
    report = "\n".join(out)
    (results_dir / "report.md").write_text(report + "\n", encoding="utf-8")
    return report
