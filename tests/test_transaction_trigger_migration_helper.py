import contextlib
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock
import apply_transaction_trigger_migration as helper


class MigrationHelperTests(unittest.TestCase):
    def run_helper(self, heads, apply=False):
        target='f74c28a610be'
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        env=Path(directory.name)/'target.env';env.write_text('DATABASE_URL=sqlite:///:memory:\n')
        engine=MagicMock();connection=engine.connect.return_value.__enter__.return_value
        with patch.dict(os.environ,{},clear=False),patch.object(helper,'create_engine',return_value=engine), \
             patch('alembic.script.ScriptDirectory.from_config',return_value=SimpleNamespace(get_heads=lambda:[target],
                get_revision=lambda revision:SimpleNamespace(down_revision='a04d61e72c39'))), \
             patch('alembic.migration.MigrationContext.configure',return_value=SimpleNamespace(get_current_heads=lambda:heads)), \
             patch('alembic.command.upgrade') as upgrade,contextlib.redirect_stdout(io.StringIO()):
            helper.run(SimpleNamespace(dotenv=str(env),apply=apply))
            return upgrade.call_count

    def test_readonly_preflight_does_not_apply(self):
        self.assertEqual(self.run_helper(('a04d61e72c39',)),0)

    def test_apply_requires_matching_reviewed_heads(self):
        self.assertEqual(self.run_helper(('a04d61e72c39',),True),1)
        with self.assertRaises(ValueError):self.run_helper(('unexpected-revision',),True)
        with self.assertRaises(ValueError):self.run_helper(('a04d61e72c39','af017c82d641','c69e1a47f358'),True)

    def test_actual_alembic_graph_requires_only_the_single_new_migration(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        root=Path(__file__).resolve().parents[1]
        config=Config(str(root/'alembic.ini'))
        config.set_main_option('script_location',str(root/'alembic'))
        scripts=ScriptDirectory.from_config(config)
        self.assertIn(helper.TARGET,{r.revision for r in scripts.iterate_revisions('heads','base')})
        self.assertEqual(scripts.get_revision(helper.TARGET).down_revision,'a04d61e72c39')
        prior={r.revision for r in scripts.iterate_revisions('a04d61e72c39','base')}
        self.assertTrue({'af017c82d641','c69e1a47f358'}.issubset(prior))
        self.assertEqual([r.revision for r in scripts.iterate_revisions(helper.TARGET,'a04d61e72c39')],[helper.TARGET])


if __name__=='__main__':unittest.main()
