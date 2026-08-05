from sentence_transformers import SentenceTransformer

model = SentenceTransformer("BAAI/bge-m3")

def generate_embeddings(chunks):

    """
    Convert text chunks into vector embeddings.
    """
    embeddings = model.encode(chunks)

    return embeddings

# if __name__ == "__main__":

#     chunks = [
#         "LifeOS is my personal AI operating system.",
#         "RAG retrieves relevant context before answering."
#     ]

#     embeddings = generate_embeddings(chunks)


#     print(f"Total Embeddings: {len(embeddings)}")
#     print(f"Embedding Dimension: {len(embeddings[0])}")
#     print("\nFirst 10 Values:")
#     print(embeddings[0])