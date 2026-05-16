"""
花园状态路由
"""

from fastapi import APIRouter, Depends

from admin.auth import verify_token
from core.garden import manager as garden_manager

router = APIRouter()


@router.get("/state", summary="获取花园状态")
async def get_garden_state(auth=Depends(verify_token)):
    return garden_manager.get_state()
