from fastapi import APIRouter,Depends
from sqlalchemy import select
from .database import Session
from .security import current,allow
from .trial_models import DryingTrial
from .trial_schemas import TrialIn,PairIn
from . import trial_service as svc
router=APIRouter()
@router.get('/drying-trials')
def trials(u=Depends(current)):
    with Session()as s:return [svc.trial_view(s,x)for x in s.scalars(select(DryingTrial).order_by(DryingTrial.id.desc()))]
@router.post('/drying-trials')
def create(p:TrialIn,u=Depends(current)):
    allow(u,'admin','blender')
    with Session.begin()as s:return svc.create_trial(s,p.code,u)
@router.post('/drying-trials/{id}/pairs')
def pair(id:int,p:PairIn,u=Depends(current)):
    allow(u,'admin','blender')
    with Session.begin()as s:return svc.add_pair(s,id,p,u)
@router.post('/drying-trials/{id}/close')
def close(id:int,u=Depends(current)):
    allow(u,'admin')
    with Session.begin()as s:return svc.close_trial(s,id,u)
