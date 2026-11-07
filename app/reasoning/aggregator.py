"""
aggregator.py

Deterministic multi-source evidence aggregation for LifeOS.
Assembles evidence across documents, personal context, knowledge graph,
structured data, and multimodal sources into a unified bundle.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from query.router import QueryIntent, QueryResult


def aggregate_evidence(
    query: str,
    sources: list[str] | None = None,
    entity_name: str | None = None,
    dataset_query: str | None = None,
    at_time: datetime | None = None,
) -> QueryResult:
    """
    Deterministically retrieve and assemble evidence from multiple repository sources.

    Supported sources:
    - 'documents': semantic document chunks from vector store
    - 'context': active & query-relevant personal context
    - 'relationships': entity relationships from knowledge graph
    - 'structured': tabular dataset query from CSV
    - 'multimodal': cross-modal image metadata
    """
    if sources is None:
        sources = ["documents", "context", "relationships"]

    aggregated: dict[str, Any] = {
        "query": query,
        "documents": [],
        "personal_context": {},
        "relationships": [],
        "structured_data": [],
        "multimodal": [],
    }

    # 1. Personal Context
    if "context" in sources:
        try:
            from context.context_store import compose_context
            ctx = compose_context(query, at_time)
            aggregated["personal_context"] = ctx
            # Merge any relationships discovered by context composition
            for rel in ctx.get("relationships", []):
                if rel not in aggregated["relationships"]:
                    aggregated["relationships"].append(rel)
        except Exception:
            pass

    # 2. Document Chunks
    if "documents" in sources:
        try:
            from retrieval.reranker import rerank
            from retrieval.retriever import retrieve_chunks
            chunks = retrieve_chunks(query, top_k=5)
            chunks = rerank(query, chunks)
            aggregated["documents"] = chunks
        except Exception:
            pass

    # 3. Knowledge Graph Relationships
    if "relationships" in sources:
        try:
            from knowledge.relationship_retriever import retrieve_relationships
            from context.context_store import _get_relationship_context

            if entity_name:
                rels = retrieve_relationships(entity_name=entity_name, direction="both")
                for r in rels:
                    if r not in aggregated["relationships"]:
                        aggregated["relationships"].append(r)
            else:
                rels = _get_relationship_context(query)
                for r in rels:
                    if r not in aggregated["relationships"]:
                        aggregated["relationships"].append(r)
        except Exception:
            pass

    # 4. Structured Data
    if "structured" in sources and dataset_query:
        try:
            from query.router import _find_dataset
            from processing.csv_processor import query_csv
            dataset = _find_dataset(dataset_query)
            res = query_csv(dataset["path"], max_results=5)
            aggregated["structured_data"].append({
                "dataset": dataset,
                "result": res,
            })
        except Exception:
            pass

    # 5. Multimodal Evidence
    if "multimodal" in sources:
        try:
            from retrieval.multimodal_retriever import search_multimodal
            mm_items = search_multimodal(query)
            aggregated["multimodal"] = mm_items
        except Exception:
            pass

    return QueryResult(
        query=query,
        intent=QueryIntent(
            "multi_source",
            1.0,
            {"sources": sources},
        ),
        answer_type="multi_source",
        data=aggregated,
    )
