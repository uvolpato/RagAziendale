-- Eseguito una sola volta, al primo avvio di Postgres (volume vuoto).
CREATE DATABASE keycloak;
CREATE DATABASE rag;

\connect rag
CREATE EXTENSION IF NOT EXISTS vector;
