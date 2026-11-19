# MediGraph AI

**From a handwritten script to a connected safety check.**

An AI-powered prescription intelligence web app. Type a prescription in plain
language; MediGraph AI structures it, normalises each medicine against an
RxNorm-derived dataset, maps it into a medical knowledge graph, checks
interactions against the patient's recorded allergies and conditions, and
explains every finding with its source. A retrieval-augmented chatbot answers
follow-up questions using only cited material.

> ⚕️ **Disclaimer.** MediGraph AI is an educational decision-support prototype.
> It supports but does **not** replace a doctor or pharmacist. Never start,
> stop, change or combine medicines based on this tool. The bundled interaction
> data is a curated subset, not a complete clinical database.

---

## 1. What it does

```
User → Streamlit UI (typed text or a photo/scan) → Python backend
     → OCR transcription → human check and correction (mandatory)
     → NLP prescription parser  → structured medication JSON
     → medication normalisation (RxNorm-derived dataset / live RxNav)
     → medical knowledge graph (Neo4j or NetworkX)
     → interaction + allergy + condition checks (curated FDA/DDInter-derived data)
     → dosage validation against labelled maxima + food/alcohol/spacing rules
     → RAG retrieval  → explanation generator (LLM optional)
     → medication safety report (Markdown / HTML / PDF)
     → AI chatbot
```

Ten pages: Login/Registration · Dashboard · Patient Profiles · Enter
Prescription · Prescription Analysis · Drug Interaction Report · AI Medical
Chatbot · Medication Schedule · Medication History · Settings.

### Everything runs out of the box
Heavy or paid dependencies are **optional**, and each has a working fallback
that is clearly labelled in the UI:

| Capability | Default (no setup) | Optional upgrade |
| --- | --- | --- |
| Knowledge graph | NetworkX in-process (built-in dict graph if NetworkX is absent) | Neo4j (`USE_NEO4J=true`) |
| Medication identification | Local RxNorm-derived dataset (64 medicines, brand names, RXCUIs) | Live RxNav API (`USE_RXNORM_API=true`) |
| Label text | Curated label extracts | openFDA API (`USE_OPENFDA_API=true`) |
| Chatbot generation | Extractive answers from retrieved passages | Anthropic or OpenAI LLM |
| Retrieval | TF-IDF cosine similarity (pure Python) | FAISS + sentence-transformers |
| NLP | Regex + vocabulary matching | spaCy (`en_core_web_sm`) as an extra hint |
| PDF export | reportlab | print-ready HTML fallback |
| REST API | not needed (Streamlit calls the backend directly) | FastAPI layer in `api/main.py` |
| Prescription OCR | Tesseract (offline) + PDF text layer | vision LLM for handwriting; EasyOCR |

---

## 2. Quick start

```powershell
cd "E:\AntiGravity Project\MediGraph_AI"
python -m pip install -r requirements.txt
python seed_demo.py --reset
python -m streamlit run app.py
```

Open <http://localhost:8501> and sign in with **demo / demo1234**, or create
your own account.

### Environment configuration

```powershell
Copy-Item .env.example .env
```

Then edit `.env`. Every key is optional — see the table above and the
**Settings** page in the app, which shows live status for each integration and
step-by-step instructions for enabling it. API keys are read from environment
variables only; they are never written into frontend code or the database.

### Database setup

Nothing to do: `database/schema.sql` is applied automatically on first run and
the interaction/medication datasets are seeded from `data/*.json`.

```powershell
python -c "from database.db import init_db; init_db()"   # explicit init
python seed_demo.py --reset                              # wipe + demo data
```

The schema is written to port cleanly to PostgreSQL (`INTEGER PRIMARY KEY
AUTOINCREMENT` → `GENERATED ALWAYS AS IDENTITY`, TEXT timestamps → `TIMESTAMPTZ`,
JSON-in-TEXT → `JSONB`); see the header comment in `database/schema.sql`.

### Neo4j setup (optional)

```powershell
docker run -d --name medigraph-neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/your_password neo4j:5
```

```ini
USE_NEO4J=true
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
```

Restart the app; **Settings → Integration status** will show `connected`. The
graph is also exportable as a Cypher script from **Prescription Analysis** if
you prefer to load it manually. Without Neo4j the identical graph is built in
NetworkX, so no feature is lost.

### OCR setup (scanning a prescription)

Tesseract is the offline default and needs two pieces:

```powershell
winget install UB-Mannheim.TesseractOCR
```

```powershell
python -m pip install pytesseract pypdf
```

MediGraph AI finds Tesseract on PATH or in the usual install folder. If yours is
elsewhere, add to `.env`:

```ini
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

For **handwritten** scripts, configure a vision LLM (see the LLM section). The
image is only sent to it when you tick the consent box on the upload — it never
leaves the machine otherwise. `Settings → OCR for scanned prescriptions` shows
the live status of every engine.

How it works in the app: **Enter Prescription → 📷 Scan an image (OCR)** →
upload → *Transcribe* → **check and correct the text** → *Use this text in the
prescription form* → tick the confirmation box → *Analyse*. Nothing is ever
analysed straight from an image. `assets/sample_prescription_scan.png` is a
generated prescription you can use to try it.

### Optional REST API

The Streamlit app calls `backend/` directly, so the API is not required. It
exists so the same engine can be driven by another client, exactly as the
architecture diagram describes:

```powershell
python -m pip install "fastapi[standard]" uvicorn
python -m uvicorn api.main:app --reload --port 8000
```

Interactive docs at <http://localhost:8000/docs>. Endpoints: `GET /health`,
`POST /parse`, `GET /normalize/{name}`, `POST /analyze`, `POST /chat`,
`GET /patients/{id}/schedule`, `GET /patients/{id}/history`, `GET /emergency`,
`GET /ocr/engines`, `POST /ocr` (base64 image in, draft transcription out — it
deliberately does **not** analyse what it reads).
There is no API authentication in this prototype — keep it on localhost.

### LLM setup (optional)

```ini
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-5
```
or
```ini
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

With a key, retrieved passages are passed to the model under a strict system
prompt (answer only from context, never diagnose, never recommend changes,
always cite). Without one, answers are composed extractively from the same
passages — the chatbot still works and still cites.

---

## 3. Project structure

```
MediGraph_AI/
├── app.py                     # Streamlit entry point + navigation
├── seed_demo.py               # demo account and sample data
├── requirements.txt
├── .env.example
├── frontend/
│   ├── components.py          # theme CSS, cards, badges, graph rendering
│   ├── auth.py                # login / registration
│   ├── dashboard.py
│   ├── profiles.py            # profiles, allergies, conditions
│   ├── prescription.py        # entry, analysis and interaction report pages
│   ├── chatbot.py
│   ├── schedule.py
│   ├── history.py
│   └── settings.py
├── backend/
│   ├── parser.py              # prescription NLP (regex + vocabulary + optional spaCy/LLM)
│   ├── medication_normalizer.py  # RxNorm-derived + optional RxNav/openFDA
│   ├── interaction_checker.py # drug-drug, allergy, condition, duplicate therapy
│   ├── dose_checker.py        # daily-dose arithmetic vs labelled maxima; food/timing rules
│   ├── ocr.py                 # image/PDF intake: engine selection, preprocessing, cleaning
│   ├── knowledge_graph.py     # Neo4j / NetworkX / built-in backends
│   ├── rag_engine.py          # corpus, retrieval, guardrails, LLM calls
│   ├── safety_report.py       # report assembly, Markdown, HTML, PDF
│   ├── scheduler.py           # dose schedule and adherence
│   └── pipeline.py            # end-to-end orchestration
├── database/
│   ├── schema.sql             # 12 tables with foreign keys
│   ├── db.py                  # connection, init, seeding
│   └── models.py              # CRUD for every table
├── api/
│   └── main.py                # optional FastAPI REST layer (/parse /analyze /chat ...)
├── data/
│   ├── sample_medications.json      # 64 medicines: RXCUI, class, brands, uses
│   ├── sample_interactions.json     # 70 curated interaction pairs with sources
│   ├── dose_limits.json             # labelled adult maxima + paediatric mg/kg figures
│   ├── food_timing_rules.json       # food, alcohol, grapefruit and spacing rules
│   ├── condition_cautions.json      # condition/pregnancy/age cautions
│   ├── allergy_cross_reactions.json # allergy classes and cross-reactivity
│   ├── medical_knowledge.json       # 26-document cited reference library (RAG)
│   └── sample_prescriptions.json    # 10 test prescriptions
├── utils/ (config, security, helpers)
└── tests/ (126 tests)
```

### Database tables
`users`, `patient_profiles`, `patient_allergies`, `medical_conditions`,
`medications`, `prescriptions`, `prescription_medications`, `drug_interactions`,
`safety_reports`, `medication_schedule`, `adherence_records`, `chat_history` —
with foreign keys and `ON DELETE CASCADE` throughout.

---

## 4. Sample prescriptions for testing

Each is loadable from a dropdown on the **Enter Prescription** page.

| Prescription | What it demonstrates |
| --- | --- |
| `Amoxicillin 500mg, take one capsule three times daily for five days.` | Baseline parse: name, strength, dose, frequency, duration, inferred oral route |
| `Warfarin 5 mg once daily at night.` + `Ibuprofen 400 mg tablet, one tablet three times a day after food for 7 days.` | High-concern bleeding interaction with FDA source |
| `Tab Metformin 500mg PO BD x 30 days` … | Clinical abbreviations: PO, BD, HS, OD, `x 30 days` |
| `Levothyroxine - take as directed` + `Omeprazole 20mg` | Missing fields flagged, not guessed; absorption interaction |
| `Brufen 400mg twice daily` + `Naproxen 250mg twice daily` | Brand→generic normalisation and duplicate NSAID therapy |
| Polypharmacy set (Amoxicillin, Prednisolone, Salbutamol, Propranolol) | Penicillin allergy, asthma/beta-blocker caution, inhaler route, PRN |
| `Sertraline` + `Tramadol` + `Alprazolam` | Serotonin syndrome and the opioid/benzodiazepine boxed warning |
| `Lisinopril` + `Spironolactone` + `Potassium chloride` + `Ibuprofen` | Three overlapping hyperkalaemia interactions |
| `Simvastatin 40mg at night` + `Clarithromycin 500mg twice daily` | Contraindicated statin/macrolide pairing |
| `Cetirizine` + `Acetaminophen` | "No known interaction found" plus an explicit statement of its limits |

Parsing `Amoxicillin 500mg, take one capsule three times daily for five days.`
produces exactly:

```json
{
  "medication": "Amoxicillin",
  "strength": "500mg",
  "dose": "1 capsule",
  "frequency": "3 times daily",
  "duration": "5 days",
  "route": "oral",
  "instructions": null,
  "needs_verification": []
}
```

---

## 5. Test results

```powershell
python -m pytest -q
```

**126 passed** (`tests/test_parser.py` 15, `tests/test_interactions.py` 19,
`tests/test_database.py` 21, `tests/test_chatbot.py` 15, `tests/test_dose_and_timing.py` 19,
`tests/test_ocr.py` 27, `tests/test_api.py` 10), covering every item in
the brief's testing list:

| # | Requirement | Tests |
| --- | --- | --- |
| 1 | Prescription text extraction | exact-shape parse, abbreviations, hourly dosing, q8h, routes, numbered lists, brand names, confidence |
| 2 | Medication name normalisation | brand→generic, RXCUI + source, fuzzy typo match, unknown name returns nothing |
| 3 | Multiple medication inputs | multi-line and single-line splitting, 3-medication pair expansion |
| 4 | Drug interaction detection | high-concern pair with source, duplicate therapy, same-class detection, unchecked medications |
| 5 | Allergy warnings | direct match, class match, cross-reactive class (moderate), unrelated allergy ignored |
| 6 | Missing prescription information | fields flagged not invented, empty/short input rejected, report marks gaps |
| 7 | Patient profile creation | CRUD, multiple profiles, allergies/conditions, cascade delete |
| 8 | Medication schedule | generation from written frequency, no schedule without frequency, adherence, open-ended courses, course completion |
| 9 | Chatbot responses | citation of findings, dosage from the prescription, "no reliable information" path, three guardrail classes, report rendering/export |
| 10 | Database operations | hashing/auth, duplicate usernames, prescriptions round-trip, status transitions, chat history, dataset seeding |
| + | Dosage validation | mg conversion, daily-total arithmetic, over-maximum (Major), single-dose breach, cumulative duplicate ingredient, paediatric mg/kg, refusal to compute without a stated frequency |
| + | Food & timing rules | empty-stomach, with-food, 2-hour spacing, alcohol avoidance, class-level matching, conflicting-rule detection |
| + | REST API | health, parse, analyze, normalize, chat (answer + refusal), 404 handling |
| + | OCR intake | engine discovery, oversize/empty/unsupported rejection, vision-LLM consent gate, preprocessing, unit-only text cleaning (numbers never altered), real Tesseract read of a generated prescription, blank-image failure, wrapped-line joining, letterhead filtering, stored provenance, schema migration |

Verified manually in the browser as well: sign-in, dashboard metrics,
prescription entry and analysis, Plotly knowledge graph, interaction report,
**PDF generation (real `%PDF` file, ~6.5 KB)**, chatbot answer with sources,
guardrail refusal, schedule marking, history and settings pages.

---

## 6. Safety design

- **Nothing is invented.** Any prescription field the text does not state is
  listed in `needs_verification` and rendered as *"not stated — verify"*. The
  optional LLM pass can only fill blanks, never overwrite a parsed value.
- **No clock times are guessed.** A schedule shows "Dose 2 of 3" and says
  explicitly when the prescription did not specify times.
- **"No interaction found" never means "safe".** Every report states which
  sources were checked and lists five concrete limitations (curated subset; no
  supplements, herbals, food or alcohol; no 3-way interactions; no knowledge of
  kidney/liver function or unentered medicines).
- **Dose checks state their working.** Every dosage finding shows the arithmetic
  (`1000 mg x 2 x 4 doses per day = 8000 mg`), the labelled maximum it was
  compared against, and that a prescriber may legitimately exceed it. Nothing is
  checked unless the strength and frequency were actually written.
- **Food and timing guidance defers to the pharmacy label** where the two differ.
- **Every finding carries a source** — FDA product labelling sections, FDA drug
  safety communications, or the named local dataset, with a link where one
  exists.
- **The chatbot refuses to diagnose or prescribe.** Regex guardrails catch
  diagnosis, treatment-change and prescribing requests before retrieval and
  redirect to a clinician; the LLM prompt repeats the constraint.
- **Emergency guidance** is shown in the report, in the PDF, and on the
  interaction page.
- **Security.** PBKDF2-HMAC-SHA256 with a 16-byte per-user salt (240k
  iterations); parameterised SQL everywhere; input validation and control
  character stripping; secrets only from environment variables; logs scrubbed of
  emails and long numbers; patient data stays in the local SQLite file.

---

## 7. Known limitations

1. The interaction dataset holds **70 curated pairs** across 64 medicines, with
   dose limits for 38 of them and food/timing rules covering 18 rule groups. It
   demonstrates the mechanism; it is not DDInter or a commercial database in
   full. Wire `backend/interaction_checker.py` to a licensed source for real use.
2. RXCUI values in `data/sample_medications.json` were compiled offline and
   should be verified against RxNav before any clinical use; enabling
   `USE_RXNORM_API` resolves names live instead.
3. OCR reads **printed and clearly written** prescriptions well. Cursive handwriting
   needs the vision-LLM engine (an API key), and even then the transcription must be
   checked by a person — the app enforces this and will not analyse an unconfirmed scan.
4. Interactions are evaluated pairwise; three-way effects are not modelled.
5. Supplements, herbal products, food and alcohol are out of scope.
6. Authentication is single-factor with no session expiry enforcement in the UI;
   the app is intended for local use with dummy data.
7. The extractive chatbot quotes retrieved passages rather than synthesising
   prose — configure an LLM key for fluent answers.
8. Reminders are in-app text only; no push notifications, email or SMS.
9. Dose validation compares against general adult maxima only. It does not do
   renal/hepatic adjustment, body-surface-area dosing, or titration schedules,
   and paediatric mg/kg checks exist for three medicines and need a recorded weight.
10. The REST API has no authentication and is intended for localhost use.

## 8. Mapping to the MSE-2 presentation

| Deck claim | Where it lives |
| --- | --- |
| Clinical NLP parsing of drugs, strengths, dosage | `backend/parser.py`, Prescription Analysis page |
| Entity normalisation to RxNorm / RxCUI | `backend/medication_normalizer.py` (local dataset or live RxNav) |
| Knowledge-graph traversal for conflicts | `backend/knowledge_graph.py`, graph canvas on the analysis page |
| Severity tiers Major / Moderate / Minor | `utils/helpers.py` tier mapping, shown on every badge |
| Dosage validated within safe limits | `backend/dose_checker.py`, "Dosage check" tab |
| Food interaction and spacing rules ("separate ciprofloxacin and calcium by 2 hours") | `data/food_timing_rules.json`, "Food & timing" tab and schedule page |
| Explainable RAG with sourced answers, zero invented facts | `backend/rag_engine.py` — answers composed only from retrieved passages, each cited |
| FastAPI REST application tier | `api/main.py` |
| Adherence calendar and dose logging | `backend/scheduler.py`, Medication Schedule page |
| Multiple family profiles (deck lists single-session as a limitation) | implemented — Patient Profiles supports several people per account |
| OCR of prescription images (ESE Milestone 1) | implemented — `backend/ocr.py`, Tesseract offline or a vision LLM for handwriting, with mandatory human verification |
| FHIR/EHR sync, vernacular voice, IoT pillbox (ESE roadmap) | not implemented |

## 9. Future improvements

- OCR for photographed or handwritten prescriptions (PaddleOCR / Tesseract +
  the existing parser).
- Licensed interaction data and live DDInter/RxNav interaction endpoints.
- Dense retrieval (FAISS + sentence-transformers) and a LangChain/LlamaIndex
  pipeline on top of `rag_engine.py`.
- PostgreSQL migration plus per-user encryption at rest.
- Renal/hepatic dose adjustment using recorded eGFR and liver function.
- Push/email reminders and a caregiver view.
- Clinician review mode with audit trails.
