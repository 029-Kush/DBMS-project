"""Read API for the graph workspace. Vector similarity uses pgvector SQL."""
import sys
from functools import lru_cache
from pathlib import Path

from django.db import connection, transaction
from django.db.models import Count
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Document, HealthSnapshot, MaintenanceEvent

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "selfheal_pgvector" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def positive_int(value, default, maximum):
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def document_json(doc, distance=None):
    result = {
        "id": doc.id,
        "category": doc.category,
        "body": doc.body,
        "title": doc.body.split(".")[0].strip()[:90],
        "model_version": doc.embedding_model_version,
        "created_at": doc.created_at.isoformat(),
    }
    if distance is not None:
        result["distance"] = float(distance)
    return result


class GraphView(APIView):
    def get(self, request):
        limit = positive_int(request.query_params.get("limit"), 90, 150)
        offset = max(0, min(int(request.query_params.get("offset", 0) or 0), 100000)) if str(request.query_params.get("offset", "0")).isdigit() else 0
        category = (request.query_params.get("category") or "").strip()
        counts = list(Document.objects.values("category").annotate(count=Count("id")).order_by("category"))
        docs = Document.objects.all().order_by("id")
        if category:
            docs = docs.filter(category__iexact=category)
        total = docs.count()
        if category:
            page = list(docs[offset:offset + limit])
        else:
            # Give every collection a visible cluster. The corpus is loaded in
            # category order, so a plain first-page slice would show one topic.
            group_count = len(counts) or 1
            base, remainder = divmod(limit, group_count)
            page = []
            for index, item in enumerate(counts):
                take = base + (1 if index < remainder else 0)
                page.extend(Document.objects.filter(category=item["category"]).order_by("id")[:take])
        ids = [doc.id for doc in page]
        links = []
        if len(ids) > 1:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT DISTINCT ON (a.id) a.id, b.id,
                              a.embedding <=> b.embedding AS distance
                       FROM documents a CROSS JOIN documents b
                       WHERE a.id = ANY(%s) AND b.id = ANY(%s) AND a.id <> b.id
                       ORDER BY a.id, distance, b.id""",
                    (ids, ids),
                )
                seen = set()
                for source, target, distance in cursor.fetchall():
                    pair = tuple(sorted((source, target)))
                    if pair not in seen and distance < 0.65:
                        links.append({"source": source, "target": target, "distance": float(distance)})
                        seen.add(pair)
        return Response({
            "documents": [document_json(doc) for doc in page],
            "links": links,
            "total": total,
            "categories": [{"name": item["category"], "count": item["count"]} for item in counts],
            "limit": limit,
            "offset": offset,
        })


@lru_cache(maxsize=1)
def embedder():
    from embed_model import load_embedder
    return load_embedder()


class SearchView(APIView):
    def get(self, request):
        query = (request.query_params.get("q") or "").strip()
        if not query:
            return Response({"error": "Enter a search phrase."}, status=400)
        if len(query) > 500:
            return Response({"error": "Search phrase must be 500 characters or less."}, status=400)
        limit = positive_int(request.query_params.get("limit"), 20, 50)
        category = (request.query_params.get("category") or "").strip()
        if category and not Document.objects.filter(category__iexact=category).exists():
            return Response({"query": query, "latency_ms": 0, "results": []})
        from search import search_documents
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    result = search_documents(cursor, embedder(), query, top_k=limit, source="web", category=category or None)
            documents = Document.objects.filter(id__in=result["ids"])
            by_id = {doc.id: doc for doc in documents}
            rows = [document_json(by_id[doc_id], distance) for doc_id, distance in zip(result["ids"], result["distances"]) if doc_id in by_id]
            return Response({"query": query, "latency_ms": result["latency_ms"], "results": rows})
        except ValueError as error:
            return Response({"error": str(error)}, status=400)


class HealthView(APIView):
    def get(self, request):
        snapshots = list(HealthSnapshot.objects.order_by("-recorded_at")[:12])
        events = list(MaintenanceEvent.objects.order_by("-started_at")[:10])

        def snapshot_json(item):
            return {
                "id": item.id,
                "status": item.health_status,
                "issues": item.issues or [],
                "recall": item.recall_at_k,
                "ann_recall": item.ann_recall_at_k,
                "distance_shift_pct": item.distance_shift_pct,
                "version_skew_pct": item.version_skew_pct,
                "sentinel_mismatch_pct": item.sentinel_mismatch_pct,
                "recorded_at": item.recorded_at.isoformat(),
            }

        return Response({
            "latest": snapshot_json(snapshots[0]) if snapshots else None,
            "history": [snapshot_json(item) for item in snapshots],
            "events": [{
                "id": item.id,
                "status": item.status,
                "diagnosis": item.diagnosis,
                "actions": item.actions or [],
                "affected_rows": item.affected_rows,
                "started_at": item.started_at.isoformat(),
            } for item in events],
        })


class NeighborsView(APIView):
    """Nearest documents in the complete corpus, measured by pgvector cosine distance."""

    def get(self, request, document_id):
        document = Document.objects.filter(id=document_id).first()
        if document is None:
            return Response({"error": "Document not found."}, status=404)

        limit = positive_int(request.query_params.get("limit"), 4, 10)
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT d.id, d.embedding <=> source.embedding AS distance
                   FROM documents d
                   JOIN documents source ON source.id = %s
                   WHERE d.id <> source.id
                   ORDER BY distance, d.id
                   LIMIT %s""",
                (document_id, limit),
            )
            ranked = cursor.fetchall()
        by_id = Document.objects.in_bulk([row[0] for row in ranked])
        return Response({
            "document": document_json(document),
            "neighbors": [
                document_json(by_id[doc_id], distance)
                for doc_id, distance in ranked
                if doc_id in by_id
            ],
        })
