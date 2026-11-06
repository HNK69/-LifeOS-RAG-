from knowledge.entity_relationship import (
    add_entity_alias,
    clear_entity_store,
    create_entity,
    create_relationship,
    find_entities,
    get_entity,
    get_entity_aliases,
    get_relationships,
    resolve_entity,
)


def test_entity_round_trip():
    clear_entity_store()

    entity = create_entity(
        "person",
        "Alice",
        {"role": "friend"},
    )

    assert entity["canonical_name"] == "Alice"
    assert entity["entity_type"] == "person"
    assert entity["attributes"]["role"] == "friend"

    loaded = get_entity(entity["id"])

    assert loaded["canonical_name"] == "Alice"
    assert loaded["attributes"]["role"] == "friend"

    clear_entity_store()


def test_entity_lookup():
    clear_entity_store()

    create_entity("person", "Alice")
    create_entity("place", "College")

    people = find_entities(entity_type="person")

    assert len(people) == 1
    assert people[0]["canonical_name"] == "Alice"

    clear_entity_store()


def test_relationship_round_trip():
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")

    relationship = create_relationship(
        alice["id"],
        bob["id"],
        "knows",
        confidence=0.9,
        metadata={"source": "test"},
    )

    assert relationship["relationship_type"] == "knows"
    assert relationship["confidence"] == 0.9
    assert relationship["metadata"]["source"] == "test"

    assert relationship["source"]["canonical_name"] == "Alice"
    assert relationship["target"]["canonical_name"] == "Bob"

    relationships = get_relationships(
        alice["id"],
        direction="outgoing",
    )

    assert len(relationships) == 1
    assert relationships[0]["target"]["canonical_name"] == "Bob"

    clear_entity_store()


def test_relationship_bidirectional_retrieval():
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")

    create_relationship(
        alice["id"],
        bob["id"],
        "knows",
    )

    incoming = get_relationships(
        bob["id"],
        direction="incoming",
    )

    assert len(incoming) == 1
    assert incoming[0]["source"]["canonical_name"] == "Alice"

    outgoing = get_relationships(
        alice["id"],
        direction="outgoing",
    )

    assert len(outgoing) == 1
    assert outgoing[0]["target"]["canonical_name"] == "Bob"

    clear_entity_store()


def test_relationship_both_directions():
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")

    create_relationship(
        alice["id"],
        bob["id"],
        "knows",
    )

    relationships = get_relationships(
        alice["id"],
        direction="both",
    )

    assert len(relationships) == 1
    assert relationships[0]["relationship_type"] == "knows"

    clear_entity_store()


def test_entity_upsert():
    clear_entity_store()

    first = create_entity(
        "person",
        "Alice",
        {"role": "friend"},
    )

    second = create_entity(
        "person",
        "Alice",
        {"role": "classmate"},
    )

    assert first["id"] == second["id"]
    assert second["attributes"]["role"] == "classmate"

    clear_entity_store()


def test_relationship_upsert():
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")

    first = create_relationship(
        alice["id"],
        bob["id"],
        "knows",
        confidence=0.5,
    )

    second = create_relationship(
        alice["id"],
        bob["id"],
        "knows",
        confidence=0.95,
    )

    assert first["id"] == second["id"]
    assert second["confidence"] == 0.95

    clear_entity_store()


def test_invalid_relationship_entities():
    clear_entity_store()

    alice = create_entity("person", "Alice")

    try:
        create_relationship(
            alice["id"],
            999999,
            "knows",
        )
        assert False
    except ValueError as exc:
        assert str(exc) == "Target entity does not exist."

    clear_entity_store()


def test_relationship_confidence_validation():
    clear_entity_store()

    alice = create_entity("person", "Alice")
    bob = create_entity("person", "Bob")

    try:
        create_relationship(
            alice["id"],
            bob["id"],
            "knows",
            confidence=1.5,
        )
        assert False
    except ValueError as exc:
        assert "between 0 and 1" in str(exc)

    clear_entity_store()


def test_entity_case_insensitive_lookup():
    clear_entity_store()

    create_entity("person", "Alice", {"role": "researcher"})

    matches_lower = find_entities(name="alice")
    assert len(matches_lower) == 1
    assert matches_lower[0]["canonical_name"] == "Alice"

    matches_upper = find_entities(name="ALICE")
    assert len(matches_upper) == 1
    assert matches_upper[0]["canonical_name"] == "Alice"

    clear_entity_store()


def test_entity_alias_resolution():
    clear_entity_store()

    entity = create_entity(
        "person",
        "Robert Smith",
        {"aliases": ["Bob", "Rob"]},
    )

    by_alias_bob = find_entities(name="bob")
    assert len(by_alias_bob) == 1
    assert by_alias_bob[0]["id"] == entity["id"]

    by_alias_rob = find_entities(name="Rob")
    assert len(by_alias_rob) == 1
    assert by_alias_rob[0]["id"] == entity["id"]

    add_entity_alias(entity["id"], "Bobby")
    aliases = get_entity_aliases(entity["id"])
    assert "Bobby" in aliases

    by_new_alias = find_entities(name="bobby")
    assert len(by_new_alias) == 1
    assert by_new_alias[0]["id"] == entity["id"]

    clear_entity_store()


def test_resolve_entity_exact_and_alias():
    clear_entity_store()

    alice = create_entity("person", "Alice", {"aliases": ["Ally"]})

    res_exact = resolve_entity("alice")
    assert res_exact["status"] == "exact"
    assert res_exact["entity"]["id"] == alice["id"]

    res_alias = resolve_entity("ally")
    assert res_alias["status"] == "alias"
    assert res_alias["entity"]["id"] == alice["id"]

    res_missing = resolve_entity("Charlie")
    assert res_missing["status"] == "not_found"
    assert res_missing["entity"] is None

    clear_entity_store()


def test_resolve_entity_ambiguous_matches():
    clear_entity_store()

    alice_s = create_entity(
        "person",
        "Alice Smith",
        {"aliases": ["Ali"]},
    )
    create_entity(
        "person",
        "Alice Jones",
        {"aliases": ["Ali"]},
    )

    res_ambiguous = resolve_entity("Ali")
    assert res_ambiguous["status"] == "ambiguous"
    assert res_ambiguous["entity"] is None
    assert len(res_ambiguous["matches"]) == 2

    res_smith = resolve_entity("Alice Smith")
    assert res_smith["status"] == "exact"
    assert res_smith["entity"]["id"] == alice_s["id"]

    clear_entity_store()


def test_entity_duplicate_upsert_case_insensitive():
    clear_entity_store()

    first = create_entity("person", "Alice", {"department": "AI"})
    second = create_entity("person", "alice", {"role": "mentor"})

    assert first["id"] == second["id"]
    assert second["attributes"]["department"] == "AI"
    assert second["attributes"]["role"] == "mentor"

    all_people = find_entities(entity_type="person")
    assert len(all_people) == 1

    clear_entity_store()