"""
agent路由 — 供Emerald-Desktop（该前端已废弃，新Emerald-client）的agent loop调用。
提供纯LLM推理接口，不走完整pipeline。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from admin.auth import verify_token

router = APIRouter()


class AgentThinkRequest(BaseModel):
    messages: list[dict]


@router.post("/agent/think")
async def agent_think(body: AgentThinkRequest, _auth=Depends(verify_token)):
    """纯LLM推理，供桌宠agent loop调用。"""
    from core.llm_client import chat
    try:
        result = await chat(messages=body.messages)
        return {"content": result}
    except Exception as e:
        return {"content": "", "error": str(e)}
