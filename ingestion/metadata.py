"""Document metadata with provenance. Unknown stays None: nothing is guessed.

Sources, in the order they are tried:
  title          labeled field ("Title:", "Policy Name") > browser tab title (web prints, refined
                 by the matching page-1 heading) > PDF title (unless a template placeholder)
                 > largest-font heading run on page 1
  organization   curated override (evidence must appear in the PDF) > tab-title segment
                 naming a University/College/Institute
  domain         curated override only (it is a classification, not a fact in the PDF)
  source_url     browser footer URL of web prints (tracking params stripped; raw kept as evidence)
  captured_at    browser header timestamp of web prints
  version / effective_date / revision_date / issued_date   labeled fields on pages 1-2
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Block, Document, LabeledField, MetaField

LABELS = {
    "title": "title",
    "policy name": "title",
    "policy title": "title",
    "revision": "version",
    "version": "version",
    "effective date": "effective_date",
    "effective": "effective_date",
    "revision date": "revision_date",
    "last updated": "revision_date",
    "last revised": "revision_date",
    "revised": "revision_date",
    "issued on": "issued_date",
    "publication date": "issued_date",
}
OTHER_LABELS = {
    "adopted", "approval date", "reviewed", "responsible office",
    "responsible official", "responsible executive", "procedure owner", "applies to",
    "campus applicability", "authority", "approval", "university standard",
    "formerly known as policy number", "document purpose",
}
PREFIX_LABELS = ("issued on", "last updated", "effective date", "revision date", "last revised")
DATE_START = re.compile(r"^(\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.IGNORECASE)
# Template placeholders and authoring-tool file names are not titles
# (e.g. Rutgers "Month XX, XXXX"; Oregon "...5.10.18_ph comments kk respo.JBEditsdocx.docx").
PLACEHOLDER_TITLE = re.compile(r"(?i)(\bXX\b|^untitled|^microsoft word\s*-|^document\d*$|\.(docx?|pdf|pptx?|xlsx?)$)")
BROWSER_TIMESTAMP = re.compile(r"^\d{1,2}/\d{1,2}/\d{2}, \d{1,2}:\d{2}\s?[AP]M$")
ORG_WORDS = re.compile(r"\b(University|College|Institute)\b")
TITLE_SPLIT = re.compile(r"\s+(?:\||@|-)\s+")
DATE_FORMATS = [
    ("%B %d, %Y", "%Y-%m-%d"), ("%B %d %Y", "%Y-%m-%d"), ("%Y-%m-%d", "%Y-%m-%d"),
    ("%m/%d/%Y", "%Y-%m-%d"), ("%B, %Y", "%Y-%m"), ("%B %Y", "%Y-%m"), ("%m/%Y", "%Y-%m"),
]


def normalize_date(value: str) -> str | None:
    value = re.sub(r"\s+", " ", value.strip().rstrip("."))
    for fmt, out in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime(out)
        except ValueError:
            continue
    return None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _label_key(label: str) -> str | None:
    key = _norm(label).rstrip(":").strip()
    return key if key in LABELS or key in OTHER_LABELS else None


def find_labeled_fields(doc: Document, max_page: int = 2) -> list[LabeledField]:
    found: list[LabeledField] = []
    for page in doc.pages[:max_page]:
        blocks = page.blocks
        for i, block in enumerate(blocks):
            if block.kind == "table" and block.table:
                for row in block.table.rows:
                    cells = [c for c in row if c]
                    if len(cells) >= 2 and _label_key(cells[0]):
                        found.append(LabeledField(label=cells[0].strip().rstrip(":"), value=cells[1], page=page.page_number))
                continue
            text = block.text
            if ":" in text:
                label, value = text.split(":", 1)
                if _label_key(label) and value.strip() and len(value) <= 120:
                    found.append(LabeledField(label=label.strip(), value=value.strip(), page=page.page_number))
                    continue
            low = text.lower()
            prefix = next((p for p in PREFIX_LABELS if low.startswith(p + " ")), None)
            if prefix and DATE_START.match(text[len(prefix):].strip()):
                found.append(LabeledField(label=text[: len(prefix)], value=text[len(prefix):].strip(), page=page.page_number))
                continue
            if _label_key(text) and i + 1 < len(blocks) and blocks[i + 1].kind != "table":
                value = blocks[i + 1].text
                if len(value) <= 80 and not _label_key(value):
                    found.append(LabeledField(label=text, value=value, page=page.page_number))
    return found


def _browser_lines(doc: Document) -> list:
    return [ln for p in doc.pages for ln in p.lines if ln.removed == "browser_header_footer"]


def strip_tracking(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _title_from_blocks(doc: Document) -> tuple[str, int] | None:
    first = doc.pages[0].page_number if doc.pages else None
    candidates = [b for b in doc.iter_blocks() if b.page == first and b.kind == "heading"]
    if not candidates:
        return None
    top = max(b.size for b in candidates)
    run: list[Block] = []
    for b in candidates:
        if b.size == top:
            run.append(b)
        elif run:
            break
    return " ".join(b.text for b in run), run[0].page


def extract_metadata(doc: Document, override: dict | None = None) -> None:
    md = doc.metadata
    md.labeled_fields = find_labeled_fields(doc)
    for lf in md.labeled_fields:
        key = LABELS.get(_norm(lf.label).rstrip(":"))
        if key and getattr(md, key).value is None:
            field = MetaField(value=lf.value, source="document_text", page=lf.page, evidence=f"{lf.label}: {lf.value}")
            if key.endswith("_date"):
                field.normalized = normalize_date(lf.value)
            setattr(md, key, field)

    # Browser header/footer of web prints: timestamp, tab title, URL.
    tab_title = None
    for ln in _browser_lines(doc):
        text = ln.text
        if BROWSER_TIMESTAMP.match(text) and md.captured_at.value is None:
            try:
                iso = datetime.strptime(text.replace(" ", " "), "%m/%d/%y, %I:%M %p").isoformat(timespec="minutes")
            except ValueError:
                iso = None
            md.captured_at = MetaField(value=text, source="browser_header", page=ln.page, evidence=text, normalized=iso)
        elif re.match(r"^https?://", text) and md.source_url.value is None:
            md.source_url = MetaField(value=strip_tracking(text), source="browser_footer", page=ln.page, evidence=text)
        elif ln.bbox[1] < 30 and tab_title is None and not BROWSER_TIMESTAMP.match(text):
            tab_title = text

    pdf_title = doc.pdf.pdf_title if doc.pdf else None
    if pdf_title and PLACEHOLDER_TITLE.search(pdf_title):
        doc.warn("pdf_title_placeholder", f"PDF title metadata is a template placeholder: {pdf_title!r}", "info")
        pdf_title = None

    if md.title.value is None:
        web_title = tab_title or (pdf_title if doc.profile == "web_print" else None)
        if web_title:
            segment = TITLE_SPLIT.split(web_title)[0].strip()
            value = segment
            for b in doc.iter_blocks():
                if b.page != doc.pages[0].page_number or b.kind != "heading":
                    continue
                nb, ns = _norm(b.text), _norm(segment)
                if ns.startswith(nb) and len(nb) >= 0.6 * len(ns):
                    value = b.text
                    break
            md.title = MetaField(value=value, source="browser_title", evidence=web_title)
        elif pdf_title:
            md.title = MetaField(value=pdf_title, source="pdf_metadata", evidence=pdf_title)
        else:
            found = _title_from_blocks(doc)
            if found:
                md.title = MetaField(value=found[0], source="document_text", page=found[1], evidence=found[0])

    if md.organization.value is None:
        for source_text in filter(None, [tab_title, doc.pdf.pdf_title if doc.pdf else None]):
            # Tab titles run page | section | site; the institution is the last matching segment.
            segments = [s.strip() for s in TITLE_SPLIT.split(source_text)[1:]]
            org = next((s for s in reversed(segments) if ORG_WORDS.search(s)), None)
            if org:
                md.organization = MetaField(value=org, source="browser_title", evidence=source_text)
                break

    apply_overrides(doc, override or {})

    for name in ("title", "organization", "domain", "document_type", "authority_level"):
        if getattr(md, name).value is None:
            doc.warn("metadata_unknown", f"{name} could not be determined; left null", "info")


def _searchable_text(doc: Document) -> str:
    """Text an override may cite as evidence: PDF content and PDF metadata. Deliberately
    excludes the filename, which is not a trustworthy source (e.g. the Rutgers file)."""
    parts = []
    if doc.pdf:
        parts += [doc.pdf.pdf_title or "", doc.pdf.author or ""]
    for page in doc.pages:
        parts.append(page.raw_text)
        parts += [ln.text for ln in page.lines]
    return _norm(" ".join(parts))


# Facts about the document: the override must quote `evidence` found in the PDF.
EVIDENCE_FIELDS = ("title", "organization", "version", "effective_date", "revision_date")
# Curator classifications / acquisition provenance: the override must state a `basis`.
# An `evidence` quote is optional, but is verified against the PDF when given.
CURATED_FIELDS = (
    "domain", "document_type", "authority_level", "source_url", "captured_at",
    "series_id", "superseded_date", "is_current",
)


def apply_overrides(doc: Document, override: dict) -> None:
    """Curated values from config/document_overrides.toml. Unsupported overrides are rejected."""
    if not override:
        return
    haystack = _searchable_text(doc)

    def found(quote: str) -> bool:
        return bool(quote) and _norm(quote) in haystack

    def reject(name: str, spec: dict, why: str) -> None:
        doc.warn("override_rejected", f"{name} override {spec.get('value')!r} rejected: {why}")

    for name in EVIDENCE_FIELDS:
        spec = override.get(name)
        if not spec:
            continue
        evidence = spec.get("evidence", "")
        if not found(evidence):
            reject(name, spec, f"evidence {evidence!r} not in document")
            continue
        field = MetaField(value=spec["value"], source="override", evidence=evidence, note=spec.get("note"))
        if name.endswith("_date"):
            field.normalized = normalize_date(spec["value"])
        setattr(doc.metadata, name, field)

    for name in CURATED_FIELDS:
        spec = override.get(name)
        if not spec:
            continue
        if not spec.get("basis"):
            reject(name, spec, "no basis given")
            continue
        evidence = spec.get("evidence")
        if evidence and not found(evidence):
            reject(name, spec, f"evidence {evidence!r} not in document")
            continue
        value = spec["value"]
        if name == "is_current":
            if not isinstance(value, bool):
                reject(name, spec, "must be true or false")
                continue
            value = "true" if value else "false"
        field = MetaField(value=value, source="curated", evidence=evidence, note=spec["basis"])
        if name == "captured_at":
            field.normalized = value
        elif name == "superseded_date":
            field.normalized = normalize_date(value)
        setattr(doc.metadata, name, field)
