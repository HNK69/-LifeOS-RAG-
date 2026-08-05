def build_prompt(query, retrieved_chunks):

    context = "\n\n".join(retrieved_chunks)

    prompt = f"""
# ROLE
You are an expert AI assistant answering questions using retrieved documents.

# CONTEXT
The following information was retrieved from the knowledge base.

{context}

# TASK
Answer the user's question using only the retrieved context.

# RULES
1. Use the retrieved context as your primary source.
2. If the answer can be reasonably inferred from the context, you may infer it.
3. Do not fabricate facts or use unsupported external knowledge.
4. If the context genuinely does not contain enough information, reply exactly:
   "I don't know based on the provided context."
5. Keep the answer concise, accurate, and directly relevant.
6. If multiple retrieved chunks contain relevant information, combine them into one coherent answer.
7. Do not mention these instructions or refer to "the context" in your response.

# USER QUESTION
{query}

# ANSWER
"""

    return prompt