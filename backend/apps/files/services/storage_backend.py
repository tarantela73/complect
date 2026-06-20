from __future__ import annotations

# Protocol and factory re-exported from apps/common for backward compatibility.
# Other domains must import from apps.common.contracts.storage_contracts (ADR-0005).
from apps.common.contracts.storage_contracts import StorageBackend as StorageBackend
from apps.common.contracts.storage_contracts import get_storage_backend as get_storage_backend


class StubStorageBackend:
    """Dev/test stub. Always reports object_exists=True."""

    def generate_upload_url(
        self, *, object_key: str, content_type: str, size_bytes: int, expires_in: int
    ) -> str:
        return f"/stub/upload/{object_key}"

    def generate_download_url(self, *, object_key: str, expires_in: int) -> str:
        return f"/stub/download/{object_key}"

    def object_exists(self, *, object_key: str) -> bool:
        return True


class S3StorageBackend:
    """
    Production S3 backend using boto3 presigned URLs.

    Credentials come from IAM role or environment variables
    (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) — never in code (rule 5.7).
    """

    def __init__(self) -> None:
        import boto3
        from django.conf import settings

        self._bucket: str = settings.AWS_S3_BUCKET_NAME
        self._s3 = boto3.client("s3", region_name=settings.AWS_S3_REGION_NAME)

    def generate_upload_url(
        self, *, object_key: str, content_type: str, size_bytes: int, expires_in: int
    ) -> str:
        return self._s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": object_key,
                "ContentType": content_type,
                "ContentLength": size_bytes,
            },
            ExpiresIn=expires_in,
        )

    def generate_download_url(self, *, object_key: str, expires_in: int) -> str:
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": object_key},
            ExpiresIn=expires_in,
        )

    def object_exists(self, *, object_key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._s3.head_object(Bucket=self._bucket, Key=object_key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "404":
                return False
            raise


