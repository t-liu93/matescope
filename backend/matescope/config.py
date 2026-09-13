from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MATESCOPE_", extra="ignore")

    app_name: str = "MateScope"
    version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    data_dir: str = "/app/data"
    public_url: str = "http://localhost:8000"
    trusted_proxies: str = ""
    cookie_secure: bool = True
    session_days: int = Field(default=30, ge=1, le=365)

    @model_validator(mode="after")
    def validate_public_url(self) -> "Settings":
        url = urlsplit(self.public_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            raise ValueError("public_url must be an HTTP(S) origin without credentials or a path")
        # Accessing port validates malformed/out-of-range values before startup.
        port = url.port
        hostname = url.hostname.encode("idna").decode("ascii").lower()
        if any(character.isspace() for character in self.public_url):
            raise ValueError("public_url must not contain whitespace")
        host = f"[{hostname}]" if ":" in hostname else hostname
        default_port = 443 if url.scheme == "https" else 80
        suffix = f":{port}" if port is not None and port != default_port else ""
        self.public_url = f"{url.scheme}://{host}{suffix}"
        if url.scheme == "https" and not self.cookie_secure:
            raise ValueError("HTTPS deployments require secure cookies")
        if "*" in self.trusted_proxies:
            raise ValueError("trusted_proxies must explicitly identify trusted proxies")
        return self


settings = Settings()
