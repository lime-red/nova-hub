# app/services/stats_service.py

from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.database import Client, League, Packet, ProcessingRun, SequenceAlert


class StatsService:
    """Service for computing statistics"""

    def __init__(self, db: Session):
        self.db = db

    def get_dashboard_stats(self) -> Dict:
        """Get stats for dashboard"""
        now = datetime.now()
        day_ago = now - timedelta(days=1)

        total_packets = self.db.query(func.count(Packet.id)).scalar()
        total_clients = self.db.query(func.count(Client.id)).scalar()
        active_clients = (
            self.db.query(func.count(Client.id))
            .filter(Client.is_active == True)
            .scalar()
        )
        active_leagues = (
            self.db.query(func.count(League.id))
            .filter(League.is_active == True)
            .scalar()
        )
        pending_alerts = (
            self.db.query(func.count(SequenceAlert.id))
            .filter(SequenceAlert.resolved_at == None)
            .scalar()
        )

        return {
            "total_packets": total_packets or 0,
            "total_clients": total_clients or 0,
            "active_clients": active_clients or 0,
            "active_leagues": active_leagues or 0,
            "pending_alerts": pending_alerts or 0,
        }

    def get_recent_activity(self, limit: int = 20) -> List[Dict]:
        """Get recent packet activity"""
        packets = (
            self.db.query(Packet).order_by(Packet.uploaded_at.desc()).limit(limit).all()
        )

        activity = []
        for packet in packets:
            # Determine if this is an upload or download
            # Upload: packet from remote BBS to hub
            # Download: packet from hub to remote BBS

            # Try to get client from packet's foreign keys first
            client = None
            if packet.source_client_id:
                client = self.db.query(Client).filter(Client.id == packet.source_client_id).first()
            elif packet.dest_client_id:
                client = self.db.query(Client).filter(Client.id == packet.dest_client_id).first()

            # If no client found via foreign keys, try looking up via LeagueMembership
            if not client:
                from app.database import LeagueMembership
                membership = (
                    self.db.query(LeagueMembership)
                    .filter(
                        LeagueMembership.league_id == packet.league_id,
                        or_(
                            LeagueMembership.bbs_index == int(packet.source_bbs_index, 16),
                            LeagueMembership.bbs_index == int(packet.dest_bbs_index, 16),
                        )
                    )
                    .first()
                )
                if membership:
                    client = self.db.query(Client).filter(Client.id == membership.client_id).first()

            activity_type = "upload" if packet.source_bbs_index != "01" else "download"

            activity.append(
                {
                    "type": activity_type,
                    "client_name": client.bbs_name
                    if client
                    else f"BBS {packet.source_bbs_index}",
                    "filename": packet.filename,
                    "league_name": f"{packet.league.game_type} League {packet.league.league_id}",
                    "route": f"{packet.source_bbs_index} → {packet.dest_bbs_index}",
                    "timestamp": self.format_timestamp(packet.uploaded_at),
                }
            )

        return activity

    def get_activity_chart_data(self, hours: int = 24) -> tuple:
        """Get packet activity for chart"""
        now = datetime.now()
        start = now - timedelta(hours=hours)

        # Group by hour
        data = []
        labels = []

        for i in range(hours):
            hour_start = start + timedelta(hours=i)
            hour_end = hour_start + timedelta(hours=1)

            count = (
                self.db.query(func.count(Packet.id))
                .filter(
                    and_(
                        Packet.uploaded_at >= hour_start, Packet.uploaded_at < hour_end
                    )
                )
                .scalar()
            )

            data.append(count or 0)
            labels.append(hour_start.strftime("%H:%M"))

        return labels, data

    def get_league_distribution(self) -> tuple:
        """Get packet distribution by league"""
        results = (
            self.db.query(
                League.game_type,
                League.league_id,
                func.count(Packet.id).label("count"),
            )
            .join(Packet)
            .group_by(League.id)
            .all()
        )

        labels = [f"{r.game_type} {r.league_id}" for r in results]
        data = [r.count for r in results]

        return labels, data

    def get_client_stats(self, client_id: int, days: int = 1) -> Dict:
        """Get stats for a specific client"""
        from app.database import LeagueMembership

        client = self.db.query(Client).filter(Client.id == client_id).first()
        if not client:
            return None

        # Get client memberships to determine BBS indexes per league
        memberships = self.db.query(LeagueMembership).filter(
            LeagueMembership.client_id == client_id,
            LeagueMembership.is_active == True
        ).all()

        # Build list of (league_id, bbs_hex) tuples
        league_bbs_map = [(m.league_id, format(m.bbs_index, "02X")) for m in memberships]

        if not league_bbs_map:
            return {
                "total_sent": 0,
                "sent_24h": 0,
                "total_received": 0,
                "received_24h": 0,
                "last_seen": "Never",
            }

        cutoff = datetime.now() - timedelta(days=days)

        # Build filters for each league membership
        sent_filters = [
            and_(
                Packet.league_id == league_id,
                Packet.source_bbs_index == bbs_hex
            )
            for league_id, bbs_hex in league_bbs_map
        ]

        received_filters = [
            and_(
                Packet.league_id == league_id,
                Packet.dest_bbs_index == bbs_hex
            )
            for league_id, bbs_hex in league_bbs_map
        ]

        total_sent = (
            self.db.query(func.count(Packet.id))
            .filter(or_(*sent_filters))
            .scalar()
        )

        sent_recent = (
            self.db.query(func.count(Packet.id))
            .filter(
                and_(
                    or_(*sent_filters),
                    Packet.uploaded_at >= cutoff,
                )
            )
            .scalar()
        )

        total_received = (
            self.db.query(func.count(Packet.id))
            .filter(or_(*received_filters))
            .scalar()
        )

        received_recent = (
            self.db.query(func.count(Packet.id))
            .filter(
                and_(
                    or_(*received_filters),
                    Packet.uploaded_at >= cutoff,
                )
            )
            .scalar()
        )

        # Last seen - any packet where this client was source or dest in any league
        all_filters = sent_filters + received_filters
        last_packet = (
            self.db.query(Packet)
            .filter(or_(*all_filters))
            .order_by(Packet.uploaded_at.desc())
            .first()
        )

        last_seen = last_packet.uploaded_at if last_packet else None

        return {
            "total_sent": total_sent or 0,
            "sent_24h": sent_recent or 0,
            "total_received": total_received or 0,
            "received_24h": received_recent or 0,
            "last_seen": self.format_timestamp(last_seen) if last_seen else "Never",
        }

    def format_timestamp(self, dt: datetime) -> str:
        """Format timestamp as relative time"""
        if not dt:
            return "Never"

        now = datetime.now()
        diff = now - dt

        if diff.days > 0:
            return f"{diff.days} day{'s' if diff.days > 1 else ''} ago"
        elif diff.seconds >= 3600:
            hours = diff.seconds // 3600
            return f"{hours} hour{'s' if hours > 1 else ''} ago"
        elif diff.seconds >= 60:
            minutes = diff.seconds // 60
            return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
        else:
            return "Just now"
