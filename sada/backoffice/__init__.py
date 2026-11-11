"""Sadā back-office: authenticated web application (live dashboard, khutba history,
clip validation queue, publishing).

Multi-mosque from V1: every piece of data belongs to a mosque (tenant), every user to a
mosque; in mode A a single mosque runs on the mosque's computer, in mode B the same code serves
several mosques from a central server.

    python -m sada.backoffice create-admin --username admin
    python -m sada.backoffice serve            # http://0.0.0.0:8090
"""
