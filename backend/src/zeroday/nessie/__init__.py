"""Capital One Nessie client.

`read` is safe for anyone (agent tools included). `write` moves money and is imported
ONLY by the approve endpoint and the seed script. The agent never gets a write function.
"""
