"""Appel du modèle pour le classement et l'extraction (sortie JSON).

Les fonctions du paquet reçoivent cet appel en paramètre : les tests y mettent un faux modèle.
"""

import json
from collections.abc import Awaitable, Callable

import litellm

import agent_vocal

AppelJson = Callable[[str, str], Awaitable[dict]]


async def appeler_json(consigne: str, texte: str) -> dict:
    reponse = await litellm.acompletion(
        **agent_vocal.parametres_llm(),
        messages=[{"role": "system", "content": consigne}, {"role": "user", "content": texte}],
        response_format={"type": "json_object"},
        temperature=0,
        timeout=60,
    )
    return json.loads(reponse.choices[0].message.content)
