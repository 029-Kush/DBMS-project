from django.urls import path
from knowledge import views

urlpatterns = [
    path("api/graph/", views.GraphView.as_view()),
    path("api/search/", views.SearchView.as_view()),
    path("api/health/", views.HealthView.as_view()),
    path("api/documents/<int:document_id>/neighbors/", views.NeighborsView.as_view()),
]
