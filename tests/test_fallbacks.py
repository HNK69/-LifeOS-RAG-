import sys
sys.path.insert(0, ".")
sys.path.insert(0, "app")

from main import handle_query


TESTS = [
    "Find a file called xyz_nonexistent_123.pdf",
    "Where are my nonexistent Java lab programs?",
    "What does my nonexistent document say about quantum networking?",
    "Find the nonexistent customer churn dataset xyz_123.csv",
]


for query in TESTS:
    print("=" * 60)
    print("QUERY:", query)

    try:
        result = handle_query(query)
        print("RESULT:", result)

    except Exception as exc:
        print("ERROR:", type(exc).__name__, str(exc))
