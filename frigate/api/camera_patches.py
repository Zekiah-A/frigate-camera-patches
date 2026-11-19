"""Camera-scoped access to bundled patch controls."""

from fastapi import APIRouter, Depends, HTTPException, Request

from frigate.api.auth import require_camera_access, require_role
from frigate.api.defs.tags import Tags
from frigate.camera_patches.provider import PatchCommand, PatchCommandResult, PatchError
from frigate.camera_patches.runtime import CameraPatchesStatus

router = APIRouter(tags=[Tags.camera])


@router.get("/{camera_name}/patches", dependencies=[Depends(require_camera_access)])
async def patch_status(request: Request, camera_name: str) -> CameraPatchesStatus:
    """Return the camera's configured controls and current device state."""
    try:
        return await request.app.camera_patches.status(camera_name)
    except PatchError as error:
        raise HTTPException(error.status_code, error.message) from None


@router.post(
    "/{camera_name}/patches/{patch_id}/{control_id}",
    dependencies=[Depends(require_role(["admin"])), Depends(require_camera_access)],
    operation_id="set_camera_patch_control",
)
async def patch_command(
    request: Request,
    camera_name: str,
    patch_id: str,
    control_id: str,
    body: PatchCommand,
) -> PatchCommandResult:
    """Send a validated command and return its acknowledgment."""
    try:
        await request.app.camera_patches.command(
            camera_name, patch_id, control_id, body
        )
    except PatchError as error:
        raise HTTPException(error.status_code, error.message) from None
    return PatchCommandResult()
