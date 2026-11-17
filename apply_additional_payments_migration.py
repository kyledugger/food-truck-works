"""Preflight/apply only the reviewed transaction-additional-payments migration.

Default is read-only. --apply explicitly authorizes the three new tables on
the target selected by --dotenv. Never prints database credentials.
"""
import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext
from alembic import command

TARGET = 'c27a90e51f68'
PARENT = 'b13d82e69a04'
PARENTS = {PARENT}


def run(args):
    path=Path(args.dotenv)
    if not path.is_file():
        raise ValueError('Selected environment file does not exist.')
    load_dotenv(path,override=True)
    raw=os.environ['DATABASE_URL']
    if raw.startswith('postgres://'):
        raw=raw.replace('postgres://','postgresql+psycopg://',1)
    elif raw.startswith('postgresql://'):
        raw=raw.replace('postgresql://','postgresql+psycopg://',1)
    url=make_url(raw)
    print('Environment file:',path.name)
    print('Database target:',url.get_backend_name(), 'host='+str(url.host or 'local'), 'database='+str(url.database))
    root=Path(__file__).resolve().parent
    config=Config(str(root/'alembic.ini'))
    config.set_main_option('script_location',str(root/'alembic'))
    scripts=ScriptDirectory.from_config(config)
    if set(scripts.get_heads()) != {TARGET}:
        raise ValueError('Source migration heads differ from the reviewed package. No schema changes made.')
    if scripts.get_revision(TARGET).down_revision != PARENT:
        raise ValueError('Transaction migration parent differs from the additional payments package. Extract both the helper and migration file. No schema changes made.')
    engine=create_engine(url)
    try:
        with engine.connect() as connection:
            heads=set(MigrationContext.configure(connection).get_current_heads())
            print('Current revisions:',', '.join(sorted(heads)) or '(none)')
            if heads=={TARGET}:
                expected={'additional_payment_days','additional_payment_controls','dashboard_payment_links'}
                if not expected.issubset(set(inspect(connection).get_table_names())):
                    raise ValueError('Migration is recorded but a required table is missing. No changes made.')
                print('Transaction additional payments migration already applied.')
                return
        if heads != PARENTS:
            raise ValueError('Database revisions differ from the reviewed parent heads. No schema changes made; send this preflight output for review. Do not stamp the database.')
        print('Planned change: advance b13d82e69a04 to c27a90e51f68 and create payment snapshots, collection controls and order identity links. No provider calls.')
        if not args.apply:
            print('Read-only preflight passed. Rerun the same command with --apply to execute this migration.')
            return
        os.environ['DATABASE_URL']=url.render_as_string(hide_password=False)
        command.upgrade(config,TARGET)
        print('Transaction additional payments migration applied.')
    finally:
        engine.dispose()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dotenv',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:
        run(args)
    except Exception as error:
        print(str(error) if type(error) is ValueError else 'Stopped: '+type(error).__name__+'. Database/provider details omitted.')
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
