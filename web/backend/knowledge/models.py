"""Unmanaged ORM mappings: existing Phase 1–5 tables remain owned by their SQL migrations."""
from django.db import models
from django.contrib.postgres.fields import ArrayField


class Document(models.Model):
    category = models.TextField()
    body = models.TextField()
    embedding_model_version = models.TextField()
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "documents"


class HealthSnapshot(models.Model):
    recall_at_k = models.FloatField(null=True)
    ann_recall_at_k = models.FloatField(null=True)
    distance_shift_pct = models.FloatField(null=True)
    version_skew_pct = models.FloatField(null=True)
    sentinel_mismatch_pct = models.FloatField(null=True)
    health_status = models.TextField(null=True)
    issues = ArrayField(models.TextField())
    recorded_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "health_snapshots"


class MaintenanceEvent(models.Model):
    diagnosis = models.TextField()
    actions = ArrayField(models.TextField())
    status = models.TextField()
    affected_rows = models.IntegerField()
    started_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "maintenance_events"
