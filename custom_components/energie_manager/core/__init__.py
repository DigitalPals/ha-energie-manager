"""Pure-python decision core for Energie Manager.

Nothing in this package may import homeassistant — it must stay unit-testable
with plain pytest. All time is injected; never call datetime.now() here.
"""
