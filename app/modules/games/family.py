"""Global Family game repository and progression."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.models import FamilyRelationship

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
        return list(await session.scalars(select(FamilyRelationship).where(
            FamilyRelationship.user_id == user_id
        )))

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
                            # The parent command creates the actor's child edge; this check is
                            # performed on the target, who may have only one parent of each type.
                            parent_type = "father" if actor_relation == "son" else "mother"
                            existing_parent = await session.scalar(select(FamilyRelationship).where(
                                FamilyRelationship.user_id == target_id,
                                FamilyRelationship.relation == parent_type,
                            ))
                            if existing_parent:
                                raise FamilyError(f"That user already has a {parent_type}: <a href=\"tg://user?id={existing_parent.related_user_id}\">view profile</a>. This relationship cannot be changed.")
                            same_parent = await session.scalar(select(FamilyRelationship).where(
                                FamilyRelationship.user_id == target_id,
                                FamilyRelationship.relation.in_(["father", "mother"]),
                                FamilyRelationship.related_user_id == actor_id,
                            ))
                            if same_parent:
                                raise FamilyError("You have already assigned this person as your child. The relationship cannot be changed.")
                        else:
                            existing_parent = await session.scalar(select(FamilyRelationship).where(
                                FamilyRelationship.user_id == actor_id,
                                FamilyRelationship.relation == actor_relation,
                            ))
                            if existing_parent:
                                raise FamilyError(f"You already have a {actor_relation}: <a href=\"tg://user?id={existing_parent.related_user_id}\">view profile</a>. It cannot be changed.")
                        session.add_all([
                            FamilyRelationship(user_id=actor_id, related_user_id=target_id, relation=actor_relation, created_at=now),
                            FamilyRelationship(user_id=target_id, related_user_id=actor_id, relation=reverse_relation, created_at=now),
                        ])
                    elif command in {"brother", "sister"}:
                        # Store only graph links; connected components form sibling groups.
                        for left, right in ((actor_id, target_id), (target_id, actor_id)):
                            exists = await session.scalar(select(FamilyRelationship.id).where(
                                FamilyRelationship.user_id == left,
                                FamilyRelationship.related_user_id == right,
                                FamilyRelationship.relation == "sibling",
                            ))
                            if not exists:
                                session.add(FamilyRelationship(user_id=left, related_user_id=right, relation="sibling", created_at=now))
                    elif command == "friend":
                        for left, right in ((actor_id, target_id), (target_id, actor_id)):
                            exists = await session.scalar(select(FamilyRelationship.id).where(
                                FamilyRelationship.user_id == left,
                                FamilyRelationship.related_user_id == right,
                                FamilyRelationship.relation == "friend",
                            ))
                            if not exists:
                                session.add(FamilyRelationship(user_id=left, related_user_id=right, relation="friend", created_at=now))
                    else:
                        raise FamilyError("Unknown relationship.")
                return "created"
            except IntegrityError:
                raise FamilyError("That relationship already exists or was created at the same time. Nothing was overwritten.") from None

    async def profile(self, user_id: int):
        async with self.sessions() as session:
            edges = await self._edges(session, user_id)
            count = len([edge for edge in edges if edge.relation != "sibling"])
            level, title = 0, "Newborn"
            for index, (threshold, name) in enumerate(LEVELS, 1):
                if count >= threshold:
                    level, title = index, name
            siblings = {user_id}
            frontier = [user_id]
            while frontier:
                linked = await session.scalars(select(FamilyRelationship.related_user_id).where(
                    FamilyRelationship.user_id.in_(frontier),
                    FamilyRelationship.relation == "sibling",
                ))
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

    async def leaderboard(self, limit: int = 10):
        async with self.sessions() as session:
            counts = await session.execute(
                select(FamilyRelationship.user_id, func.count(FamilyRelationship.id).label("total"))
                .group_by(FamilyRelationship.user_id).order_by(func.count(FamilyRelationship.id).desc()).limit(limit)
            )
            return list(counts.all())
