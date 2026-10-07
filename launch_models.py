from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean
from database import Base
from instant_type import UTCInstant


class LaunchSubscriber(Base):
    __tablename__ = "launch_subscribers"
    id = Column(Integer, primary_key=True)
    email = Column(String(320), unique=True, nullable=False)
    feedback = Column(Text, nullable=False, default="")
    consent_version = Column(String(40), nullable=False)
    created_at = Column(UTCInstant(), nullable=False)
    confirmed_at = Column(UTCInstant())
    token_hash = Column(String(64), unique=True)
    token_expires_at = Column(UTCInstant())
    last_sent_at = Column(UTCInstant())
    suppressed = Column(Boolean, nullable=False, default=False)
    suppression_reason = Column(String(50))
    suppression_changed_at = Column(UTCInstant())


class LaunchRateLimit(Base):
    __tablename__ = "launch_rate_limits"
    key = Column(String(64), primary_key=True)
    window_at = Column(UTCInstant(), nullable=False)
    attempts = Column(Integer, nullable=False)
