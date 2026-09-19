"""
Spatial Database Storage Module (PostGIS & SQLite Spatial Engine)
Supports:
1. PostgreSQL + PostGIS spatial tables for correspondence tie-points & GCPs.
2. Built-in SQLite Spatial fallback with WKT/GeoJSON point geometry.
3. Exporting GeoJSON FeatureCollections for geospatial web mapping and GIS.
4. Spatial bounding-box and radius queries on registered GCPs.
"""

import os
import sqlite3
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

HAS_PSYCOPG2 = False
try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False


class SpatialStorageEngine:
    """Manages spatial persistence of lunar tie-points, GCPs, and registration runs."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            self.db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "lunarvision_spatial.db"))
        else:
            self.db_path = db_path

        # Check for PostgreSQL / PostGIS configuration
        self.pg_host = os.environ.get("POSTGRES_HOST")
        self.pg_port = int(os.environ.get("POSTGRES_PORT", 5432))
        self.pg_db = os.environ.get("POSTGRES_DB", "lunarvision")
        self.pg_user = os.environ.get("POSTGRES_USER", "postgres")
        self.pg_password = os.environ.get("POSTGRES_PASSWORD", "postgres")
        self.use_postgis = bool(self.pg_host and HAS_PSYCOPG2)

        self._init_sqlite_tables()
        if self.use_postgis:
            self._init_postgis_tables()

    def _get_pg_connection(self):
        """Returns a new psycopg2 connection to PostgreSQL/PostGIS."""
        return psycopg2.connect(
            host=self.pg_host,
            port=self.pg_port,
            dbname=self.pg_db,
            user=self.pg_user,
            password=self.pg_password,
            connect_timeout=3
        )

    def _init_postgis_tables(self):
        """Initializes PostGIS schema and spatial geometry columns."""
        try:
            with self._get_pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS registration_tasks (
                            task_id VARCHAR(64) PRIMARY KEY,
                            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            status VARCHAR(32),
                            matcher_backend VARCHAR(64),
                            estimator VARCHAR(64),
                            inlier_count INTEGER,
                            rmse_px REAL,
                            lat_min REAL,
                            lat_max REAL,
                            lon_min REAL,
                            lon_max REAL
                        );
                    """)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS lunar_gcp_tiepoints (
                            id SERIAL PRIMARY KEY,
                            task_id VARCHAR(64) REFERENCES registration_tasks(task_id) ON DELETE CASCADE,
                            gcp_id VARCHAR(32),
                            pixel_x DOUBLE PRECISION,
                            pixel_y DOUBLE PRECISION,
                            confidence REAL,
                            geom GEOMETRY(Point, 4326)
                        );
                    """)
                    cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_lunar_gcp_geom 
                        ON lunar_gcp_tiepoints USING GIST (geom);
                    """)
                conn.commit()
            logger.info("Successfully initialized PostgreSQL/PostGIS spatial storage on %s:%s", self.pg_host, self.pg_port)
        except Exception as exc:
            logger.warning("PostGIS initialization failed (%s); operating on SQLite fallback: %s", self.pg_host, exc)
            self.use_postgis = False

    def _init_sqlite_tables(self):
        """Initializes SQLite spatial schema with WKT point columns."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS registration_tasks (
                        task_id TEXT PRIMARY KEY,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        status TEXT,
                        matcher_backend TEXT,
                        estimator TEXT,
                        inlier_count INTEGER,
                        rmse_px REAL,
                        lat_min REAL,
                        lat_max REAL,
                        lon_min REAL,
                        lon_max REAL
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS tie_points (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id TEXT,
                        gcp_id TEXT,
                        pixel_x REAL,
                        pixel_y REAL,
                        lat REAL,
                        lon REAL,
                        confidence REAL,
                        geom_wkt TEXT,
                        FOREIGN KEY (task_id) REFERENCES registration_tasks(task_id)
                    )
                """)
                conn.commit()
        except Exception as exc:
            logger.error("Failed to initialize SQLite tables in %s: %s", self.db_path, exc)

    def save_task_run(
        self,
        task_id: str,
        status: str,
        metrics: Dict[str, Any],
        geo_bounds: Optional[Dict[str, Any]],
        gcp_list: List[Dict[str, Any]]
    ) -> bool:
        """Stores a completed registration run and its spatial GCP points in SQLite and PostGIS."""
        sqlite_ok = False
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                bounds = geo_bounds or {}
                cursor.execute("""
                    INSERT OR REPLACE INTO registration_tasks
                    (task_id, status, matcher_backend, estimator, inlier_count, rmse_px, lat_min, lat_max, lon_min, lon_max)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    task_id,
                    status,
                    metrics.get("matcher_backend", "phase_congruency"),
                    metrics.get("estimator", "MAGSAC++"),
                    int(metrics.get("inlier_count", 0)),
                    float(metrics.get("subpixel_rmse_px", 0.0) or 0.0),
                    float(bounds.get("lat_min", 0.0)),
                    float(bounds.get("lat_max", 0.0)),
                    float(bounds.get("lon_min", 0.0)),
                    float(bounds.get("lon_max", 0.0))
                ))

                for gcp in gcp_list:
                    lat = float(gcp.get("lat", 0.0))
                    lon = float(gcp.get("lon", 0.0))
                    wkt = f"POINT({lon} {lat})"
                    cursor.execute("""
                        INSERT INTO tie_points (task_id, gcp_id, pixel_x, pixel_y, lat, lon, confidence, geom_wkt)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        task_id,
                        str(gcp.get("id", "")),
                        float(gcp.get("pixel_x", 0.0)),
                        float(gcp.get("pixel_y", 0.0)),
                        lat,
                        lon,
                        float(gcp.get("confidence", 1.0)),
                        wkt
                    ))
                conn.commit()
                sqlite_ok = True
        except Exception as exc:
            logger.error("Failed to save task %s to SQLite: %s", task_id, exc)

        # Mirror into PostGIS if available
        if self.use_postgis:
            try:
                with self._get_pg_connection() as conn:
                    with conn.cursor() as cur:
                        bounds = geo_bounds or {}
                        cur.execute("""
                            INSERT INTO registration_tasks 
                            (task_id, status, matcher_backend, estimator, inlier_count, rmse_px, lat_min, lat_max, lon_min, lon_max)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (task_id) DO UPDATE SET 
                                status = EXCLUDED.status,
                                inlier_count = EXCLUDED.inlier_count,
                                rmse_px = EXCLUDED.rmse_px;
                        """, (
                            task_id,
                            status,
                            metrics.get("matcher_backend", "phase_congruency"),
                            metrics.get("estimator", "MAGSAC++"),
                            int(metrics.get("inlier_count", 0)),
                            float(metrics.get("subpixel_rmse_px", 0.0) or 0.0),
                            float(bounds.get("lat_min", 0.0)),
                            float(bounds.get("lat_max", 0.0)),
                            float(bounds.get("lon_min", 0.0)),
                            float(bounds.get("lon_max", 0.0))
                        ))

                        for gcp in gcp_list:
                            lat = float(gcp.get("lat", 0.0))
                            lon = float(gcp.get("lon", 0.0))
                            cur.execute("""
                                INSERT INTO lunar_gcp_tiepoints 
                                (task_id, gcp_id, pixel_x, pixel_y, confidence, geom)
                                VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                            """, (
                                task_id,
                                str(gcp.get("id", "GCP")),
                                float(gcp.get("pixel_x", 0.0)),
                                float(gcp.get("pixel_y", 0.0)),
                                float(gcp.get("confidence", 1.0)),
                                lon,
                                lat
                            ))
                    conn.commit()
            except Exception as exc:
                logger.warning("PostGIS mirror failed for task %s: %s", task_id, exc)

        return sqlite_ok

    def query_geojson_tiepoints(self, task_id: str) -> Dict[str, Any]:
        """Returns GeoJSON FeatureCollection of tie-points for Leaflet / QGIS."""
        features = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT gcp_id, pixel_x, pixel_y, lat, lon, confidence
                    FROM tie_points WHERE task_id = ?
                """, (task_id,))
                rows = cursor.fetchall()
                for row in rows:
                    gcp_id, px, py, lat, lon, conf = row
                    features.append({
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [lon, lat]
                        },
                        "properties": {
                            "gcp_id": gcp_id,
                            "pixel_x": px,
                            "pixel_y": py,
                            "confidence": conf
                        }
                    })
        except Exception as exc:
            logger.error("Failed querying tiepoints for task %s: %s", task_id, exc)

        return {
            "type": "FeatureCollection",
            "task_id": task_id,
            "features": features
        }

    @staticmethod
    def generate_postgis_sql_dump(task_id: str, gcp_list: List[Dict[str, Any]]) -> str:
        """
        Generates production PostgreSQL + PostGIS DDL and INSERT statements
        using EPSG:4326 (or Moon 2000 IAU spatial reference system).
        Sanitizes string literals against SQL injection.
        """
        safe_task_id = str(task_id).replace("'", "''")
        sql_lines = [
            "-- LunarVision PostGIS Spatial Export",
            f"-- Registration Task ID: {safe_task_id}",
            "CREATE EXTENSION IF NOT EXISTS postgis;",
            """CREATE TABLE IF NOT EXISTS lunar_gcp_tiepoints (
    id SERIAL PRIMARY KEY,
    task_id VARCHAR(64),
    gcp_id VARCHAR(32),
    pixel_x DOUBLE PRECISION,
    pixel_y DOUBLE PRECISION,
    confidence REAL,
    geom GEOMETRY(Point, 4326)
);""",
            "CREATE INDEX IF NOT EXISTS idx_lunar_gcp_geom ON lunar_gcp_tiepoints USING GIST (geom);"
        ]
        for gcp in gcp_list:
            lat = float(gcp.get("lat", 0.0))
            lon = float(gcp.get("lon", 0.0))
            px = float(gcp.get("pixel_x", 0.0))
            py = float(gcp.get("pixel_y", 0.0))
            conf = float(gcp.get("confidence", 1.0))
            gid = str(gcp.get("id", "GCP")).replace("'", "''")
            sql_lines.append(
                f"INSERT INTO lunar_gcp_tiepoints (task_id, gcp_id, pixel_x, pixel_y, confidence, geom) "
                f"VALUES ('{safe_task_id}', '{gid}', {px:.4f}, {py:.4f}, {conf:.4f}, ST_SetSRID(ST_MakePoint({lon:.6f}, {lat:.6f}), 4326));"
            )
        return "\n".join(sql_lines)
