from typing import Any, Optional

import jwt
from django.conf import settings
from ninja.security import HttpBearer

from apps.common.contracts.auth_context import get_auth_context_validator


class JWTAuth(HttpBearer):
    def authenticate(self, request, token: str) -> Optional[dict[str, Any]]:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

        validator = get_auth_context_validator()
        if validator is None:
            return None
        try:
            context = validator.validate(payload)
        except Exception:
            return None

        request.jwt_user_id = context.actor_id
        request.jwt_tenant_id = context.tenant_id
        return payload


jwt_auth = JWTAuth()
