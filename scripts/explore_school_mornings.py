"""Exploration (11 Oct 2026): alert at 08:00 per raion, zoning counterfactual, MoE control-point simulation.
Run: python scripts/explore_school_mornings.py data/raw/vadimkin/official_data_uk.csv"""
import sys, pandas as pd, numpy as np
df = pd.read_csv(sys.argv[1], parse_dates=['started_at','finished_at']).dropna(subset=['finished_at'])
n0=len(df); df=df.drop_duplicates(); print('rows',n0,'after exact dedup',len(df))
df=df[(df.finished_at>df.started_at)&((df.finished_at-df.started_at)<pd.Timedelta(hours=24))]
df=df[df.started_at>='2025-12-15']
for c in ('started_at','finished_at'): df[c]=df[c].dt.tz_convert('UTC').dt.tz_localize(None)  # raion-level regime
raions=df[df.level=='raion'].groupby('oblast').raion.unique().to_dict()
units=[(o,r) for o,rs in raions.items() for r in rs]+[('м. Київ',None)]
def intervals(o,r,mode):
    if r is None: sub=df[df.oblast==o]
    elif mode=='actual': sub=df[(df.oblast==o)&((df.level=='oblast')|((df.level=='raion')&(df.raion==r)))]
    else: sub=df[(df.oblast==o)&(df.level.isin(['oblast','raion']))]   # counterfactual: oblast-wide
    return list(zip(sub.started_at.values,sub.finished_at.values))
days=[d for d in pd.date_range('2026-01-12','2026-05-29',freq='D',tz='Europe/Kyiv') if d.weekday()<5]+\
     [d for d in pd.date_range('2026-09-01','2026-09-05',freq='D',tz='Europe/Kyiv') if d.weekday()<5]
def active_at(iv,t):
    t=np.datetime64(t.tz_convert('UTC').tz_localize(None))
    ends=[e for s,e in iv if s<=t<e]
    return max(ends) if ends else None
def end_of_chain(iv,t_end):
    # extend through overlapping/adjacent intervals
    changed=True
    while changed:
        changed=False
        for s,e in iv:
            if s<=t_end<e: t_end=e; changed=True
    return t_end
rows=[]
for o,r in units:
    ivA=intervals(o,r,'actual')
    ivC=intervals(o,r,'cf') if r else ivA
    for d in days:
        t8=d+pd.Timedelta(hours=8)
        for mode,iv in (('actual',ivA),('cf',ivC)):
            e=active_at(iv,t8)
            if e is None: rows.append((o,r,d,mode,0,None)); continue
            e=end_of_chain(iv,e)
            allclear=pd.Timestamp(e).tz_localize('UTC').tz_convert('Europe/Kyiv')
            rows.append((o,r,d,mode,1,(allclear-d).total_seconds()/3600))
R=pd.DataFrame(rows,columns=['oblast','raion','day','mode','alert8','clear_h'])
A=R[R['mode']=='actual']; C=R[R['mode']=='cf']
print('units',len(units),'school days',len(days))
print('share of unit-mornings with alert at 08:00: actual %.1f%%, oblast-wide counterfactual %.1f%%'%(A.alert8.mean()*100,C.alert8.mean()*100))
print('mornings saved by zoning (unit-days):', int(C.alert8.sum()-A.alert8.sum()), 'of', len(A))
# control point policy: in-person hours achieved 08-15 given rule, ignoring alerts after resumption
def inperson(clear_h,cp,trans=1.0):
    if pd.isna(clear_h): return 7.0
    if clear_h<=cp: return max(0,15-(clear_h+trans))
    return 0.0
for cp in (9,10,11,12):
    A['ip_%d'%cp]=[inperson(h,cp) if a else 7.0 for a,h in zip(A.alert8,A.clear_h)]
    A['rem_%d'%cp]=[(1 if a and (h>cp) else 0) for a,h in zip(A.alert8,A.clear_h)]
g=A.groupby(['oblast','raion'],dropna=False).agg(alert8=('alert8','mean'),**{f'ip{cp}':(f'ip_{cp}','mean') for cp in (9,10,11,12)},**{f'remote{cp}':(f'rem_{cp}','mean') for cp in (9,10,11,12)})
print(g.alert8.describe().round(3).to_string())
print('raions with alert at 08:00 on >=20% of school days:', (g.alert8>=0.2).sum(), '/', len(g))
mid=g[(g.alert8>=0.05)&(g.alert8<0.6)]
print('mid-intensity raions:',len(mid))
print('mean in-person hours/day by control point (mid raions):', mid[['ip9','ip10','ip11','ip12']].mean().round(3).to_dict())
print('fully-remote day share by control point (mid raions):', mid[['remote9','remote10','remote11','remote12']].mean().round(3).to_dict())
best=mid[['ip9','ip10','ip11','ip12']].idxmax(axis=1).value_counts(); print('best control point counts:',best.to_dict())
print(g.loc[[('м. Київ',np.nan)] if ('м. Київ',np.nan) in g.index else g.index[:0]].round(3).to_string())
active=A[A.alert8==1].clear_h; print('given alert at 08:00: P(clear by 10)=%.2f P(by 11)=%.2f, median clear %.2f h'%((active<=10).mean(),(active<=11).mean(),active.median()))
print(g.sort_values('alert8',ascending=False).head(8)[['alert8','ip10','remote10']].round(2).to_string())
