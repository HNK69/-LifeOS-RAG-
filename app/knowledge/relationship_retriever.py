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


def traverse_relationships(
    start_entity_name: str,
    max_hops: int = 2,
    max_nodes: int = 25,
    relationship_types: list[str] | None = None,
    direction: str = "both",
) -> list[dict]:
    """
    Deterministically traverse multi-hop relationships from a starting entity.

    Guarantees:
    - Bounded execution (max_hops clamped to 1..3, max_nodes clamped to 1..50)
    - Cycle prevention via visited entity tracking along paths
    - Returns structured paths with depth and cumulative confidence
    """
    if not start_entity_name or not str(start_entity_name).strip():
        return []

    entities = find_entities(name=str(start_entity_name).strip())
    if not entities:
        return []

    max_hops = max(1, min(int(max_hops), 3))
    max_nodes = max(1, min(int(max_nodes), 50))

    filter_types = (
        {str(t).strip().lower() for t in relationship_types}
        if relationship_types
        else None
    )

    traversed: list[dict] = []
    seen_edges: set[tuple[int, int, str]] = set()

    for start_entity in entities:
        start_id = start_entity["id"]
        start_name = start_entity["canonical_name"]

        # Queue items: (current_id, current_name, current_hop, path_history, cumulative_conf)
        queue = [(start_id, start_name, 0, [start_name], 1.0)]

        while queue and len(traversed) < max_nodes:
            curr_id, curr_name, curr_hop, path_hist, cum_conf = queue.pop(0)

            if curr_hop >= max_hops:
                continue

            relationships = get_relationships(curr_id, direction=direction)

            for rel in relationships:
                if len(traversed) >= max_nodes:
                    break

                rel_type = str(rel.get("relationship_type", "")).strip().lower()
                if filter_types and rel_type not in filter_types:
                    continue

                source_id = rel.get("source_entity_id")
                target_id = rel.get("target_entity_id")
                edge_key = (source_id, target_id, rel_type)

                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)

                # Determine neighbor
                if curr_id == source_id:
                    neighbor_id = target_id
                    neighbor_name = (
                        rel.get("target", {}).get("canonical_name")
                        if isinstance(rel.get("target"), dict)
                        else str(target_id)
                    )
                else:
                    neighbor_id = source_id
                    neighbor_name = (
                        rel.get("source", {}).get("canonical_name")
                        if isinstance(rel.get("source"), dict)
                        else str(source_id)
                    )

                # Prevent cycles along this path
                if neighbor_name in path_hist:
                    continue

                rel_conf = float(rel.get("confidence", 0.9) or 0.9)
                new_cum_conf = round(cum_conf * rel_conf, 3)
                new_path = path_hist + [rel_type, neighbor_name]
                next_hop = curr_hop + 1

                step_record = {
                    "source_entity": start_name,
                    "target_entity": neighbor_name,
                    "relationship_type": rel_type,
                    "depth": next_hop,
                    "path": new_path,
                    "confidence": new_cum_conf,
                    "relationship": rel,
                }
                traversed.append(step_record)

                if next_hop < max_hops:
                    queue.append((
                        neighbor_id,
                        neighbor_name,
                        next_hop,
                        new_path,
                        new_cum_conf,
                    ))

    return traversed
