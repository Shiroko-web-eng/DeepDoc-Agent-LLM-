from __future__ import annotations

from pathlib import Path
from typing import Protocol

from app.config import Settings


class ObjectStorage(Protocol):
    def locator(self, key: str) -> str: ...
    def write(self, locator: str, content: bytes) -> None: ...
    def read(self, locator: str) -> bytes: ...
    def delete(self, locator: str) -> None: ...


class LocalObjectStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def locator(self, key: str) -> str:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("storage key escapes configured root")
        return str(path)

    def write(self, locator: str, content: bytes) -> None:
        path = Path(locator)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def read(self, locator: str) -> bytes:
        return Path(locator).read_bytes()

    def delete(self, locator: str) -> None:
        Path(locator).unlink(missing_ok=True)


class S3ObjectStorage:
    def __init__(self, settings: Settings):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("install the production extra to use S3 storage") from exc
        self.bucket = settings.s3_bucket
        self.prefix = settings.s3_prefix.strip("/")
        self.encryption = settings.s3_server_side_encryption
        self.kms_key_id = settings.s3_kms_key_id
        self.client = boto3.client(
            "s3", endpoint_url=settings.s3_endpoint_url or None,
            region_name=settings.s3_region or None,
        )

    def locator(self, key: str) -> str:
        return f"{self.prefix}/{key}" if self.prefix else key

    def write(self, locator: str, content: bytes) -> None:
        options = {"ServerSideEncryption": self.encryption}
        if self.encryption == "aws:kms":
            options["SSEKMSKeyId"] = self.kms_key_id
        self.client.put_object(
            Bucket=self.bucket, Key=locator, Body=content, **options
        )

    def read(self, locator: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=locator)["Body"].read()

    def delete(self, locator: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=locator)


def build_storage(settings: Settings) -> ObjectStorage:
    if settings.storage_backend == "s3":
        return S3ObjectStorage(settings)
    return LocalObjectStorage(settings.upload_dir)
