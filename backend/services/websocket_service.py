# backend/services/websocket_service.py
#
# WebSocket support has been removed. Dashboard updates use REST polling.
# This file is retained as a stub to avoid breaking any lingering imports
# during the transition; all functions are no-ops.

async def broadcast_packet_available(filename: str, dest: str) -> None:
    pass


async def broadcast_processing_complete(run_id: int) -> None:
    pass


async def broadcast_nodelist_available(league_id: str, game_type: str) -> None:
    pass
