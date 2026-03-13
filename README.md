# CIN Document Processor — v2.0

> Pipeline complet : **PDF → OCR → extraction → JSON → base de données**
> Carte Nationale d'Identité marocaine (français + arabe)

---

## Structure du projet

```
cin_processor/
├── main.py                  # FastAPI — tous les endpoints
├── ocr.py                   # PaddleOCR (FR + AR)
├── extractor.py             # Extraction Regex → NLP
├── utils.py                 # PDF→image, preprocessing OpenCV
├── database.py              # SQLAlchemy async engine + modèles ORM
├── crud.py                  # Toutes les opérations DB (create/read/delete)
├── schemas.py               # Schémas Pydantic pour les endpoints DB
├── seed.py                  # Peupler la DB avec des données de test
├── generate_samples.py      # Générer 10 CIN PDF mock
├── test_extractor.py        # Tests unitaires extraction (sans OCR)
├── test_database.py         # Tests intégration DB (SQLite in-memory)
├── Dockerfile               # Image Docker multi-stage
├── docker-compose.yml       # API + PostgreSQL + pgAdmin
├── alembic.ini              # Config migrations
├── migrations/
│   ├── env.py               # Alembic async env
│   ├── script.py.mako       # Template migration
│   └── versions/
│       └── 0001_initial.py  # Schéma initial
├── requirements.txt
├── .env.example
├── temp/                    # Uploads temporaires (auto-créé)
└── data/cin_pdfs/           # 10 CIN PDF mock
```

---

## Installation rapide (SQLite — zéro config)

```bash
# 1. Environnement virtuel (Python 3.10 recommandé)
python3.10 -m venv .venv && source .venv/bin/activate

# 2. Dépendances
pip install -r requirements.txt

# 3. Générer les PDF mock
pip install reportlab
python generate_samples.py

# 4. Migrations DB (crée cin_results.db)
alembic upgrade head

# 5. (Optionnel) Peupler avec des données de test
python seed.py --count 20

# 6. Démarrer le serveur
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

API disponible sur **http://localhost:8000**
Swagger UI : **http://localhost:8000/docs**

---

## Déploiement Docker (API + PostgreSQL)

```bash
# Copier la config
cp .env.example .env

# Build + démarrage
docker-compose up --build

# Avec pgAdmin (interface web DB)
docker-compose --profile debug up --build
```

| Service  | URL                        |
|----------|----------------------------|
| API      | http://localhost:8000      |
| Swagger  | http://localhost:8000/docs |
| pgAdmin  | http://localhost:5050      |

---

## Passer à PostgreSQL

Dans `.env` :
```
DATABASE_URL=postgresql+asyncpg://cin_user:cin_secret@localhost:5432/cin_db
```

Puis :
```bash
alembic upgrade head
```

---

## Endpoints

### Système

| Méthode | Route    | Description                        |
|---------|----------|------------------------------------|
| GET     | /health  | Liveness probe                     |
| GET     | /stats   | Statistiques agrégées              |

### Traitement CIN

| Méthode | Route           | Description                                   |
|---------|-----------------|-----------------------------------------------|
| POST    | /upload-cin/    | Upload PDF uniquement (pas d'OCR)             |
| POST    | /process-cin/   | Pipeline complet → persistance → JSON         |

### Résultats (DB)

| Méthode | Route                      | Description                              |
|---------|----------------------------|------------------------------------------|
| GET     | /results/                  | Liste paginée de tous les résultats      |
| GET     | /results/{id}              | Un résultat par ID                       |
| GET     | /results/cin/{cin_number}  | Tous les résultats pour un numéro CIN    |
| DELETE  | /results/{id}              | Supprimer un enregistrement              |

---

## Exemples curl

```bash
# Health check
curl http://localhost:8000/health

# Pipeline complet
curl -X POST http://localhost:8000/process-cin/ \
     -F "file=@data/cin_pdfs/cin_sample_01.pdf"

# Liste paginée
curl "http://localhost:8000/results/?skip=0&limit=10"

# Filtrer par texte
curl "http://localhost:8000/results/?search=Ahmed"

# Uniquement les documents à réviser manuellement
curl "http://localhost:8000/results/?warnings_only=true"

# Récupérer par ID
curl http://localhost:8000/results/1

# Tous les résultats pour un numéro CIN
curl http://localhost:8000/results/cin/AB123456

# Supprimer
curl -X DELETE http://localhost:8000/results/1

# Statistiques
curl http://localhost:8000/stats
```

---

## Réponses JSON

### POST /process-cin/
```json
{
  "db_id": 42,
  "cin_number": "AB123456",
  "name": "Ahmed Benali",
  "birth_date": "2000-01-12",
  "issue_date": "2018-03-05",
  "place_of_birth": "Casablanca",
  "raw_ocr_text": "ROYAUME DU MAROC\n...",
  "warnings": [],
  "duration_ms": 1234.5
}
```

### GET /results/
```json
{
  "total": 87,
  "skip": 0,
  "limit": 20,
  "results": [
    {
      "id": 42,
      "cin_number": "AB123456",
      "name": "Ahmed Benali",
      "birth_date": "2000-01-12",
      "issue_date": "2018-03-05",
      "place_of_birth": "Casablanca",
      "has_warnings": false,
      "created_at": "2024-01-15T10:30:00Z"
    }
  ]
}
```

### GET /stats
```json
{
  "total_processed": 87,
  "success": 71,
  "partial": 14,
  "error": 2,
  "avg_duration_ms": 1842.3
}
```

---

## Schéma de la base de données

```
cin_results
  id               INTEGER  PK
  cin_number       VARCHAR(20)  INDEX
  name             VARCHAR(120)
  birth_date       VARCHAR(10)   -- ISO-8601
  issue_date       VARCHAR(10)
  place_of_birth   VARCHAR(80)
  raw_ocr_text     TEXT
  has_warnings     BOOLEAN
  warnings_text    TEXT          -- JSON array
  original_filename VARCHAR(255)
  created_at       TIMESTAMP WITH TIMEZONE

processing_logs
  id            INTEGER  PK
  result_id     INTEGER  FK → cin_results.id (CASCADE DELETE)
  status        VARCHAR(20)  -- "success" | "partial" | "error"
  duration_ms   FLOAT
  error_message TEXT
  created_at    TIMESTAMP WITH TIMEZONE
```

---

## Migrations Alembic

```bash
# Appliquer toutes les migrations
alembic upgrade head

# Créer une nouvelle migration (après avoir modifié database.py)
alembic revision --autogenerate -m "add_expiry_date_column"

# Revenir en arrière d'une version
alembic downgrade -1

# Voir l'historique
alembic history
```

---

## Tests

```bash
pip install pytest pytest-asyncio

# Tests extraction (sans OCR ni DB)
pytest test_extractor.py -v

# Tests base de données (SQLite in-memory, sans OCR)
pytest test_database.py -v

# Tous les tests
pytest -v
```

---

## Seeder

```bash
# 20 enregistrements (défaut)
python seed.py

# 50 enregistrements
python seed.py --count 50

# Vider la DB puis repeupler
python seed.py --clear --count 30
```

---

## Architecture & points d'extension

```
PDF
 └─► utils.pdf_to_images()            PyMuPDF @ 200 DPI
      └─► utils.preprocess_image()    Deskew → CLAHE → Bilateral
           └─► ocr.ocr_pdf_pages()    PaddleOCR FR + AR
                └─► extractor.CINExtractor.extract()
                      ├─ RegexExtractor    (rapide, déterministe)
                      └─ NLPExtractor      (fallback proximité)
                           └─► crud.create_cin_result()
                                └─► PostgreSQL / SQLite
```

**Ajouter un nouveau type de document :**
1. Créer `extractor_passport.py` avec `PassportExtractor`
2. Ajouter un schéma Pydantic dans `schemas.py`
3. Ajouter un endpoint `/process-passport/` dans `main.py`

**Remplacer le regex par un modèle ML :**
```python
# Dans extractor.py → CINExtractor.__init__()
from my_ml_extractor import CamembertNERExtractor
self._pipeline.append(CamembertNERExtractor("models/cin_ner/"))
```

---

## Dépannage

| Symptôme | Solution |
|----------|----------|
| `ModuleNotFoundError: paddleocr` | `pip install paddleocr paddlepaddle` |
| `libGL.so.1 not found` sur Linux | `apt-get install libgl1` |
| Premier appel lent (30–60 s) | PaddleOCR télécharge les modèles au premier démarrage |
| Tous les champs à `null` | Vérifier `raw_ocr_text` dans la réponse pour diagnostiquer |
| `asyncpg: connection refused` | Vérifier que PostgreSQL est démarré et que DATABASE_URL est correct |

---

## Licence
MIT