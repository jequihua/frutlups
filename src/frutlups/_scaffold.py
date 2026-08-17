"""Private configured-scaffold renderer for prompt generation (M003-S03).

This module is the smallest deterministic, heading-aware slot renderer that
honors the selected layout's configured prompt template path and configured
required-section list. It is **not** a template language: the only slot token
is the existing template-v3 ``TBD`` placeholder in fixed positions, there are
no braces, named variables, loops, conditionals, includes, expressions,
filters, evaluation, or recursive expansion, and substitution runs in one
pass from positions discovered in the original validated scaffold.

Slot forms (exactly these):

- workflow line slot — inside the scaffold's one fenced YAML workflow block,
  a line ``<field>: TBD`` for the configured milestone/slice field names;
- list slot — a ``- TBD`` line inside an owned section body, replaced by one
  ``- <item>`` line per typed item, preserving the slot line's indentation;
- prose slot — a ``TBD`` line inside an owned section body, replaced by typed
  prose text;
- path slot — a ````TBD`` line inside an owned section body, replaced by a
  backticked typed path.

Structure is enforced document-wide over the original scaffold: Markdown
fences (backtick or tilde runs of at least three identical characters, opened
with up to three leading spaces, and closed only by the same character, in a
run at least as long as the opener, indented zero to three spaces, with only
spaces/tabs after the run) keep headings inside them out of the H2 contract;
exactly one fenced YAML/YML block may carry the configured milestone/slice
routing fields (slot or rendered form); and every exact supported slot-form
position the scaffold itself authors — workflow ``<field>: TBD`` lines and
the exact ``TBD`` / ``- TBD`` / ````TBD`` forms, wherever they occur in the
scaffold — must be the one declared owned position consumed exactly once or a
refusal. A typed value substituted at a consumed position is data even when
its rendered bytes equal a slot form (the exact-substitution setext matrix
pins this). Inserted values are data: any value containing a line that the same scanner would recognize
as a fence opener is refused before substitution, the rendered body's
fence-topology signature (character, opener length, language, order, and
count, ignoring line shifts) must equal the scaffold's, and the rendered
body's all-heading topology — every live ATX level 1-6 heading and setext
level 1-2 heading outside fenced blocks, recorded as kind, level, exact
text, order, and count — must equal the scaffold's. Arbitrary prose
mentioning ``TBD`` and ordinary inline backticks, tildes, or hashes remain
data. The all-heading inertness check is separate from the exact ATX
level-two required-section contract, which observes only H2.

The required-section contract: ATX level-two headings outside fenced blocks,
every configured name exactly once, exact spelling, configured order;
additional scaffold-owned headings are permitted. The rendered body must keep
exactly the scaffold's heading set and order and still satisfy the contract.
A scaffold with a leading ``---`` metadata frame is refused: generated
prompts keep exactly one workflow routing region, the fenced block.

Every diagnostic uses owned fixed vocabulary — owner (``coding``/``review``),
a canonical semantic role (``milestone``/``slice``) or fixed section role
label, and a fixed defect class — individually capped at 240 characters, and
never echoes a configured physical field or section name, a path value, an
inserted value, scaffold content, exception text, a secret marker, or a
machine-local absolute path.

Public surface: none. This module is private (``frutlups._scaffold``) and is
not re-exported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from frutlups.layout import is_safe_relative, normalize_section
from frutlups.prompt_template import _is_within

_MAX_DIAGNOSTIC = 240
_TBD_WORD = re.compile(r"\bTBD\b")
_HEADING = re.compile(r"^## (.+?)\s*$")
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_YAML_LANGS = ("yaml", "yml")
_WORKFLOW_ROLES = ("milestone", "slice")


@dataclass(frozen=True)
class ScaffoldSlot:
    """One owned section slot: its form, fixed role label, and typed values.

    ``kind`` is ``"list"`` (``- TBD`` -> one item line per value), ``"prose"``
    (``TBD`` -> the single value as prose), or ``"path"`` (`````TBD````` ->
    the single value backticked). ``label`` is the fixed semantic role label
    used in diagnostics; it is never a configured name.
    """

    kind: str
    values: tuple[str, ...]
    label: str = ""


def _cap(message: str) -> str:
    """Individually cap one diagnostic at the accepted 240-character bound."""

    if len(message) <= _MAX_DIAGNOSTIC:
        return message
    return message[: _MAX_DIAGNOSTIC - 3] + "..."


def _fail(errors: list[str] | tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """Central diagnostic bounding: every return path is individually capped."""

    return "", tuple(_cap(error) for error in dict.fromkeys(errors))


def _read_scaffold(root: Path, template_rel: str, owner: str) -> tuple[str, tuple[str, ...]]:
    """Read the configured scaffold once, safely, or return bounded errors."""

    if not isinstance(template_rel, str) or not is_safe_relative(template_rel):
        return "", (f"configured {owner} template path is not a safe repo-relative path",)
    candidate = root / PurePosixPath(template_rel.strip().replace("\\", "/"))
    try:
        resolved_root = root.resolve(strict=False)
        resolved = candidate.resolve(strict=False)
    except OSError:
        return "", (f"configured {owner} template path could not be resolved",)
    if not _is_within(resolved, resolved_root):
        return "", (f"configured {owner} template resolves outside the project root",)
    if not resolved.is_file():
        return "", (f"configured {owner} template is missing or not a file",)
    try:
        data = resolved.read_bytes()
    except OSError:
        return "", (f"configured {owner} template could not be read",)
    try:
        return data.decode("utf-8"), ()
    except UnicodeDecodeError:
        return "", (f"configured {owner} template is not valid UTF-8",)


def _closing_fence(line: str, char: str, length: int) -> bool:
    """Whether ``line`` is a valid closing fence for ``(char, length)``.

    Zero to three leading ASCII spaces (a four-space indent or a leading tab
    is content, not a closer), a run of the same fence character at least as
    long as the opener, and only spaces/tabs after the run.
    """

    indent = len(line) - len(line.lstrip(" "))
    if indent > 3:
        return False
    body = line[indent:]
    run_length = len(body) - len(body.lstrip(char))
    if run_length < length:
        return False
    return all(c in " \t" for c in body[run_length:])


def _scan_document(
    lines: list[str],
) -> tuple[list[tuple[int, str]], list[tuple[int, int]], tuple[tuple[str, int, str], ...], bool]:
    """One full-document structural scan.

    Returns ``(headings, yaml_spans, fence_signature, has_unterminated_fence)``
    where ``headings`` is a list of ``(line_index, name)`` for ATX level-two
    headings outside every fence, ``yaml_spans`` is a list of
    ``(opener_index, closer_index)`` for fenced blocks whose normalized
    info-string language is exactly ``yaml`` or ``yml``, and
    ``fence_signature`` is the ordered ``(character, opener_length, language)``
    of every closed fence — a stable topology signature that ignores
    line-number shifts but preserves character, length, language, order, and
    count.
    """

    headings: list[tuple[int, str]] = []
    yaml_spans: list[tuple[int, int]] = []
    signature: list[tuple[str, int, str]] = []
    in_fence: tuple[str, int, str, int] | None = None  # char, length, lang, opener
    for index, line in enumerate(lines):
        if in_fence is None:
            match = _FENCE_OPEN.match(line)
            if match:
                fence_run = match.group(1)
                info = match.group(2).strip()
                lang = info.split()[0].lower() if info else ""
                in_fence = (fence_run[0], len(fence_run), lang, index)
                continue
            heading = _HEADING.match(line)
            if heading:
                headings.append((index, heading.group(1).strip()))
            continue
        if _closing_fence(line, in_fence[0], in_fence[1]):
            if in_fence[2] in _YAML_LANGS:
                yaml_spans.append((in_fence[3], index))
            signature.append((in_fence[0], in_fence[1], in_fence[2]))
            in_fence = None
    return headings, yaml_spans, tuple(signature), in_fence is not None


def _value_has_fence_opener(value: str) -> bool:
    """Whether an inserted value contains a live structural fence opener line."""

    return any(_FENCE_OPEN.match(line) for line in value.splitlines())


_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t].*)?$")
_SETEXT_UNDERLINE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_UL_MARKER = re.compile(r"^ {0,3}[-+*](?=\s|$)")
_OL_MARKER = re.compile(r"^ {0,3}\d{1,9}[.)](?=\s|$)")
_QUOTE_MARKER = re.compile(r"^ {0,3}>")


def _is_thematic_break(line: str) -> bool:
    """At least three matching ``*``, ``-``, or ``_`` with only whitespace."""

    indent = len(line) - len(line.lstrip(" "))
    if indent > 3:
        return False
    compact = line.strip().replace(" ", "").replace("\t", "")
    return len(compact) >= 3 and compact[0] in "*-_" and set(compact) == {compact[0]}


def _is_indented_code(line: str) -> bool:
    """Whether a nonblank line begins a Markdown indented-code block.

    Only the leading run of ASCII spaces and tabs before the first
    non-whitespace character counts: a space advances one column and a tab
    advances to the next multiple-of-four column stop. The line is indented
    code when the resulting indentation reaches at least four columns. Tabs
    after the first non-whitespace character are content, not indentation.
    """

    column = 0
    for char in line:
        if char == " ":
            column += 1
        elif char == "\t":
            column += 4 - (column % 4)
        else:
            break
    else:
        return False  # whitespace-only line: blank, not code
    return column >= 4


@dataclass(frozen=True)
class _LiveMarkdownEvent:
    """One live content line or heading from the accepted scaffold scanner."""

    line: str | None = None
    heading: tuple[str, int, tuple[str, ...]] | None = None


def _live_markdown_events(lines: list[str]) -> tuple[_LiveMarkdownEvent, ...]:
    """Classify live content and headings with the accepted Markdown rules.

    Ordinary paragraph lines are held only until the next line determines
    whether they are setext heading text. Fenced and indented-code bytes are
    absent from the result; every other non-heading live line is retained in
    order. This is the single private scanner authority used by both scaffold
    topology validation and consumers that need section-aware live content.
    """

    events: list[_LiveMarkdownEvent] = []
    paragraph: list[str] = []
    in_fence: tuple[str, int] | None = None  # char, length

    def flush_paragraph() -> None:
        events.extend(_LiveMarkdownEvent(line=item) for item in paragraph)
        paragraph.clear()

    for line in lines:
        if in_fence is not None:
            if _closing_fence(line, in_fence[0], in_fence[1]):
                in_fence = None
            continue

        fence = _FENCE_OPEN.match(line)
        if fence:
            flush_paragraph()
            fence_run = fence.group(1)
            in_fence = (fence_run[0], len(fence_run))
            continue
        if _ATX_HEADING.match(line):
            flush_paragraph()
            hashes = len(line.strip()) - len(line.strip().lstrip("#"))
            events.append(
                _LiveMarkdownEvent(
                    heading=("atx", hashes, (line.strip(),)),
                )
            )
            continue
        if _SETEXT_UNDERLINE.match(line):
            if paragraph:
                underline = line.strip()
                level = 1 if underline.startswith("=") else 2
                events.append(
                    _LiveMarkdownEvent(
                        heading=("setext", level, tuple(paragraph)),
                    )
                )
                paragraph.clear()
            else:
                # An underline without eligible paragraph text is live
                # content, but not a heading.
                events.append(_LiveMarkdownEvent(line=line))
            continue
        if not line.strip():
            flush_paragraph()
            events.append(_LiveMarkdownEvent(line=line))
            continue
        if _is_indented_code(line):
            flush_paragraph()
            continue
        if (
            _QUOTE_MARKER.match(line)
            or _UL_MARKER.match(line)
            or _OL_MARKER.match(line)
            or _is_thematic_break(line)
        ):
            flush_paragraph()
            events.append(_LiveMarkdownEvent(line=line))
            continue
        paragraph.append(line)

    flush_paragraph()
    return tuple(events)


def _heading_topology(lines: list[str]) -> tuple[tuple[str, int, tuple[str, ...]], ...]:
    """The ordered all-heading topology of a document.

    Records every live Markdown heading outside fenced blocks: ATX levels
    1-6 as ``("atx", level, (line_text,))`` and setext levels 1-2 as
    ``("setext", level, (text_line, ...))`` carrying **every** ordered
    paragraph line of the heading as its exact original line string —
    leading and trailing spaces and tabs are part of the identity, never
    stripped, normalized, case-folded, or tab-expanded — so a change to the
    first, middle, or final text line, or to its whitespace, changes the
    signature. Fence tracking uses the accepted opener/closer rules, and
    indented-code classification expands leading spaces/tabs to four-column
    stops.

    Setext eligibility uses bounded paragraph-versus-block conditions, never
    a raw first character: consecutive ordinary paragraph lines (including
    ``#tag``, emphasis, code spans, tildes, ``-word``/``+word``, and escaped
    markers) accumulate until a blank line or an actual block start — an ATX
    heading, a fence, a block quote (``>``), a list marker (``-``/``+``/``*``
    or a numeric marker followed by required whitespace), a thematic break,
    an indented-code start, or a completed setext heading — resets the
    paragraph. A valid ``=`` or ``-`` underline turns the accumulated
    paragraph into exactly one setext heading; an underline with no eligible
    paragraph text is not a heading. The signature preserves kind, level,
    exact text, order, and count while ignoring line-number shifts.
    """

    return tuple(
        event.heading
        for event in _live_markdown_events(lines)
        if event.heading is not None
    )


def _required_section_errors(
    headings: list[tuple[int, str]], required: tuple[str, ...]
) -> tuple[str, ...]:
    """Each required name exactly once, exact spelling, configured order.

    Diagnostics name only the fixed section ordinal and defect class, never
    the configured name.
    """

    if not required or any(not isinstance(name, str) or not name.strip() for name in required):
        return ("required-section configuration is empty or malformed",)
    required_names = [name.strip() for name in required]
    counts: dict[str, int] = {}
    seen_order: list[str] = []
    for _, name in headings:
        if name in required_names:
            counts[name] = counts.get(name, 0) + 1
            seen_order.append(name)
    errors: list[str] = []
    for position, name in enumerate(required_names, 1):
        count = counts.get(name, 0)
        if count == 0:
            errors.append(f"required section {position} is missing")
        elif count > 1:
            errors.append(f"required section {position} appears more than once")
    expected = [name for name in required_names if counts.get(name)]
    if seen_order != expected:
        broken = next(
            (index for index, name in enumerate(expected)
             if index >= len(seen_order) or seen_order[index] != name),
            0,
        )
        errors.append(f"required section {broken + 1} is out of configured order")
    return tuple(errors)


def _block_has_routing(
    lines: list[str], span: tuple[int, int], fields: tuple[str, str]
) -> bool:
    """Whether a YAML block carries a configured milestone/slice routing field."""

    for index in range(span[0] + 1, span[1]):
        stripped = lines[index].strip()
        if any(stripped.startswith(f"{field}:") for field in fields):
            return True
    return False


def render_configured_scaffold(
    *,
    root: Path,
    template_rel: str,
    required_sections: tuple[str, ...],
    workflow_values: tuple[tuple[str, str], tuple[str, str]],
    section_slots: dict[str, ScaffoldSlot],
    owner: str,
) -> tuple[str, tuple[str, ...]]:
    """Render one configured scaffold body in one pass, or bounded errors.

    ``workflow_values`` is ``((milestone_field, value), (slice_field, value))``
    using the profile's configured field names. ``section_slots`` maps the
    normalized owned section heading to its :class:`ScaffoldSlot`. Returns
    ``(content, errors)``; on any error ``content`` is ``""``.
    """

    scaffold, errors = _read_scaffold(root, template_rel, owner)
    if errors:
        return "", errors
    lines = scaffold.splitlines()
    if lines and lines[0] == "---":
        return _fail(
            [
                f"configured {owner} template must not open a leading metadata frame; "
                "workflow metadata belongs in the fenced block"
            ]
        )

    scaffold_topology = _heading_topology(scaffold.splitlines())
    headings, yaml_spans, fence_signature, unterminated = _scan_document(lines)
    if unterminated:
        return _fail([f"configured {owner} template has invalid fence structure"])
    errors = list(_required_section_errors(headings, required_sections))
    if errors:
        return _fail(errors)

    # Inserted values are data: none may introduce a live fence opener line.
    for _, value in workflow_values:
        if _value_has_fence_opener(value):
            return _fail(["inserted value would alter document structure"])
    for slot in section_slots.values():
        for value in slot.values:
            if _value_has_fence_opener(value):
                return _fail(["inserted value would alter document structure"])

    fields = tuple(field for field, _ in workflow_values)
    routing_spans = [span for span in yaml_spans if _block_has_routing(lines, span, fields)]
    if not routing_spans:
        return _fail([f"configured {owner} template has no fenced yaml workflow block"])
    if len(routing_spans) > 1:
        return _fail([f"configured {owner} template has more than one workflow routing region"])
    routing_span = routing_spans[0]

    errors = []
    consumed: set[int] = set()

    # --- workflow line slots, inside the one routing block only ---
    slot_counts = [0, 0]
    for index in range(routing_span[0] + 1, routing_span[1]):
        stripped = lines[index].strip()
        for slot_index, (field, value) in enumerate(workflow_values):
            if stripped == f"{field}: TBD":
                indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
                lines[index] = f"{indent}{field}: {value}"
                slot_counts[slot_index] += 1
                consumed.add(index)
    for slot_index, role in enumerate(_WORKFLOW_ROLES):
        if slot_counts[slot_index] == 0:
            errors.append(f"workflow metadata slot '{role}' is missing")
        elif slot_counts[slot_index] > 1:
            errors.append(f"workflow metadata slot '{role}' is duplicated")

    # A configured-field slot line outside the routing block is unconsumed.
    for index, line in enumerate(lines):
        if routing_span[0] < index < routing_span[1]:
            continue
        stripped = line.strip()
        if any(stripped == f"{field}: TBD" for field in fields):
            errors.append("unconsumed TBD placeholder remains after rendering")
            break

    # --- section slots, in owned section bodies only ---
    heading_indices = [index for index, _ in headings] + [len(lines)]
    for position, (_, name) in enumerate(headings):
        normalized = normalize_section(name)
        if normalized not in section_slots:
            continue
        slot = section_slots[normalized]
        slot_lines = [
            index
            for index in range(heading_indices[position] + 1, heading_indices[position + 1])
            if _slot_form(lines[index]) == slot.kind
        ]
        if not slot_lines:
            errors.append(f"expected slot missing in section '{slot.label}'")
        elif len(slot_lines) > 1:
            errors.append(f"duplicate slot in section '{slot.label}'")
        else:
            index = slot_lines[0]
            indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
            if slot.kind == "list":
                lines[index] = "\n".join(f"{indent}- {item}" for item in slot.values)
            elif slot.kind == "prose":
                lines[index] = f"{indent}{slot.values[0]}"
            else:
                lines[index] = f"{indent}`{slot.values[0]}`"
            consumed.add(index)

    # Every other exact slot-form position in the document is a refusal.
    for index, line in enumerate(lines):
        if index in consumed:
            continue
        if _slot_form(line):
            errors.append("unconsumed TBD placeholder remains after rendering")
            break

    if errors:
        return _fail(errors)

    content = "\n".join(lines).rstrip() + "\n"
    content_lines = content.splitlines()

    # One pass, no injection: identical heading set/order, identical fence
    # topology, exactly one workflow routing region, and the contract still
    # satisfied.
    final_headings, final_yaml_spans, final_signature, final_unterminated = _scan_document(
        content_lines
    )
    if final_unterminated or final_signature != fence_signature:
        return _fail(["rendered body fence structure differs from the scaffold's"])
    if _heading_topology(content_lines) != scaffold_topology:
        return _fail(["rendered body headings differ from the scaffold's headings"])
    if [name for _, name in final_headings] != [name for _, name in headings]:
        return _fail(["rendered body headings differ from the scaffold's headings"])
    final_routing = [
        span for span in final_yaml_spans if _block_has_routing(content_lines, span, fields)
    ]
    if len(final_routing) != 1:
        return _fail(
            [f"configured {owner} template does not yield exactly one workflow routing region"]
        )
    errors = list(_required_section_errors(final_headings, required_sections))
    if errors:
        return _fail(errors)
    return content, ()


def _slot_form(line: str) -> str:
    """The slot form of a line: ``list``, ``prose``, ``path``, or ``""``."""

    stripped = line.strip()
    if stripped == "- TBD":
        return "list"
    if stripped == "TBD":
        return "prose"
    if stripped == "`TBD`":
        return "path"
    return ""
