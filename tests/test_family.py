from sqlalchemy import select
import pytest

from app.database.models import Base, FamilyRelationship
from app.database.session import create_database
from app.modules.games.family import FamilyError, FamilyRepository


@pytest.fixture
async def family(tmp_path):
    engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path / 'family.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield FamilyRepository(sessions), sessions
    await engine.dispose()


@pytest.mark.asyncio
async def test_parent_child_edges_are_reciprocal_and_permanent(family):
    repo, sessions = family
    await repo.add(10, 20, "son")
    async with sessions() as session:
        edges = list(await session.scalars(select(FamilyRelationship)))
    assert {(e.user_id, e.related_user_id, e.relation) for e in edges} == {
        (10, 20, "son"), (20, 10, "father")
    }
    with pytest.raises(FamilyError, match="already has a father"):
        await repo.add(30, 20, "son")
    await repo.add(10, 30, "father")
    with pytest.raises(FamilyError, match="already have a father"):
        await repo.add(10, 40, "father")


@pytest.mark.asyncio
async def test_child_cannot_make_parent_their_child_or_create_ancestor_cycle(family):
    repo, sessions = family
    await repo.add(10, 20, "son")  # 10 is 20's father.
    with pytest.raises(FamilyError, match="family cycle"):
        await repo.add(20, 10, "son")
    with pytest.raises(FamilyError):
        await repo.add(20, 10, "father")

    # The rejected commands must not alter the original parent-child edges.
    async with sessions() as session:
        edges = list(await session.scalars(select(FamilyRelationship)))
    assert {(e.user_id, e.related_user_id, e.relation) for e in edges} == {
        (10, 20, "son"), (20, 10, "father")
    }


@pytest.mark.asyncio
async def test_cannot_make_an_ancestor_a_child_in_a_multigeneration_tree(family):
    repo, _ = family
    await repo.add(1, 2, "son")
    await repo.add(2, 3, "son")
    with pytest.raises(FamilyError, match="family cycle"):
        await repo.add(3, 1, "son")


@pytest.mark.asyncio
async def test_father_and_mother_are_separate_single_slots(family):
    repo, sessions = family
    await repo.add(10, 20, "son")
    await repo.add(30, 20, "daughter")
    async with sessions() as session:
        edges = list(await session.scalars(select(FamilyRelationship).where(
            FamilyRelationship.user_id == 20
        )))
    assert {edge.relation for edge in edges} == {"father", "mother"}


@pytest.mark.asyncio
async def test_sibling_groups_merge_transitively(family):
    repo, _ = family
    await repo.add(1, 2, "brother")
    await repo.add(3, 4, "sister")
    await repo.add(2, 3, "brother")
    edges, count, level, title, siblings = await repo.profile(1)
    assert siblings == [2, 3, 4]
    assert count == 3
    assert level == 2 and title == "Beginner"


@pytest.mark.asyncio
async def test_friendship_is_reciprocal_and_can_coexist_with_family(family):
    repo, sessions = family
    await repo.add(1, 2, "friend")
    await repo.add(1, 2, "brother")
    await repo.add(1, 2, "son")
    async with sessions() as session:
        edges = list(await session.scalars(select(FamilyRelationship).where(
            FamilyRelationship.user_id == 1,
            FamilyRelationship.related_user_id == 2,
        )))
    assert {edge.relation for edge in edges} == {"friend", "sibling", "son"}


@pytest.mark.asyncio
async def test_self_relationship_is_rejected(family):
    repo, _ = family
    with pytest.raises(FamilyError, match="yourself"):
        await repo.add(1, 1, "friend")
