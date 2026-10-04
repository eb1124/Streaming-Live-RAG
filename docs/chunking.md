# Chunking layer (Phase 1, slice 2)

Turns the normalized ingestion output (`data/ingested/`) into retrieval chunks (`data/chunks/`).
It only reads `data/ingested/`; nothing there is modified. There are no embeddings, indexes or
retrieval yet.

```
python -m chunking                               # data/ingested/ -> data/chunks/
python -m chunking.inspect                       # corpus statistics (--write-report saves data/chunks/chunk_report.md)
python -m chunking.inspect oregon --brief        # one line per chunk of a document
python -m chunking.inspect 03-010                # every chunk in full: id, path, clause, pages, tokens, prev/next, text
python -m chunking.inspect 03-010 --excluded     # preserved non-retrievable records
python -m chunking.inspect --chunk <chunk_id>    # one chunk incl. retrieval text, metadata, line ids, tables
python -m chunking.inspect --find "per diem" | --low
python -m pytest tests/test_chunking.py          # synthetic rules;  -m corpus for tests/test_chunking_corpus.py
```

## Output

| File | Content |
|---|---|
| `data/chunks/<doc_id>.jsonl` | retrievable chunks in reading order, linked by `prev_chunk_id` / `next_chunk_id` |
| `data/chunks/excluded/<doc_id>.jsonl` | non-retrievable content kept as records (`retrievable: false`, `exclusion_reason`) |
| `data/chunks/_manifest.json` | chunker version, parameters, input content hashes, per-document diagnostics |

Chunk fields (`chunking/models.py`): document identity copied from ingestion metadata (`title`,
`organization`, `domain`, `document_type`, `authority_level`, `version`, `effective_date`,
`superseded_date`, `is_current`, `series_id`, `source_url`, `capture_date`; null = unknown), structure
(`section_path`, `section_ids`, `clause_id`, `clause_ids`), provenance (`page_start`, `page_end`,
`pages`, `source_block_ids`, `source_line_ids`, `char_span`), content (`text`, `context_header`,
`lead_in`, `retrieval_text`, `token_count`, `retrieval_token_count`, `content_hash`, `tables`), and
quality (`split`, `confidence`, `confidence_reasons`).

* `text` is the chunk's own content, verbatim from ingestion blocks. Headings that are in the
  `section_path` are not repeated in it; headings of grouped subsections are.
* `retrieval_text` = `context_header` (title, organization, section path) + `lead_in` + `text`.
  This is what an index should embed and match. The size limit applies to it.
* `chunk_id` = `<doc_id>::<first page>-<hash of doc_id, block ids and span>`. It is deterministic
  and depends only on which source blocks the chunk covers, so unchanged input gives unchanged ids
  (tested). Changes elsewhere in the document don't renumber other chunks.

## Strategy

1. **Units come from structure.** Retrievable blocks (`ingestion.structure.is_retrievable`, minus
   detected tables of contents) are arranged in a tree of headings and numbered clauses
   (`chunking/tree.py`).
   * Heading levels are ingestion's, which are relative to each document.
   * Clauses are detected from enumerators (`chunking/numbering.py`), whether ingestion classified
     the block as a heading or a list item. Structural numbers are: decimal `5.1.1` / `4.4.3.1` /
     `11.4.9` / `5002.1`, prefixed `PR1.1`, and bare integers when they are provisions
     (Penn `1`–`7`: at least 12 words, not introduced by a `…:` line, not inside another numbered
     clause). `a.` / `i.` / `(1)` / bullets are local list items, never boundaries.
   * Nesting uses numbering first: `11.4.9` goes under `11.4` even when both are headings of the same
     visual level, and `5.7` closes the heading `5.6.`. Heading level is used second.
2. **A clause is atomic.** A clause with its sub-items and sub-clauses stays in one chunk when it
   fits the maximum. A heading section is split into its subsections. The section's own text
   before the first subsection is a unit of its own.
3. **Grouping.** Adjacent units under the same parent may be grouped up to `TARGET_TOKENS`:
   * sibling clauses or content, when one side is below `SMALL_TOKENS` (Oregon's definitions,
     Penn's clauses);
   * heading sections only when one side is below `TINY_TOKENS` (Penn's "EFFECTIVE / September, 1990"
     fields);
   * units never merge across parents. A grouped chunk's `section_path` is the parent's path.
4. **Oversized units** (> `MAX_TOKENS`) are split in this order:
   1. paragraph boundaries;
   2. list-item boundaries;
   3. after a `…:` lead-in;
   4. sentence boundaries;
   5. `;`/`:` boundaries;
   6. word windows, as a last resort.

   A lead-in is never separated from its list unless nothing else works. A block that continues a
   sentence (starts lowercase) is never separated from the block before it. Tables are split
   between rows, repeating the header row. `split` records which of these produced each chunk.
5. **Overlap is structural, not a token window.** A fragment of a split clause or list carries,
   in `lead_in`, the clause head and/or the `…:` line that introduces its list (at most 80 tokens
   each). Example: Oregon `5.2.1`'s items c–e and f–g carry "5.2.1. When acquiring goods …". The
   overlap appears only in `retrieval_text`, never in `text`. 16 of 466 chunks carry one.
6. **Tables.** Key-value tables become `Label: value` lines. Other tables become a pipe table with
   the first row as header, and the structured rows are kept in `tables[]`. Tables that look
   unreliable keep ingestion's original text and make the chunk `low` confidence. A table counts as
   unreliable when ≥ 25% of cells are empty, it has fewer than 2 rows, or its first cell starts
   mid-sentence.
7. **Scrambled borderless tables** are detected as grid regions: runs of ≥ 6 short fragments whose
   x-position keeps jumping between columns, like UT's data-classification table on pp. 47–48. They
   are isolated into their own `low`-confidence chunks, in extraction order, with no relationships
   invented.

## Size limits (`chunking/config.py`)

| Parameter | Value | Why |
|---|---|---|
| `MAX_TOKENS` | 400 | Hard ceiling on `retrieval_text`. It fits 512-token encoders and cross-encoder rerankers with room for a query. |
| `TARGET_TOKENS` | 256 | Size that grouping and fallback splitting aim for. A unit between 256 and 400 is kept whole, never cut to reach it. |
| `SMALL_TOKENS` | 100 | Clauses below this may be grouped with siblings. |
| `TINY_TOKENS` | 24 | Heading sections below this are too thin to stand alone. |
| `LEAD_IN_MAX_TOKENS` | 80 | Longer lead-ins are cut at a word boundary with "…". |

Tokens are estimated as words + punctuation marks (`chunking/tokens.py`), so the chunker doesn't
depend on the embedding model, which isn't chosen yet. On this corpus, OpenAI's cl100k tokenizer
gives 1.04× the estimate (p95 1.10×). BERT-style WordPiece usually runs 10–20% above cl100k on this
kind of text, so 400 estimated tokens is about 420–500 real tokens. Swap in a real tokenizer in
`count_tokens` once the model is fixed.

The maximum is a constraint, not the strategy. On the current corpus, 399 of 466 chunks are one
structural unit or a group of small ones, and 67 come from a paragraph (63) or list (4) fallback.
No chunk needed a sentence or word split.

## Exclusions

* Everything `is_retrievable()` rejects (site chrome, curated `exclude_sections_from_retrieval`,
  for example UT's revision history on pp. 78–93) is written to `excluded/` and never linked into
  the chunk sequence.
* **Tables of contents** are also excluded, as `navigation_toc`. These are runs of ≥ 3 short
  non-heading blocks that repeat headings appearing *later* in the document. Examples: UT's "Table
  of Contents" and standards index, Yale's "Policy Sections" link list, Rutgers' "Procedure
  Outline", and the UConn policy's in-page menu. The sections themselves are kept.

* **Page furniture** is detected after chunking and moved to `excluded/`. `navigation_links` is a
  chunk made only of headings of empty sections and link tiles (items ingestion demoted from a run
  of same-style headings), such as Michigan's "Join our Travel Slack Channel / Sign up for our
  Travel Program Newsletter / Travel Resources". `document_metadata` is a chunk that is only a
  labeled date stamp already held in the metadata (Rochester "ISSUED ON 03/2026" → `issued_date`).
  Reference lists (UT's supplemental policies, UConn REFERENCES) and contact details are content
  and stay retrievable.

## Structure repairs made by the chunker (reported as diagnostics)

The ingested JSON is not changed. These repairs only affect how chunks are built, and each is
listed in `_manifest.json`.

* `heading_recovered`: a short paragraph such as "UT-IRUSP Standard 9: Data Classification." is
  used as a heading when three things hold: it names a number ("Standard 9"), the next clause starts
  that family (`9.1`), and it is set in the same size as the heading it would close. Ingestion
  rejected it only because it ends with a period. UT Standards 9 and 17 are recovered this way.
* `heading_demoted`: a "heading" whose enumerator continues the list just before it (UConn's bold
  `d. TOTAL - $50/day` after `a.`–`c.`) is kept as a list item.
* `out_of_sequence_clause_number`: a number that can't belong where it appears (UT `11.3.3` inside
  `8.3`) stays as content of the current clause, and the chunk is `medium` confidence.
* `reference_number_ignored`: a multi-level number with no parent, sibling or child anywhere in
  the document isn't part of its numbering. Examples are Stanford's "Related Policies" entries for
  other memos (`3.2.1`, `1.5.2`, `5.3.3`) and the memo number in its own title (`5.1.1`). Such a
  number gets no `clause_id` and doesn't affect nesting.
* Numbers with 3-digit or zero-padded sub-components (`2054.517`, `552.021`) are statute citations,
  not clauses.

## Confidence

`low`: unreconstructed table region, unreliable table, possibly wrong section path, or word-window
split. `medium`: sentence split, out-of-sequence clause number, low-confidence heading in the path,
or a split made at an implicit (low-confidence) list boundary. Reasons are listed in
`confidence_reasons`.

## Known limitations

* Errors in ingestion's block structure pass through when there is no numbering to correct them.
  The ones found in review (Rutgers p1, UT p16, McGill `PR6.1`) are fixed upstream in ingestion.
* Stanford's "5.3.3 Purchasing Cards" teaser starts "his policy applies…". The live page has the
  same truncated text, so it is kept as published.
* Word's `o` sub-bullets are paragraphs in ingestion (both UConn procedures). They stay in the right
  chunk, but their marker is part of the text.
