from uuid import UUID

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = ("objects", "0005_add_public_fields")
AFTER = ("objects", "0006_backfill_object_slugs")
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def migrate_object_rows():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    executor.migrate([BEFORE])
    objects = executor.loader.project_state([BEFORE]).apps.get_model("objects", "SupplyObject")
    ids = []

    def migrate(rows):
        assert objects._meta.get_field("name").max_length == 255
        assert objects._meta.get_field("slug").max_length == 255
        for index, (tenant, name, slug, deleted) in enumerate(rows, start=1):
            pk = UUID(int=index)
            ids.append(pk)
            objects.objects.using(connection.alias).create(
                id=pk,
                tenant_id=UUID(int=tenant),
                name=name,
                slug=slug,
                is_deleted=deleted,
                object_type="OTHER",
                resource_type="OTHER",
            )
        MigrationExecutor(connection).migrate([AFTER])
        state = MigrationExecutor(connection).loader.project_state([AFTER])
        migrated = state.apps.get_model("objects", "SupplyObject")
        return list(
            migrated.objects.using(connection.alias)
            .filter(pk__in=ids)
            .order_by("id")
            .values_list("slug", flat=True)
        )

    try:
        yield migrate
    finally:
        objects.objects.using(connection.alias).filter(pk__in=ids).delete()
        MigrationExecutor(connection).migrate(latest)


def test_slug_backfill_accepts_duplicate_maximum_length_names(migrate_object_rows):
    slugs = migrate_object_rows([(1, "x" * 255, None, False)] * 2)
    assert slugs == ["x" * 255, "x" * 253 + "-2"]


def test_slug_backfill_reserves_space_for_multidigit_suffixes(migrate_object_rows):
    slugs = migrate_object_rows([(1, "x" * 255, None, False)] * 100)
    assert len(set(slugs)) == 100
    assert all(len(slug) <= 255 for slug in slugs)
    assert slugs[0] == "x" * 255
    assert slugs[1] == "x" * 253 + "-2"
    assert slugs[9] == "x" * 252 + "-10"
    assert slugs[99] == "x" * 251 + "-100"


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        pytest.param(
            [
                (1, "Existing", "x" * 255, False),
                (1, "Existing suffix", "x" * 253 + "-2", False),
                (1, "x" * 255, None, False),
            ],
            ["x" * 255, "x" * 253 + "-2", "x" * 253 + "-3"],
            id="preserve-existing-slugs-and-skip-occupied-suffix",
        ),
        pytest.param(
            [
                (1, "Existing A", "x" * 253 + "aa", False),
                (1, "Existing B", "x" * 253 + "bb", False),
                (1, "x" * 253 + "aa", None, False),
                (1, "x" * 253 + "bb", None, False),
            ],
            ["x" * 253 + "aa", "x" * 253 + "bb", "x" * 253 + "-2", "x" * 253 + "-3"],
            id="different-bases-collide-after-suffix-truncation",
        ),
        pytest.param(
            [(1, "я" * 255, None, False)] * 2,
            ["я" * 255, "я" * 253 + "-2"],
            id="unicode-policy-and-character-limit",
        ),
        pytest.param(
            [(1, "ﬃ" * 86, None, False), (1, "ﬃ" * 85 + "fff", None, False)],
            ["ffi" * 85, "ffi" * 84 + "f-2"],
            id="unicode-normalization-expands-and-initial-truncation-collides",
        ),
        pytest.param(
            [(1, "", None, False), (1, "!!!", None, False), (1, "   ", None, False)],
            ["object", "object-2", "object-3"],
            id="blank-and-unsluggable-fallback",
        ),
        pytest.param(
            [(1, "Same Name", None, False), (2, "Same Name", None, False)],
            ["same-name", "same-name"],
            id="tenants-do-not-share-collision-state",
        ),
        pytest.param(
            [
                (1, "Deleted occupied", "same-name", True),
                (1, "Same Name", None, True),
                (1, "Same Name", None, False),
            ],
            ["same-name", None, "same-name"],
            id="deleted-rows-neither-reserve-nor-receive-slugs",
        ),
    ],
)
def test_slug_backfill_preserves_existing_contract(migrate_object_rows, rows, expected):
    slugs = migrate_object_rows(rows)
    assert slugs == expected
    assert all(slug is None or len(slug) <= 255 for slug in slugs)


@pytest.mark.django_db(transaction=True, databases=["default", "replica"])
def test_slug_backfill_uses_schema_editor_alias():
    from django.db import connections
    from django.test import override_settings

    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    executor.migrate([BEFORE])
    objects = executor.loader.project_state([BEFORE]).apps.get_model("objects", "SupplyObject")
    ids = [UUID(int=1), UUID(int=2)]

    def reject_default_objects_access(execute, sql, params, many, context):
        if "objects_supply_objects" in sql.lower():
            raise AssertionError("Objects migration fell back to the default DB alias")
        return execute(sql, params, many, context)

    try:
        for pk, slug in zip(ids, ["sample", None], strict=True):
            objects.objects.using("default").create(
                id=pk, tenant_id=UUID(int=1), name="Sample", slug=slug,
                object_type="OTHER", resource_type="OTHER", is_deleted=False,
            )
        # The configured replica mirrors the test DB, but has a separate real
        # PostgreSQL connection. Permit this test-only migration on that alias;
        # the production router intentionally permits DDL on default only.
        with override_settings(DATABASE_ROUTERS=[]):
            target = MigrationExecutor(connections["replica"])
            with connection.execute_wrapper(reject_default_objects_access):
                target.migrate([AFTER])
        slugs = list(
            objects.objects.using("replica").filter(pk__in=ids)
            .order_by("id").values_list("slug", flat=True)
        )
        assert slugs == ["sample", "sample-2"]
    finally:
        objects.objects.using("default").filter(pk__in=ids).delete()
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.parametrize(
    ("max_length", "used", "expected"),
    [
        (2, {"xx"}, "-2"),
        (3, {"xxx"} | {f"x-{n}" for n in range(2, 10)}, "-10"),
        (
            4,
            {"xxxx"} | {f"xx-{n}" for n in range(2, 10)} | {f"x-{n}" for n in range(10, 100)},
            "-100",
        ),
    ],
)
def test_slug_candidate_allows_suffix_equal_to_limit(max_length, used, expected):
    from importlib import import_module

    migration = import_module("apps.objects.migrations.0006_backfill_object_slugs")
    candidate = migration._unique_slug_candidate("xxxxx", used, max_length)
    assert candidate == expected
    assert len(candidate) == max_length


def test_slug_candidate_rejects_suffix_larger_than_limit():
    from importlib import import_module

    migration = import_module("apps.objects.migrations.0006_backfill_object_slugs")
    used = {"xx"} | {f"-{n}" for n in range(2, 10)}
    with pytest.raises(ValueError, match="Slug collision suffix exceeds max_length=2"):
        migration._unique_slug_candidate("xxxxx", used, 2)
