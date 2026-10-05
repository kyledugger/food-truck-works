import os
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse
import httpx
from integrations.providers import SQUARE_SCOPES


class SquareError(Exception):
    def __init__(self, status=502):
        self.status = status
        super().__init__("Square request failed. Retry or reconnect if authorization has expired.")


@dataclass(frozen=True)
class SquareConfig:
    environment: str
    application_id: str
    application_secret: str
    redirect_uri: str
    version: str

    @property
    def base_url(self):
        return "https://connect.squareupsandbox.com" if self.environment == "sandbox" else "https://connect.squareup.com"

    @classmethod
    def load(cls, environment):
        if environment not in {"sandbox", "production"}:
            raise ValueError("Invalid Square environment")
        prefix = f"SQUARE_{environment.upper()}_"
        values = [os.getenv(prefix + key, "") for key in ("APPLICATION_ID", "APPLICATION_SECRET", "REDIRECT_URI")]
        if not all(values):
            raise RuntimeError(f"Configure {prefix}APPLICATION_ID, APPLICATION_SECRET and REDIRECT_URI")
        parsed = urlparse(values[2])
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise RuntimeError("Square redirect URI must be an HTTPS URL without query or fragment")
        return cls(environment, *values, os.getenv("SQUARE_API_VERSION", "2026-09-16"))


class SquareProvider:
    def __init__(self, config, transport=None):
        self.config = config
        self.transport = transport

    def authorization_url(self, state):
        return self.config.base_url + "/oauth2/authorize?" + urlencode({
            "client_id": self.config.application_id, "scope": " ".join(SQUARE_SCOPES),
            "state": state, "session": "false",
        })

    async def request(self, method, path, token=None, body=None, client_auth=False):
        headers = {"Square-Version": self.config.version}
        if client_auth:
            headers["Authorization"] = "Client " + self.config.application_secret
        elif token:
            headers["Authorization"] = "Bearer " + token
        try:
            async with httpx.AsyncClient(base_url=self.config.base_url, timeout=20, transport=self.transport) as client:
                response = await client.request(method, path, headers=headers, json=body)
            if response.status_code >= 400:
                raise SquareError(response.status_code)
            result = response.json()
            if not isinstance(result, dict) or result.get("errors"):
                raise SquareError()
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise SquareError() from exc

    async def exchange(self, code):
        return await self.request("POST", "/oauth2/token", body={
            "client_id": self.config.application_id, "client_secret": self.config.application_secret,
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": self.config.redirect_uri,
        })

    async def refresh(self, refresh_token):
        return await self.request("POST", "/oauth2/token", body={
            "client_id": self.config.application_id, "client_secret": self.config.application_secret,
            "grant_type": "refresh_token", "refresh_token": refresh_token,
        })

    async def scopes(self, token):
        result = await self.request("POST", "/oauth2/token/status", token=token)
        return result["scopes"]

    async def revoke(self, token):
        result = await self.request("POST", "/oauth2/revoke", client_auth=True, body={
            "client_id": self.config.application_id, "access_token": token,
            "revoke_only_access_token": False,
        })
        if not result.get("success"):
            raise SquareError()

    async def locations(self, token):
        result = await self.request("GET", "/v2/locations", token=token)
        return [{"id": x["id"], "name": x.get("name", x["id"]), "timezone": x.get("timezone"), "status": x.get("status")} for x in result.get("locations", [])]

    async def employees(self, token):
        records, cursor, seen = [], None, set()
        while True:
            body = {"limit": 100}
            if cursor:
                body["cursor"] = cursor
            result = await self.request("POST", "/v2/team-members/search", token=token, body=body)
            records.extend({"id": x["id"], "name": " ".join(filter(None, [x.get("given_name"), x.get("family_name")])) or x["id"], "status": x.get("status")} for x in result.get("team_members", []))
            cursor = result.get("cursor")
            if not cursor:
                return records
            if cursor in seen:
                raise SquareError()
            seen.add(cursor)
