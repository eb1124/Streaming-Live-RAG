"""Build a per-document structure tree from ingestion blocks: headings + numbered clauses.

Headings come from ingestion with document-relative levels. Numbered clauses come from block
enumerators (chunking.numbering), whether ingestion classified the block as a heading or as a
list item. Nesting is decided by numbering first and heading level second:

  * a numbered node nests under the nearest open node whose number is its prefix
    (Rutgers "11.4.9" under "11.4" although both are headings of the same visual level;
    Oregon "5.6." heading under "5." heading; "5.1.1" list item under "5.1" list item);
  * clause nodes close when a node arrives that is not their descendant; they never close headings;
  * headings otherwise close headings of the same or a deeper level (ingestion's levels).

Blocks that are not headings or clauses are content of the innermost open node.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ingestion.models import Block, Document

from .numbering import ClauseNumber, implied_number, parse_number

# A list item numbered with a bare integer is a clause (Penn "1 Procurement Services ... is
# responsible") only if it is substantial; short entries ("1. Normal commute mileage") are a list.
INTEGER_CLAUSE_MIN_WORDS = 12
# Labels: a clause's title is shown in the section path when it is short.
TITLE_MAX_WORDS = 12
TERM_MAX_WORDS = 8

# Scrambled borderless tables: runs of short blocks that alternate between x-columns.
GRID_MAX_WORDS = 10
GRID_MIN_RUN = 6
GRID_COLUMN_GAP = 40.0  # pt between column x-positions
GRID_MIN_SWITCHES = 3
GRID_BRIDGE = 2  # non-fragment blocks allowed between two grid runs of one region

# Missed headings: a short paragraph such as "UT-IRUSP Standard 9: Data Classification." (ingestion
# rejects headings that end with a period) is treated as a heading when the next clause starts the
# family it names, and it is set in the same font size as the heading of the section it would close.
PROMOTE_MAX_WORDS = 15
PROMOTE_LOOKAHEAD = 3
PROMOTE_SIZE_TOLERANCE = 0.1

# Tables of contents / section indexes: runs of short blocks that repeat later headings.
NAV_MAX_WORDS = 16
NAV_MIN_RUN = 3


@dataclass(eq=False)
class Node:
    kind: str  # root | heading | clause
    block: Block | None  # the heading block, or the clause's first block
    label: str
    level: int = 0  # heading level (ingestion, per document); unused for clauses
    number: ClauseNumber | None = None
    parent: Node | None = None
    items: list = field(default_factory=list)  # Block | Node, in reading order
    suspect: list[str] = field(default_factory=list)  # structural doubts, e.g. possible missed heading

    @property
    def title_only(self) -> bool:
        """A clause whose block is just its title ("5.1. General Procurement Threshold"): like a
        heading, its text belongs in the section path of its content, not in a chunk of its own."""
        return self.kind == "clause" and norm(self.label) == norm(self.block.text)

    @property
    def is_heading_like(self) -> bool:
        return self.kind == "heading" or self.title_only

    def ancestors(self) -> list[Node]:
        out, node = [], self
        while node is not None and node.kind != "root":
            out.append(node)
            node = node.parent
        return out[::-1]

    def path(self) -> list[str]:
        return [n.label for n in self.ancestors()]

    def nearest_clause(self) -> Node | None:
        node = self
        while node is not None and node.kind != "root":
            if node.kind == "clause":
                return node
            node = node.parent
        return None

    def nearest_numbered(self) -> Node | None:
        """Nearest numbered node, clause or numbered heading ("PR1.1", "11.4.9", "5002.1")."""
        node = self
        while node is not None and node.kind != "root":
            if node.number is not None:
                return node
            node = node.parent
        return None

    def subtree_blocks(self) -> list[Block]:
        out = [self.block] if self.kind == "heading" and self.block is not None else []
        for item in self.items:
            out.extend(item.subtree_blocks() if isinstance(item, Node) else [item])
        return out

    def child_nodes(self) -> list[Node]:
        return [i for i in self.items if isinstance(i, Node)]


@dataclass
class Tree:
    root: Node
    node_of: dict[str, Node]  # block_id -> node the block belongs to (heading blocks -> their node)
    prev_in_node: dict[str, Block | None]  # block_id -> previous content block of the same node
    grid_block_ids: set[str]
    diagnostics: list[dict]


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _comparable(a: ClauseNumber, b: ClauseNumber) -> bool:
    """Decimal/integer numbers are one scheme, prefixed (PR1.1) another; they never nest."""
    return (a.kind == "prefixed") == (b.kind == "prefixed")


def clause_label(block: Block, number: ClauseNumber) -> str:
    """Short label for the section path: the clause's title if it has one, else its number."""
    marker = block.marker or number.label
    rest = block.text[len(marker):].strip() if block.text.startswith(marker) else block.text
    words = rest.split()
    if words and len(words) <= TITLE_MAX_WORDS and not rest.endswith((".", ";", ":", ",")):
        return f"{marker} {rest}"  # a title-only clause: "5.1. General Procurement Threshold"
    term = re.match(r"^([^:.;]{2,80}):\s", rest)  # definitions: "4.3. Award: The presentation of ..."
    if term and len(term.group(1).split()) <= TERM_MAX_WORDS:
        return f"{marker} {term.group(1)}"
    # "Title. Body": "4.2 Remote and Wireless Access. Remote and wireless Access to ..."
    # A period after a single capital letter is an initial ("U. T. Austin"), not a sentence end.
    for m in re.finditer(r"(?<!\b[A-Z])\.\s+(?=[A-Z])", rest):
        title = rest[: m.start()]
        if len(title.split()) <= TERM_MAX_WORDS:
            return f"{marker} {title}"
        break
    return number.label


def _place(stack: list[Node], node: Node) -> None:
    while len(stack) > 1:
        top = stack[-1]
        if node.number and top.number and _comparable(top.number, node.number) and top.number.is_ancestor_of(node.number):
            break
        if top.kind == "clause":
            stack.pop()
            continue
        if node.number and top.number and _comparable(top.number, node.number):
            stack.pop()  # a numbered heading that is not our ancestor: sibling or cousin ("5.7" closes "5.6.")
            continue
        if node.kind == "clause":
            break  # clauses never close unnumbered headings
        if top.level >= node.level:
            stack.pop()
            continue
        break
    node.parent = stack[-1]
    stack[-1].items.append(node)
    stack.append(node)


def _numbered_context(stack: list[Node]) -> list[Node]:
    """Open numbered nodes since the last unnumbered heading (innermost last)."""
    out = []
    for n in reversed(stack):
        if n.kind == "root" or (n.kind == "heading" and n.number is None):
            break
        if n.number is not None:
            out.append(n)
    return out[::-1]


def _is_clause(num: ClauseNumber, block: Block, stack: list[Node], prev: Block | None, diag: list[dict]) -> bool:
    context = _numbered_context(stack)
    if num.kind == "integer":
        if context:
            inner = context[-1]
            # a sibling of an open integer clause (Penn 1, 2, 3 ...); otherwise a list inside a clause
            return inner.kind == "clause" and inner.number.kind == "integer"
        if prev is not None and prev.text.rstrip().endswith(":"):
            return False  # "... expenditures be: 1. Reasonable and necessary" is a list
        return len(block.text.split()) >= INTEGER_CLAUSE_MIN_WORDS
    comparable = [n for n in context if _comparable(n.number, num)]
    if any(n.number.is_ancestor_of(num) for n in comparable) or len(num.parts) == 1 or not comparable:
        return True
    if any(len(n.number.parts) == len(num.parts) and n.number.parts[:-1] == num.parts[:-1] for n in comparable):
        return True  # sibling of an open clause whose parent was never printed
    if num.parts[-1] == "1":
        inner = comparable[-1]
        if inner.number.root != num.root:
            diag.append({
                "code": "clause_family_changed",
                "block_id": block.block_id,
                "page": block.page,
                "message": f"clause {num.label} follows {inner.number.label} with no heading between "
                           f"(possible missed heading)",
            })
        return True
    diag.append({
        "code": "out_of_sequence_clause_number",
        "block_id": block.block_id,
        "page": block.page,
        "message": f"{num.label} inside {comparable[-1].number.label}: kept as content of that clause",
    })
    return False


def detect_navigation(blocks: list[Block], all_blocks: list[Block] | None = None) -> set[str]:
    """Runs of >= 3 short non-heading blocks that repeat headings appearing later in the document
    (tables of contents, 'Policy Sections' link lists, 'Procedure Outline'). One short
    non-matching block may sit inside a run (an entry whose heading ingestion missed).
    `all_blocks` (the whole document) supplies the headings, so entries pointing to excluded
    sections (UT's "Revision History") are recognized too."""
    all_blocks = all_blocks or blocks
    order = {b.block_id: i for i, b in enumerate(all_blocks)}
    heading_at = [(order[b.block_id], norm(b.text)) for b in all_blocks if b.kind == "heading"]

    def matches(_: int, b: Block) -> bool:
        i = order[b.block_id]
        if b.kind in ("heading", "table") or len(b.text.split()) > NAV_MAX_WORDS:
            return False
        nb = norm(b.text)
        if len(nb) < 3:
            return False
        words = set(nb.split())
        for j, nh in heading_at:
            if j <= i or not nh:
                continue
            if nb == nh:
                return True
            short, long_ = sorted((nb, nh), key=len)
            if len(short) >= 8 and long_.startswith(short) and len(short) >= 0.6 * len(long_):
                return True
            hw = set(nh.split())
            if len(words & hw) / len(words | hw) >= 0.75:
                return True
        return False

    hits = [matches(i, b) for i, b in enumerate(blocks)]
    short = [b.kind != "heading" and len(b.text.split()) <= NAV_MAX_WORDS for b in blocks]
    out: set[str] = set()
    i = 0
    while i < len(blocks):
        if not hits[i]:
            i += 1
            continue
        j, members, n_hits = i, [], 0
        while j < len(blocks):
            if hits[j]:
                members.append(j)
                n_hits += 1
                j += 1
            elif short[j] and j + 1 < len(blocks) and hits[j + 1] and blocks[j].kind != "heading":
                members.append(j)  # single gap inside a run
                j += 1
            else:
                break
        if n_hits >= NAV_MIN_RUN:
            out.update(blocks[k].block_id for k in members)
        i = max(j, i + 1)
    return out


def detect_grid_regions(blocks: list[Block]) -> list[list[Block]]:
    """Borderless tables that ingestion read cell by cell: runs of short paragraph fragments whose
    x-position keeps jumping between columns. Returned as regions (lists of blocks)."""

    def fragment(b: Block) -> bool:
        return b.kind in ("paragraph", "list_item") and b.number is None and len(b.text.split()) <= GRID_MAX_WORDS

    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(blocks):
        j = i
        while j < len(blocks) and fragment(blocks[j]):
            j += 1
        if j - i >= GRID_MIN_RUN:
            xs = [b.bbox[0] for b in blocks[i:j]]
            switches = sum(1 for a, b in zip(xs, xs[1:]) if abs(a - b) > GRID_COLUMN_GAP)
            if switches >= GRID_MIN_SWITCHES:
                runs.append((i, j))
        i = max(j, i + 1)
    regions: list[list[int]] = []
    for start, end in runs:  # bridge short interruptions (a long cell) between runs
        if regions and start - regions[-1][-1] - 1 <= GRID_BRIDGE and all(
            blocks[k].kind != "heading" for k in range(regions[-1][-1] + 1, start)
        ):
            regions[-1].extend(range(regions[-1][-1] + 1, end))
        else:
            regions.append(list(range(start, end)))
    return [[blocks[k] for k in region] for region in regions]


def find_missed_headings(blocks: list[Block]) -> dict[str, tuple[int, str]]:
    """block_id -> (heading level, evidence) for paragraphs that are really section headings."""
    out: dict[str, tuple[int, str]] = {}
    enclosing: Block | None = None  # last heading that implies a number
    for i, b in enumerate(blocks):
        if b.kind == "heading":
            if implied_number(b.text):
                enclosing = b
            continue
        n = implied_number(b.text)
        if (
            enclosing is None
            or b.kind != "paragraph"
            or n is None
            or len(b.text.split()) > PROMOTE_MAX_WORDS
            or n == implied_number(enclosing.text)
            or abs(b.size - enclosing.size) > PROMOTE_SIZE_TOLERANCE * enclosing.size
        ):
            continue
        for nxt in blocks[i + 1: i + 1 + PROMOTE_LOOKAHEAD]:
            num = parse_number(nxt.number, nxt.marker)
            if num is not None:
                if num.root == n:
                    out[b.block_id] = (enclosing.heading_level or 99, f"next clause {num.label}; same size as '{enclosing.text}'")
                    enclosing = b
                break
    return out


LOCAL_ENUM = re.compile(r"^\(?([a-z]|[ivx]{1,4})[.)]$")


def continues_list(prev: Block | None, heading: Block) -> bool:
    """A 'heading' that is really the next item of the list right before it: UConn's bold
    'd. TOTAL - $50/day' after 'a. Breakfast', 'b. Lunch', 'c. Dinner'."""
    if prev is None or prev.kind != "list_item" or not heading.marker or not prev.marker:
        return False
    a, b = LOCAL_ENUM.match(prev.marker), LOCAL_ENUM.match(heading.marker)
    if not a or not b or len(a.group(1)) != 1 or len(b.group(1)) != 1:
        return False
    return ord(b.group(1)) == ord(a.group(1)) + 1 and abs(prev.bbox[0] - heading.bbox[0]) <= 3


def reference_numbers(blocks: list[Block]) -> set[str]:
    """Block ids whose multi-level number is not part of the document's own numbering: nothing else
    in the document is its parent, sibling or child. Stanford's "Related Policies" list other memos
    ("3.2.1 Responsibility for University Funds", "5.3.3 Purchasing Cards") and its title carries
    its own memo number ("5.1.1 Procurement Policies"); none of these are clauses of the document."""
    numbered = [(b, parse_number(b.number, b.marker)) for b in blocks if b.kind in ("heading", "list_item")]
    keys = {n.parts for _, n in numbered if n is not None}
    out = set()
    for b, n in numbered:
        if n is None or len(n.parts) < 2:
            continue
        parent = n.parts[:-1]
        related = parent in keys or any(
            k != n.parts and (k[:-1] == parent and len(k) == len(n.parts) or k[: len(n.parts)] == n.parts) for k in keys
        )
        if not related:
            out.add(b.block_id)
    return out


def build_tree(blocks: list[Block]) -> Tree:
    """`blocks`: the document's retrievable blocks in reading order."""
    diag: list[dict] = []
    references = reference_numbers(blocks)
    for b in blocks:
        if b.block_id in references:
            diag.append({"code": "reference_number_ignored", "block_id": b.block_id, "page": b.page,
                         "message": f"{b.number} in {b.text[:50]!r} has no parent, sibling or child in the document: not a clause"})
    root = Node("root", None, "")
    stack = [root]
    prev: Block | None = None
    promoted = find_missed_headings(blocks)
    for b in blocks:
        if b.kind == "heading" and continues_list(prev, b):
            diag.append({"code": "heading_demoted", "block_id": b.block_id, "page": b.page,
                         "message": f"{b.text!r} continues the list before it ({prev.marker}): kept as a list item"})
            stack[-1].items.append(b)
            prev = b
            continue
        if b.kind == "heading" or b.block_id in promoted:
            num = None if b.block_id in references else parse_number(b.number, b.marker)
            level = b.heading_level or 99
            if b.block_id in promoted:
                level, evidence = promoted[b.block_id]
                diag.append({"code": "heading_recovered", "block_id": b.block_id, "page": b.page,
                             "message": f"paragraph {b.text!r} used as a section heading ({evidence})"})
            _place(stack, Node("heading", b, b.text, level=level, number=num))
            continue
        num = parse_number(b.number, b.marker) if b.kind == "list_item" and b.block_id not in references else None
        if num is not None and _is_clause(num, b, stack, prev, diag):
            node = Node("clause", b, clause_label(b, num), number=num)
            _place(stack, node)
            node.items.append(b)
        else:
            stack[-1].items.append(b)
        prev = b

    node_of: dict[str, Node] = {}
    prev_in_node: dict[str, Block | None] = {}

    def index(node: Node) -> None:
        if node.kind == "heading":
            node_of[node.block.block_id] = node
        last: Block | None = None
        for item in node.items:
            if isinstance(item, Node):
                index(item)
            else:
                node_of[item.block_id] = node
                prev_in_node[item.block_id] = last
                last = item

    index(root)
    _check_implied_numbers(root, diag)
    grid = {b.block_id for region in detect_grid_regions(blocks) for b in region}
    return Tree(root, node_of, prev_in_node, grid, diag)


def _check_implied_numbers(root: Node, diag: list[dict]) -> None:
    """Flag clauses whose number contradicts the enclosing heading ('9.1' under 'Standard 8'):
    the heading for their own section was probably not detected, so their path is wrong."""

    def walk(node: Node, implied: str | None) -> None:
        if node.kind == "heading":
            implied = node.number.root if node.number and node.number.kind != "prefixed" else implied_number(node.label)
        if node.kind == "clause" and implied and node.number.kind == "decimal" and node.number.root != implied:
            node.suspect.append(f"clause {node.number.label} is under a heading numbered {implied}: section path may be wrong")
            if node.parent.kind != "clause":  # report once per family, at its top
                diag.append({
                    "code": "clause_outside_numbered_section",
                    "block_id": node.block.block_id,
                    "page": node.block.page,
                    "message": f"clause {node.number.label} under '{node.parent.label}'",
                })
        for child in node.child_nodes():
            walk(child, implied)

    walk(root, None)
