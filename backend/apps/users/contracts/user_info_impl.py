import uuid

from apps.common.contracts.user_info_contracts import UserDisplayDTO
from apps.users.models import User

_UUID_PK_MASK = 0xFFFFFFFFFFFF


def _uuid_to_pk(uid: uuid.UUID) -> int:
    return uid.int & _UUID_PK_MASK


class DjangoUserInfoProvider:
    """Implements UserInfoProtocol: batch-resolves user display names within tenant."""

    def get_users_display(
        self,
        ids: list[uuid.UUID],
        tenant_id: uuid.UUID,
    ) -> dict[uuid.UUID, UserDisplayDTO]:
        if not ids:
            return {}
        pk_to_uuid: dict[int, uuid.UUID] = {_uuid_to_pk(uid): uid for uid in ids}
        users = User.objects.filter(
            pk__in=list(pk_to_uuid.keys()), tenant_id=tenant_id
        ).values("pk", "first_name", "last_name", "username")
        result: dict[uuid.UUID, UserDisplayDTO] = {}
        for u in users:
            uid = pk_to_uuid[u["pk"]]
            full_name = f"{u['first_name']} {u['last_name']}".strip() or u["username"]
            result[uid] = UserDisplayDTO(user_id=uid, full_name=full_name)
        return result
