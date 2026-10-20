-- Schéma de la gateway, appliqué à chaque démarrage (idempotent).
-- L'extension pgvector est installée par l'administrateur (Job intake-db-init) ; si elle existe
-- déjà, cette ligne ne fait rien et ne demande aucun droit particulier.
CREATE EXTENSION IF NOT EXISTS vector;
-- Les dossiers et les résumés restent en JSON : ils sont validés par le code avant d'arriver ici.

CREATE TABLE IF NOT EXISTS intake_cases (
    case_id    text PRIMARY KEY,
    data       jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- Un créneau ne peut être pris qu'une fois, et un numéro n'a qu'un rendez-vous à la fois
-- (une nouvelle réservation du même numéro déplace l'ancienne).
CREATE TABLE IF NOT EXISTS consultations (
    slot        timestamptz PRIMARY KEY,
    caller_name text NOT NULL,
    phone_key   text NOT NULL UNIQUE,
    booked_at   timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS call_summaries (
    call_id     text PRIMARY KEY,
    data        jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

-- Base de connaissances du cabinet (RAG) : une ligne par section de connaissances/*.md.
-- vector(1536) correspond à text-embedding-3-small ; changer de modèle demande de recréer la table.
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id           text PRIMARY KEY,
    source       text NOT NULL,
    title        text NOT NULL,
    content      text NOT NULL,
    content_hash text NOT NULL,
    embedding    vector(1536) NOT NULL,
    indexed_at   timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);

-- Affaires (dossiers à plaider) : pièces importées, texte page par page, chronologie sourcée.
-- Les originaux sont conservés tels quels (original) ; le texte lu n'est qu'une copie de travail.
CREATE TABLE IF NOT EXISTS matters (
    matter_id  text PRIMARY KEY,
    title      text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS documents (
    doc_id       text PRIMARY KEY,
    matter_id    text NOT NULL REFERENCES matters ON DELETE CASCADE,
    filename     text NOT NULL,
    sha256       text NOT NULL,
    doc_type     text NOT NULL,
    doc_date     date,
    parties      jsonb NOT NULL DEFAULT '[]',
    title        text NOT NULL DEFAULT '',
    status       text NOT NULL,          -- ok | doublon | version | illisible | incomplet
    related_to   text,                   -- doc_id dont celui-ci est le doublon ou une version
    flags        jsonb NOT NULL DEFAULT '[]',
    original     bytea NOT NULL,
    imported_at  timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS documents_matter ON documents (matter_id);
CREATE TABLE IF NOT EXISTS pages (
    doc_id   text NOT NULL REFERENCES documents ON DELETE CASCADE,
    page_no  int NOT NULL,
    content  text NOT NULL,
    readable boolean NOT NULL,
    PRIMARY KEY (doc_id, page_no)
);
CREATE TABLE IF NOT EXISTS events (
    event_id   text PRIMARY KEY,
    matter_id  text NOT NULL REFERENCES matters ON DELETE CASCADE,
    doc_id     text NOT NULL REFERENCES documents ON DELETE CASCADE,
    page_no    int NOT NULL,
    event_date date,
    title      text NOT NULL,
    quote      text NOT NULL,            -- passage exact de la page, vérifié par le code
    status     text NOT NULL DEFAULT 'proposed',  -- proposed | confirmed | disputed | rejected
    note       text NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_matter ON events (matter_id, event_date);

-- Questions litigieuses d'une affaire, avec les pièces qui les appuient ou les contredisent.
CREATE TABLE IF NOT EXISTS issues (
    issue_id  text PRIMARY KEY,
    matter_id text NOT NULL REFERENCES matters ON DELETE CASCADE,
    question  text NOT NULL,
    gaps      jsonb NOT NULL DEFAULT '[]',   -- ce qui manque au dossier pour trancher
    analysed_at timestamptz
);
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id bigserial PRIMARY KEY,
    issue_id    text NOT NULL REFERENCES issues ON DELETE CASCADE,
    doc_id      text NOT NULL REFERENCES documents ON DELETE CASCADE,
    page_no     int NOT NULL,
    stance      text NOT NULL,               -- supports | contradicts | context
    quote       text NOT NULL,               -- passage exact, vérifié par le code
    why         text NOT NULL DEFAULT '',
    warning     text NOT NULL DEFAULT ''
);
