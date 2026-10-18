-- Schéma de la gateway, appliqué à chaque démarrage (idempotent).
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
