import sys

with open("api/app/services/directory.py", "r") as f:
    content = f.read()

old_func = """async def get_directory_paths(
    db: AsyncSession, directory_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not directory_ids:
        return {}

    base_case = (
        select(
            Directory.id,
            Directory.slug,
            Directory.parent_id,
            Directory.slug.cast(String).label("full_path"),
        )
        .where(Directory.parent_id.is_(None))
        .cte(name="dir_path_cte", recursive=True)
    )

    base_alias = aliased(base_case, name="p")
    dir_alias = aliased(Directory, name="d")

    recursive_case = select(
        dir_alias.id,
        dir_alias.slug,
        dir_alias.parent_id,
        (base_alias.c.full_path + "/" + dir_alias.slug).label("full_path"),
    ).join(base_alias, dir_alias.parent_id == base_alias.c.id)

    cte = base_case.union_all(recursive_case)
    stmt = select(cte.c.id, cte.c.full_path).where(cte.c.id.in_(directory_ids))
    result = await db.execute(stmt)

    return {row.id: row.full_path for row in result.all()}"""

new_func = """async def get_directory_paths(
    db: AsyncSession, directory_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not directory_ids:
        return {}

    # Bottom-up recursive CTE starting ONLY from the requested IDs.
    base_case = (
        select(
            Directory.id.label("start_id"),
            Directory.id,
            Directory.slug,
            Directory.parent_id,
            literal(0).label("depth"),
        )
        .where(Directory.id.in_(directory_ids))
        .cte(name="dir_path_cte", recursive=True)
    )

    base_alias = aliased(base_case, name="p")
    dir_alias = aliased(Directory, name="d")

    recursive_case = select(
        base_alias.c.start_id,
        dir_alias.id,
        dir_alias.slug,
        dir_alias.parent_id,
        (base_alias.c.depth + 1).label("depth"),
    ).join(base_alias, dir_alias.id == base_alias.c.parent_id)

    cte = base_case.union_all(recursive_case)
    
    # Order by depth descending so that when we iterate, we see the root-most slug first.
    stmt = select(cte.c.start_id, cte.c.slug).order_by(cte.c.start_id, cte.c.depth.desc())
    result = await db.execute(stmt)
    
    paths: dict[uuid.UUID, list[str]] = {}
    for start_id, slug in result.all():
        paths.setdefault(start_id, []).append(slug)
        
    return {k: "/".join(v) for k, v in paths.items()}"""

if old_func in content:
    content = content.replace(old_func, new_func)
    with open("api/app/services/directory.py", "w") as f:
        f.write(content)
    print("Replaced successfully!")
else:
    print("Could not find old func. Old func snippet:")
    print(old_func)
