from knowledge.entity_relationship import (
    clear_entity_store,
    find_entities,
    get_entity_by_person_id,
    link_person_to_entity,
)
from knowledge.people import (
    create_person,
    get_person,
    initialize_people_registry,
    label_person,
)


def test_link_person_to_entity():
    clear_entity_store()
    initialize_people_registry()

    person_id = create_person(label="Alice", status="confirmed")

    entity = link_person_to_entity(person_id)

    assert entity is not None
    assert entity["canonical_name"] == "Alice"
    assert entity["entity_type"] == "person"
    assert entity["attributes"]["person_id"] == person_id

    # Lookup by person_id
    retrieved = get_entity_by_person_id(person_id)
    assert retrieved is not None
    assert retrieved["id"] == entity["id"]

    clear_entity_store()


def test_label_person_auto_links_entity():
    clear_entity_store()
    initialize_people_registry()

    person_id = create_person(status="unknown")

    success = label_person(person_id, "Bob")
    assert success is True

    # Person in people.db is confirmed
    person = get_person(person_id)
    assert person["label"] == "Bob"
    assert person["status"] == "confirmed"

    # Person in entities.db is created and linked
    entity = get_entity_by_person_id(person_id)
    assert entity is not None
    assert entity["canonical_name"] == "Bob"
    assert entity["attributes"]["person_id"] == person_id

    # Lookup by name in entities
    matches = find_entities(name="bob")
    assert len(matches) == 1
    assert matches[0]["id"] == entity["id"]

    clear_entity_store()


def test_update_person_label_updates_entity():
    clear_entity_store()
    initialize_people_registry()

    person_id = create_person(status="unknown")
    label_person(person_id, "Charlie")

    entity1 = get_entity_by_person_id(person_id)
    assert entity1["canonical_name"] == "Charlie"

    # Relabel person
    label_person(person_id, "Charles")
    entity2 = get_entity_by_person_id(person_id)
    assert entity2["id"] == entity1["id"]
    assert entity2["canonical_name"] == "Charles"

    clear_entity_store()
