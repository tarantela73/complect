import uuid

import pytest

from apps.common.tenancy.context import set_tenant_context
from apps.objects.models.supply_object import ResourceType, SupplyObject, SupplyObjectType
from apps.objects.schemas.object_dto import SupplyObjectCreateRequest, SupplyObjectUpdateRequest
from apps.objects.services.object_service import ObjectCreateService, ObjectUpdateService, _generate_unique_slug


def _make_object(tenant_id: uuid.UUID, name: str) -> SupplyObject:
    set_tenant_context(tenant_id)
    return ObjectCreateService.execute(
        SupplyObjectCreateRequest(
            name=name,
            object_type=SupplyObjectType.BUILDING,
            resource_type=ResourceType.GAS,
        )
    )


@pytest.mark.django_db(transaction=True)
class TestGenerateUniqueSlug:
    def setup_method(self):
        self.tenant = uuid.uuid4()
        set_tenant_context(self.tenant)

    def test_cyrillic_name_slugified_unicode(self):
        slug = _generate_unique_slug("Подстанция Иванова", self.tenant)
        assert slug == "подстанция-иванова"

    def test_latin_name_slugified(self):
        slug = _generate_unique_slug("Gas Station Alpha", self.tenant)
        assert slug == "gas-station-alpha"

    def test_collision_gets_suffix(self):
        _make_object(self.tenant, "Тест")
        slug = _generate_unique_slug("Тест", self.tenant)
        assert slug == "тест-2"

    def test_collision_chain(self):
        _make_object(self.tenant, "Тест")
        obj2 = _make_object(self.tenant, "Тест")
        SupplyObject.objects.filter(id=obj2.id).update(slug="тест-2")
        slug = _generate_unique_slug("Тест", self.tenant)
        assert slug == "тест-3"

    def test_different_tenants_no_conflict(self):
        other_tenant = uuid.uuid4()
        _make_object(other_tenant, "Тест")
        slug = _generate_unique_slug("Тест", self.tenant)
        assert slug == "тест"

    def test_exclude_id_allows_same_slug_on_update(self):
        obj = _make_object(self.tenant, "Тест")
        assert obj.slug == "тест"
        slug = _generate_unique_slug("Тест", self.tenant, exclude_id=obj.id)
        assert slug == "тест"

    def test_empty_name_fallback(self):
        slug = _generate_unique_slug("   ", self.tenant)
        assert slug == "object"


@pytest.mark.django_db(transaction=True)
class TestObjectCreateSlug:
    def setup_method(self):
        self.tenant = uuid.uuid4()
        set_tenant_context(self.tenant)

    def test_slug_generated_on_create(self):
        obj = _make_object(self.tenant, "Подстанция Иванова")
        assert obj.slug == "подстанция-иванова"

    def test_two_same_name_objects_different_slugs(self):
        obj1 = _make_object(self.tenant, "Тест")
        obj2 = _make_object(self.tenant, "Тест")
        assert obj1.slug == "тест"
        assert obj2.slug == "тест-2"


@pytest.mark.django_db(transaction=True)
class TestObjectUpdateSlug:
    def setup_method(self):
        self.tenant = uuid.uuid4()
        set_tenant_context(self.tenant)

    def test_name_change_regenerates_slug(self):
        obj = _make_object(self.tenant, "Старое название")
        ObjectUpdateService.execute(obj.id, SupplyObjectUpdateRequest(name="Новое название"))
        obj.refresh_from_db()
        assert obj.slug == "новое-название"

    def test_explicit_slug_override(self):
        obj = _make_object(self.tenant, "Тест")
        ObjectUpdateService.execute(obj.id, SupplyObjectUpdateRequest(slug="my-custom-slug"))
        obj.refresh_from_db()
        assert obj.slug == "my-custom-slug"

    def test_is_public_toggle(self):
        obj = _make_object(self.tenant, "Тест")
        assert obj.is_public is False
        ObjectUpdateService.execute(obj.id, SupplyObjectUpdateRequest(is_public=True))
        obj.refresh_from_db()
        assert obj.is_public is True

    def test_cover_image_key_set(self):
        obj = _make_object(self.tenant, "Тест")
        ObjectUpdateService.execute(obj.id, SupplyObjectUpdateRequest(cover_image_key="objects/abc123.jpg"))
        obj.refresh_from_db()
        assert obj.cover_image_key == "objects/abc123.jpg"

    def test_no_slug_change_if_name_unchanged(self):
        obj = _make_object(self.tenant, "Тест")
        original_slug = obj.slug
        ObjectUpdateService.execute(obj.id, SupplyObjectUpdateRequest(address="Новый адрес"))
        obj.refresh_from_db()
        assert obj.slug == original_slug


@pytest.mark.django_db(transaction=True)
class TestSlugValidation:
    def test_uppercase_slug_rejected(self):
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            SupplyObjectUpdateRequest(slug="UPPERCASE-slug")

    def test_slug_with_spaces_rejected(self):
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            SupplyObjectUpdateRequest(slug="slug with spaces")

    def test_slug_with_underscore_rejected(self):
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            SupplyObjectUpdateRequest(slug="slug_with_underscore")

    def test_valid_latin_slug_accepted(self):
        req = SupplyObjectUpdateRequest(slug="valid-slug-123")
        assert req.slug == "valid-slug-123"

    def test_valid_cyrillic_slug_accepted(self):
        req = SupplyObjectUpdateRequest(slug="подстанция-иванова")
        assert req.slug == "подстанция-иванова"
