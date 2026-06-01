import uuid
from typing import Any, Optional

import jwt
from django.conf import settings
from ninja.security import HttpBearer


class JWTAuth(HttpBearer):
    def authenticate(self, request, token: str) -> Optional[dict[str, Any]]:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

        user_id = payload.get("user_id")
        tenant_id = payload.get("tenant_id")
        if not user_id or not tenant_id:
            return None

        request.jwt_user_id = uuid.UUID(user_id)
        request.jwt_tenant_id = uuid.UUID(tenant_id)
        return payload


jwt_auth = JWTAuth()
