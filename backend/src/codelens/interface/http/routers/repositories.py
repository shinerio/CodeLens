from typing import Annotated

from fastapi import APIRouter, Depends

from codelens.interface.http.dependencies import HttpComponents, get_components
from codelens.interface.http.dto import RepositoryInspectionRequest, RepositoryResponse

router = APIRouter(prefix="/api/repositories", tags=["repositories"])


@router.post("/inspect", response_model=RepositoryResponse)
async def inspect_repository(
    request: RepositoryInspectionRequest,
    components: Annotated[HttpComponents, Depends(get_components)],
) -> RepositoryResponse:
    """Resolve one filesystem input to a contained, stable repository identity."""

    repository = await components.repository_inspector.inspect(request.path)
    return RepositoryResponse.from_domain(repository)
