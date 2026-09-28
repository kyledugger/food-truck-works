
from database import engine
from sqlalchemy import text

sql = text("""
    SELECT indexname, indexdef
    FROM pg_indexes
    WHERE tablename = 'poynt_connections'
    ORDER BY indexname
""")

with engine.connect() as conn:
    rows = conn.execute(sql).fetchall()

for name, definition in rows:
    print(f"{name}:")
    print(f"  {definition}")
