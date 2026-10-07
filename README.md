Replace tools/test_poynt_price.py with the enclosed version. Keep the existing tools/check_poynt_products.py dependency.

First check current prices with --action status. If still truck $7.00 / popup $9.00, run one popup apply with a NEW journal:

```powershell
python tools/test_poynt_price.py --dotenv .env.local-prod-db --organization-id 1 --store popup --action apply --journal poynt_price_test_popup_diagnostic.json
```

This is still a real attempt to set the popup price to $9.01, not a read-only probe. If accepted, check terminal/catalog and restore using --action restore and the SAME journal. If rejected, run status and share the PATCH diagnostic output or journal.

The journal records last_patch_response: HTTP status, UUID-form Poynt request ID when returned, short symbolic/numeric provider error codes when returned, and body format. Raw response bodies, message text, and authorization headers are omitted. An empty code list means no supported safe code field was found, not that there was no provider error.

No automatic PATCH retries. Existing journal preservation and price/store guards remain enabled. Four mocked tests cover apply/restore, wrong-store blocking, rejected-response persistence/redaction, and non-JSON handling. No live provider requests were made while developing this patch.
