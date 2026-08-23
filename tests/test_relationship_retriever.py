from knowledge.entity_relationship import (
    clear_entity_store,
    create_entity,
    create_relationship,
)

from knowledge.relationship_retriever import (
    retrieve_relationships,
)


def test_retrieve_relationships():
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")

    create_relationship(
        alice["id"],
        bob["id"],
        "knows",
        confidence=0.9,
    )

    results = retrieve_relationships(
        "Alice",
        direction="outgoing",
    )

    assert len(results) == 1
    assert results[0]["relationship_type"] == "knows"
    assert results[0]["target"]["canonical_name"] == "Bob"

    clear_entity_store()


def test_retrieve_relationships_by_type():
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")
    college = create_entity("place", "College")

    create_relationship(
        alice["id"],
        bob["id"],
        "knows",
    )

    create_relationship(
        alice["id"],
        college["id"],
        "studies_at",
    )

    results = retrieve_relationships(
        "Alice",
        relationship_type="knows",
        direction="outgoing",
    )

    assert len(results) == 1
    assert results[0]["target"]["canonical_name"] == "Bob"

    clear_entity_store()


def test_unknown_entity_returns_empty():
    clear_entity_store()

    assert retrieve_relationships(
        "Unknown",
    ) == []

    clear_entity_store()