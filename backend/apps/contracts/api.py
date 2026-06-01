from django.http import HttpRequest
from ninja import Router

router = Router(tags=["contracts"])


@router.get("/", response={200: dict})
def list_contracts(request: HttpRequest):
    return 200, {"results": [], "count": 0}
