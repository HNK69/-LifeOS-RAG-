from knowledge.entity_relationship import (
    find_entities,
    get_relationships,
)


def retrieve_relationships(
    entity_name,
    entity_type=None,
    relationship_type=None,
    direction="both",
):
    """
    Deterministically retrieve relationships for an entity.

    Resolution:
        entity name/type
            ↓
        canonical entity
            ↓
        relationship store
    """

    if not entity_name or not str(entity_name).strip():
        return []

    entities = find_entities(
        entity_type=entity_type,
        name=str(entity_name).strip(),
    )

    if not entities:
        return []

    results = []

    for entity in entities:
        relationships = get_relationships(
            entity["id"],
            direction=direction,
        )

        for relationship in relationships:
            if (
                relationship_type is not None
                and relationship["relationship_type"]
                != str(relationship_type).strip().lower()
            ):
                continue

            results.append(relationship)

    return results