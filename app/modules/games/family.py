"""Global Family game repository and progression."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models import FamilyRelationship, FamilyRequest

LEVELS = [
    (1, "Newborn"), (2, "Beginner"), (5, "Rookie"), (10, "Learner"),
    (25, "Explorer"), (50, "Familiar"), (100, "Socialite"), (200, "Popular"),
    (500, "Influencer"), (1_000, "Expert"), (2_000, "Veteran"), (3_500, "Pro"),
    (5_000, "Elite"), (7_500, "Champion"), (10_000, "Master"),
    (15_000, "Senior Master"), (20_000, "Grandmaster"), (30_000, "Supreme Master"),
    (40_000, "Mythic"), (50_000, "Legendary"), (60_000, "Immortal"),
    (70_000, "Divine"), (80_000, "Celestial"), (90_000, "Eternal"),
    (100_000, "Family God"),
]


class FamilyError(ValueError):
    pass


class FamilyRepository:
    """Relationships are global by Telegram user ID, never scoped to a chat."""

    def __init__(self, sessions: async_sessionmaker):
        self.sessions = sessions

    async def _edges(self, session, user_id: int):
        return list(await session.scalars(select(FamilyRelationship).where(FamilyRelationship.user_id == user_id)))

    async def add(self, actor_id: int, target_id: int, command: str) -> str:
        if actor_id == target_id:
            raise FamilyError("You cannot create a family relationship with yourself.")
        command = command.casefold()
        mapping = {
            "son": ("son", "father"), "daughter": ("daughter", "mother"),
            "father": ("father", "son"), "mother": ("mother", "daughter"),
        }
        now = datetime.now(UTC)
        async with self.sessions() as session:
            try:
                async with session.begin():
                    if command in mapping:
                        actor_relation, reverse_relation = mapping[command]
                        if actor_relation in {"son", "daughter"}:
                            parent_type = "father" if actor_relation == "son" else "mother"
                            existing_parent = await session.scalar(select(FamilyRelationship).where(
                                FamilyRelationship.user_id == target_id, FamilyRelationship.relation == parent_type))
                            if existing_parent:
                                raise FamilyError(f"That user already has a {parent_type}: <a href=\"tg://user?id={existing_parent.related_user_id}\">view profile</a>. This relationship cannot be changed.")
                            if await session.scalar(select(FamilyRelationship.id).where(
                                FamilyRelationship.user_id == target_id,
                                FamilyRelationship.relation.in_(["father", "mother"]),
                                FamilyRelationship.related_user_id == actor_id)):
                                raise FamilyError("You have already assigned this person as your child.")
                        else:
                            existing_parent = await session.scalar(select(FamilyRelationship).where(
                                FamilyRelationship.user_id == actor_id, FamilyRelationship.relation == actor_relation))
                            if existing_parent:
                                raise FamilyError(f"You already have a {actor_relation}: <a href=\"tg://user?id={existing_parent.related_user_id}\">view profile</a>. It cannot be changed.")
                        session.add_all([
                            FamilyRelationship(user_id=actor_id, related_user_id=target_id, relation=actor_relation, created_at=now),
                            FamilyRelationship(user_id=target_id, related_user_id=actor_id, relation=reverse_relation, created_at=now),
                        ])
                    elif command in {"brother", "sister", "friend"}:
                        relation = "sibling" if command in {"brother", "sister"} else "friend"
                        for left, right in ((actor_id, target_id), (target_id, actor_id)):
                            if not await session.scalar(select(FamilyRelationship.id).where(
                                FamilyRelationship.user_id == left, FamilyRelationship.related_user_id == right,
                                FamilyRelationship.relation == relation)):
                                session.add(FamilyRelationship(user_id=left, related_user_id=right, relation=relation, created_at=now))
                    else:
                        raise FamilyError("Unknown relationship.")
                return "created"
            except IntegrityError:
                raise FamilyError("That relationship already exists. Nothing was overwritten.") from None

    async def create_request(self, requester_id: int, target_id: int, kind: str, role: str | None, chat_id: int) -> int:
        if requester_id == target_id:
            raise FamilyError("You cannot send a request to yourself.")
        if kind not in {"marriage", "divorce"}:
            raise FamilyError("Unknown request type.")
        now = datetime.now(UTC)
        async with self.sessions() as session:
            async with session.begin():
                # Expire old pending requests before checking duplicates.
                await session.execute(
                    FamilyRequest.__table__.update().where(
                        FamilyRequest.status == "pending", FamilyRequest.expires_at <= now
                    ).values(status="expired", resolved_at=now)
                )
                if await session.scalar(select(FamilyRequest.id).where(
                    FamilyRequest.status == "pending",
                    FamilyRequest.kind == kind,
                    ((FamilyRequest.requester_id == requester_id) & (FamilyRequest.target_id == target_id))
                    | ((FamilyRequest.requester_id == target_id) & (FamilyRequest.target_id == requester_id)),
                )):
                    raise FamilyError("There is already a pending request between you two.")
                if kind == "marriage":
                    if role not in {"wife", "husband"}:
                        raise FamilyError("Use /wife or /husband.")
                    existing = await session.scalar(select(FamilyRelationship).where(
                        FamilyRelationship.user_id.in_([requester_id, target_id]),
                        FamilyRelationship.relation.in_(["wife", "husband"]),
                    ))
                    if existing:
                        raise FamilyError("One of you is already married.")
                else:
                    partner = await session.scalar(select(FamilyRelationship).where(
                        FamilyRelationship.user_id == requester_id,
                        FamilyRelationship.related_user_id == target_id,
                        FamilyRelationship.relation.in_(["wife", "husband"]),
                    ))
                    if not partner:
                        raise FamilyError("You can only request divorce from your current spouse.")
                request = FamilyRequest(
                    requester_id=requester_id, target_id=target_id, kind=kind,
                    requester_role=role, chat_id=chat_id, status="pending",
                    created_at=now, expires_at=now + timedelta(hours=24),
                )
                session.add(request)
                await session.flush()
                return request.id

    async def resolve_request(self, request_id: int, actor_id: int, accept: bool) -> tuple[str, int, int, str]:
        now = datetime.now(UTC)
        async with self.sessions() as session:
            async with session.begin():
                req = await session.get(FamilyRequest, request_id)
                if not req:
                    raise FamilyError("This request no longer exists.")
                if req.target_id != actor_id:
                    raise FamilyError("Only the intended person can respond to this request.")
                if req.status != "pending" or req.expires_at <= now:
                    if req.status == "pending":
                        req.status, req.resolved_at = "expired", now
                    raise FamilyError("This request has expired or was already answered.")
                if not accept:
                    req.status, req.resolved_at = "declined", now
                    return req.kind, req.requester_id, req.target_id, "declined"
                if req.kind == "marriage":
                    already = await session.scalar(select(FamilyRelationship.id).where(
                        FamilyRelationship.user_id.in_([req.requester_id, req.target_id]),
                        FamilyRelationship.relation.in_(["wife", "husband"]),
                    ))
                    if already:
                        req.status, req.resolved_at = "declined", now
                        raise FamilyError("One of you is already married; this proposal cannot be accepted.")
                    role = req.requester_role
                    reverse = "husband" if role == "wife" else "wife"
                    session.add_all([
                        FamilyRelationship(user_id=req.requester_id, related_user_id=req.target_id, relation=role, created_at=now),
                        FamilyRelationship(user_id=req.target_id, related_user_id=req.requester_id, relation=reverse, created_at=now),
                    ])
                else:
                    await session.execute(FamilyRelationship.__table__.delete().where(
                        FamilyRelationship.user_id.in_([req.requester_id, req.target_id]),
                        FamilyRelationship.related_user_id.in_([req.requester_id, req.target_id]),
                        FamilyRelationship.relation.in_(["wife", "husband"]),
                    ))
                req.status, req.resolved_at = "accepted", now
                return req.kind, req.requester_id, req.target_id, "accepted"

    async def profile(self, user_id: int):
        async with self.sessions() as session:
            edges = await self._edges(session, user_id)
            count = len([edge for edge in edges if edge.relation != "sibling"])
            siblings = {user_id}
            frontier = [user_id]
            while frontier:
                linked = await session.scalars(select(FamilyRelationship.related_user_id).where(
                    FamilyRelationship.user_id.in_(frontier), FamilyRelationship.relation == "sibling"))
                next_ids = set(linked) - siblings
                siblings.update(next_ids)
                frontier = list(next_ids)
            sibling_ids = sorted(siblings - {user_id})
            count += len(sibling_ids)
            level, title = 0, "Newborn"
            for index, (threshold, name) in enumerate(LEVELS, 1):
                if count >= threshold:
                    level, title = index, name
            return edges, count, level, title, sibling_ids

    async def leaderboard(self, category: str = "total", limit: int = 10):
        relation = {"sons": "son", "daughters": "daughter"}.get(category)
        async with self.sessions() as session:
            if relation:
                stmt = select(FamilyRelationship.user_id, func.count(FamilyRelationship.id).label("score")).where(
                    FamilyRelationship.relation == relation).group_by(FamilyRelationship.user_id)
            else:
                stmt = select(FamilyRelationship.user_id, func.count(FamilyRelationship.id).label("score")).where(
                    FamilyRelationship.relation != "sibling").group_by(FamilyRelationship.user_id)
            stmt = stmt.order_by(func.count(FamilyRelationship.id).desc(), FamilyRelationship.user_id.asc()).limit(limit)
            rows = list((await session.execute(stmt)).all())
            if category == "total":
                # Sibling groups count as distinct connections for progression.
                for index, (uid, score) in enumerate(rows):
                    sibling_count = await session.scalar(select(func.count(FamilyRelationship.id)).where(
                        FamilyRelationship.user_id == uid, FamilyRelationship.relation == "sibling"))
                    rows[index] = (uid, score + int(sibling_count or 0))
                rows.sort(key=lambda row: (-row[1], row[0]))
            return rows

    async def longest_marriages(self, limit: int = 10):
        async with self.sessions() as session:
            rows = list((await session.execute(select(
                FamilyRelationship.user_id, FamilyRelationship.related_user_id, FamilyRelationship.created_at
            ).where(FamilyRelationship.relation == "wife").order_by(FamilyRelationship.created_at.asc()).limit(limit))).all())
            return rows
