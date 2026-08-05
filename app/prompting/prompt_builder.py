def build_prompt(query,retrieved_chunks):

    context = "\n\n".join(retrieved_chunks)

    prompt = f"""
        You are a helpful AI assistant.

        Answer ONLY using the provided context.
        If the answer is not in the context, say "I don't know."

        Context:
        {context}

        Question:
        {query}

        Answer:

        """

    return prompt