import uuid

from apps.common.contracts.user_info_contracts import UserDisplayDTO, UserInfoProtocol
from apps.users.models import User


class DjangoUserInfoProvider:
    """Implements UserInfoProtocol: batch-resolves user display names within tenant."""

    def get_users_display(
        self,
        ids: list[uuid.UUID],
        tenant_id: uuid.UUID,
    ) -> dict[uuid.UUID, UserDisplayDTO]:
        if not ids:
            return {}
        users = User.objects.filter(id__in=ids, tenant_id=tenant_id).values("id", "first_name", "last_name")
        result: dict[uuid.UUID, UserDisplayDTO] = {}
        for u in users:
            uid = u["id"]
            full_name = f"{u['first_name']} {u['last_name']}".strip() or str(uid)
            result[uid] = UserDisplayDTO(user_id=uid, full_name=full_name)
        return result
