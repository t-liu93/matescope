import uvicorn

from .config import settings

if __name__ == "__main__":
    uvicorn.run(
        "matescope.main:app",
        host=settings.host,
        port=settings.port,
        proxy_headers=bool(settings.trusted_proxies),
        forwarded_allow_ips=settings.trusted_proxies,
    )
