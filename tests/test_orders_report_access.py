"""Verify the real handler's access guard before any database or Poynt work."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest


class Denied(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail


class OrdersReportAccessTests(unittest.TestCase):
    def run_guard(self, role, user_id=1, organization_id=1):
        path = Path(__file__).resolve().parents[1] / 'routers' / 'poynt.py'
        node = next(n for n in ast.parse(path.read_text()).body
                    if isinstance(n, ast.AsyncFunctionDef) and n.name == 'poynt_orders')
        node.decorator_list = []
        # Keep the actual handler through the guard, stopping before its first DB query.
        first_db = next(i for i, statement in enumerate(node.body) if isinstance(statement, ast.With))
        node.body = node.body[:first_db] + [ast.Return(value=ast.Constant('authorized'))]
        scope = dict(Request=object, Query=lambda **kw: None,
            logger=SimpleNamespace(info=lambda *args: None), HTTPException=Denied,
            RedirectResponse=lambda url, status_code: SimpleNamespace(url=url,status_code=status_code),
            get_current_organization_id=lambda request: organization_id,
            get_organization_role=lambda user, org: role,
            role_can_manage_organization=lambda value: value in {'owner','manager'})
        tree = ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[]))
        exec(compile(tree,str(path),'exec'),scope)
        request = SimpleNamespace(session={'user_id':user_id})
        return asyncio.run(scope['poynt_orders'](request))

    def test_owner_and_manager_allowed(self):
        for role in ('owner','manager'):
            with self.subTest(role=role): self.assertEqual(self.run_guard(role),'authorized')

    def test_employee_payroll_and_unknown_denied(self):
        for role in ('member','employee','payroll','store_display',None,'unknown'):
            with self.subTest(role=role), self.assertRaises(Denied) as error:
                self.run_guard(role)
            self.assertEqual(error.exception.status_code,403)

    def test_login_redirect_remains(self):
        self.assertEqual(self.run_guard(None,user_id=None).url,'/login')
        self.assertEqual(self.run_guard(None,organization_id=None).url,'/login')


if __name__ == '__main__': unittest.main()
