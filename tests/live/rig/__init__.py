"""Live test rig: real dosemu, real BRE installs, real packets.

Everything here needs novatest-hl. tests/live/conftest.py skips the whole tree
where the rig is absent, so `pytest tests/` on a dev box stays fast and green.
"""
