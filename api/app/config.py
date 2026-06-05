from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    environment: Literal["development", "production", "test"] = "development"
    secret_key: SecretStr = SecretStr(
        "change-this-to-a-secure-random-string-with-at-least-32-bytes"
    )

    # Auth toggles
    totp_enabled: bool = True
    google_oauth_enabled: bool = False
    google_client_id: str | None = None
    classic_auth_enabled: bool = False
    allow_all_domains: bool = False
    auto_approve_all_domains: bool = False
    guest_access_enabled: bool = False

    # Allowed domains — comma-separated "domain:auto|domain:manual" entries.
    # When set, this wins over DB rows. Empty means fall back to DB.
    # Example: "telecom-sudparis.eu:auto,imt-bs.eu:auto"
    allowed_domains: str = ""

    # Branding Defaults
    site_name: str = "WikINT"
    site_name_style: str | None = None
    site_description: str = "Wiki for SudParis Intelligence"
    site_logo_url: str | None = None
    site_favicon_url: str | None = None
    og_image_url: str | None = None
    bg_watermark_url: str | None = None
    bg_watermark_opacity_light: float | None = None
    bg_watermark_opacity_dark: float | None = None
    footer_logo_url: str | None = None
    primary_color: str = "#3b82f6"
    footer_text: str = "© 2024 WikINT"
    organization_url: str | None = "https://www.telecom-sudparis.eu"
    legal_name: str | None = None
    legal_address: str | None = None
    legal_siret: str | None = None
    contact_email: str | None = None
    dpo_email: str | None = None
    dpo_address: str | None = None
    data_transfers: str | None = (
        "Vos données sont hébergées et traitées exclusivement au sein de l'Union européenne. Aucun transfert vers un pays tiers n'est effectué."
    )
    legal_version: str = "1.0"

    database_url: str = "postgresql+asyncpg://wikint:wikint@localhost:5432/wikint"

    redis_url: str = "redis://localhost:6379/0"

    # Object-storage backend selector. All backends speak S3, but each carries a
    # few quirks (checksum handling, presigned-PUT host rewriting). Switching is
    # env-only — see app/core/storage/backends.py.
    #   r2        — Cloudflare R2 (default; current production)
    #   seaweedfs — self-hosted SeaweedFS
    #   garage    — self-hosted Garage
    #   rustfs    — self-hosted RustFS
    storage_backend: str = "r2"

    s3_endpoint: str = "localhost:9000"
    s3_public_endpoint: str | None = None
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "wikint"
    s3_region: str = "us-east-1"
    s3_use_ssl: bool = False
    s3_use_accelerate_endpoint: bool = False
    max_storage_gb: int = 10

    meili_url: str = "http://localhost:7700"
    meili_master_key: str = "change-me"
    # Search-only key for public search route.  Provision via Meilisearch admin API and set here.
    # If unset, falls back to master key with a startup warning (acceptable in dev).
    meili_search_key: str | None = None

    max_file_size_mb: int = 100

    # Pull Request Limits
    pr_max_ops_student: int = 50
    pr_max_ops_staff: int = 500
    pr_max_attachments_per_material: int = 50
    pr_max_open_per_user: int = 5
    pr_expiry_days: int = 7
    pr_revert_grace_days: int = 7

    # Per-category size caps (MiB) — enforced server-side before transfer/processing
    max_svg_size_mb: int = 5
    max_image_size_mb: int = 50
    max_audio_size_mb: int = 200
    max_video_size_mb: int = 500
    max_document_size_mb: int = 200
    max_office_size_mb: int = 100
    max_text_size_mb: int = 20

    # Upload pipeline settings
    upload_pipeline_max_seconds: int = 600  # hard deadline for the entire worker pipeline

    # tus resumable upload settings
    tus_chunk_min_bytes: int = 5 * 1024 * 1024  # 5 MiB (S3 multipart minimum)
    tus_chunk_max_bytes: int = 100 * 1024 * 1024  # 100 MiB
    tus_max_size_bytes: int = 500 * 1024 * 1024  # 500 MiB
    tus_max_concurrent_per_user: int = 8

    yara_rules_dir: str = "yara_rules"
    yara_scan_timeout: int = 60
    malwarebazaar_timeout: int = 5
    malwarebazaar_url: str = "https://mb-api.abuse.ch/api/v1/"
    malwarebazaar_api_key: str | None = None
    # When True, a MalwareBazaar timeout/error fails the scan (fail-closed).
    # When False (default), YARA remains the authoritative gatekeeper on API failure.
    malwarebazaar_fail_closed: bool = True
    # When True, MalwareBazaar runs as a background ARQ job after YARA-only promotion.
    # YARA remains the synchronous gate; Bazaar flags trigger retroactive quarantine.
    # Set to False to restore the legacy synchronous behaviour (blocks ~6 s per upload).
    bazaar_async_enabled: bool = True
    # When True, retroactive quarantine also soft-deletes any approved MaterialVersion
    # rows that reference the flagged cas/ S3 key.
    bazaar_retroactive_check_materials: bool = True

    smtp_host: str = "smtp.example.com"
    smtp_ip: str | None = None
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_sender_name: str | None = None
    smtp_avatar_url: str | None = None
    smtp_use_tls: bool = True

    backup_dir: str = "/var/lib/wikint/backups"

    # Observability — Prometheus /metrics endpoint
    # When set, callers must pass ?token=<value> or Authorization: Bearer <value> to scrape.
    # Leave empty (default) to allow unauthenticated scraping (safe inside a private network).
    metrics_token: str = ""

    # OpenTelemetry Collector endpoint (e.g. "localhost:4317")
    otel_endpoint: str = ""

    enable_presigned_multipart: bool = True
    direct_upload_threshold_mb: int = 10  # files smaller than this use direct upload

    # CAS deduplication: maximum age (seconds) of a CAS entry before it requires
    # re-scanning.  Set to 0 to disable staleness checks (always trust cache).
    # Default: 7 days.  Entries older than this or older than the YARA rules
    # compilation timestamp will be re-processed through the full pipeline.
    cas_max_age_seconds: int = 7 * 24 * 3600

    # Concurrency guards for heavy worker operations
    global_max_subprocesses: int = 0  # 0 = auto (os.cpu_count())
    max_concurrent_image_ops: int = 0  # 0 = auto (cpu_count // 2)

    # Upload worker concurrency — how many jobs each worker process runs in parallel.
    # Tune alongside WORKER_FAST_REPLICAS / WORKER_SLOW_REPLICAS in compose.prod.yaml.
    worker_fast_max_jobs: int = 4  # small files: I/O-bound, safe to over-subscribe
    worker_slow_max_jobs: int = 2  # large files: heavier compression, keep lower

    # Video compression profile (controls ffmpeg resolution capping and CRF limits)
    video_compression_profile: Literal[
        "none", "light", "medium", "aggressive", "heavy", "extreme"
    ] = "medium"

    # PDF compression quality level (0-100).
    # Can be set via PDF_QUALITY (int) or PDF_COMPRESSION_LEVEL (Ghostscript alias).
    pdf_quality: int = 75
    pdf_compression_level: str | None = None

    thumbnail_quality: int = 85
    thumbnail_size_px: int = 640

    # Comma-separated allowed file extensions (e.g. ".pdf,.docx"). Empty = allow all.
    allowed_extensions: str | None = None
    # Comma-separated allowed MIME types. Empty = allow all.
    allowed_mime_types: str | None = None

    @model_validator(mode="after")
    def _map_pdf_quality(self) -> "Settings":
        if self.pdf_compression_level:
            # Map Ghostscript levels to quality integers
            mapping = {
                "/screen": 50,
                "/ebook": 70,
                "/printer": 85,
                "/prepress": 95,
                "screen": 50,
                "ebook": 70,
                "printer": 85,
                "prepress": 95,
            }
            val = self.pdf_compression_level.lower()
            if val in mapping:
                self.pdf_quality = mapping[val]
        return self

    # Webhook signing secret (HMAC-SHA256).  Defaults to a derivative of secret_key.
    # Set explicitly to rotate independently of the JWT secret.
    webhook_secret: str = ""

    # Cloudflare Worker ZIP endpoint.  When set, directory downloads are offloaded to
    # the Worker (which reads R2 directly via binding) instead of streaming through
    # the API server.  Leave empty to fall back to server-side streaming.
    worker_zip_url: str = ""
    # Shared HMAC-SHA256 secret between the API and the Worker.  Must match the
    # HMAC_SECRET variable set on the Worker via `wrangler secret put HMAC_SECRET`.
    worker_zip_hmac_secret: str = ""

    jwt_access_token_expire_days: int = 7
    jwt_refresh_token_expire_days: int = 31

    frontend_url: str = "http://localhost:3000"

    # Stored as a comma-separated string so pydantic-settings never attempts JSON
    # parsing on it. Use the `cors_headers_list` property in application code.
    cors_allowed_headers: str = "Content-Type,Authorization,X-Client-ID,X-Upload-ID,Accept,X-Requested-With,Upload-Checksum,Tus-Checksum-Algorithm"

    @property
    def cors_headers_list(self) -> list[str]:
        """Return CORS headers as a list, handling empty / blank values gracefully."""
        return [h.strip() for h in self.cors_allowed_headers.split(",") if h.strip()] or [
            "Content-Type",
            "Authorization",
            "X-Client-ID",
            "Accept",
            "X-Requested-With",
        ]

    # EuroOffice Document Server
    eurooffice_internal_api_base_url: str = "http://api:8000"
    eurooffice_jwt_secret: str = "change-me-eurooffice-jwt-secret"
    # Separate secret for file-access tokens — known only to the API.
    # Must differ from eurooffice_jwt_secret so a compromised EuroOffice
    # container cannot forge file-download tokens.
    eurooffice_file_token_secret: str = "change-me-eurooffice-file-token-secret"
    eurooffice_file_token_ttl: int = 60  # seconds (1 minute)

    @model_validator(mode="after")
    def _check_secrets(self) -> "Settings":
        if self.is_dev:
            return self

        # Production guard for critical secrets
        _jwt_placeholders = {
            "change-this-to-a-secure-random-string-with-at-least-32-bytes",
        }
        if self.secret_key.get_secret_value() in _jwt_placeholders:
            raise ValueError(
                "SECRET_KEY must be set to a secure value in production. "
                'Generate one: python -c "import secrets; print(secrets.token_hex(32))"'
            )

        if self.meili_master_key == "change-me":
            raise ValueError("MEILI_MASTER_KEY must be set to a secure value in production.")

        _known_placeholders = {
            "change-me-eurooffice-jwt-secret",
            "insecure-dev-only-eurooffice-secret",
        }
        if self.eurooffice_jwt_secret in _known_placeholders:
            raise ValueError("EUROOFFICE_JWT_SECRET must be set to a secure value in production.")

        _file_token_placeholders = {
            "change-me-eurooffice-file-token-secret",
            "insecure-dev-only-eurooffice-file-token-secret",
        }
        if self.eurooffice_file_token_secret in _file_token_placeholders:
            raise ValueError(
                "EUROOFFICE_FILE_TOKEN_SECRET must be set to a secure value in production."
            )

        if self.eurooffice_file_token_secret == self.eurooffice_jwt_secret:
            raise ValueError("EUROOFFICE_FILE_TOKEN_SECRET must differ from EUROOFFICE_JWT_SECRET.")

        return self

    @property
    def is_dev(self) -> bool:
        return self.environment == "development"


settings = Settings()
