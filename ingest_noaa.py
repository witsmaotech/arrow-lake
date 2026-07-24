import time
import pyarrow.parquet as pq
from arrow_lake import Lake

PARQUET = "/mnt/e/WITSMT-ALL-IN-AI/Pi-run-Datas/noaa/noaa_china.parquet"
NAME = "noaa_china"

lake = Lake()
print(f"connected: backend={lake.config.storage.backend} bucket={lake.config.storage.s3_bucket}")

if NAME in lake.list_datasets():
    print(f"dataset '{NAME}' exists -> dropping for clean ingest")
    lake.delete_dataset(NAME)

print("reading parquet into Arrow Table ...")
t0 = time.time()
table = pq.read_table(PARQUET)
print(f"  rows={table.num_rows:,}  cols={table.num_columns}  ({time.time()-t0:.1f}s)")

print(f"ingesting -> dataset '{NAME}' (MinIO/Lance) ...")
t0 = time.time()
lake.create_dataset(NAME, table)
print(f"  done in {time.time()-t0:.1f}s")

# verify
print("\n=== verify ===")
print("datasets:", [d for d in lake.list_datasets() if "noaa" in d])
res = lake.olap_query(NAME, f"SELECT province, COUNT(*) AS cnt FROM {NAME} GROUP BY province ORDER BY cnt DESC LIMIT 8")
print("\nrows per province (top 8):")
print(res.table.to_pandas().to_string(index=False))
tot = lake.olap_query(NAME, f"SELECT COUNT(*) AS total FROM {NAME}")
print("\ntotal rows:", tot.table.column("total")[0].as_py())
