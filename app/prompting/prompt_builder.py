def build_prompt(query, retrieved_chunks):

    context = "\n\n---\n\n".join(
        str(chunk) for chunk in retrieved_chunks
    )

    prompt = f"""
        You are LifeOS, a personal knowledge assistant.

        Your job is to answer the user's request using ONLY the retrieved context below.

        RULES:
        1. Use only information explicitly present in the retrieved context.
        2. Do not invent, assume, reconstruct, or modify facts.
        3. Never invent filenames, file paths, dates, numbers, code, or document details.
        4. If the user asks to find or identify a document, report the source exactly as provided in the context.
        5. If the user asks for code, reproduce only code that is actually present in the context. Do not complete missing code.
        6. If the requested information is not present in the context, say:
        "I don't know based on the provided context."
        7. Do not use outside knowledge.
        8. Be concise and directly answer the user's question.

        RETRIEVED CONTEXT:
        {context}

        USER QUESTION:
        {query}

        ANSWER:
    """

    return prompt