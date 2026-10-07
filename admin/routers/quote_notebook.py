"""Owner-controlled quote notebook inspection and editing."""
from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import BaseModel,Field
from admin.auth import require_scopes
from admin.routers.food_memory import scope
from core import quote_notebook as store

router=APIRouter()


@router.get('/observability/quotes')
async def listing(char_id:str=Query(min_length=1,max_length=100),query:str=Query('',max_length=200),
                  limit:int=Query(20,ge=1,le=50),_auth=Depends(require_scopes('memory.read'))):
    uid,cid=scope(char_id)
    return store.list_quotes(uid,cid,query,limit)


@router.get('/observability/quotes/{quote_id}')
async def detail(quote_id:str,char_id:str=Query(min_length=1,max_length=100),_auth=Depends(require_scopes('memory.read'))):
    uid,cid=scope(char_id)
    try:
        return store.read(uid,cid,quote_id)
    except ValueError as exc:
        raise HTTPException(404,str(exc)) from None


class Note(BaseModel):
    note:str=Field(max_length=2000)


@router.put('/settings/quotes/{quote_id}/note')
async def note(quote_id:str,body:Note,char_id:str=Query(min_length=1,max_length=100),_auth=Depends(require_scopes('admin'))):
    uid,cid=scope(char_id)
    try:
        return store.update_note(uid,cid,quote_id,body.note)
    except ValueError as exc:
        raise HTTPException(404,str(exc)) from None


@router.delete('/settings/quotes/{quote_id}')
async def delete(quote_id:str,char_id:str=Query(min_length=1,max_length=100),_auth=Depends(require_scopes('admin'))):
    uid,cid=scope(char_id)
    return store.delete(uid,cid,quote_id)
