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
