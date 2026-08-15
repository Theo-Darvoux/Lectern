import re
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    environment: Literal["development", "production", "test"] = "development"
    secret_key: SecretStr = SecretStr(
        "change-this-to-a-secure-random-string-with-at-least-32-bytes"
    )

    # CAS (content-addressed storage) key-derivation domain separation. These
    # seed the HKDF (over SECRET_KEY) that produces every CAS digest — i.e. the
    # `cas/` S3 object keys and the Redis dedup/ref-count keys. Changing either
    # re-derives all digests, so a deployment that already has stored files must
    # keep the values it was created with.
    cas_hkdf_salt: str = "lectern-cas-salt-v1"
    cas_hkdf_info: str = "lectern-cas-v1"

    # One-time operator capability used only by the production first-run HTTP bootstrap.
    # Existing initialized installations may leave this unset. Fresh production installs
    # must configure a strong value before POST /api/auth/setup can succeed.
    bootstrap_token: SecretStr | None = None

    # Auth toggles
    totp_enabled: bool = True
    google_oauth_enabled: bool = False
    google_client_id: str | None = None
    classic_auth_enabled: bool = False
    classic_auth_max_concurrent_hashes: int = Field(default=4, ge=1, le=32)
    allow_all_domains: bool = False
    auto_approve_all_domains: bool = False
    guest_access_enabled: bool = False

    # Feature toggles
    # Set TUTORIALS_ENABLED=false to disable the in-app guided tours platform-wide.
    tutorials_enabled: bool = True
    # Allow safe http(s)/mailto links embedded in documents and document viewers.
    # This is public runtime configuration because the browser renderers enforce it too.
    allow_external_document_links: bool = True

    # Allowed domains — comma-separated "domain:auto|domain:manual" entries.
    # When set, this wins over DB rows. Empty means fall back to DB.
    # Example: "example.com:auto,example.org:manual"
    allowed_domains: str = ""

    # Custom placeholder email displayed on login inputs (e.g. "prenom.nom@telecom-sudparis.eu").
    email_placeholder: str | None = None

    # Branding Defaults
    # Default product name; override per-instance with SITE_NAME in .env.
    site_name: str = "Lectern"
    site_name_style: str | None = None
    site_description: str = "Collaborative course materials platform"
    site_logo_url: str | None = None
    site_favicon_url: str | None = None
    og_image_url: str | None = None
    bg_watermark_url: str | None = None
    bg_watermark_opacity_light: float | None = None
    bg_watermark_opacity_dark: float | None = None
    footer_logo_url: str | None = None
    primary_color: str = "#3b82f6"
    footer_text: str = ""
    organization_url: str | None = None
    repo_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("repo_url", "next_public_repo_url"),
    )
    eurooffice_public_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("eurooffice_public_url", "next_public_eurooffice_url"),
    )
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

    database_url: str = "postgresql+asyncpg://lectern:lectern@localhost:5432/lectern"
    # CAS physical-mutation fencing uses PostgreSQL session advisory locks.
    # Transaction-pooled proxies cannot preserve that identity across COMMIT.
    database_pool_mode: Literal["session", "transaction"] = "session"

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
    s3_bucket: str = "lectern"
    s3_region: str = "us-east-1"
    s3_use_ssl: bool = False
    s3_use_accelerate_endpoint: bool = False
    max_storage_gb: int = 10

    meili_url: str = "http://localhost:7700"
    meili_master_key: str = "change-me"
    # Search-only key for public search. If absent/invalid, startup provisions a
    # restricted key through the admin client; the master key is never exposed.
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

    # Physical CAS mutations are journaled before S3 I/O. The live operation has
    # a hard application deadline; an orphaned journal may be recovered only after
    # that deadline plus an additional ambiguity grace period has elapsed.
    cas_mutation_io_timeout_seconds: int = Field(default=900, ge=60, le=3600)
    cas_mutation_recovery_grace_seconds: int = Field(default=900, ge=30, le=3600)

    # tus resumable upload settings
    tus_chunk_min_bytes: int = 5 * 1024 * 1024  # 5 MiB (S3 multipart minimum)
    tus_chunk_max_bytes: int = 100 * 1024 * 1024  # 100 MiB
    tus_max_size_bytes: int = 500 * 1024 * 1024  # 500 MiB
    tus_max_concurrent_per_user: int = 8
    tus_max_concurrent_global: int = Field(default=8, ge=1, le=64)

    yara_rules_dir: str = "yara_rules"
    yara_scan_timeout: int = 60
    malwarebazaar_timeout: int = 5
    malwarebazaar_url: str = "https://mb-api.abuse.ch/api/v1/"
    malwarebazaar_api_key: str | None = None
    # When True, a MalwareBazaar timeout/error fails the scan (fail-closed).
    # When False (default), YARA remains the authoritative gatekeeper on API failure.
    malwarebazaar_fail_closed: bool = True
    # Deprecated compatibility setting. MalwareBazaar admission checks now always
    # finish before an upload is published as clean; this flag no longer defers them.
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
    # Explicit transport mode. When omitted, legacy SMTP_USE_TLS maps port 465
    # to implicit TLS and all other ports to STARTTLS.
    smtp_tls_mode: Literal["none", "starttls", "implicit"] | None = None

    backup_dir: str = "/var/lib/lectern/backups"

    # Observability — Prometheus /metrics endpoint
    # When set, callers must pass Authorization: Bearer <value>; URL query secrets are rejected.
    # Leave empty (default) to allow unauthenticated scraping inside a private network.
    metrics_token: str = ""

    # OpenTelemetry Collector endpoint (e.g. "localhost:4317")
    otel_endpoint: str = ""
    otel_insecure: bool = False

    enable_presigned_multipart: bool = True
    direct_upload_threshold_mb: int = 10  # files smaller than this use direct upload

    # CAS deduplication: maximum age (seconds) of a CAS entry before it requires
    # re-scanning.  Set to 0 to disable staleness checks (always trust cache).
    # Default: 7 days.  Entries older than this or older than the YARA rules
    # compilation timestamp will be re-processed through the full pipeline.
    cas_max_age_seconds: int = 7 * 24 * 3600

    # Sandboxed file-processing limits. All temporary inputs and outputs used by
    # sandboxed processors must be descendants of processing_root.
    processing_root: str = "/tmp/lectern-processing"
    sandbox_memory_limit_mb: int = Field(default=1024, ge=128)
    sandbox_cpu_limit_seconds: int = Field(default=60, ge=1)
    sandbox_file_size_limit_mb: int = Field(default=100, ge=1)
    sandbox_process_limit: int = Field(default=64, ge=1)

    # Concurrency guards for heavy worker operations. These are explicit rather
    # than CPU-derived so every production worker uses the same Redis limit.
    global_max_subprocesses: int = Field(default=4, ge=1)
    max_concurrent_image_ops: int = Field(default=1, ge=1)

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

    thumbnail_quality: int = Field(default=85, ge=0, le=100)
    thumbnail_size_px: int = Field(default=640, ge=64, le=2048)

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
    cors_allowed_headers: str = "Content-Type,Authorization,X-Client-ID,X-Upload-ID,X-Upload-Group-ID,Accept,X-Requested-With,Upload-Checksum,Tus-Checksum-Algorithm"
    # Only these reverse proxies may supply X-Forwarded-For/Proto. The API's
    # direct listener must never accept a client-spoofed source address.
    trusted_proxy_hosts: str = "127.0.0.1,::1"

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

    @property
    def trusted_proxy_hosts_list(self) -> list[str]:
        return [host.strip() for host in self.trusted_proxy_hosts.split(",") if host.strip()]

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
        if self.database_url.startswith("postgresql") and self.database_pool_mode != "session":
            raise ValueError(
                "DATABASE_POOL_MODE=transaction is incompatible with CAS process fencing; "
                "use a direct PostgreSQL connection or a session-pooled proxy."
            )

        if self.bootstrap_token is not None:
            bootstrap_value = self.bootstrap_token.get_secret_value()
            if re.fullmatch(r"[0-9A-Fa-f]{64}", bootstrap_value) is None:
                raise ValueError(
                    "BOOTSTRAP_TOKEN must be exactly 64 hexadecimal characters (32 random bytes)."
                )

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
        if len(self.secret_key.get_secret_value().encode()) < 32:
            raise ValueError("SECRET_KEY must contain at least 32 bytes in production.")

        if self.meili_master_key == "change-me":
            raise ValueError("MEILI_MASTER_KEY must be set to a secure value in production.")

        _known_placeholders = {
            "change-me-eurooffice-jwt-secret",
            "insecure-dev-only-eurooffice-secret",
        }
        if self.eurooffice_jwt_secret in _known_placeholders:
            raise ValueError("EUROOFFICE_JWT_SECRET must be set to a secure value in production.")
        if len(self.eurooffice_jwt_secret.encode()) < 32:
            raise ValueError("EUROOFFICE_JWT_SECRET must contain at least 32 bytes in production.")

        _file_token_placeholders = {
            "change-me-eurooffice-file-token-secret",
            "insecure-dev-only-eurooffice-file-token-secret",
        }
        if self.eurooffice_file_token_secret in _file_token_placeholders:
            raise ValueError(
                "EUROOFFICE_FILE_TOKEN_SECRET must be set to a secure value in production."
            )
        if len(self.eurooffice_file_token_secret.encode()) < 32:
            raise ValueError(
                "EUROOFFICE_FILE_TOKEN_SECRET must contain at least 32 bytes in production."
            )

        if self.eurooffice_file_token_secret == self.eurooffice_jwt_secret:
            raise ValueError("EUROOFFICE_FILE_TOKEN_SECRET must differ from EUROOFFICE_JWT_SECRET.")

        if self.s3_access_key == "minioadmin" or self.s3_secret_key == "minioadmin":
            raise ValueError("Default MinIO development credentials are forbidden in production.")

        if self.worker_zip_url and len(self.worker_zip_hmac_secret.encode()) < 32:
            raise ValueError(
                "WORKER_ZIP_HMAC_SECRET must contain at least 32 bytes when worker delivery "
                "is enabled in production."
            )

        if self.webhook_secret and len(self.webhook_secret.encode()) < 32:
            raise ValueError("WEBHOOK_SECRET must contain at least 32 bytes in production.")

        return self

    @property
    def is_dev(self) -> bool:
        return self.environment == "development"


settings = Settings()
