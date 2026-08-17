import sys
sys.path.insert(0, ".")
sys.path.insert(0, "app")

from app.intelligence.planner import plan_query


TESTS = [
    ("Find my Java lab programs", "file_discovery"),
    ("Where are my Python assignments?", "file_discovery"),
    ("Show me the DBMS notes", "file_discovery"),
    ("I need my project report", "file_discovery"),
    ("Locate my operating systems PDF", "file_discovery"),

    ("What is the maximum stack size?", "document_search"),
    ("Explain the TCP three way handshake", "document_search"),
    ("What does my notes say about normalization?", "document_search"),
    ("Find the definition of deadlock in my documents", "document_search"),
    ("What is written about inheritance?", "document_search"),

    ("Which class do I have today?", "schedule_query"),
    ("What is my timetable for tomorrow?", "schedule_query"),
    ("When is my next class?", "schedule_query"),
    ("Do I have a lab today?", "schedule_query"),
    ("What classes are scheduled this week?", "schedule_query"),

    ("What time is it?", "current_time"),
    ("What is today's date?", "current_time"),
    ("Tell me the current time", "current_time"),

    ("Find my customer churn dataset", "structured_discovery"),
    ("Which spreadsheet contains customer data?", "structured_discovery"),
    ("Show me my CSV datasets", "structured_discovery"),

    ("How many customers churned?", "structured_query"),
    ("What is the average monthly charge?", "structured_query"),
    ("What is the maximum monthly charge?", "structured_query"),
    ("Sort customers by monthly charge", "structured_query"),
    ("How many customers have churned?", "structured_query"),

    ("Tell me a joke", "unknown"),
    ("Write me a poem", "unknown"),
    ("What is the capital of France?", "unknown"),
    ("Help me cook pasta", "unknown"),
]


passed = 0
failed = 0

for query, expected in TESTS:
    try:
        plan = plan_query(query)
        actual = plan.intent

        if actual == expected:
            passed += 1
        else:
            failed += 1
            print(f"FAIL | expected={expected} | actual={actual} | {query}")

    except Exception as exc:
        failed += 1
        print(f"ERROR | {type(exc).__name__}: {query}")

print()
print(f"TOTAL: {len(TESTS)}")
print(f"PASS:  {passed}")
print(f"FAIL:  {failed}")
