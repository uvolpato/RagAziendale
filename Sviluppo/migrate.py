"""Applica le migrazioni pendenti al database rag.

    py migrate.py           applica le migrazioni non ancora applicate
    py migrate.py --stato   elenca senza applicare

Nessuna dipendenza: usa psql dentro il container Postgres. Quando
l'orchestratore esistera potra chiamare la stessa logica all'avvio.

ponytail: file SQL numerati e una tabella di registro. Un ORM qui non
servirebbe — lo schema usa CHECK, GIN, HNSW e pgvector, cioe' tutto cio'
che un ORM costringe a dichiarare come "non supportato".
"""
import hashlib
import pathlib
import subprocess
import sys

QUI = pathlib.Path(__file__).parent
MIGRAZIONI = QUI / "migrations"
DB = "rag"
CONTAINER = "postgres"

REGISTRO = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    nome        text PRIMARY KEY,
    sha256      text        NOT NULL,
    applicata_il timestamptz NOT NULL DEFAULT now()
);
"""


def psql(sql=None, file_interno=None, silenzioso=False):
    cmd = ["docker", "compose", "exec", "-T", CONTAINER,
           "psql", "-U", "postgres", "-d", DB, "-v", "ON_ERROR_STOP=1"]
    if file_interno:
        cmd += ["-f", file_interno]
    else:
        cmd += ["-tAc", sql]
    r = subprocess.run(cmd, cwd=QUI, capture_output=True, text=True)
    if r.returncode != 0 and not silenzioso:
        print(r.stdout)
        print(r.stderr, file=sys.stderr)
        raise SystemExit(f"psql ha restituito {r.returncode}")
    return r.stdout.strip()


def main():
    solo_stato = "--stato" in sys.argv
    psql(REGISTRO)
    applicate = {r.split("|")[0]: r.split("|")[1]
                 for r in psql("SELECT nome || '|' || sha256 FROM schema_migrations;").splitlines() if r}

    files = sorted(MIGRAZIONI.glob("*.sql"))
    if not files:
        raise SystemExit("nessuna migrazione in migrations/")

    pendenti = []
    for f in files:
        sha = hashlib.sha256(f.read_bytes()).hexdigest()
        if f.name not in applicate:
            pendenti.append((f, sha))
            print(f"  PENDENTE  {f.name}")
        elif applicate[f.name] != sha:
            # Una migrazione applicata e poi modificata: i DB divergono in
            # silenzio. Meglio fermarsi che proseguire.
            raise SystemExit(
                f"ERRORE: {f.name} e' stata modificata dopo essere stata applicata.\n"
                f"  Aggiungere una migrazione nuova invece di modificare questa."
            )
        else:
            print(f"  applicata {f.name}")

    if solo_stato or not pendenti:
        print(f"\n{len(pendenti)} pendenti." if not solo_stato else "")
        return

    # Copia dentro il container ed esegue. Ogni file gira in una transazione
    # (ON_ERROR_STOP + BEGIN/COMMIT impliciti di psql -f su singolo file).
    for f, sha in pendenti:
        print(f"\n== applico {f.name}")
        subprocess.run(["docker", "compose", "cp", f"migrations/{f.name}",
                        f"{CONTAINER}:/tmp/{f.name}"], cwd=QUI, check=True,
                       capture_output=True)
        psql(file_interno=f"/tmp/{f.name}")
        psql(f"INSERT INTO schema_migrations (nome, sha256) VALUES ('{f.name}', '{sha}');")
        print(f"   ok")

    print(f"\n{len(pendenti)} migrazioni applicate.")


if __name__ == "__main__":
    main()
