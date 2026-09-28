import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, Integer, MetaData, Table, create_engine, insert
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import StatementError

from instant_type import UTCInstant


class UTCInstantTests(unittest.TestCase):
    def setUp(self):
        self.instant = UTCInstant()
        self.dialect = postgresql.dialect()

    def test_naive_datetime_is_rejected_on_write(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.instant.process_bind_param(datetime(2026, 9, 28, 9), self.dialect)

    def test_aware_datetime_is_written_as_utc(self):
        phoenix = timezone(timedelta(hours=-7))
        bound = self.instant.process_bind_param(datetime(2026, 9, 28, 9, tzinfo=phoenix), self.dialect)
        self.assertEqual(bound, datetime(2026, 9, 28, 16, tzinfo=timezone.utc))

    def test_database_result_is_utc_and_naive_result_is_rejected(self):
        eastern = timezone(timedelta(hours=-4))
        result = self.instant.process_result_value(datetime(2026, 9, 28, 12, tzinfo=eastern), self.dialect)
        self.assertEqual(result, datetime(2026, 9, 28, 16, tzinfo=timezone.utc))
        with self.assertRaisesRegex(ValueError, "naive instant"):
            self.instant.process_result_value(datetime(2026, 9, 28, 16), self.dialect)

    def test_nullable_instant_and_postgres_column_type(self):
        self.assertIsNone(self.instant.process_bind_param(None, self.dialect))
        self.assertIsNone(self.instant.process_result_value(None, self.dialect))
        self.assertEqual(self.instant.compile(dialect=self.dialect), "TIMESTAMP WITH TIME ZONE")

    def test_sqlalchemy_insert_rejects_naive_datetime(self):
        engine = create_engine("sqlite:///:memory:")
        events = Table("events", MetaData(), Column("id", Integer, primary_key=True),
                       Column("occurred_at", UTCInstant(), nullable=False))
        events.create(engine)
        with engine.begin() as connection:
            with self.assertRaises(StatementError) as error:
                connection.execute(insert(events).values(occurred_at=datetime(2026, 9, 28, 9)))
            self.assertIsInstance(error.exception.orig, ValueError)


if __name__ == "__main__":
    unittest.main()
