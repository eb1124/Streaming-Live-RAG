# Ingestion layer (Phase 1, slice 1)

Turns the PDFs in `corpus/` into a normalized, inspectable JSON representation in
`data/ingested/`. `corpus/` is read-only: the pipeline never writes there and checks
file hashes before and after each run.

```
python -m ingestion                         # ingest corpus/ -> data/ingested/*.json + _manifest.json
python -m ingestion.inspect                 # overview of every document
python -m ingestion.inspect yale            # one document (doc_id prefix or filename substring)
python -m ingestion.inspect yale --page 1 [--lines | --raw]
python -m ingestion.inspect --find "per diem"
python -m ingestion.validate [--rebuild]    # corpus integrity vs config/corpus_lock.json + data/validation_report.md
python -m ingestion.validate --update-lock  # only after a deliberate corpus change
python -m pytest                            # unit + synthetic tests; corpus tests: -m corpus
```

Corpus remediation history (replaced files, sources, capture method) is in `corpus_archive/README.md`.
The corpus manifest (one row per document, explicit nulls, per-field provenance) is `data/ingested/_manifest.json`.

## Pipeline

| Stage | Module | What it does |
|---|---|---|
| discover | `discovery.py` | Recursive `*.pdf` (case-insensitive). `doc_id = slug(filename)[:48] + "-" + sha256(relative_path)[:6]`: stable across runs; content changes show up in `file_sha256`, not the id. |
| extract | `extract.py` | PyMuPDF `rawdict` → visual lines with font, size, bold ratio and bbox. Glyph repairs (below). Candidate tables via `find_tables()`. |
| clean | `clean.py` | Marks noise lines with a `removed` reason. Nothing is deleted. |
| layout | `layout.py` | Reading order by recursive XY-cut (stream order is unreliable in browser prints). |
| structure | `structure.py` | Lines → blocks (heading / paragraph / list_item / table), heading levels, section tree. |
| metadata | `metadata.py` | Title, organization, domain, URL, dates, version: each with provenance. |
| profiles | `profiles.py` + `config/document_overrides.toml` | Source-specific behaviour, kept out of the general pipeline. |

## Output schema (`models.py`)

`Document` → `source` (filename, relative path, sha256) · `pdf` (page count, producer…) ·
`metadata` (each field is `{value, source, page, evidence, normalized}`) · `pages[]` ·
`sections[]` · `warnings[]` · `stats` · `content_hash`.

`Page` → `raw_text` (PyMuPDF's untouched text) · `lines[]` (every visual line, including removed
ones with their reason) · `blocks[]` (reading order; each lists its `line_ids`, `bbox`, `section_id`).
All coordinates are PDF points on the original page, so every block maps back to the page region
it came from.

## Profiles found in this corpus

* **web_print** (8 docs): Chrome "Print to PDF" (`Skia/PDF`, creator `Mozilla/…`). A browser
  header (timestamp + tab title) and footer (URL + `n/N`) sit in 8pt bands at the page edges.
  They're removed, but the URL, capture timestamp and tab title become metadata first.
  Other traits: cookie banners, site navigation, icon-font glyphs, CSS bullets with no bullet
  glyph, text-shadow re-renders (the same text drawn up to 13×), and out-of-order content streams.
* **office_export** (4 docs): Word → PDF (one via Microsoft Print to PDF, set by override). These have running headers/footers, real bullet glyphs,
  and Calibri ligature mis-encodings.

## Cleaning rules (conservative by design)

A line is removed only when it is:

1. empty after normalization (for example an icon-font glyph),
2. a duplicate re-render: the same text within 5pt of an identical line,
3. in the browser header/footer band (web_print only; text ≤ 9pt within 28/35pt of the edge),
4. a page number: a bare number, `Page N of M` or `N/M` in the top or bottom 15%, **and**
   following the page sequence (constant offset on ≥ 2 pages). A lone clause number in the margin
   is kept.
5. repeated boilerplate: **identical** text at the same height on ≥ 50% of pages (≥ 3 pages),
   inside the header/footer zone (top 12% / bottom 20%),
6. matched by a per-document `drop_line_patterns` regex in the overrides file.

Site navigation is **flagged** (`suspected_chrome:header|footer`), not removed:
* header: blocks before the title (searched on the first two pages);
* footer: the trailing run of short, sentence-less blocks that includes a ©/copyright/privacy line,
  walking back from the end and stopping at the first real sentence or table, so a policy's own
  closing sections (references, history, roles) aren't flagged.

`exclude_sections_from_retrieval` (overrides) flags whole sections, e.g. UT Austin's revision-history
table, as `exclude_from_retrieval:<reason>` with `confidence: low`. `structure.is_retrievable(block)`
is the single check a chunker should use; flagged blocks stay in the JSON.

## Glyph repairs (recorded per line in `repairs`)

* Presentation ligatures (ﬁ ﬂ ﬀ ﬃ) → letters. NBSP and exotic spaces → space. Private-use icon glyphs are stripped.
* Calibri from Word/PDFMaker: `Ɵ`→`ti`, `Ō`→`ft`, `ƞ`→`tf`, `Ʃ`→`tt`, applied only to Calibri spans.
  Microsoft Print to PDF anonymizes font names (`CIDFont+F2`). The override `calibri_fonts` names
  fonts to treat as Calibri, and `profile` forces a profile.
* A lost `tt` ligature, extracted as one `t` twice the normal width, is detected from character
  boxes (≥ 1.6× the median width of `t` in that font and size) and restored to `tt`.

## Structure heuristics and their limits

* **Heading:** font ≥ 1.15× the body size, or a fully bold line when body text isn't bold;
  ≤ 3 lines, ≤ 25 words, no sentence-final punctuation. ALL CAPS alone never makes a heading;
  it only orders heading levels. Runs of ≥ 3 same-style headings with nothing between them
  (tables of contents, link lists) are demoted to list items.
* **Heading level:** the rank of the visual style (size, then bold, then caps) within the
  document. Levels are relative per document, not comparable across documents.
* **Lists:** explicit bullets or enumerators (`1.`, `1.1`, `(a)`, `ii.`, `PR1.1.`). A bare number
  in a left gutter is attached to the paragraph beside it. "Implicit" lists (CSS bullets with no
  glyph) come from indentation after a `:` line or from runs of short single-line blocks, and are
  marked `confidence: low`.
* **Tables:** PyMuPDF `find_tables()` candidates are kept only if they have ≥ 2 rows with 2+
  values, are mostly filled and cover < 60% of the page. Split sub-columns are merged using the
  header row. Borderless tables aren't detected; the UT Austin revision history is read cell by
  cell instead.
* **Line joining:** a line that fills at least 75% of the typical line width, stops mid-sentence
  and is followed by a line in the same font, size and weight has wrapped, so indentation doesn't
  split the pair (Rutgers p1). A line with an unclosed `(` can't end a sentence (UT's
  "… Administration (U. T. System" / "Administration) - …"). A multi-level or prefixed number alone
  on its line (`PR6.1.`) starts a block and takes the text directly below it at the same indent
  (McGill p10). After a wrapped line, such a number is a cross-reference ending the sentence
  (`… Policy` / `P3.6.`) and is left in place.
* **Breadcrumbs:** a row of short blocks on one baseline starting with "Home" is flagged
  `suspected_chrome:breadcrumb`, including when it sits below the page title (UT p1).
* **Reading order:** XY-cut prefers horizontal cuts at paragraph-sized gaps, then vertical
  gutters. Centered or hanging-indent text can split into extra blocks.

## Metadata rules

Nothing is guessed: unknown fields stay `null` and raise a `metadata_unknown` info warning.
Each field is `{value, source, page, evidence, normalized, note}`. `source` is one of
`document_text`, `browser_title`, `browser_header`, `browser_footer`, `pdf_metadata`, `override`
(a curated fact) or `curated` (a curator classification or provenance).

* Facts (`title`, `organization`, `version`, `effective_date`, `revision_date`): an override must
  quote `evidence` that occurs in the PDF text or PDF metadata. The filename never counts.
  `note` records qualifications, e.g. McGill's organization is identified only indirectly.
* Classifications and provenance (`domain`, `document_type`, `authority_level`, `source_url`,
  `captured_at`): an override must state a `basis`; an `evidence` quote is optional but verified.
* `issued_date` comes from "Issued on" / "Publication Date" labels (Rochester 03/2026,
  McGill December 2, 2005).
* Version identity (`series_id`, `superseded_date`, `is_current`) is curated, with a `basis`.
  Versions of one document share a `series_id` and differ in `effective_date`, for example the
  UConn Travel and Entertainment Procedures of February 1, 2026 (`is_current = false`, superseded
  July 1, 2026) and July 1, 2026 (`is_current = true`). `is_current` is as of the curation date;
  null means unknown. No temporal retrieval uses these fields yet.
* Rejected overrides raise `override_rejected`. `curator_warnings` surface known issues (e.g. a
  possibly superseded version) in the manifest and the validation report.
* PDF title fields that are template placeholders or authoring file names are ignored.

Next layer: chunking, see `docs/chunking.md`.
