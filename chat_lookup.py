import sqlite3
from pathlib import Path

db = Path("/mnt/ssd/orion/orion.db")
con = sqlite3.connect(db)
print("--- ultimas 25 conversas ---")
for linha in con.execute(
    "SELECT timestamp, pessoa_id, papel, substr(texto,1,160) FROM conversas ORDER BY id DESC LIMIT 25"
):
    print(linha)
con.close()