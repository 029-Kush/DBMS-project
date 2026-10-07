# Academic Requirements Gap Analysis and Revised Roadmap

Primary academic reference: `Database Systems Lab QP 9.pdf` (9 pages)

Repository reviewed: `029-Kush/DBMS-project`, local `main` at `a500184`

Audit date: 5 October 2026

## Decision summary

Preserve the existing PostgreSQL/pgvector implementation as the search and
health-monitoring subsystem. Build a secure organizational knowledge-base
application around it using the required full-stack architecture:

```text
React
  ↓ HTTPS / JSON
Django REST Framework + Django ORM
  ↓
PostgreSQL + pgvector
  ↓
health monitor → diagnosis → targeted repair → verification → audit log
```

Working application domain: **Secure Semantic Knowledge Base with Self-Healing
Vector Search**. Organizations upload and manage documents, search them by
meaning, inspect search health, and authorize maintenance actions by role.

This is an extension of the completed work, not a restart. Existing embedding,
canary, health, query-log, fault-injection, and pgvector logic should become
backend services or scheduled workers behind Django APIs.

## Contradictions requiring confirmation

1. The PDF identifies the course as **Database Systems, BCSE307L**, Fall
   Semester 2026–2027, faculty Dr. Deepika J.
2. The original repository README and demo material identify **BCSE302P**.
3. The newer local technical overview and pitch already identify **BCSE307L**.

Do not silently replace the code everywhere. Confirm the registered course and
class number with the faculty/team, then update all deliverables consistently.

Additional accuracy issue: the pitch describes the intended closed-loop system
in present tense. Current code can measure faults and run a scripted experiment,
but the restoration action is predetermined by the experiment runner. It does
not yet independently diagnose a fault and choose a repair.

## Work that remains valid

- PostgreSQL 16 and pgvector setup.
- `documents` with 64-dimensional embeddings and HNSW index.
- 480-document, six-category reproducible corpus.
- TF-IDF + SVD baseline embedder.
- Twelve-query canary set and Recall@10 baseline.
- Query logging, latency, distance-distribution, version-skew, dead-tuple, and
  index-state monitoring.
- Explainable health statuses and tests.
- Transactional incompatible-embedding fault injection with exact backup.
- Verified experiment: Recall@10 `1.000 → 0.775 → 1.000`; distance shift
  `0% → +21.8% → 0%`; 120/480 vectors affected and restored.
- Fault-run, query-log, and health-snapshot records as foundations for audit and
  monitoring bonus evidence.

## Status legend

- **Complete** — implemented and directly evidenced.
- **Partial** — some evidence exists, but the rubric is not fully satisfied.
- **Missing** — no implementation/evidence found.
- **Needs verification** — cannot be proven from repository/local metadata.

## 1. Problem statement and planning — 2 marks

| Requirement | Status | Current evidence | Required action |
|---|---|---|---|
| Project title | Complete | README, technical overview and pitch | Standardize final title |
| Problem statement | Complete | Formal organizational problem is now in `PROJECT_PROPOSAL.md` | Confirm faculty-approved domain |
| Objectives | Complete | Numbered mandatory-app and self-healing objectives now documented | Validate with team/faculty |
| Scope | Complete | In-scope and out-of-scope boundaries now documented | Validate with team/faculty |
| Team members | Complete | Kush Gupta and Arnav Tiwari named | Confirm registration details |
| Responsibilities | Partial | Proposed matrix now exists | Confirm split with both team members |
| Technology stack | Complete | Full target stack is documented in the proposal | Implement it |
| Major deliverables | Complete | Twelve deliverables now documented | Track completion |
| Gantt chart | Complete | Six-week marks-first chart now documented | Replace W1–W6 with deadline dates |

## 2. Database design — 6 marks

| Requirement | Status | Current evidence | Required action |
|---|---|---|---|
| ER diagram | Missing | None found | Draw before implementing final ORM models |
| Relational schema document | Partial | SQL schema exists | Produce ER-derived relational schema with cardinalities |
| Data dictionary | Missing | Only prose table summaries | Document every column, type, constraint and purpose |
| At least 4 primary entities | Partial | Six technical tables exist | Define application entities, not only monitoring tables |
| At least 3 relationships | Partial | Five FKs exist in fault/health subsystem | Show all relationships in ERD and add application relationships |
| At least 8–10 related tables | Missing | Live catalog has 6 tables | Design at least 10 connected application/monitoring tables |
| Every table derived from ERD | Missing | No ERD | Include every final table in ERD |
| Primary key on every table | Complete | Verified on all 6 current tables | Preserve in Django models |
| Appropriate foreign keys | Partial | Verified on fault backups/runs/snapshots | Add workspace, membership, document and audit FKs |
| NOT NULL/UNIQUE/CHECK/DEFAULT | Partial | NOT NULL, defaults and checks exist | Add business-level uniqueness and validation constraints |
| Third Normal Form | Needs verification | No functional-dependency/normalization document | Document 1NF→2NF→3NF reasoning for final schema |
| Performance indexes | Partial | HNSW, PK and timestamp indexes exist | Add FK, user, workspace, filtering and pagination indexes |

### Recommended final relational model

The exact ERD must be approved before migrations, but this design preserves all
completed work and satisfies the application requirement:

1. `users` — one table; email, password hash, role (`ADMIN`/`MEMBER`), status.
2. `workspaces` — organization/project knowledge spaces.
3. `workspace_memberships` — users ↔ workspaces with membership permissions.
4. `collections` — groups of documents inside a workspace.
5. `documents` — existing table expanded with owner, collection and lifecycle.
6. `document_versions` — immutable text/version history and embedding metadata.
7. `query_log` — existing observable searches linked to user/workspace.
8. `canary_set` — existing fixed regression queries linked to workspace.
9. `health_snapshots` — existing metrics linked to workspace.
10. `maintenance_events` — evolve fault/repair runs into diagnosis/action/audit.
11. `embedding_backups` — existing reversible experiment/repair backup rows.
12. `audit_logs` — authenticated CRUD, role and maintenance actions.

The rubric states a minimum of 8 tables; 12 are acceptable only if every table
has a real purpose, appears in the ERD, and remains in 3NF. Do not add decorative
tables merely to increase the count.

## 3. Database implementation — 8 marks

| Requirement | Status | Current evidence | Required action |
|---|---|---|---|
| Database creation | Complete | Reproducible PostgreSQL/pgvector setup | Move credentials to environment configuration |
| 8–10 related tables | Missing | Six tables | Implement approved ERD through Django migrations |
| CRUD operations | Partial | Scripted inserts, selects, updates and truncation | User-facing ORM CRUD APIs and screens |
| Joins | Missing | No demonstrated relational join report/API | Add ORM joins and at least one documented multi-table report |
| Aggregate queries | Complete | Counts, grouping and health aggregates exist | Expose dashboard aggregates via ORM/API |
| Views | Missing | Live catalog: 0 views | Add useful health-summary/reporting view |
| Transactions | Complete | Commit/rollback and atomic fault injection verified | Add Django `transaction.atomic()` workflows |
| Stored procedure | Missing | No custom procedure | Add one meaningful PostgreSQL procedure through migration |
| Trigger | Missing | Live catalog: 0 triggers | Add meaningful audit/staleness trigger through migration |
| Indexes | Complete for current subsystem | HNSW plus PK/time indexes | Extend indexes for final access patterns |
| ORM database access | Missing | `psycopg2` raw SQL only | Make Django ORM the default; raw SQL only for pgvector/advanced features |

Recommended database features:

- View: `workspace_health_summary` joining workspaces, health snapshots and
  maintenance events.
- Procedure: transactionally archive a document version and mark its embedding
  stale before replacement.
- Trigger: write an audit/staleness event when document content changes.
- Transaction demonstration: create document + first version + audit record as
  one atomic operation, with a deliberate rollback test.

## 4. Application development — 4 marks

| Requirement | Status | Current evidence | Required action |
|---|---|---|---|
| Framework application | Missing | CLI/PowerShell only | React + Django REST Framework |
| Responsive UI | Missing | No web UI | Mobile/tablet/desktop layouts |
| Login page | Missing | None | Token/session login screen |
| Dashboard | Missing | HTML visual summary is not an application dashboard | Live role-aware dashboard |
| CRUD UI/API | Missing | No application endpoints | Documents, collections, workspaces and user admin |
| Search | Partial | CLI vector search exists | Authenticated search API and React results page |
| Filtering | Missing | None | Category, collection, owner, date and health filters |
| Pagination | Missing | None | DRF pagination plus UI controls |
| Form validation | Missing | CLI argument checks only | Serializer and React form validation |
| Exception handling | Partial | Python rollback/exception paths exist | Standard API error model and UI error states |

## 5. Authentication, authorization and security — 5 marks

| Requirement | Status | Current evidence | Required action |
|---|---|---|---|
| Login/logout | Missing | None | Implement both flows |
| Secure token/session handling | Missing | None | Prefer HttpOnly secure cookies or carefully managed JWT |
| Authenticated-user retrieval | Missing | None | `/api/auth/me/` endpoint |
| At least two roles | Missing | None | `ADMIN` and `MEMBER` in one `users` table |
| Role-based menus | Missing | None | React navigation by authenticated role |
| Role-based pages | Missing | None | Route guards and unauthorized states |
| Role-based APIs | Missing | None | DRF permission classes and tests |
| Single users table | Missing | No users table | Use Django custom user model; never separate admin/member tables |
| Password hashing | Missing | None | Django Argon2 or PBKDF2; document configuration |
| SQL injection prevention | Partial | Current SQL is parameterized | Move normal access to ORM and test hostile input |
| `.env` secret management | Partial | `.env.example` now exists, but credentials remain hardcoded in legacy files | Refactor every runtime/configuration path to environment variables |
| Secret exclusion | Complete | `.gitignore` now excludes `.env`, keys, certificates and secret directories | Keep `.env.example` placeholder-only |

Security blocker: the current default DSN contains `password=svpass`, the setup
creates a superuser role, and local initialization uses trust authentication.
These are acceptable only as explicitly labelled local-demo defaults; they do
not satisfy the submitted application’s security requirement.

## 6. Professional practices — 5 marks

| Requirement | Status | Current evidence | Required action |
|---|---|---|---|
| GitHub repository | Partial | Remote exists | Change visibility from public to private |
| Private repository | Missing | GitHub API reports `public` | Make private before submission |
| Team collaborators | Needs verification | Unauthenticated API cannot list collaborators | Verify both team members in repository settings |
| `main` branch | Complete | Local and remote `main` verified | Protect stable branch if possible |
| `dev` branch | Missing | Only remote `main` exists | Create `dev`; use feature branches/PRs |
| 10 meaningful commits | Missing | Exactly 1 commit | Commit real milestones progressively; do not manufacture empty commits |
| Meaningful commit messages | Partial | Existing commit is meaningful | Continue one deliverable per commit |
| README description | Partial | Vector subsystem documented | Add full-stack description and screenshots/live URL |
| README installation/stack/execution | Partial | Local Phase 1/2 instructions exist | Add frontend/backend/Docker/deployment instructions |
| `.gitignore` secrets | Complete | Environment, keys, certificates, dumps and generated secrets are excluded | Review before every deployment |
| Native backup | Missing | No `pg_dump` script/evidence | Add timestamped backup command/script |
| Native restore | Missing | No `pg_restore` workflow/test | Restore into clean DB and record verification |
| Docker | Missing | README contains only an example `docker run` | Add Dockerfiles and Compose configuration |
| Docker Hub | Missing | No evidence | Publish images if required by faculty wording |
| Cloud deployment | Missing | None | Deploy frontend, backend and PostgreSQL |
| Live URL | Missing | None | Add verified URL to README/submission |

## Bonus opportunities — up to 3 marks

| Bonus | Status | Current evidence | Required action |
|---|---|---|---|
| CI/CD | Missing | No workflow | GitHub Actions: tests, lint, build, deploy |
| Custom domain | Missing | None | Optional after live deployment |
| One industry practice | Partial | 10 unit tests, query/health/fault logs, HNSW optimization | Integrate tests and audit logging into full-stack app and CI |

## Revised marks-first implementation order

### Milestone 0 — Resolve identity and freeze the design

- Confirm `BCSE307L` versus `BCSE302P`.
- Confirm final title and faculty-approved domain.
- Approve ERD/table list before generating Django migrations.
- Confirm responsibility split with Kush and Arnav.

### Milestone 1 — Planning and database design (protect 8 marks)

- Formal proposal: problem, objectives, scope, team responsibilities, stack,
  deliverables and Gantt chart.
- ER diagram containing every final table.
- Relational schema, data dictionary, constraints, indexes and 3NF proof.
- No application-table implementation before design review.

### Milestone 2 — Django ORM database implementation (protect 8 marks)

- Scaffold Django/DRF backend without deleting existing scripts.
- Create custom `users` model first.
- Implement final entities and relationships through migrations.
- Import existing 480 documents/canaries through a data migration/management
  command rather than treating CSV scripts as the application data layer.
- Implement ORM CRUD, joins, aggregates, view, transaction, procedure, trigger
  and indexes; keep raw SQL only for justified pgvector/database features.

### Milestone 3 — Authentication and security (protect 5 marks)

- Login, logout, `/me`, secure tokens/sessions and password hashing.
- `ADMIN` and `MEMBER` roles in the single users table.
- Role-aware API permissions, pages and menus.
- Environment-only secrets and expanded `.gitignore`.
- Security tests for unauthenticated access, wrong roles and hostile inputs.

### Milestone 4 — React application (protect 4 marks)

- Responsive login and dashboard.
- Workspace/collection/document CRUD.
- Semantic search, filters and pagination.
- Form validation and consistent API/UI exception handling.
- Health and maintenance views visible only to authorized roles.

### Milestone 5 — Professional delivery (protect 5 marks)

- Private GitHub repository, collaborators, `main` + `dev`, progressive commits.
- Dockerfiles, Compose and Docker Hub images.
- `pg_dump`/`pg_restore` scripts and tested recovery evidence.
- Cloud deployment and verified live URL.
- Complete README and submission checklist.

### Milestone 6 — Self-healing differentiator and bonus

- Preserve Phase 2 monitoring and Phase 3 reversible fault lab.
- Add a diagnosis engine that maps independent signals to causes.
- Add targeted repairs: stale-row re-embedding, index recreation/rebuild, and
  `VACUUM ANALYZE` for bloat.
- Re-measure after every repair.
- Store detector input, diagnosis, action, affected rows, before/after metrics,
  result and error in an immutable audit log.
- Only then describe the system as automatically self-healing.
- Add CI/CD and monitoring/testing evidence for bonus marks.

## Simple six-week Gantt proposal

| Workstream | W1 | W2 | W3 | W4 | W5 | W6 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Proposal, ERD, schema, dictionary, 3NF | ■ | ■ |  |  |  |  |
| Django models, migrations, CRUD, DB features |  | ■ | ■ |  |  |  |
| Authentication, RBAC and security |  |  | ■ | ■ |  |  |
| React UI, search, filters and pagination |  |  | ■ | ■ | ■ |  |
| Docker, backup/restore and cloud deployment |  |  |  | ■ | ■ |  |
| Diagnosis, targeted repair and audit |  |  |  |  | ■ | ■ |
| Tests, documentation and final demo |  |  |  |  | ■ | ■ |

Adjust week labels to the faculty deadline after it is confirmed.

## Recommended responsibility split (requires team confirmation)

| Area | Proposed owner | Support |
|---|---|---|
| Django models, PostgreSQL features, pgvector integration | Kush | Arnav |
| React responsive UI and role-aware navigation | Arnav | Kush |
| Authentication, API integration and Docker | Shared | Shared |
| ERD, 3NF, data dictionary and proposal | Shared | Shared |
| Cloud deployment, backup/restore and README | Shared | Shared |
| Self-healing diagnosis/repair and evaluation | Kush | Arnav |

## Definition of automatic healing

The project may claim automatic healing only when one unattended workflow can:

```text
measure → diagnose → select repair → execute repair → re-measure → audit result
```

The current full experiment is valuable evidence, but restoration is still
hard-coded by the experiment runner. It therefore remains a reversible fault
experiment, not autonomous healing.
