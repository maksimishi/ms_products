# get_productkind.py
import os, requests
from dotenv import load_dotenv

load_dotenv()                             # в .env лежит APIKEY или TOKEN
BASE    = "https://апи.национальный-каталог.рф"
APIKEY  = os.getenv("NC_API_KEY")         # или передавайте token в headers
CAT_ID  = 215009                           # пример: «Швейные изделия»

def q(path, **params):
    params.setdefault("apikey", APIKEY)
    r = requests.get(f"{BASE}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()["result"]

# 1. ищем атрибут «Вид»
attrs = q("/v3/attributes", cat_id=CAT_ID, attr_type="a")
kind = next(a for a in attrs if a["attr_name"] in ("Вид", "Вид товара"))

# 2. берём значения
if kind.get("attr_preset"):
    values = kind["attr_preset"]
else:
    values = q(kind["preset_url"])

print(f"Вид товара (cat_id={CAT_ID}) — {len(values)} значений:")
for v in values:
    print(" •", v)
