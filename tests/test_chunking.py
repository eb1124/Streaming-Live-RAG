"""Chunking on small synthetic documents: each test isolates one rule."""

import json
import re

from chunking import config as C
from chunking.chunker import chunk_document
from chunking.pipeline import run as run_pipeline
from chunking.render import boundary_spans
from chunking.tokens import count_tokens

from chunk_helpers import H, L, P, T, make_doc, sentence


def para(i: int, n: int = 5) -> str:
    return " ".join(sentence(i * 100 + k) for k in range(n))


def chunks_of(specs, **kw):
    return chunk_document(make_doc(specs, **kw)).chunks


def by_block(chunks):
    out = {}
    for c in chunks:
        for bid in c.source_block_ids:
            out.setdefault(bid, []).append(c)
    return out


# ---------------------------------------------------------------- identity and links


def test_chunk_ids_are_deterministic():
    specs = [H("Policy"), H("A", 2), P(para(1)), H("B", 2), P(para(2)), P(para(3), page=2)]
    first = [c.chunk_id for c in chunks_of(specs)]
    second = [c.chunk_id for c in chunks_of(specs)]  # rebuilt from scratch
    assert first == second
    assert len(set(first)) == len(first)
    assert all(cid.startswith("test-doc::") for cid in first)


def test_prev_next_links_form_one_chain():
    chunks = chunks_of([H("Policy"), H("A", 2), P(para(1)), H("B", 2), P(para(2)), H("C", 2), P(para(3))])
    assert len(chunks) == 3
    assert chunks[0].prev_chunk_id is None and chunks[-1].next_chunk_id is None
    for a, b in zip(chunks, chunks[1:]):
        assert a.next_chunk_id == b.chunk_id and b.prev_chunk_id == a.chunk_id
    assert [c.ordinal for c in chunks] == [0, 1, 2]


def test_metadata_is_copied_not_invented():
    doc = make_doc([H("Policy"), P(para(1))], domain="travel_expenses", effective_date="2026-07-01")
    (c,) = chunk_document(doc).chunks
    assert (c.title, c.organization, c.domain, c.effective_date) == ("Test Policy", "Test University", "travel_expenses", "2026-07-01")
    assert c.version is None and c.superseded_date is None and c.is_current is None and c.source_url is None


# ---------------------------------------------------------------- structure


def test_section_boundaries():
    chunks = chunks_of([H("Policy"), H("Approvals", 2), P(para(1)), H("Receipts", 2), P(para(2))])
    assert [c.section_path for c in chunks] == [["Policy", "Approvals"], ["Policy", "Receipts"]]
    assert "Sentence 100" in chunks[0].text and "Sentence 200" not in chunks[0].text
    assert chunks[0].context_header.endswith("Approvals")  # the path, not repeated as the title


def test_clause_is_kept_with_its_subclauses():
    chunks = chunks_of([
        H("Policy"), H("1. Scope", 2),
        L("1.1 " + para(1)),
        L("1.2 The following requirements apply:"), L("1.2.1 " + para(2, 3)), L("1.2.2 " + para(3, 3)),
        L("1.3 " + para(4)),
    ])
    owner = by_block(chunks)
    head, sub1, sub2 = "p1-b3", "p1-b4", "p1-b5"
    assert owner[head] == owner[sub1] == owner[sub2]
    c = owner[head][0]
    assert c.clause_id == "1.2" and c.clause_ids == ["1.2", "1.2.1", "1.2.2"]
    assert c.section_path == ["Policy", "1. Scope", "1.2"]
    assert owner["p1-b2"][0] is not c and owner["p1-b6"][0] is not c


def test_clause_between_target_and_max_is_not_split():
    text = "4.2 " + para(1, 12)  # ~280 tokens: above TARGET, below MAX
    assert C.TARGET_TOKENS < count_tokens(text) < C.MAX_TOKENS - 40
    chunks = chunks_of([H("Policy"), L("4.1 " + para(0, 12)), L(text), L("4.3 " + para(2, 12))])
    assert [c.clause_id for c in chunks] == ["4.1", "4.2", "4.3"]
    assert all(c.split == "structural" for c in chunks)


def test_small_sibling_clauses_are_grouped():
    defs = [L(f"4.{i} Term{i}: {sentence(i, 30)}") for i in range(1, 9)]  # ~37 tokens each
    chunks = chunks_of([H("Policy"), H("4. Definitions", 2), *defs])
    assert 1 < len(chunks) < 8
    assert all(c.split == "grouped" and c.section_path == ["Policy", "4. Definitions"] for c in chunks)
    assert [i for c in chunks for i in c.clause_ids] == [f"4.{i}" for i in range(1, 9)]
    assert all(count_tokens(c.text) <= C.TARGET_TOKENS for c in chunks)


def test_numbered_headings_nest_by_number_not_level():
    # Rutgers: "11.4" and "11.4.9" are headings of the same visual level.
    chunks = chunks_of([
        H("Procedures", 2), H("11.4 Transportation", 3), P(para(1)), H("11.4.9 Car Rental", 3), P(para(2)),
        H("11.5 Lodging", 3), P(para(3)),
    ])
    paths = {c.clause_id: c.section_path for c in chunks}
    assert paths["11.4.9"] == ["Procedures", "11.4 Transportation", "11.4.9 Car Rental"]
    assert paths["11.5"] == ["Procedures", "11.5 Lodging"]


def test_clause_closes_a_numbered_heading_it_does_not_belong_to():
    # Oregon: "5.6." is a heading, "5.7." a list item; 5.7 is a sibling of 5.6, not its child.
    chunks = chunks_of([
        H("Policy"), H("5. Responsibilities", 2), H("5.6. Methods", 4), L("5.6.1. " + para(1)),
        L("5.7. Conflict of Interest"), L("5.7.1. " + para(2)),
    ])
    c57 = next(c for c in chunks if "5.7.1" in c.clause_ids)
    assert c57.section_path == ["Policy", "5. Responsibilities", "5.7. Conflict of Interest"]
    assert "5.7. Conflict of Interest" not in c57.text  # a title-only clause lives in the path


def test_integer_numbers_are_clauses_only_when_they_are_provisions():
    penn = chunks_of([H("POLICY"), L("1 Procurement Services is responsible for ensuring compliance with University procurement policy.", marker="1"),
                      L("2 Authorization to suppliers must be made through an approved University Purchase Order.", marker="2")])
    assert penn[0].clause_ids == ["1", "2"]
    listing = chunks_of([H("Purpose"), P("The policy requires that expenditures be:"), L("1. Reasonable and necessary"), L("2. Allocable")])
    assert listing[0].clause_ids == [] and listing[0].clause_id is None


def test_out_of_sequence_number_stays_in_its_clause():
    # UT: "11.3.3" printed inside 8.3, while Standard 11 does have an 11.3 (so the number is anchored).
    res = chunk_document(make_doc([H("Standard 8: Malware", 2), L("8.3 Any personally owned device must be protected and"),
                                   L("11.3.3 must be verifiably configured to report its status."), L("8.4 " + para(1)),
                                   H("Standard 11: Safeguarding Data", 2), L("11.3 " + para(2))]))
    owner = by_block(res.chunks)
    assert owner["p1-b1"] == owner["p1-b2"]
    assert any(d["code"] == "out_of_sequence_clause_number" for d in res.diagnostics)
    assert owner["p1-b2"][0].confidence == "medium"


def test_reference_numbers_are_not_clauses():
    # Stanford: "Related Policies" lists other memos; the title carries the memo's own number.
    res = chunk_document(make_doc([
        H("5.1.1 Procurement Policies"), H("1. Purpose", 2), P(para(1)),
        H("Related Policies", 2), H("3.2.1 Responsibility for University Funds", 3), P("This Guide Memo describes funds."),
        H("5.3.3 Purchasing Cards", 3), P("his policy applies to expenditures from all university funding sources."),
    ]))
    assert all(i not in ("5.1.1", "3.2.1", "5.3.3") for c in res.chunks for i in c.clause_ids + [c.clause_id])
    assert res.chunks[0].section_path == ["5.1.1 Procurement Policies", "1. Purpose"]  # title no longer popped by "1."
    assert {d["code"] for d in res.diagnostics} >= {"reference_number_ignored"}


def test_furniture_chunks_are_excluded_not_indexed():
    doc = make_doc([
        H("International Travel Policy"), P("ISSUED ON 03/2026"), H("Introduction", 2), H("Objective", 3), P(para(1)),
        H("Scope", 3), P(para(2)),
        H("Announcements", 2), P(para(3)), H("Join our Travel Slack Channel", 6), H("Sign up for our Newsletter", 6),
        H("Travel Resources", 5), L("Online Booking", flags=["heading_run_demoted"]), L("Group Travel", flags=["heading_run_demoted"]),
    ])
    from ingestion.models import LabeledField, MetaField
    doc.metadata.labeled_fields = [LabeledField(label="ISSUED ON", value="03/2026", page=1)]
    doc.metadata.issued_date = MetaField(value="03/2026", normalized="2026-03", source="document_text")
    res = chunk_document(doc)
    texts = " ".join(c.text for c in res.chunks)
    assert "ISSUED ON" not in texts and "Slack" not in texts and "Online Booking" not in texts
    reasons = {c.exclusion_reason: c for c in res.excluded}
    assert reasons["document_metadata"].text == "ISSUED ON 03/2026"
    assert "Online Booking" in reasons["navigation_links"].text
    assert all(c.issued_date == "2026-03" for c in res.chunks)
    assert not [d for d in res.diagnostics if d["code"] == "block_not_chunked"]
    for a, b in zip(res.chunks, res.chunks[1:]):  # the retrievable chain skips moved chunks
        assert a.next_chunk_id == b.chunk_id


def test_missed_heading_is_recovered_from_numbering():
    res = chunk_document(make_doc([
        H("Policy", 1), H("Standard 8: Malware Prevention", 2, size=18.8), L("8.1 " + para(1)),
        P("Standard 9: Data Classification.", size=18.8), L("9.1 " + para(2)), L("9.2 " + para(3)),
    ]))
    c91 = next(c for c in res.chunks if "9.1" in c.clause_ids)
    assert c91.section_path[:2] == ["Policy", "Standard 9: Data Classification."]
    assert "Standard 9" not in c91.text
    assert any(d["code"] == "heading_recovered" for d in res.diagnostics)


def test_grouped_sections_keep_their_headings_inline():
    # Oregon p1: tiny front matter absorbs "1. Policy Statement", whose only content is clause 1.1.
    chunks = chunks_of([H("Policy"), P("Last Revised"), P("July 15, 2026"), H("1. Policy Statement", 2),
                        L("1.1. " + sentence(1)), H("2. Reason for Policy", 2), L("2.1. " + para(2))])
    first = chunks[0]
    assert first.section_path == ["Policy"]
    assert first.text.split("\n") == ["Last Revised", "July 15, 2026", "1. Policy Statement", "1.1. " + sentence(1)]
    assert chunks[1].section_path == ["Policy", "2. Reason for Policy", "2.1"]
    for c in chunks:  # every heading is either in the path or in the text of the chunks under it
        assert all(h in c.section_path or h in c.text for h in ("Policy",) if c.ordinal == 0)


def test_heading_that_continues_a_list_is_demoted():
    chunks = chunks_of([H("Meal Per Diem", 2), P("I. Away competitions follow these rates:"), L("a. Breakfast - $11"),
                        L("b. Lunch - $14"), L("c. Dinner - $25"), H("d. TOTAL - $50/day", 3), P("II. High-cost cities follow other rates.")])
    (c,) = chunks
    assert "d. TOTAL - $50/day" in c.text and c.section_path == ["Meal Per Diem"]


# ---------------------------------------------------------------- size limits and fallbacks


def test_maximum_size_is_respected():
    specs = [H("Policy")] + [P(para(i, 7)) for i in range(12)] + [L(f"• item {i} " + sentence(i)) for i in range(30)]
    for c in chunks_of(specs):
        assert c.retrieval_token_count <= C.MAX_TOKENS
        assert c.retrieval_token_count == count_tokens(c.retrieval_text)


def test_paragraph_fallback_splits_between_blocks():
    specs = [H("Long Section")] + [P(para(i)) for i in range(6)]
    chunks = chunks_of(specs)
    assert len(chunks) > 1 and all(c.split == "paragraph" for c in chunks)
    ids = [bid for c in chunks for bid in c.source_block_ids]
    assert ids == [f"p1-b{i}" for i in range(1, 7)]  # every paragraph whole, once, in order
    assert all(c.char_span is None for c in chunks)
    assert all(c.section_path == ["Long Section"] for c in chunks)


def test_long_list_splits_between_items_and_carries_its_lead_in():
    lead = "Receipts must include all of the following:"
    specs = [H("Receipts"), P(lead)] + [L(f"• requirement {i}: " + sentence(i, 25)) for i in range(16)]
    chunks = chunks_of(specs)
    assert len(chunks) > 1
    assert chunks[0].text.startswith(lead) and chunks[0].lead_in is None
    for c in chunks[1:]:
        assert c.split == "list" and c.lead_in == lead
        assert lead not in c.text  # overlap only in lead_in, never duplicated in text
        assert c.retrieval_text.split("\n")[2] == lead  # [0] title, [1] section path, [2] lead-in
    items = [bid for c in chunks for bid in c.source_block_ids]
    assert items == [f"p1-b{i}" for i in range(1, 18)]


def test_short_list_stays_with_its_lead_in():
    (c,) = chunks_of([H("Approvals"), P("Approval is required if:"), L("• the trip cost exceeds $7,500"), L("• the trip exceeds 21 days")])
    assert c.text == "Approval is required if:\n• the trip cost exceeds $7,500\n• the trip exceeds 21 days"


def test_low_confidence_list_is_a_hint_not_a_boundary():
    items = [L(f"Entry {i} of an implicit list", list_type="implicit", confidence="low") for i in range(5)]
    (c,) = chunks_of([H("Section"), P("Items:"), *items])
    assert c.confidence == "high" and c.clause_ids == []


def test_sentence_fallback_for_one_long_paragraph():
    text = para(1, 40)  # ~900 tokens in one block
    chunks = chunks_of([H("Section"), P(text)])
    assert len(chunks) > 2
    assert all(c.split == "sentence" and c.source_block_ids == ["p1-b1"] for c in chunks)
    assert all(c.retrieval_token_count <= C.MAX_TOKENS for c in chunks)
    assert all(c.text.endswith(".") for c in chunks)  # cut at sentence ends
    assert " ".join(c.text for c in chunks) == text
    spans = [c.char_span for c in chunks]
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))  # disjoint, in order
    assert all(c.confidence == "medium" and "sentence_level_split" in c.confidence_reasons for c in chunks)


def test_token_fallback_only_for_a_giant_sentence():
    text = "The " + " ".join(f"term{i}" for i in range(1200)) + " apply"
    chunks = chunks_of([H("Section"), P(text)])
    assert all(c.split == "token" and c.confidence == "low" for c in chunks)
    assert all(c.retrieval_token_count <= C.MAX_TOKENS for c in chunks)
    assert " ".join(c.text for c in chunks).split() == text.split()


def test_sentence_boundaries_ignore_initials_and_abbreviations():
    text = "U. T. Austin requires it, e.g. for servers. The next rule applies."
    spans = boundary_spans(text)
    assert [text[a:b] for a, b in spans] == ["U. T. Austin requires it, e.g. for servers.", "The next rule applies."]


# ---------------------------------------------------------------- tables


def test_table_keeps_rows_and_columns():
    rows = [["Meal", "Within Canada", "Outside Canada"], ["Breakfast", "$24", "$30"], ["Dinner", "$68", "$81"]]
    (c,) = chunks_of([H("Per Diem"), P("Per diem allowance:"), T(rows)])
    assert "| Meal | Within Canada | Outside Canada |" in c.text
    assert "| Breakfast | $24 | $30 |" in c.text
    assert c.tables[0].rows == rows and c.tables[0].confidence == "medium"


def test_key_value_table_renders_as_labels():
    (c,) = chunks_of([T([["Title", "Travel Procedures"], ["Effective Date", "July 1, 2026"]])])
    assert c.text == "Title: Travel Procedures\nEffective Date: July 1, 2026"


def test_unreliable_table_keeps_the_extracted_text_and_is_low_confidence():
    rows = [["by", "the Assistant Vice President"], ["Officer) or their designee", "for procurement from one"]]
    original = "by | the Assistant Vice President\nOfficer) or their designee | for procurement from one"
    (c,) = chunks_of([H("Definitions"), T(rows)])
    assert c.text == original
    assert c.confidence == "low" and "table_low_confidence" in c.confidence_reasons


def test_oversized_table_is_split_between_rows_with_header():
    header = ["City", "Breakfast", "Lunch", "Dinner"]
    rows = [header] + [[f"City number {i} in the state", f"${i}", f"${i + 1}", f"${i + 2}"] for i in range(80)]
    chunks = chunks_of([H("Rates"), T(rows)])
    assert len(chunks) > 1 and all(c.split == "table_rows" for c in chunks)
    for c in chunks:
        assert c.text.startswith("| City | Breakfast | Lunch | Dinner |")
        assert c.tables[0].header_row == header
        assert c.retrieval_token_count <= C.MAX_TOKENS
    assert [r for c in chunks for r in c.tables[0].rows] == rows[1:]


def test_scrambled_grid_is_isolated_and_low_confidence():
    cells = [P(f"cell {i}", x=111 if i % 2 else 253) for i in range(10)]
    chunks = chunks_of([H("Classification"), P("9.5 The standard consists of three classifications. " + para(1, 2)), *cells, P(para(2, 2))])
    grid = [c for c in chunks if "unreconstructed_table_region" in c.confidence_reasons]
    assert len(grid) == 1 and grid[0].confidence == "low"
    assert grid[0].source_block_ids == [f"p1-b{i}" for i in range(2, 12)]
    assert all("cell" not in c.text for c in chunks if c not in grid)


# ---------------------------------------------------------------- excluded content, pages


def test_excluded_content_is_preserved_but_not_chunked():
    specs = [
        P("Home > Policies", flags=["suspected_chrome:header"]),
        H("Policy"), P(para(1)),
        H("Revision History", 2, flags=["exclude_from_retrieval:revision_history_table_unreliable"]),
        P("11/18/2023 | Revised", flags=["exclude_from_retrieval:revision_history_table_unreliable"]),
        P("© 2026 University", flags=["suspected_chrome:footer"]),
    ]
    res = chunk_document(make_doc(specs))
    retrievable_ids = {bid for c in res.chunks for bid in c.source_block_ids}
    assert retrievable_ids == {"p1-b2"}
    reasons = {c.exclusion_reason: c for c in res.excluded}
    assert set(reasons) == {"site_chrome:header", "revision_history_table_unreliable", "site_chrome:footer"}
    assert "11/18/2023" in reasons["revision_history_table_unreliable"].text
    assert all(not c.retrievable and c.prev_chunk_id is None and c.retrieval_text == "" for c in res.excluded)


def test_table_of_contents_is_excluded_as_navigation():
    specs = [H("Policy"), H("Contents", 2), L("Purpose", list_type="implicit"), L("Scope", list_type="implicit"),
             L("Definitions", list_type="implicit"), H("Purpose", 2), P(para(1)), H("Scope", 2), P(para(2)),
             H("Definitions", 2), P(para(3))]
    res = chunk_document(make_doc(specs))
    (nav,) = res.excluded
    assert nav.exclusion_reason == "navigation_toc" and nav.source_block_ids == ["p1-b2", "p1-b3", "p1-b4"]
    assert [c.section_path[-1] for c in res.chunks] == ["Purpose", "Scope", "Definitions"]


def test_empty_pages_and_multi_page_provenance():
    specs = [H("Policy"), L("1.1 " + para(1, 3)), P("continued on a later page " + sentence(9), page=3), L("1.2 " + para(2), page=3)]
    doc = make_doc(specs, n_pages=3)  # page 2 has no blocks
    assert not doc.pages[1].blocks
    chunks = chunk_document(doc).chunks
    first = next(c for c in chunks if c.clause_id == "1.1")
    assert (first.page_start, first.page_end, first.pages) == (1, 3, [1, 3])
    assert all(2 not in c.pages for c in chunks)
    assert "1.1 Sentence" in first.text and "continued on a later page" in first.text


def test_page_and_line_provenance_match_source_blocks():
    doc = make_doc([H("Policy")] + [P(para(i), page=1 + i // 2) for i in range(8)])
    blocks = {b.block_id: b for b in doc.iter_blocks()}
    for c in chunk_document(doc).chunks:
        pages = sorted({blocks[bid].page for bid in c.source_block_ids})
        assert c.pages == pages and (c.page_start, c.page_end) == (pages[0], pages[-1])
        assert set(c.source_line_ids) <= {lid for bid in c.source_block_ids for lid in blocks[bid].line_ids}


def test_no_block_is_chunked_twice_or_lost():
    specs = [H("Policy"), H("A", 2), P(para(1, 30)), L("1.1 " + para(2)), L("1.2 " + para(3, 2)), H("B", 2)]
    specs += [L(f"• item {i} " + sentence(i, 30)) for i in range(20)]
    res = chunk_document(make_doc(specs))
    seen = {}
    for c in res.chunks:
        for bid in c.source_block_ids:
            seen.setdefault(bid, []).append(c.char_span)
    content = [b.block_id for b in make_doc(specs).iter_blocks() if b.kind != "heading"]
    assert sorted(seen) == sorted(content)
    for spans in seen.values():  # a block in several chunks only via disjoint sentence spans
        if len(spans) > 1:
            assert all(s is not None for s in spans)
            assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))
    texts = [c.content_hash for c in res.chunks]
    assert len(texts) == len(set(texts))
    assert not [d for d in res.diagnostics if d["code"] in ("block_not_chunked", "duplicate_chunk_id", "oversized_chunk")]


# ---------------------------------------------------------------- pipeline


def test_pipeline_is_deterministic_and_never_touches_ingested_output(tmp_path):
    ingested, out = tmp_path / "ingested", tmp_path / "chunks"
    ingested.mkdir()
    for i, specs in enumerate([[H("Policy"), P(para(1)), P(para(2))], [H("Other"), L("1.1 " + para(3))]]):
        doc = make_doc(specs, doc_id=f"doc-{i}")
        (ingested / f"{doc.doc_id}.json").write_text(doc.model_dump_json(indent=1), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in ingested.iterdir()}
    run_pipeline(ingested, out)
    first = {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    run_pipeline(ingested, out)
    second = {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert {p.name: p.read_bytes() for p in ingested.iterdir()} == before
    assert first == second
    assert set(first) == {"doc-0.jsonl", "doc-1.jsonl", "excluded/doc-0.jsonl", "excluded/doc-1.jsonl", "_manifest.json"}
    manifest = json.loads(first["_manifest.json"])
    assert [d["doc_id"] for d in manifest["documents"]] == ["doc-0", "doc-1"]
    assert re.match(r"chunk-\d", manifest["chunker_version"])
