"""Où le graphe écrit son point de reprise : la mémoire (tests, essais) ou Postgres (reprise après un redémarrage)."""

from contextlib import asynccontextmanager


@asynccontextmanager
async def sauvegarde_postgres(url: str):
    """Ouvre le point de reprise Postgres et crée ses tables au besoin (checkpoints, checkpoint_writes, ...).

        async with sauvegarde_postgres(os.environ["DATABASE_URL"]) as sauvegarde:
            graphe = construire_graphe(magasin, appeler, fichiers, sauvegarde)
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(url) as sauvegarde:
        await sauvegarde.setup()
        yield sauvegarde


def sauvegarde_memoire():
    """Point de reprise en mémoire : perdu à la fin du processus. Suffit pour les tests et pour voir la pause."""
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()
