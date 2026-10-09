"""Provider and cleanup safety checks using only an in-memory mock transport."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import httpx
import importlib.util
spec=importlib.util.spec_from_file_location("hook_probe_tool",Path(__file__).resolve().parents[1]/"tools/test_poynt_hooks.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
run,CheckError=module.run,module.CheckError


class HookProbeTests(unittest.TestCase):
    def test_create_preserves_original_and_delete_only_probe(self):
        with tempfile.TemporaryDirectory() as folder:
            args=argparse.Namespace(action="create",journal=folder+"/probe.json",organization_id=1,
                callback_url="https://example.test/webhooks/poynt/orders",confirm_create=True,confirm_delete=True)
            original={"id":"old","businessId":"business","applicationId":"app","deliveryUrl":args.callback_url}
            rows=[original];deleted=[]
            def handle(req):
                if req.method=="GET":return httpx.Response(200,json={"hooks":rows})
                if req.method=="POST":
                    body=json.loads(req.content);rows.append({**body,"id":"new"});return httpx.Response(201,json=rows[-1])
                deleted.append(req.url.path);rows.pop();return httpx.Response(204)
            with httpx.Client(transport=httpx.MockTransport(handle)) as http,patch.dict(os.environ,{"POYNT_WEBHOOK_SECRET":"s"*32}),contextlib.redirect_stdout(io.StringIO()):
                run(http,args,{"business_id":"business"},"app")
                self.assertEqual(len(rows),2)
                journal=json.loads(Path(args.journal).read_text());self.assertEqual(journal["hook_id"],"new")
                self.assertNotIn("s"*32,Path(args.journal).read_text())
                with self.assertRaises(CheckError):run(http,args,{"business_id":"business"},"app")
                args.action="delete";run(http,args,{"business_id":"business"},"app")
                self.assertEqual(deleted,["/hooks/new"]);self.assertEqual(rows,[original])

    def test_timeout_keeps_intent_for_status_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            args=argparse.Namespace(action="create",journal=folder+"/probe.json",organization_id=1,
                callback_url="https://example.test/webhooks/poynt/orders",confirm_create=True,confirm_delete=False)
            rows=[]
            def handle(req):
                if req.method=="GET":return httpx.Response(200,json={"hooks":rows})
                rows.append({**json.loads(req.content),"id":"uncertain"});raise httpx.ReadTimeout("timeout",request=req)
            with httpx.Client(transport=httpx.MockTransport(handle)) as http,patch.dict(os.environ,{"POYNT_WEBHOOK_SECRET":"s"*32}),contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(httpx.ReadTimeout):run(http,args,{"business_id":"business"},"app")
                self.assertTrue(Path(args.journal).exists())
                args.action="status";run(http,args,{"business_id":"business"},"app")
                args.action="delete"
                with self.assertRaises(CheckError):run(http,args,{"business_id":"business"},"app")

    def test_default_list_never_mutates_or_discloses_secret(self):
        args=argparse.Namespace(action="list",journal="unused",organization_id=1)
        def handle(req):
            self.assertEqual(req.method,"GET")
            return httpx.Response(200,json={"hooks":[{"id":"old","secret":"hidden-secret",
                "deliveryUrl":"https://user:password@example.test/callback?secret=hidden-secret"}]})
        output=io.StringIO()
        with httpx.Client(transport=httpx.MockTransport(handle)) as http,contextlib.redirect_stdout(output):
            run(http,args,{"business_id":"business"},"app")
        self.assertNotIn("hidden-secret",output.getvalue());self.assertNotIn("password",output.getvalue())
