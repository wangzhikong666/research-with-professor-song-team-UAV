"""Fixed-camera depth foreground regions; no semantic object identity."""
from dataclasses import dataclass
import warnings
import cv2
import numpy as np
from point_quality import assess_pixel


@dataclass
class Settings:
    minimum_mm: float=600
    maximum_mm: float=4000
    foreground_delta_mm: float=50
    minimum_area_px: int=200
    maximum_changed_fraction: float=0.5
    minimum_background_pixels: int=3000


def valid_depth(raw,settings):
    return (raw>0)&(raw>=settings.minimum_mm)&(raw<=settings.maximum_mm)


def build_background(frames,settings):
    stack=np.stack(frames)
    good=valid_depth(stack,settings)
    data=np.where(good,stack,np.nan).astype(np.float32)
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore',message='All-NaN slice encountered')
        median=np.nanmedian(data,axis=0)
        mad=np.nanmedian(np.abs(data-median),axis=0)
    counts=good.sum(axis=0)
    eligible=(counts>=int(np.ceil(0.8*len(frames))))&np.isfinite(median)&(mad<=np.maximum(5,0.01*median))
    return {'median_raw':np.nan_to_num(median,nan=0),'valid':eligible,
            'valid_observations':counts.astype(np.uint16),'mad_raw':np.nan_to_num(mad,nan=0),
            'frames':len(frames)}


def find_foreground(raw,background,settings):
    empty=np.zeros(raw.shape,np.uint8)
    result={'valid':False,'position_m':None,'reason':'NO_FOREGROUND',
            'semantic_class':None,'reference_point':'Observed foreground surface sample, not object center'}
    if int(background['valid'].sum())<settings.minimum_background_pixels:
        return dict(result,reason='INSUFFICIENT_BACKGROUND'),empty
    overlap=background['valid']&valid_depth(raw,settings)
    if overlap.sum()<settings.minimum_background_pixels:
        return dict(result,reason='INSUFFICIENT_CURRENT_DEPTH'),empty
    difference=background['median_raw']-raw.astype(np.float32)
    changed=np.abs(difference[overlap])>=settings.foreground_delta_mm
    changed_fraction=float(changed.mean())
    result['background_changed_fraction']=changed_fraction
    if changed_fraction>settings.maximum_changed_fraction:
        return dict(result,reason='BACKGROUND_CHANGED_TOO_MUCH'),empty
    mask=(overlap&(difference>=settings.foreground_delta_mm)).astype(np.uint8)*255
    mask[:3]=0; mask[-3:]=0; mask[:,:3]=0; mask[:,-3:]=0
    mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
    # Morphology never grants validity to a missing depth sample.
    mask[~overlap]=0
    count,labels,stats,_=cv2.connectedComponentsWithStats(mask,connectivity=8)
    components=sorted([(int(stats[i,cv2.CC_STAT_AREA]),i) for i in range(1,count)
                        if stats[i,cv2.CC_STAT_AREA]>=settings.minimum_area_px],reverse=True)
    if not components:
        return result,mask
    if len(components)>1 and components[1][0]>=components[0][0]*0.5:
        return dict(result,reason='AMBIGUOUS_MULTIPLE_COMPONENTS',
                    candidate_areas_px=[x[0] for x in components]),mask
    area,label=components[0]
    component=labels==label
    distance=cv2.distanceTransform(component.astype(np.uint8),cv2.DIST_L2,3)
    interior=component&(distance>=3)
    values=raw[interior]
    if not len(values):
        return dict(result,reason='NO_SAFE_INTERIOR'),component.astype(np.uint8)*255
    median=float(np.median(values))
    candidate=interior&(np.abs(raw.astype(float)-median)<=10)
    ys,xs=np.nonzero(candidate)
    if not len(xs):
        return dict(result,reason='NO_REPRESENTATIVE_PIXEL'),component.astype(np.uint8)*255
    order=np.argsort(distance[ys,xs])[::-1]
    point=None
    for k in order[:100]:
        u,v=int(xs[k]),int(ys[k])
        quality=assess_pixel(raw,u,v,1,settings.minimum_mm,settings.maximum_mm,0,10000)
        if quality['valid']:
            point=quality
            break
    if point is None:
        return dict(result,reason='REPRESENTATIVE_PIXEL_QUALITY_FAILED'),component.astype(np.uint8)*255
    x,y,width,height=map(int,stats[label,:4])
    return dict(result,valid=True,reason='CANDIDATE_FOUND',pixel_uv=point['pixel_uv'],
                raw_depth=point['raw_depth'],depth_mm=point['depth_mm'],
                bbox_xywh=[x,y,width,height],mask_area_px=area,quality=point['quality'],
                region_depth_median_mm=median,
                point_selection='Valid interior pixel near region median depth; no box-center lookup'),component.astype(np.uint8)*255


def self_check():
    # All scenes here are SIMULATED_OFFLINE algorithm cases.
    s=Settings(minimum_background_pixels=1000,minimum_area_px=100)
    base=np.full((100,140),1400,np.uint16)
    bg=build_background([base.copy() for _ in range(10)],s)
    assert not find_foreground(base,bg,s)[0]['valid']
    obj=base.copy(); obj[30:60,45:75]=1000
    r,mask=find_foreground(obj,bg,s)
    assert r['valid'] and r['raw_depth']==1000
    u,v=r['pixel_uv']; assert mask[v,u] and 45<u<75 and 30<v<60
    missing=base.copy(); missing[30:60,45:75]=0
    assert not find_foreground(missing,bg,s)[0]['valid']
    behind=base.copy(); behind[30:60,45:75]=1600
    assert not find_foreground(behind,bg,s)[0]['valid']
    two=obj.copy(); two[65:95,90:120]=1000
    assert find_foreground(two,bg,s)[0]['reason']=='AMBIGUOUS_MULTIPLE_COMPONENTS'
    moved=base-200
    assert find_foreground(moved,bg,s)[0]['reason']=='BACKGROUND_CHANGED_TOO_MUCH'
    bad_bg=build_background([np.zeros_like(base) for _ in range(10)],s)
    assert find_foreground(obj,bad_bg,s)[0]['reason']=='INSUFFICIENT_BACKGROUND'
    sparse=np.zeros_like(base)
    assert find_foreground(sparse,bg,s)[0]['reason']=='INSUFFICIENT_CURRENT_DEPTH'
    ring=base.copy(); ring[25:65,35:85]=1000; ring[38:52,51:69]=1400
    r,mask=find_foreground(ring,bg,s)
    assert r['valid'] and r['raw_depth']==1000 and mask[45,60]==0
    u,v=r['pixel_uv']; assert mask[v,u] and int(ring[v,u])==1000
    unstable=build_background([base.copy() if i<5 else base-200 for i in range(10)],s)
    assert not unstable['valid'].any()
    return '10 SIMULATED_OFFLINE foreground checks passed; hardware validation is separate'
