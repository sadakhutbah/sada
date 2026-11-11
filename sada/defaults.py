"""Values that must be identical on both sides of the live: orchestrator config and back-office database.

A default written twice drifts silently: one copy here, imported by both processes.
"""

# Title given to a khutba when nothing else is known: label of the pipeline output, default clip
# title of a mosque created from the back-office.
CLIP_TITLE = "Khutba du vendredi"
