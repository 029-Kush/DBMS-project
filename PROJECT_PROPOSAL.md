# Project Proposal

## Administrative details requiring confirmation

- Course in faculty PDF: **Database Systems — BCSE307L**
- Course in original repository material: **BCSE302P**
- Faculty in PDF: **Dr. Deepika J**
- Team: **Kush Gupta and Arnav Tiwari**

The course code must be confirmed before final submission; this proposal does
not guess between contradictory sources.

## 1. Project title

**Secure Semantic Knowledge Base with Self-Healing Vector Search**

## 2. Problem statement

Organizations need to store, secure, retrieve and analyze growing document
collections. Keyword search misses semantically related information, while
vector search can silently degrade when embeddings from incompatible models are
mixed, indexes disappear or become unhealthy, or database churn creates bloat.
Existing small deployments usually depend on manual diagnosis and broad
rebuilds. This project will provide a role-secured PostgreSQL application for
document management and semantic search, with observable vector-search health
and eventually verified, targeted repair.

## 3. Objectives

1. Design a normalized relational database with at least eight related tables,
   documented through an ER diagram, schema and data dictionary.
2. Build a responsive React application and Django REST API using Django ORM.
3. Implement authenticated, role-authorized document and workspace CRUD.
4. Provide semantic document search through PostgreSQL and pgvector.
5. Implement joins, aggregates, views, transactions, a procedure, a trigger and
   workload-appropriate indexes.
6. Measure recall, latency, distance drift, model-version skew, table bloat and
   vector-index state.
7. Diagnose supported failure modes and apply only the relevant repair.
8. Re-measure every repair and store an immutable audit trail.
9. Containerize, back up, restore and deploy the complete application.

## 4. Scope

### In scope

- User login/logout and authenticated-user retrieval.
- `ADMIN` and `MEMBER` roles stored in one users table.
- Role-based menus, pages and APIs.
- Workspace, collection and document CRUD.
- Document versions and pgvector embeddings.
- Search, category/collection/date filters and pagination.
- Admin health dashboard and maintenance history.
- Canary monitoring, reversible fault experiments and supported repairs.
- PostgreSQL backup/restore, Docker and cloud deployment.

### Out of scope for the graded release

- Multi-region database replication.
- Training a new neural embedding model from scratch.
- Autonomous repair for unknown or ambiguous failure types.
- Production-scale guarantees based only on the 480-document demo corpus.

## 5. Team members and proposed responsibilities

This split is a proposal and requires confirmation by both members.

| Member | Primary responsibilities | Shared responsibilities |
|---|---|---|
| Kush Gupta | Django models/APIs, PostgreSQL/pgvector, health and repair subsystem | ERD, security, tests, deployment, documentation |
| Arnav Tiwari | React UI, responsive dashboard, role-aware navigation and forms | ERD, security, tests, deployment, documentation |

## 6. Technology stack

| Layer | Technology |
|---|---|
| Frontend | React, React Router, responsive CSS component system |
| Backend | Django, Django REST Framework |
| ORM | Django ORM; justified raw SQL for pgvector/procedure/trigger features |
| Database | PostgreSQL 16, pgvector |
| Authentication | Django custom user model; secure token/session design |
| Password hashing | Django Argon2 or configured secure default |
| Monitoring | Existing canary and health-metric subsystem |
| Testing | Django/DRF tests, frontend tests, existing Python unit tests |
| Packaging | Docker and Docker Compose |
| Deployment | Free cloud frontend/backend/PostgreSQL platform, to be selected |
| CI/CD | GitHub Actions after mandatory deployment works |

## 7. Major deliverables

1. Approved proposal and Gantt chart.
2. ER diagram, relational schema, data dictionary and 3NF justification.
3. PostgreSQL database with ORM models, constraints and indexes.
4. CRUD, joins, aggregates, views, transactions, procedure and trigger.
5. Responsive authenticated React application and DRF API.
6. Role-based menus, pages and endpoints.
7. Semantic search and filtering/pagination workflows.
8. Health monitoring, diagnosis, repair verification and audit history.
9. Native database backup and tested restoration evidence.
10. Private collaborative GitHub repository with progressive history.
11. Docker images/configuration and deployed live URL.
12. README, test evidence and final presentation/demo.

## 8. Simple Gantt chart

| Deliverable | W1 | W2 | W3 | W4 | W5 | W6 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Proposal, ERD, schema, dictionary, 3NF | ■ | ■ |  |  |  |  |
| Django ORM models and database features |  | ■ | ■ |  |  |  |
| Authentication, RBAC and security |  |  | ■ | ■ |  |  |
| React CRUD/search/dashboard |  |  | ■ | ■ | ■ |  |
| Docker, backup/restore and deployment |  |  |  | ■ | ■ |  |
| Diagnosis, targeted repair and audit |  |  |  |  | ■ | ■ |
| Testing, documentation and final demo |  |  |  |  | ■ | ■ |

Replace W1–W6 with faculty deadline dates once confirmed.
