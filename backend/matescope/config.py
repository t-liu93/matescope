from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MATESCOPE_", extra="ignore")

    app_name: str = "MateScope"
    version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: str = "/app/data"


settings = Settings()
