from app.graph.schema import INDEXES, NODE_LABELS, RELATIONSHIP_TYPES, GraphSchema


class FakeQueryResult:
    def __init__(self, header: list[str], rows: list[list[object]]) -> None:
        self.header = header
        self.result_set = rows


class FakeGraphClient:
    def __init__(self) -> None:
        self.indexes: set[tuple[str, str]] = set()
        self.full_text_index = False
        self.constraints: set[tuple[str, str, str]] = set()
        self.schema_writes: list[str] = []

    def query(self, cypher: str, params: dict[str, object] | None = None) -> FakeQueryResult:
        del params
        if cypher == "CALL db.indexes()":
            rows = [
                [label, [property_name], ["RANGE"]]
                for label, property_name in sorted(self.indexes)
            ]
            if self.full_text_index:
                rows.append(["Chunk", ["content"], ["FULLTEXT"]])
            return FakeQueryResult(
                ["label", "properties", "types"],
                rows,
            )
        if cypher == "CALL db.constraints()":
            return FakeQueryResult(
                ["type", "entitytype", "label", "properties", "status"],
                [
                    ["UNIQUE", "NODE", label, [property_name], "OPERATIONAL"]
                    for label, property_name, _ in sorted(self.constraints)
                ],
            )
        self.schema_writes.append(cypher)
        if cypher.startswith("CREATE INDEX"):
            label_and_property = cypher.removeprefix("CREATE INDEX FOR (n:").removesuffix(")")
            label, property_expression = label_and_property.split(") ON (n.")
            self.indexes.add((label, property_expression))
        if cypher.startswith("CALL db.idx.fulltext.createNodeIndex"):
            self.full_text_index = True
        return FakeQueryResult([], [])

    def create_unique_constraint(self, label: str, property_name: str) -> None:
        self.schema_writes.append(f"UNIQUE NODE {label} {property_name}")
        self.constraints.add((label, property_name, "id"))


def test_schema_initialization_is_repeatable() -> None:
    client = FakeGraphClient()
    schema = GraphSchema(client)

    schema.initialize()
    first_writes = list(client.schema_writes)
    schema.initialize()

    assert client.schema_writes == first_writes
    assert len(client.indexes) == len(INDEXES)
    assert client.full_text_index
    assert len(client.constraints) == len(NODE_LABELS)
    assert {"OWNS", "DEPENDS_ON", "SUPPORTED_BY", "SUPERSEDES"}.issubset(RELATIONSHIP_TYPES)
