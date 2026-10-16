import argparse

from app.core.config import get_settings
from app.demo_data import load_demo_data, reset_demo_data
from app.graph.client import GraphClient
from app.graph.repository import GraphRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="Load AletheiaOS's synthetic company graph.")
    parser.add_argument(
        "--reset-demo-sources",
        action="store_true",
        help="Retire only source IDs in data/demo, then reload those fixtures.",
    )
    arguments = parser.parse_args()
    settings = get_settings()
    client = GraphClient(settings)
    repository = GraphRepository(client)
    try:
        repository.initialize()
        loader = reset_demo_data if arguments.reset_demo_sources else load_demo_data
        receipts = loader(repository, settings.demo_data_directory)
        for receipt in receipts:
            print(
                f"{receipt.source_id}: {receipt.revision_id} "
                f"({receipt.chunks_processed} passages, {receipt.status})"
            )
    finally:
        client.close()


if __name__ == "__main__":
    main()
