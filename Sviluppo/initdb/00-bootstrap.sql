-- Eseguito una sola volta, al primo avvio di Postgres (volume vuoto).
CREATE DATABASE keycloak;
CREATE DATABASE rag;
-- Database di LiteLLM (utenti/chiavi/spend della UI e SSO).
CREATE DATABASE litellm;

\connect rag
CREATE EXTENSION IF NOT EXISTS vector;
