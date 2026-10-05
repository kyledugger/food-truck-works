import os
from cryptography.fernet import Fernet, MultiFernet


def cipher():
    keys = os.getenv("INTEGRATION_TOKEN_KEYS", "").split(",")
    if not all(key.strip() for key in keys):
        raise RuntimeError("INTEGRATION_TOKEN_KEYS is required for integration credentials")
    return MultiFernet([Fernet(key.strip().encode()) for key in keys])


def encrypt(value: str) -> str:
    return cipher().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return cipher().decrypt(value.encode()).decode()
