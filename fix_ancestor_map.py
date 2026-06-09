import sys

with open("api/app/services/directory.py", "r") as f:
    content = f.read()

old_func = """async def get_ancestor_map(
    db: AsyncSession, directory_ids: set[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str]]:
    \"\"\"Return (name_path, slug_path) for each directory_id in a single recursive CTE.

    name_path: space-joined names from root to the directory (inclusive).
    slug_path: slash-joined slugs from root to the directory (inclusive).

    Used by batch indexers to avoid O(depth × n) individual queries.
    \"\"\"
    if not directory_ids:
        return {}

    base_case = (
        select(
            Directory.id,
            Directory.parent_id,
            Directory.name.cast(String).label("name_path"),
            Directory.slug.cast(String).label("slug_path"),
        )
        .where(Directory.parent_id.is_(None))
        .cte(name="ancestor_map_cte", recursive=True)
    )

    base_alias = aliased(base_case, name="p")
    dir_alias = aliased(Directory, name="d")

    recursive_case = select(
        dir_alias.id,
        dir_alias.parent_id,
        (base_alias.c.name_path + " " + dir_alias.name).label("name_path"),
        (base_alias.c.slug_path + "/" + dir_alias.slug).label("slug_path"),
    ).join(base_alias, dir_alias.parent_id == base_alias.c.id)

    cte = base_case.union_all(recursive_case)
    stmt = select(cte.c.id, cte.c.name_path, cte.c.slug_path).where(
        cte.c.id.in_(list(directory_ids))
    )
    result = await db.execute(stmt)
    return {row.id: (row.name_path, row.slug_path) for row in result.all()}"""

new_func = """async def get_ancestor_map(
    db: AsyncSession, directory_ids: set[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str]]:
    \"\"\"Return (name_path, slug_path) for each directory_id using a bottom-up recursive CTE.

    name_path: space-joined names from root to the directory (inclusive).
    slug_path: slash-joined slugs from root to the directory (inclusive).

    Used by batch indexers to avoid O(depth × n) individual queries.
    \"\"\"
    if not directory_ids:
        return {}

    base_case = (
        select(
            Directory.id.label("start_id"),
            Directory.id,
            Directory.parent_id,
            Directory.name,
            Directory.slug,
            literal(0).label("depth"),
        )
        .where(Directory.id.in_(directory_ids))
        .cte(name="ancestor_map_cte", recursive=True)
    )

    base_alias = aliased(base_case, name="p")
    dir_alias = aliased(Directory, name="d")

    recursive_case = select(
        base_alias.c.start_id,
        dir_alias.id,
        dir_alias.parent_id,
        dir_alias.name,
        dir_alias.slug,
        (base_alias.c.depth + 1).label("depth"),
    ).join(base_alias, dir_alias.id == base_alias.c.parent_id)

    cte = base_case.union_all(recursive_case)
    stmt = select(cte.c.start_id, cte.c.name, cte.c.slug).order_by(cte.c.start_id, cte.c.depth.desc())
    result = await db.execute(stmt)

    paths: dict[uuid.UUID, tuple[list[str], list[str]]] = {}
    for start_id, name, slug in result.all():
        if start_id not in paths:
            paths[start_id] = ([], [])
        paths[start_id][0].append(name)
        paths[start_id][1].append(slug)

    return {k: (" ".join(v[0]), "/".join(v[1])) for k, v in paths.items()}"""

if old_func in content:
    content = content.replace(old_func, new_func)
    with open("api/app/services/directory.py", "w") as f:
        f.write(content)
    print("Replaced successfully!")
else:
    print("Could not find old func. Old func snippet:")
    print(old_func)
