# Corpus archive

Files that were in `corpus/` but were replaced during corpus remediation (2026-09-25), plus the web
sources the replacements were made from. This directory is **not ingested**. Originals are kept
byte-for-byte (sha256 prefixes below). The expected state of `corpus/` is recorded in
`config/corpus_lock.json`. Check it with `python -m ingestion.validate`.

## Replacements

### 1. Yale 5002 Remote Work Policy
- **Superseded:** `superseded/5002 Remote Work Policy _ It’s Your Yale.pdf` (sha256 `8573b8265a232627…`).
  The browser print showed only the section list and scope; the site's print CSS clipped the policy text.
- **Replacement:** `corpus/5002 Remote Work Policy _ It’s Your Yale (full-text capture 2026-09-25).pdf` (`24d059c5ccc72fc7…`, 7 pages).
- **Source:** https://your.yale.edu/policies-procedures/5002-remote-work-policy, fetched 2026-09-25.
  Yale publishes this policy only as a web page; no PDF of the policy is linked.
- **Method:** the fetched HTML (`sources/yale_5002-remote-work-policy_2026-09-25.html`) was printed with
  headless Chrome after adding a single print-only rule, `html,body{overflow:visible;height:auto;max-height:none}`,
  plus a `<base href>` so assets resolve (`sources/…_printfix.html`). No page text was changed.
  Check: 97.6% of the policy text's 6-word sequences occur verbatim in the capture. Misses are only
  where the site's text-shadow repeats link text; ingestion removes those repeats.
- The PDF has no browser URL/date footer (it was printed from the fetched copy). The URL and capture
  date are recorded as curated metadata in `config/document_overrides.toml`.

### 2. UConn Travel Policy
- **Superseded:** `superseded/Travel Policy _ Travel Services _ Procurement _ University of Connecticut.pdf` (`fef77dfe86787218…`).
  This was the travel-services landing page (links, news, travel advisories), not the policy.
- **Replacement:** `corpus/Travel and Entertainment Policy _ University Policies _ University of Connecticut (capture 2026-09-25).pdf` (`9f7a9d58de733fa5…`, 11 pages).
- **Source:** the landing page's "Complete Travel Policy (Effective 2/1/26)" link,
  https://policy.uconn.edu/2020/04/29/travel-and-entertainment-policies-and-procedures/, which redirects to
  https://policy.uconn.edu/2026/04/21/travel-and-entertainment-policies-and-procedures/ (Office of
  University Compliance). **No PDF of the policy exists**: it is published only as this web page, so the
  replacement is a direct headless-Chrome print of the page, with its browser URL/date footer.
  Check: 97.5% of the policy body's 6-word sequences occur verbatim in the capture.
- **Note:** the page now reads "Effective Date: July 1, 2026" (approved June 17, 2026), so the landing
  page's "(Effective 2/1/26)" label is stale.
- **Procedures:** the page also links `2026-07-01-Travel-and-Entertainment-Procedures.pdf` (effective
  July 1, 2026), which supersedes `corpus/Travel-and-Entertainment-Procedures-FINAL.pdf` (effective
  February 1, 2026). Curator decision: keep both versions (see Additions below).

### 3. Oregon State 03-010 Procurement Thresholds and Methods
- **Superseded:** `superseded/Procurement Thresholds and Methods _ University Policies and Standards _ Oregon State University.pdf` (`84408f1c587f9f44…`).
  This was a web-page print, and the page itself says: "The PDF is the official text of the policy… the PDF
  will be considered accurate and overriding."
- **Replacement:** `corpus/03-010 Procurement Thresholds and Methods 071526.pdf` (`ebfbe1e9afaa9cb7…`, 16 pages),
  the official PDF, downloaded unchanged on 2026-09-25 from the page's "Download the Policy: (PDF)" link:
  https://policy.oregonstate.edu/sites/policy.oregonstate.edu/files/03-010%20Procurement%20Thresholds%20and%20Methods%20071526.pdf
  It reads "Last Revised July 15, 2026", matching the web page.

## Additions

### 4. UConn Travel and Entertainment Procedures, July 1, 2026 version
- **Added:** `corpus/2026-07-01-Travel-and-Entertainment-Procedures.pdf` (`db7e3ef5096947c7…`, 7 pages),
  downloaded unchanged on 2026-09-25 from the "Travel and Entertainment Procedures" link under
  PROCEDURES/APPENDICES on the official policy page (`sources/uconn_travel-and-entertainment-policy_2026-09-25.html`):
  https://policy.media.uconn.edu/wp-content/uploads/sites/3519/2023/06/2026-07-01-Travel-and-Entertainment-Procedures.pdf
  It reads "Approval Date June 17, 2026 / Effective Date July 1, 2026" and "Revised June 17, 2026".
- **Kept, not replaced:** `corpus/Travel-and-Entertainment-Procedures-FINAL.pdf` (effective February 1, 2026).
  Curator decision (2026-09-25): both versions stay in the corpus as separate documents so that
  historical questions can later be answered from the version in force at the time.
- **Identity metadata** (`config/document_overrides.toml`): both share `series_id =
  "uconn-travel-entertainment-procedures"`. February: `superseded_date` July 1, 2026, `is_current = false`.
  July: `is_current = true`. Neither states a version number, so `version` stays null.
- **Extraction note:** this file was printed with "Microsoft: Print To PDF", which anonymizes the
  embedded Calibri font (`CIDFont+F1..F7`). The override `calibri_fonts = ["CIDFont+F"]` enables the
  Calibri ligature repair for it; `Ʃ` → `tt` was added to that repair table.

## Web sources (`sources/`)
| File | Fetched | sha256 |
|---|---|---|
| yale_5002-remote-work-policy_2026-09-25.html | 2026-09-25 | `39c921bf8511e2ee…` |
| yale_5002-remote-work-policy_2026-09-25_printfix.html | derived (print CSS + base href only) | `b83ebd85d7409b1e…` |
| uconn_travel-and-entertainment-policy_2026-09-25.html | 2026-09-25 | `a571430450ca8ef4…` |
| uconn_travel-policy-landing_2026-09-25.html | 2026-09-25 | `1462c9961e2316a5…` |
| oregon_03-010-web-page_2026-09-25.html | 2026-09-25 | `5aa55cf8b6024b05…` |
