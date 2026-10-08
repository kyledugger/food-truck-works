"""Inspect a raw Poynt order export without printing customer/payment objects."""
import argparse
import json
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file",help="Raw Poynt order JSON, or a JSON array of orders")
    args=parser.parse_args()
    data=json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
    orders=data if isinstance(data,list) else data.get("orders",[data])
    for order in orders:
        customer=order.get("customer") or {}
        print(json.dumps({"order_id":order.get("id"),"order_number":order.get("orderNumber"),
            "customerUserId":order.get("customerUserId"),
            "embedded_name":{k:customer.get(k) for k in ("firstName","lastName","nickName")},
            "notes":order.get("notes"),
            "attribute_keys":list((order.get("attributes") or {}).keys()),
            "transaction_customer_ids":[t.get("customerUserId") for t in order.get("transactions") or [] if t.get("customerUserId")]},ensure_ascii=False))
if __name__=="__main__":main()
