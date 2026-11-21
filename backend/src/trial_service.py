"""延烘首测对比试验：实投料包与配比相同的两单配对，开工前抽签分长短档。

Rules enforced here:
- A pair joins exactly two released blends with identical fed bales and kg portions, on one dryer.
- A blend can belong to at most one pair (drying_trial_members.blend_id is unique).
- The long/short arm is drawn with `secrets` before either drying run starts and is never re-drawn;
  repeating the dispatch returns the original pair instead of changing the grouping.
- The first moisture measurement of a member's drying run is its trial first test; later retests never
  replace it. The arm stays the one drawn at dispatch even if extended drying follows.
- Closing locks the trial row, so first-test registration serializes with closing. If any member is still
  missing a first test, closing refuses and waits; only a complete set is frozen with the paired estimate.
"""
import math,secrets
from decimal import Decimal
from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy import select
from .models import Blend
from .production_models import Dryer,DryingRun
from .trial_models import DryingTrial,DryingTrialPair,DryingTrialMember,DryingTrialFirstTest
from .blending import obj,get,audit,portions

MOISTURE_LIMIT=Decimal('0.05')
Z95=1.959963984540054

def now():return datetime.now(timezone.utc).isoformat()
def member_for(s,blend_id,lock=False):
    q=select(DryingTrialMember).where(DryingTrialMember.blend_id==blend_id)
    if lock:q=q.with_for_update().execution_options(populate_existing=True)
    return s.scalar(q)
def lock_trial(s,id):
    return get(s,DryingTrial,id,True)
def trial_pairs(s,trial_id):
    return list(s.scalars(select(DryingTrialPair).where(DryingTrialPair.trial_id==trial_id).order_by(DryingTrialPair.id)))
def first_tests(s,trial_id,lock=False):
    q=select(DryingTrialFirstTest).where(DryingTrialFirstTest.trial_id==trial_id).order_by(DryingTrialFirstTest.id)
    if lock:q=q.with_for_update().execution_options(populate_existing=True)
    return list(s.scalars(q))

def _fmt(v):return None if v is None else round(float(v),6)
def _rate(k,n):return None if n==0 else round(k/n,6)
def _wilson(k,n):
    if n==0:return 0.0,1.0
    p=k/n;d=1+Z95*Z95/n
    c=(p+Z95*Z95/(2*n))/d
    h=Z95/d*math.sqrt(p*(1-p)/n+Z95*Z95/(4*n*n))
    return max(0.0,c-h),min(1.0,c+h)
def _phi(a,b,c,d):
    den=math.sqrt((a+b)*(c+d)*(a+c)*(b+d))
    return 0.0 if den==0 else (a*d-b*c)/den
def _mcnemar_exact(b,c):
    n=b+c
    if n==0:return 1.0
    k=min(b,c)
    tail=sum(math.comb(n,i)for i in range(k+1))/2**n
    return min(1.0,2*tail)
def _paired(a,b,c,d):
    """Newcombe score interval for a paired risk difference (long - short)."""
    n=a+b+c+d
    if n==0:
        return {'pairs':0,'both_exceeded':0,'long_only':0,'short_only':0,'neither':0,
                'long_rate':None,'short_rate':None,'difference':None,'ci95':[None,None],
                'mcnemar_exact_p':None,'method':'尚无完整配对，不能估计两档差'}
    long_n=a+b;short_n=a+c
    p1=long_n/n;p2=short_n/n;diff=p1-p2
    l1,u1=_wilson(long_n,n);l2,u2=_wilson(short_n,n);phi=_phi(a,b,c,d)
    low=diff-math.sqrt(max(0.0,(p1-l1)**2-2*phi*(p1-l1)*(u2-p2)+(u2-p2)**2))
    high=diff+math.sqrt(max(0.0,(u1-p1)**2-2*phi*(u1-p1)*(p2-l2)+(p2-l2)**2))
    return {'pairs':n,'both_exceeded':a,'long_only':b,'short_only':c,'neither':d,
            'long_rate':_rate(long_n,n),'short_rate':_rate(short_n,n),
            'difference':_fmt(diff),'ci95':[_fmt(max(-1.0,low)),_fmt(min(1.0,high))],
            'mcnemar_exact_p':_fmt(_mcnemar_exact(b,c)),
            'method':'配对风险差（延烘-常规），Newcombe 95%区间；McNemar 精确双侧检验'}

def _stats(s,trial,pairs=None,tests=None):
    pairs=trial_pairs(s,trial.id) if pairs is None else pairs
    tests=first_tests(s,trial.id) if tests is None else tests
    by_blend={x.blend_id:x for x in tests}
    long_n=long_x=short_n=short_x=0;a=b=c=d=0;incomplete=[];missing=[]
    for p in pairs:
        lf=by_blend.get(p.long_blend_id);sf=by_blend.get(p.short_blend_id)
        for arm,bid,ft in (('long',p.long_blend_id,lf),('short',p.short_blend_id,sf)):
            if ft is None:
                missing.append({'pair_id':p.id,'blend_id':bid,'blend_code':s.get(Blend,bid).code,'arm':arm})
            elif arm=='long':long_n+=1;long_x+=int(bool(ft.exceeded))
            else:short_n+=1;short_x+=int(bool(ft.exceeded))
        if lf is not None and sf is not None:
            le=bool(lf.exceeded);se=bool(sf.exceeded)
            if le and se:a+=1
            elif le:b+=1
            elif se:c+=1
            else:d+=1
        else:incomplete.append(p.id)
    return {'arms':{'long':{'n':long_n,'exceeded':long_x,'rate':_rate(long_x,long_n)},
                    'short':{'n':short_n,'exceeded':short_x,'rate':_rate(short_x,short_n)}},
            'paired':_paired(a,b,c,d),'complete_pairs':len(pairs)-len(incomplete),
            'incomplete_pair_ids':incomplete,'missing':missing,
            'moisture_limit_pct':float(MOISTURE_LIMIT),
            'definition':'首测=该配方烘干作业的第一次含水实测；首测超标=含水>0.05%；复测不顶替首测'}

def _ft_view(s,ft):
    return {**obj(ft),'blend_code':s.get(Blend,ft.blend_id).code}
def pair_view(s,p):
    a=s.get(Blend,p.blend_a_id);b=s.get(Blend,p.blend_b_id);dryer=s.get(Dryer,p.dryer_id)
    tests={x.blend_id:x for x in s.scalars(select(DryingTrialFirstTest).where(DryingTrialFirstTest.pair_id==p.id))}
    arms={}
    for arm,bid in (('long',p.long_blend_id),('short',p.short_blend_id)):
        blend=s.get(Blend,bid) if bid else None
        arms[arm]={'blend_id':bid,'blend_code':blend.code if blend else None,
                   'first_test':_ft_view(s,tests[bid]) if bid in tests else None}
    return {**obj(p),'dryer_code':dryer.code,'blend_a_code':a.code,'blend_b_code':b.code,
            'long':arms['long'],'short':arms['short'],
            'complete':bool(arms['long']['first_test'] and arms['short']['first_test'])}

def trial_view(s,tr):
    pairs=[pair_view(s,x) for x in trial_pairs(s,tr.id)]
    base={**obj(tr),'pairs':pairs}
    if tr.state=='closed' and tr.freeze_snapshot:
        snap=tr.freeze_snapshot
        return {**base,'stats':snap['stats'],'missing':snap['missing'],'frozen':True,'freeze_snapshot':snap}
    live=_stats(s,tr)
    return {**base,'stats':live,'missing':live['missing'],'frozen':False}

def create_trial(s,code,u):
    if s.scalar(select(DryingTrial.id).where(DryingTrial.code==code)):raise HTTPException(409,'试验编号已存在')
    x=DryingTrial(code=code,state='open',created_by=u['email'],created_at=now());s.add(x);s.flush()
    audit(s,u,'drying_trial_created',code);return trial_view(s,x)

def composition(s,b):
    """Canonical fed-material signature: same PVC gate plus the same multiset of (kg, cluster snapshot).

    Two orders can never share the same physical bale (release deducts stock), so "same fed bales and
    ratio" is operationalized as the same cluster group and the same weighed kg per portion, under the
    same PVC gate. One portion with a different group, weight or gate breaks the match.
    """
    return (b.max_pvc,sorted((p.kg,p.cluster_snapshot)for p in portions(s,b)))

def _same_portions(s,a,b):
    ca=composition(s,a)
    return bool(portions(s,a)) and ca==composition(s,b)

def _existing_member(s,ids,lock=False):
    q=select(DryingTrialMember).where(DryingTrialMember.blend_id.in_(ids))
    if lock:q=q.with_for_update().execution_options(populate_existing=True)
    return list(s.scalars(q))

def add_pair(s,tid,p,u):
    trial=lock_trial(s,tid)
    if trial.state!='open':raise HTTPException(409,'试验已封账，不能继续配对或分派')
    if p.blend_a_id==p.blend_b_id:raise HTTPException(409,'同一单不能与自己配对')
    ids=sorted({p.blend_a_id,p.blend_b_id})
    def retry_or_reject():
        found=_existing_member(s,ids)
        if len(found)==2 and found[0].pair_id==found[1].pair_id and found[0].trial_id==trial.id:
            pair=get(s,DryingTrialPair,found[0].pair_id)
            if pair.dryer_id!=p.dryer_id:raise HTTPException(409,'配对已分派；重试不得改换设备或长短分组')
            return pair
        raise HTTPException(409,'该单已进入其它配对；每单只进一对，重试不改换分组')
    if _existing_member(s,ids):
        return pair_view(s,retry_or_reject())
    blends={x.id:x for x in s.scalars(select(Blend).where(Blend.id.in_(ids)).order_by(Blend.id).with_for_update().execution_options(populate_existing=True))}
    if len(blends)!=2:raise HTTPException(404,'所选配方不存在')
    # Locking re-read after acquiring blend locks: a concurrent dispatch may have just committed membership.
    if _existing_member(s,ids,lock=True):
        return pair_view(s,retry_or_reject())
    a,b=blends[ids[0]],blends[ids[1]]
    for x in (a,b):
        if x.status!='released':raise HTTPException(409,f'{x.code}未实际投料，不能进入延烘对比试验')
    if not _same_portions(s,a,b):raise HTTPException(409,'两单实投料包或配比不同，不能配对')
    # Locking current read so a drying run committed while we waited on the blend locks is visible.
    started=s.scalar(select(DryingRun.id).where(DryingRun.blend_id.in_(ids)).limit(1).with_for_update().execution_options(populate_existing=True))
    if started:raise HTTPException(409,'已有单开工，抽签须在开工前完成')
    dryer=get(s,Dryer,p.dryer_id)
    if dryer.state!='active':raise HTTPException(409,'烘干设备已停用')
    long_id=ids[secrets.randbelow(2)];short_id=ids[1]if long_id==ids[0]else ids[0]
    pair=DryingTrialPair(trial_id=trial.id,blend_a_id=ids[0],blend_b_id=ids[1],dryer_id=dryer.id,
        long_blend_id=long_id,short_blend_id=short_id,draw_token=secrets.token_hex(16),
        draw_method='secrets.randbelow(2) 开工前抽签',assigned_at=now(),assigned_by=u['email'])
    s.add(pair);s.flush()
    s.add_all([DryingTrialMember(trial_id=trial.id,pair_id=pair.id,blend_id=long_id,arm='long'),
               DryingTrialMember(trial_id=trial.id,pair_id=pair.id,blend_id=short_id,arm='short')])
    s.flush();audit(s,u,'drying_trial_assigned',f'{trial.code} / {a.code}+{b.code} / long={s.get(Blend,long_id).code}')
    return pair_view(s,pair)

def paired_dryer_for(s,blend_id,lock=False):
    """Dispatch gate used by drying runs: returns (trial, member, pair) or None for non-trial blends.

    Callers must already hold the blend row lock (create_dry does), so a locking current read here
    serializes pairing against starting the first run: whichever transaction gets the blend lock first
    commits its membership/run before the other reads it.
    """
    member=member_for(s,blend_id,lock=lock)
    if not member:return None
    return s.get(DryingTrial,member.trial_id),member,s.get(DryingTrialPair,member.pair_id)

def maybe_register_first_test(s,x,u):
    """Called after the measurement was appended; only the true first measurement is registered."""
    probe=member_for(s,x.blend_id)
    if not probe:return
    # Same first lock as add_pair/close (trial row), so registration serializes with closing.
    trial=lock_trial(s,probe.trial_id)
    if trial.state!='open':return
    member=member_for(s,x.blend_id,lock=True)
    if not member:return
    if s.scalar(select(DryingTrialFirstTest.id).where(DryingTrialFirstTest.trial_id==trial.id,DryingTrialFirstTest.blend_id==x.blend_id).with_for_update().execution_options(populate_existing=True)):return
    if len(x.measurements)!=1:return
    ft=DryingTrialFirstTest(trial_id=trial.id,pair_id=member.pair_id,blend_id=x.blend_id,arm=member.arm,
        after_moisture=x.after_moisture,moisture_limit=MOISTURE_LIMIT,
        exceeded=bool(x.after_moisture>MOISTURE_LIMIT),first_record=dict(x.measurements[0]),
        drying_snapshot={'run_id':x.id,'dryer_id':x.dryer_id,'blend_id':x.blend_id,'starts_at':x.starts_at,
                         'ends_at':x.ends_at,'input_moisture':float(x.input_moisture),'input_snapshot':x.input_snapshot},
        registered_by=u['email'],registered_at=now())
    s.add(ft);s.flush();audit(s,u,'drying_trial_first_test',f'{trial.code} / {x.blend_id} / {member.arm}')

def close_trial(s,tid,u):
    trial=lock_trial(s,tid)
    if trial.state!='open':raise HTTPException(409,'试验已封账，不能重复冻结')
    # Current read: MySQL REPEATABLE READ would otherwise return the snapshot from before the trial
    # lock, hiding first tests another transaction committed while we waited.
    pairs=list(s.scalars(select(DryingTrialPair).where(DryingTrialPair.trial_id==trial.id).order_by(DryingTrialPair.id).with_for_update().execution_options(populate_existing=True)))
    if not pairs:raise HTTPException(409,'试验还没有配对单，不能封账')
    tests=first_tests(s,trial.id,lock=True)
    stats=_stats(s,trial,pairs=pairs,tests=tests)
    missing=stats['missing']
    if missing:
        codes='、'.join(dict.fromkeys(x['blend_code'] for x in missing))
        raise HTTPException(409,f'有{len(missing)}单缺首测，继续等待收齐后再封账：{codes}')
    closed_at=now()
    tests=first_tests(s,trial.id)
    snapshot={'code':trial.code,'closed_by':u['email'],'closed_at':closed_at,'stats':stats,'missing':[],
              'pairs':[{'pair_id':pv['id'],'dryer_id':pv['dryer_id'],'dryer_code':pv['dryer_code'],
                        'blend_a_code':pv['blend_a_code'],'blend_b_code':pv['blend_b_code'],
                        'long_blend_code':pv['long']['blend_code'],'short_blend_code':pv['short']['blend_code'],
                        'long_first':pv['long']['first_test'],'short_first':pv['short']['first_test']}
                       for pv in (pair_view(s,x)for x in pairs)],
              'first_tests':[_ft_view(s,x)for x in tests]}
    for x in tests:x.frozen_at=closed_at
    trial.state='closed';trial.closed_by=u['email'];trial.closed_at=closed_at;trial.freeze_snapshot=snapshot
    s.flush();audit(s,u,'drying_trial_closed',trial.code);return trial_view(s,trial)
