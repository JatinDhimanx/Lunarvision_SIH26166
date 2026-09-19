"""
Object Storage Adapter for MinIO / AWS S3 Compatible Storage
Supports:
1. Connecting to MinIO / S3 object stores via standard S3 API or minio SDK.
2. Uploading registered deliverable rasters, chips, GCP tables, and ZIP bundles.
3. Clean, resilient local filesystem fallback when MinIO is offline.
"""

import os
from typing import Dict, Any, Optional


class ObjectStorageManager:
    """Manages object storage for large planetary raster tiles and output deliverables."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        bucket_name: str = "lunarvision-deliverables",
        secure: bool = False
    ):
        self.endpoint = endpoint or os.environ.get("MINIO_ENDPOINT", "localhost:9000")
        self.access_key = access_key or os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
        self.secret_key = secret_key or os.environ.get("MINIO_SECRET_KEY", "minioadmin")
        self.bucket_name = bucket_name
        self.secure = secure
        self.client = None
        self.is_connected = False

        self._init_client()

    def _init_client(self):
        """Attempts connection to MinIO client if library is installed and server reachable."""
        try:
            from minio import Minio
            self.client = Minio(
                self.endpoint,
                access_key=self.access_key,
                secret_key=self.secret_key,
                secure=self.secure
            )
            # Check bucket presence
            if not self.client.bucket_exists(self.bucket_name):
                self.client.make_bucket(self.bucket_name)
            self.is_connected = True
        except Exception:
            self.client = None
            self.is_connected = False

    def upload_file(self, local_path: str, object_name: str) -> Dict[str, Any]:
        """
        Uploads a local deliverable file to MinIO bucket.
        Falls back cleanly to local filesystem URI if MinIO is not running.
        """
        if not os.path.exists(local_path):
            return {"success": False, "error": f"Local file not found: {local_path}"}

        if self.is_connected and self.client is not None:
            try:
                self.client.fput_object(self.bucket_name, object_name, local_path)
                uri = f"s3://{self.bucket_name}/{object_name}"
                return {
                    "success": True,
                    "storage_backend": "MinIO / S3 Object Store",
                    "bucket": self.bucket_name,
                    "object_name": object_name,
                    "uri": uri,
                    "http_url": f"http://{self.endpoint}/{self.bucket_name}/{object_name}"
                }
            except Exception as exc:
                return {
                    "success": True,
                    "storage_backend": "Local Filesystem Fallback",
                    "local_path": os.path.abspath(local_path),
                    "note": f"MinIO upload error: {exc}; stored on persistent volume"
                }

        return {
            "success": True,
            "storage_backend": "Local Filesystem (MinIO offline)",
            "local_path": os.path.abspath(local_path),
            "uri": f"file://{os.path.abspath(local_path).replace(os.sep, '/')}"
        }

    def get_storage_status(self) -> Dict[str, Any]:
        """Returns diagnostic status of object storage."""
        return {
            "minio_connected": self.is_connected,
            "endpoint": self.endpoint,
            "bucket": self.bucket_name,
            "backend": "MinIO / S3 Compatible" if self.is_connected else "Local Filesystem Storage"
        }
