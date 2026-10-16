from typing import Any

from falkordb import FalkorDB

from app.core.config import Settings


class GraphClient:
    """Reusable wrapper around FalkorDB's official Python client."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: FalkorDB | None = None

    def connect(self) -> None:
        if self._client is None:
            self._client = FalkorDB(
                host=self._settings.falkordb_host,
                port=self._settings.falkordb_port,
                password=self._settings.graph_password,
            )

    def query(
        self,
        cypher: str,
        params: dict[str, Any] | None = None,
        timeout_ms: int = 5000,
    ) -> Any:
        self.connect()
        assert self._client is not None
        return self._client.select_graph(self._settings.falkordb_graph_name).query(
            cypher, params=params or {}, timeout=timeout_ms
        )

    def create_unique_constraint(self, label: str, property_name: str) -> Any:
        self.connect()
        assert self._client is not None
        return self._client.execute_command(
            "GRAPH.CONSTRAINT",
            "CREATE",
            self._settings.falkordb_graph_name,
            "UNIQUE",
            "NODE",
            label,
            "PROPERTIES",
            1,
            property_name,
        )

    def ping(self) -> None:
        self.query("RETURN 1")

    def close(self) -> None:
        self._client = None
