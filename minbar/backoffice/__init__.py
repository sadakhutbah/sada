"""Back-office Minbar Live : application web authentifiée (tableau de bord du live, historique des khutbas,
file de validation des clips, publication).

Conçu multi-mosquées dès la V1 : chaque donnée appartient à une mosquée (tenant), chaque utilisateur à une
mosquée ; en mode A une seule mosquée tourne sur l'ordinateur de la mosquée, en mode B le même code sert
plusieurs mosquées depuis un serveur central.

    python -m minbar.backoffice create-admin --username admin
    python -m minbar.backoffice serve            # http://0.0.0.0:8090
"""
