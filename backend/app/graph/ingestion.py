import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime

MAX_CHUNK_CHARACTERS = 4000
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_STABLE_CSV_KEYS = ("id", "source_id", "issue_id", "task_id", "key")
ENTITY_LABELS = frozenset({"Person", "Team", "Project", "Task", "Decision", "Skill", "Rationale"})
PROJECTED_PREDICATES = {
    "OWNS": "OWNS",
    "MEMBER_OF": "MEMBER_OF",
    "HAS_SKILL": "HAS_SKILL",
    "ASSIGNED_TO": "ASSIGNED_TO",
    "PARTICIPATED_IN": "PARTICIPATED_IN",
    "COMPLETED": "COMPLETED",
    "AUTHORED": "AUTHORED",
    "REVIEWED": "REVIEWED",
    "CONTAINS": "CONTAINS",
    "DEPENDS_ON": "DEPENDS_ON",
    "REFERENCES": "REFERENCES",
    "MENTIONS": "MENTIONS",
    "FIXES": "FIXES",
    "SUPERSEDES": "SUPERSEDES",
}
STRUCTURED_PREDICATES = frozenset(PROJECTED_PREDICATES) | {"DECISION_RATIONALE"}
PREDICATE_ENDPOINTS = {
    "OWNS": ({"Person", "Team"}, {"Project", "Task", "Decision"}),
    "MEMBER_OF": ({"Person"}, {"Team"}),
    "HAS_SKILL": ({"Person"}, {"Skill"}),
    "ASSIGNED_TO": ({"Person"}, {"Task"}),
    "PARTICIPATED_IN": ({"Person"}, {"Project", "Task", "Decision"}),
    "COMPLETED": ({"Person"}, {"Task"}),
    "AUTHORED": ({"Person"}, {"Project", "Task", "Decision"}),
    "REVIEWED": ({"Person"}, {"Project", "Task", "Decision"}),
    "CONTAINS": ({"Team", "Project"}, {"Project", "Task"}),
    "DEPENDS_ON": ({"Project", "Task"}, {"Project", "Task"}),
    "REFERENCES": (ENTITY_LABELS, {"Project", "Task", "Decision", "Skill"}),
    "MENTIONS": (ENTITY_LABELS, ENTITY_LABELS),
    "FIXES": ({"Task"}, {"Project", "Task"}),
    "SUPERSEDES": ({"Decision"}, {"Decision"}),
    "DECISION_RATIONALE": ({"Decision"}, {"Rationale"}),
}


class IngestionFormatError(ValueError):
    pass


@dataclass(frozen=True)
class SourceSection:
    section_key: str
    content: str
    content_hash: str
    citation_locator: str
    chunk_id: str


@dataclass(frozen=True)
class ParsedSource:
    revision_id: str
    sections: tuple[SourceSection, ...]


@dataclass(frozen=True)
class EntityRecord:
    id: str
    label: str
    name: str


@dataclass(frozen=True)
class FactRecord:
    id: str
    predicate: str
    subject_id: str
    object_id: str
    subject_label: str
    object_label: str
    quote: str
    valid_from: str | None
    valid_to: str | None
    status: str
    ownership_policy: str


@dataclass(frozen=True)
class StructuredGraph:
    entities: tuple[EntityRecord, ...]
    facts: tuple[FactRecord, ...]


def parse_source(source_id: str, content: str, file_format: str) -> ParsedSource:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    revision_id = _digest(normalized)
    format_name = file_format.lower().removeprefix(".")

    if format_name in {"md", "markdown"}:
        raw_sections = _markdown_sections(normalized)
    elif format_name in {"txt", "text"}:
        raw_sections = _text_sections(normalized)
    elif format_name == "json":
        raw_sections = _json_sections(normalized)
    elif format_name == "csv":
        raw_sections = _csv_sections(normalized)
    else:
        raise IngestionFormatError(f"Unsupported source format: {file_format}")

    sections = tuple(
        _make_section(source_id, revision_id, key, text, locator, part_index)
        for key, text, locator in raw_sections
        for part_index, (text, locator) in enumerate(_split_text(text, locator), start=1)
    )
    return ParsedSource(revision_id=revision_id, sections=sections)


def extract_structured_graph(content: str, sections: tuple[SourceSection, ...]) -> StructuredGraph:
    try:
        document = json.loads(content)
    except json.JSONDecodeError:
        return StructuredGraph((), ())
    if not isinstance(document, dict):
        return StructuredGraph((), ())

    raw_entities = document.get("entities", [])
    raw_facts = document.get("facts", [])
    if not isinstance(raw_entities, list) or not isinstance(raw_facts, list):
        raise IngestionFormatError("Structured entities and facts must be arrays")

    entities: list[EntityRecord] = []
    entities_by_id: dict[str, EntityRecord] = {}
    for raw in raw_entities:
        if not isinstance(raw, dict):
            raise IngestionFormatError("Each structured entity must be an object")
        entity_id = _required_string(raw, "id", "entity")
        label = _required_string(raw, "type", "entity")
        name = _required_string(raw, "name", "entity")
        if label not in ENTITY_LABELS:
            raise IngestionFormatError(f"Unsupported entity type: {label}")
        if entity_id in entities_by_id:
            raise IngestionFormatError(f"Duplicate entity ID: {entity_id}")
        entity = EntityRecord(entity_id, label, name)
        entities.append(entity)
        entities_by_id[entity_id] = entity

    section_text = [section.content for section in sections]
    facts: list[FactRecord] = []
    seen_fact_ids: set[str] = set()
    for raw in raw_facts:
        if not isinstance(raw, dict):
            raise IngestionFormatError("Each structured fact must be an object")
        predicate = _required_string(raw, "predicate", "fact").upper()
        if predicate not in STRUCTURED_PREDICATES:
            raise IngestionFormatError(f"Unsupported fact predicate: {predicate}")
        subject_id = _required_string(raw, "subject_id", "fact")
        object_id = _required_string(raw, "object_id", "fact")
        if subject_id not in entities_by_id or object_id not in entities_by_id:
            raise IngestionFormatError("Fact references must resolve to supplied stable entity IDs")
        subject_label = entities_by_id[subject_id].label
        object_label = entities_by_id[object_id].label
        allowed_subjects, allowed_objects = PREDICATE_ENDPOINTS[predicate]
        if subject_label not in allowed_subjects or object_label not in allowed_objects:
            raise IngestionFormatError(
                f"{predicate} does not support {subject_label} -> {object_label}"
            )
        quote = _required_string(raw, "quote", "fact")
        if not any(quote in text for text in section_text):
            raise IngestionFormatError("Fact quote is not present in any ingested evidence chunk")

        valid_from = _optional_string(raw.get("valid_from"), "valid_from")
        valid_to = _optional_string(raw.get("valid_to"), "valid_to")
        _validate_date(valid_from, "valid_from")
        _validate_date(valid_to, "valid_to")
        if valid_from and valid_to and valid_from >= valid_to:
            raise IngestionFormatError("valid_to must be later than valid_from")
        status = str(raw.get("status", "accepted")).lower()
        if status not in {"accepted", "candidate"}:
            raise IngestionFormatError("Fact status must be accepted or candidate")
        ownership_policy = raw.get("ownership_policy")
        if predicate == "OWNS":
            if ownership_policy is None:
                ownership_policy = "exclusive"
            if not isinstance(ownership_policy, str) or ownership_policy not in {
                "exclusive",
                "shared",
            }:
                raise IngestionFormatError("ownership_policy for OWNS must be exclusive or shared")
        else:
            if ownership_policy not in (None, "not_applicable"):
                raise IngestionFormatError("ownership_policy applies only to OWNS facts")
            ownership_policy = "not_applicable"
        fact_id = raw.get("id")
        if fact_id is None:
            fact_id = "fact:" + _digest(
                "\0".join(
                    (
                        predicate,
                        subject_id,
                        object_id,
                        valid_from or "",
                        valid_to or "",
                        ownership_policy,
                    )
                )
            )
        if not isinstance(fact_id, str) or not fact_id.strip():
            raise IngestionFormatError("Fact id must be a non-empty string")
        if fact_id in seen_fact_ids:
            raise IngestionFormatError(f"Duplicate fact ID: {fact_id}")
        seen_fact_ids.add(fact_id)
        facts.append(
            FactRecord(
                fact_id,
                predicate,
                subject_id,
                object_id,
                subject_label,
                object_label,
                quote,
                valid_from,
                valid_to,
                status,
                ownership_policy,
            )
        )
    return StructuredGraph(tuple(entities), tuple(facts))


def _validate_date(value: str | None, field_name: str) -> None:
    if value is None:
        return
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as error:
        raise IngestionFormatError(f"{field_name} must use YYYY-MM-DD format") from error
    if parsed.strftime("%Y-%m-%d") != value:
        raise IngestionFormatError(f"{field_name} must use YYYY-MM-DD format")


def _required_string(value: dict[str, object], key: str, record_type: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise IngestionFormatError(f"Each structured {record_type} needs a non-empty {key}")
    return result.strip()


def _optional_string(value: object, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise IngestionFormatError(f"{key} must be a non-empty string when provided")
    return value.strip()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _make_section(
    source_id: str,
    revision_id: str,
    section_key: str,
    content: str,
    citation_locator: str,
    part_index: int,
) -> SourceSection:
    key = section_key if part_index == 1 else f"{section_key}#part-{part_index:03}"
    chunk_id = _digest(f"{source_id}\0{revision_id}\0{key}")
    return SourceSection(
        section_key=key,
        content=content,
        content_hash=_digest(content),
        citation_locator=citation_locator,
        chunk_id=chunk_id,
    )


def _split_text(text: str, locator: str) -> list[tuple[str, str]]:
    if len(text) <= MAX_CHUNK_CHARACTERS:
        return [(text, locator)] if text.strip() else []

    pieces: list[tuple[str, str]] = []
    remaining = text
    part_number = 1
    while remaining:
        boundary = min(MAX_CHUNK_CHARACTERS, len(remaining))
        if boundary < len(remaining):
            newline = remaining.rfind("\n", 0, boundary)
            space = remaining.rfind(" ", 0, boundary)
            boundary = max(newline, space)
            if boundary <= 0:
                boundary = MAX_CHUNK_CHARACTERS
        piece = remaining[:boundary].strip()
        if piece:
            pieces.append((piece, f"{locator}, part {part_number}"))
        remaining = remaining[boundary:].lstrip()
        part_number += 1
    return pieces


def _text_sections(content: str) -> list[tuple[str, str, str]]:
    sections: list[tuple[str, str, str]] = []
    lines = content.splitlines()
    start = 0
    paragraph_number = 0
    paragraph: list[str] = []

    def flush(end: int) -> None:
        nonlocal paragraph_number, paragraph, start
        text = "\n".join(paragraph).strip()
        if text:
            paragraph_number += 1
            sections.append((f"paragraph-{paragraph_number:04}", text, f"lines {start + 1}-{end}"))
        paragraph = []

    for line_number, line in enumerate(lines, start=1):
        if line.strip():
            if not paragraph:
                start = line_number - 1
            paragraph.append(line)
        elif paragraph:
            flush(line_number - 1)
    if paragraph:
        flush(len(lines))
    return sections


def _markdown_sections(content: str) -> list[tuple[str, str, str]]:
    sections: list[tuple[str, str, str]] = []
    heading_path: list[str] = []
    occurrences: dict[tuple[str, ...], int] = {}
    current_key = "intro"
    current_heading = "Introduction"
    current_lines: list[str] = []
    start_line = 1

    def flush(end_line: int) -> None:
        text = "\n".join(current_lines).strip()
        if text:
            sections.append(
                (current_key, text, f"{current_heading}, lines {start_line}-{end_line}")
            )

    for line_number, line in enumerate(content.splitlines(), start=1):
        match = _HEADING.match(line)
        if not match:
            current_lines.append(line)
            continue

        flush(line_number - 1)
        level = len(match.group(1))
        title = match.group(2).strip()
        heading_path = heading_path[: level - 1]
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "section"
        base_path = tuple(heading_path + [slug])
        occurrences[base_path] = occurrences.get(base_path, 0) + 1
        unique_slug = slug if occurrences[base_path] == 1 else f"{slug}-{occurrences[base_path]}"
        heading_path.append(unique_slug)
        current_key = "/".join(heading_path)
        current_heading = " > ".join(heading_path)
        current_lines = [line]
        start_line = line_number

    flush(len(content.splitlines()))
    return sections


def _json_sections(content: str) -> list[tuple[str, str, str]]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise IngestionFormatError(f"Invalid JSON source: {error.msg}") from error

    if isinstance(value, dict):
        entries = list(value.items())
    elif isinstance(value, list):
        entries = [(str(index), item) for index, item in enumerate(value)]
    else:
        entries = [("value", value)]

    return [
        (
            f"json/{_json_pointer(str(key))}",
            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            f"JSON Pointer /{_json_pointer(str(key))}",
        )
        for key, item in entries
    ]


def _json_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _csv_sections(content: str) -> list[tuple[str, str, str]]:
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise IngestionFormatError("CSV source must include a header row")
    if len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise IngestionFormatError("CSV source contains duplicate column names")

    sections: list[tuple[str, str, str]] = []
    seen_keys: dict[str, int] = {}
    for row_number, row in enumerate(reader, start=2):
        if None in row:
            raise IngestionFormatError(f"CSV row {row_number} has more values than the header")
        stable_value = next(
            (row.get(name) for name in _STABLE_CSV_KEYS if row.get(name)),
            None,
        )
        base_key = str(stable_value) if stable_value else f"row-{row_number:06}"
        seen_keys[base_key] = seen_keys.get(base_key, 0) + 1
        section_key = base_key if seen_keys[base_key] == 1 else f"{base_key}-{seen_keys[base_key]}"
        sections.append(
            (
                f"csv/{section_key}",
                json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                f"CSV row {row_number}",
            )
        )
    return sections
